# agents.py
# One set of worker handlers; what changes between modes is HOW they're invoked.
# The fixed demo (run_case) stays poll-based and unchanged. Option C — the unified
# finale — executes the model's composed plan on an EVENT-DRIVEN blackboard: each
# handler blocks on an asyncio.Condition that fires on every write, and every
# write emits a {"type":"bb"} event so the UI mirrors the real shared state.

import asyncio

from planner import compose_plan, topo_order, plan_to_json, plan_to_code

TICK = 0.05          # fixed-demo mesh poll interval
STEP = 0.9           # unit of "work" (slow enough to click Kill)


# ===========================================================================
# The blackboard — a plain dict you can watch. set() writes + notifies waiters +
# emits an event; watchers block on wait_until(predicate) and wake on any write.
# No polling.
# ===========================================================================

class Blackboard:
    def __init__(self, send):
        self._d = {}
        self._send = send
        self._cond = asyncio.Condition()

    def get(self, k, default=None): return self._d.get(k, default)
    def snapshot(self): return dict(self._d)

    async def set(self, key, value, emit=True):
        async with self._cond:
            self._d[key] = value
            self._cond.notify_all()
        if emit and self._send:
            await self._send({"type": "bb", "key": key, "value": value})

    async def poke(self):                       # wake waiters without a write (teardown)
        async with self._cond:
            self._cond.notify_all()

    async def wait_until(self, pred):
        async with self._cond:
            await self._cond.wait_for(lambda: pred(self._d))


class Session:
    def __init__(self, send, cfg, scenario, mode):
        self.send = send
        self.cfg = cfg
        self.g = cfg["log"]
        self.p = cfg["payload"]
        self.scenario = scenario
        self.mode = mode

        # fixed-demo blackboard (plain dict, polled)
        self.bb = {"request": None, "research": None, "draft": None, "validation": None, "done": False}
        self.tasks = []
        self.orch_task = None
        self.orch_alive = True
        self.stopped = False
        self.all_tasks = []

        # Option C (executed-plan) state
        self.kind = "case"               # "case" | "exec"
        self.board = None                # event-driven Blackboard (exec)
        self.out = {}                    # step id -> blackboard key it writes
        self.steps = []                  # enriched, topo-ordered
        self.terminals = []
        self.exec_done = False
        self.coord_task = None
        self.killed_nodes = set()
        self.step_tasks = {}
        self.completed = 0

    def spawn(self, coro):
        t = asyncio.create_task(coro)
        self.all_tasks.append(t)
        return t

    async def cancel_all(self):
        self.stopped = True
        self.exec_done = True
        pending = [t for t in self.all_tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def node(self, id, state, label=None, sub=None):
        ev = {"type": "node", "id": id, "state": state}
        if label is not None: ev["label"] = label
        if sub is not None: ev["sub"] = sub
        await self.send(ev)
    async def link(self, id, state):   await self.send({"type": "link", "id": id, "state": state})
    async def linkft(self, a, b, state):
        await self.send({"type": "link", "from": a, "to": b, "state": state})
    async def log(self, role, text, cls=""): await self.send({"type": "log", "role": role, "text": text, "cls": cls})
    async def status(self, text, cls=""):    await self.send({"type": "status", "text": text, "cls": cls})

    async def bb_set(self, key, value):
        """Run-case shared-state write: update the polled dict AND mirror it to the
        UI blackboard as a {"type":"bb"} event — the exact shape the executed-plan
        path emits. The dict write is synchronous (before the await) so the poll
        predicates and do_activate's re-entry guard observe the value with the
        same timing they had before emits existed. Poll-based recovery is emergent
        and untouched; this only makes the shared state visible."""
        self.bb[key] = value
        await self.send({"type": "bb", "key": key, "value": value})

    async def kill(self, target=None):
        if self.kind == "exec":
            if target:
                await self._kill_node(target)
            else:
                await self._kill_coord()
            return

        # ---- original fixed-demo kill ----
        if not self.orch_task or self.orch_task.done() or self.bb["done"] or self.stopped:
            return
        self.orch_alive = False
        self.orch_task.cancel()
        await self.node("orch", "off")
        for lk in ("orch_res", "orch_dra", "orch_val", "res_resp", "dra_resp", "val_resp"):
            await self.link(lk, "dead")
        if self.mode == "orchestrator":
            self.spawn(_stall(self))
        else:
            await self.log("system", self.g["rrWarn"], "warn")

    # --- exec kill targets ---
    async def _kill_coord(self):
        if self.exec_done:
            return
        self.orch_alive = False
        if self.coord_task and not self.coord_task.done():
            self.coord_task.cancel()
        await self.node("coord", "off")
        if self.mode == "orchestrator":
            self.spawn(_exec_stall(self))
        else:
            await self.log("system", "coordinator offline. surviving tasks coordinating peer-to-peer.", "warn")

    async def _kill_node(self, sid):
        if self.exec_done or self.board.get(self.out.get(sid)) is not None:
            return
        self.killed_nodes.add(sid)
        await self.node(sid, "off")
        t = self.step_tasks.get(sid)
        if t and not t.done():
            t.cancel()
        await self.log("system", f"{sid} killed. its dependents can't proceed; independent branches continue.", "warn")
        self.spawn(_exec_partial_watchdog(self))


# ---------------------------------------------------------------------------
# fixed-demo worker handlers (poll-based, unchanged)
# ---------------------------------------------------------------------------

async def do_research(s):
    await s.node("res", "active")
    if s.orch_alive: await s.link("orch_res", "flow")
    await asyncio.sleep(STEP)
    if s.orch_alive: await s.link("orch_res", "off")
    await s.bb_set("research", s.p["research"])
    await s.log("res", s.g["res"]); await s.node("res", "done")

async def do_draft(s):
    await s.node("dra", "active")
    if s.orch_alive: await s.link("orch_dra", "flow")
    await asyncio.sleep(STEP)
    if s.orch_alive: await s.link("orch_dra", "off")
    await s.bb_set("draft", s.p["draft"])
    await s.log("dra", s.g["dra"]); await s.node("dra", "done")

async def do_validation(s):
    await s.node("val", "active")
    if s.orch_alive:
        await s.link("orch_val", "flow")
    else:
        await s.log("dra", s.g["rrHandoff"], "reroute"); await s.link("dra_val", "reroute")
    await asyncio.sleep(STEP)
    if s.orch_alive:
        await s.link("orch_val", "off"); await s.log("val", s.g["val"])
    else:
        await s.link("dra_val", "off"); await s.log("val", s.g["rrVal"])
    await s.bb_set("validation", s.p["validation"])
    await s.node("val", "done")

async def do_activate(s):
    if s.bb["done"]: return
    await s.bb_set("done", s.p.get("activation", True))
    if s.orch_alive:
        await s.log("orch", s.g["merge"])
        for lk in ("res_resp", "dra_resp", "val_resp"): await s.link(lk, "flow")
        await asyncio.sleep(0.8)
        for lk in ("res_resp", "dra_resp", "val_resp"): await s.link(lk, "off")
        await s.node("resp", "active"); await s.log("done", s.g["done"], "good"); await s.status(s.g["doneStatus"], "")
    else:
        await s.link("val_resp", "reroute"); await asyncio.sleep(0.7); await s.link("val_resp", "off")
        await s.node("resp", "active"); await s.log("done", s.g["rrDone"], "good"); await s.status(s.g["rrStatus"], "recovered")
    s.stopped = True

async def _stall(s):
    await asyncio.sleep(0.4)
    if s.stopped: return
    await s.log("system", s.g["stall1"], "warn"); await asyncio.sleep(0.7)
    await s.log("system", s.g["stall2"], "warn"); await s.status(s.g["stallStatus"], "stalled")
    s.stopped = True

async def _orchestrator_boss(s):
    await s.log("system", s.g["start"]); await asyncio.sleep(0.5)
    await s.node("req", "done"); await s.link("req_orch", "flow"); await asyncio.sleep(0.5); await s.link("req_orch", "off")
    await s.node("orch", "active"); await s.log("orch", s.g["dispatch"]); await asyncio.sleep(0.7); await s.node("orch", "done")
    await s.bb_set("request", s.cfg["request"])
    await do_research(s); await asyncio.sleep(0.3)
    await do_draft(s); await asyncio.sleep(0.3)
    await do_validation(s); await asyncio.sleep(0.3)
    await do_activate(s)

async def _mesh_worker(s, trigger, action):
    try:
        while not trigger(s.bb):
            if s.stopped: return
            await asyncio.sleep(TICK)
        if s.stopped: return
        await action(s)
    except asyncio.CancelledError:
        raise

async def _run_mesh(s):
    await s.log("system", s.g["start"]); await asyncio.sleep(0.5)
    await s.node("req", "done"); await s.link("req_orch", "flow"); await asyncio.sleep(0.5); await s.link("req_orch", "off")
    await s.node("orch", "active"); await s.log("orch", s.g["dispatch"]); await asyncio.sleep(0.4); await s.node("orch", "done")
    await s.bb_set("request", s.cfg["request"])
    s.tasks = [
        s.spawn(_mesh_worker(s, lambda bb: bb["request"]  and not bb["research"],   do_research)),
        s.spawn(_mesh_worker(s, lambda bb: bb["research"] and not bb["draft"],      do_draft)),
        s.spawn(_mesh_worker(s, lambda bb: bb["draft"]    and not bb["validation"], do_validation)),
        s.spawn(_mesh_worker(s, lambda bb: bb["validation"] and not bb["done"],     do_activate)),
    ]
    s.orch_task = s.spawn(_mesh_worker(s, lambda bb: bb["validation"] and not bb["done"], do_activate))
    await asyncio.gather(*s.tasks, s.orch_task, return_exceptions=True)

async def run_case(s):
    try:
        if s.mode == "orchestrator":
            s.orch_task = s.spawn(_orchestrator_boss(s))
            await asyncio.gather(s.orch_task, return_exceptions=True)
        else:
            await _run_mesh(s)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# POISON WRITE — the contrasting demo. research returns a plausible, WRONG
# result (injected from scenario config; the do_* handlers are never touched).
# Orchestrator has a merge gate that rejects it; the mesh has none, so it
# reaches the irreversible step. See backend/poison_demo.py for the CLI trace
# and the third (scoped-mesh) variant. Nodes it touches go amber ("flag").
# ---------------------------------------------------------------------------

def _short(v):
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {v[k]}" for k in list(v)[:2]) + ", …}"
    return str(v)

async def run_poison(s):
    try:
        pois = s.cfg["poison"]
        bad, gate = pois["research"], pois["gate"]
        await s.log("system", "case received — one handler will return a plausible, wrong result.", "warn")
        await asyncio.sleep(0.4)
        await s.node("req", "done"); await s.link("req_orch", "flow"); await asyncio.sleep(0.4); await s.link("req_orch", "off")
        await s.node("orch", "active"); await s.log("orch", s.g["dispatch"]); await asyncio.sleep(0.5); await s.node("orch", "done")
        await s.bb_set("request", s.cfg["request"])

        # research writes the POISON (config-injected — the handler itself is untouched)
        await s.node("res", "flag"); await s.link("orch_res", "flow"); await asyncio.sleep(STEP); await s.link("orch_res", "off")
        await s.bb_set("research", bad)
        await s.log("res", f"returned {_short(bad)} — well-shaped, plausible, and WRONG.", "warn")
        await s.node("res", "flag")

        consumed = 0
        # draft reads it
        await s.node("dra", "flag"); await s.link("orch_dra", "flow"); await asyncio.sleep(STEP); await s.link("orch_dra", "off")
        await s.bb_set("draft", s.p["draft"])
        await s.log("dra", pois["spread_log"] + " — built the creative off it.", "warn")
        await s.node("dra", "flag"); consumed += 1

        # validation reads it
        await s.node("val", "flag"); await s.link("orch_val", "flow"); await asyncio.sleep(STEP); await s.link("orch_val", "off")
        await s.bb_set("validation", s.p["validation"])
        await s.log("val", pois["spread_log"] + " — checked the wrong campaign.", "warn")
        await s.node("val", "flag"); consumed += 1

        field, mx, got = gate["field"], gate["max"], bad.get(gate["field"])
        if s.mode == "orchestrator":
            # the chokepoint — validate at the merge point, before the irreversible step
            await s.log("orch", f"merge gate — {field} is {got:,}, over the {mx:,} limit. REJECTED.", "warn")
            await s.node("orch", "off", sub="gate — rejected the write")
            await s.log("done", f"bad write caught at the gate — would have been: {pois['external']}. Not fired.", "good")
            await s.status(f"poison caught at the gate — 0 external actions · {consumed} handlers touched it", "recovered")
        else:
            # no gatekeeper — the poison reaches the irreversible step and fires
            await s.node("resp", "flag"); await s.link("val_resp", "reroute"); await asyncio.sleep(0.7); await s.link("val_resp", "off")
            await s.bb_set("done", True); consumed += 1
            await s.log("done", f"⚡ WOULD FIRE: {pois['external']} — not executed.", "warn")
            await s.status(f"poison spread — {consumed} handlers acted on it · 1 external action", "stalled")
        s.stopped = True
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# FINALE (compose only) — kept for the standalone "here is the plan" reveal.
# ---------------------------------------------------------------------------

def _enrich(cfg, steps, order_index):
    catalog = cfg["catalog"]
    return sorted(
        ({"id": st["id"], "title": st["title"], "capability": st["capability"],
          "depends_on": st["depends_on"], "custom": st["capability"] not in catalog,
          "sub": ("custom · needs a human" if st["capability"] not in catalog else st["capability"])}
         for st in steps),
        key=lambda e: order_index[e["id"]],
    )

async def run_plan(s, goal):
    steps, meta = await compose_plan(s.cfg, goal)
    order_index = {st["id"]: i for i, st in enumerate(topo_order(steps))}
    enriched = _enrich(s.cfg, steps, order_index)
    await s.send({"type": "plan", "source": meta["source"], "reason": meta.get("reason"),
                  "goal": goal, "rationale": meta.get("rationale", ""), "steps": enriched,
                  "json": plan_to_json(steps, meta.get("rationale", "")),
                  "code": plan_to_code(s.cfg, goal, steps)})
    s.stopped = True


# ===========================================================================
# OPTION C — execute the model's plan on the event-driven blackboard.
# ===========================================================================

def _terminals(steps):
    dep = set()
    for st in steps: dep.update(st["depends_on"])
    return [st["id"] for st in steps if st["id"] not in dep]

def _slug(t):
    return ("".join(c if c.isalnum() else "_" for c in t.lower()).strip("_") or "step")[:20]

def _enrich_keys(cfg, steps):
    """Compute out_key per step (semantic, deduped) + dep_keys; returns
    (enriched_topo_sorted, out_map, terminals)."""
    catalog = cfg["catalog"]
    ordered = topo_order(steps)
    out = {}; used = {}
    for st in ordered:
        base = st["capability"] if st["capability"] in catalog else _slug(st["title"])
        n = used.get(base, 0); used[base] = n + 1
        out[st["id"]] = base if n == 0 else f"{base}_{n + 1}"
    enriched = []
    for st in ordered:
        cap = st["capability"]
        enriched.append({
            "id": st["id"], "title": st["title"], "capability": cap,
            "depends_on": st["depends_on"], "custom": cap not in catalog,
            "out_key": out[st["id"]], "dep_keys": [out[d] for d in st["depends_on"]],
            "sub": ("custom · needs a human" if cap not in catalog else cap),
        })
    return enriched, out, _terminals(steps)


async def _watch(s, pred, action):
    try:
        await s.board.wait_until(lambda d: s.exec_done or pred(d))
        if s.exec_done: return
        await action(s)
    except asyncio.CancelledError:
        raise

def _ready(step, out_self):
    deps = step["dep_keys"]
    def trig(d):
        return all(k in d for k in deps) and out_self not in d
    return trig

async def _exec_do_step(s, step):
    if s.exec_done: return
    sid = step["id"]; cap = step["capability"]; catalog = s.cfg["catalog"]
    custom = cap not in catalog
    peer = not s.orch_alive
    out = s.out[sid]
    await s.node(sid, "flag" if custom else "active")
    for d in step["depends_on"]:
        await s.linkft(d, sid, "reroute" if peer else "flow")
    await asyncio.sleep(STEP)
    for d in step["depends_on"]:
        await s.linkft(d, sid, "off")
    if custom:
        await s.board.set(out, {"custom": True, "needs": "human"})
        await s.log("system", f'{step["title"]} — custom step, no catalog capability. needs a human.', "warn")
        await s.node(sid, "flag")
    else:
        c = catalog[cap]
        await s.board.set(out, c.get("state", c.get("payload")))
        await s.log(c.get("role", "system"), f'{c.get("desc", cap)} — wrote {out}.', "reroute" if peer else "")
        await s.node(sid, "done")
    s.completed += 1

async def _exec_finalize(s, peer=False):
    if s.exec_done: return
    s.exec_done = True
    if peer:
        await s.log("done", "case closed without a coordinator — the surviving tasks finished it.", "good")
        await s.status("recovered — finished without the coordinator", "recovered")
    else:
        await s.log("done", "all steps complete — case delivered.", "good")
        await s.status("delivered — case complete", "")
    await s.board.poke()

async def _exec_stall(s):
    await asyncio.sleep(0.4)
    if s.exec_done: return
    await s.log("system", "coordinator offline. nobody owns the remaining steps.", "warn")
    await asyncio.sleep(0.6)
    await s.log("system", "the plan is stuck mid-flight — nothing finishes.", "warn")
    await s.status("stalled — nobody owns it", "stalled")
    s.exec_done = True
    await s.board.poke()

async def _exec_partial_watchdog(s):
    by_id = {st["id"]: st for st in s.steps}
    def stranded(sid):
        st = by_id.get(sid)
        if not st: return False
        return any(d in s.killed_nodes or stranded(d) for d in st["depends_on"])
    def all_runnable_done(d):
        blocked = {st["id"] for st in s.steps if st["id"] in s.killed_nodes or stranded(st["id"])}
        runnable = [st["id"] for st in s.steps if st["id"] not in blocked]
        return all(s.out[sid] in d for sid in runnable)
    await s.board.wait_until(lambda d: s.exec_done or all_runnable_done(d))
    if s.exec_done: return
    blocked = {st["id"] for st in s.steps if st["id"] in s.killed_nodes or stranded(st["id"])}
    if blocked:
        s.exec_done = True
        await s.status("partial — independent branches finished, dependents stranded", "stalled")
        await s.board.poke()

async def _exec_orchestrator(s, steps):
    try:
        await s.node("coord", "active")
        await s.log("orch", "coordinator online — driving the plan step by step.")
        await asyncio.sleep(0.4)
        for step in steps:
            if s.exec_done: return
            await s.linkft("coord", step["id"], "flow")
            await _exec_do_step(s, step)
            await s.linkft("coord", step["id"], "off")
            await asyncio.sleep(0.2)
        await s.node("coord", "done")
        await _exec_finalize(s)
    except asyncio.CancelledError:
        raise

async def _exec_mesh(s, steps):
    await s.node("coord", "active")
    await s.log("orch", "coordinator online — but every step is its own task on the blackboard.")
    await asyncio.sleep(0.4)
    await s.node("coord", "done")
    term_keys = [s.out[t] for t in s.terminals]
    for step in steps:
        pred = _ready(step, s.out[step["id"]])
        def guarded(d, st=step, pred=pred):
            return st["id"] not in s.killed_nodes and pred(d)
        t = s.spawn(_watch(s, guarded, (lambda st: (lambda ss: _exec_do_step(ss, st)))(step)))
        s.step_tasks[step["id"]] = t
    def fin(d):
        return all(k in d for k in term_keys)
    s.spawn(_watch(s, fin, lambda ss: _exec_finalize(ss, peer=not ss.orch_alive)))
    s.coord_task = s.spawn(_watch(s, fin, lambda ss: _exec_finalize(ss, peer=not ss.orch_alive)))
    await asyncio.gather(*s.all_tasks, return_exceptions=True)

async def run_executed_plan(s, goal):
    s.kind = "exec"
    steps, meta = await compose_plan(s.cfg, goal)
    s.board = Blackboard(s.send)
    enriched, out, terms = _enrich_keys(s.cfg, steps)
    s.steps = enriched; s.out = out; s.terminals = terms
    await s.send({
        "type": "graph", "mode": s.mode, "goal": goal,
        "source": meta["source"], "reason": meta.get("reason"),
        "rationale": meta.get("rationale", ""),
        "steps": enriched, "terminals": terms,
        "json": plan_to_json(steps, meta.get("rationale", "")),
        "code": plan_to_code(s.cfg, goal, steps),
    })
    await asyncio.sleep(1.4)   # the beat: let the composed plan + code sit on screen before it runs
    await s.board.set("request", s.cfg.get("request", goal))
    try:
        if s.mode == "orchestrator":
            s.coord_task = s.spawn(_exec_orchestrator(s, enriched))
            await asyncio.gather(s.coord_task, return_exceptions=True)
        else:
            await _exec_mesh(s, enriched)
    except asyncio.CancelledError:
        pass
