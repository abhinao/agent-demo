# planner.py
# The one REAL model call in the whole sandbox. Everything else is fake payloads
# and scripted-but-emergent topology; this is where composition is genuinely
# model-generated.
#
# The model is handed a FIXED capability catalog per scenario and forced (via
# tool_choice) to return an emit_plan tool call: an ordered list of steps, each
# naming one catalog capability (or "custom"), with dependency refs. We then
# VALIDATE that plan (catalog membership, dependency refs, no cycles) before the
# app renders it. REAL = which steps and in what order. FAKE = the step payloads
# (tiny hardcoded blobs, in scenarios.py). No real external services.
#
# Wrapped in an 8s timeout with a recorded-plan fallback, so a slow/absent API,
# a bad key, or an invalid plan degrades to a canned plan instead of breaking the
# demo. ANTHROPIC_API_KEY stays in the environment — never in the frontend.

import asyncio
import json
import os

MODEL = "claude-opus-5"     # forced-tool composition; any error degrades to the recorded plan
TIMEOUT_S = 8.0

# Forced tool schema. tool_choice pins the model to exactly this shape.
EMIT_PLAN_TOOL = {
    "name": "emit_plan",
    "description": (
        "Return an ordered execution plan for the goal, composed ONLY from the "
        "provided capability catalog."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "Ordered plan steps.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "short unique id, e.g. s1"},
                        "capability": {
                            "type": "string",
                            "description": "a capability id from the catalog, or the literal 'custom'",
                        },
                        "title": {"type": "string", "description": "<=6 words: what this step does"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ids of steps that must finish first (empty if none)",
                        },
                    },
                    "required": ["id", "capability", "title", "depends_on"],
                    "additionalProperties": False,
                },
            },
            "rationale": {"type": "string", "description": "one short sentence"},
        },
        "required": ["steps", "rationale"],
        "additionalProperties": False,
    },
}

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.AsyncAnthropic()   # reads ANTHROPIC_API_KEY from env
    return _client


# ---------------------------------------------------------------------------
# validation — the model's plan is untrusted; check it before we render it
# ---------------------------------------------------------------------------

def topo_order(steps):
    """Kahn topological sort. Raises ValueError on a cycle. Ties break by the
    model's original ordering so the reveal stays stable."""
    by_id = {s["id"]: s for s in steps}
    orig = {s["id"]: i for i, s in enumerate(steps)}
    indeg = {sid: 0 for sid in by_id}
    adj = {sid: [] for sid in by_id}
    for s in steps:
        for d in s["depends_on"]:
            adj[d].append(s["id"])
            indeg[s["id"]] += 1
    ready = [sid for sid in by_id if indeg[sid] == 0]
    order = []
    while ready:
        ready.sort(key=lambda x: orig[x])
        n = ready.pop(0)
        order.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if len(order) != len(by_id):
        raise ValueError("cycle in plan")
    return [by_id[sid] for sid in order]


def validate_plan(steps, catalog):
    """catalog membership + dependency refs + acyclic. Raises ValueError."""
    if not steps:
        raise ValueError("empty plan")
    ids = [s["id"] for s in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate step ids")
    idset = set(ids)
    for s in steps:
        for key in ("id", "capability", "title", "depends_on"):
            if key not in s:
                raise ValueError(f"step missing {key}")
        cap = s["capability"]
        if cap != "custom" and cap not in catalog:
            raise ValueError(f"unknown capability: {cap}")
        for d in s["depends_on"]:
            if d == s["id"]:
                raise ValueError("self dependency")
            if d not in idset:
                raise ValueError(f"dangling dependency: {d}")
    topo_order(steps)   # raises on cycle
    return True


# ---------------------------------------------------------------------------
# the real call
# ---------------------------------------------------------------------------

async def _call_model(cfg, goal):
    catalog = cfg["catalog"]
    catalog_text = "\n".join(f"- {cid}: {c['desc']}" for cid, c in catalog.items())
    system = (
        "You are the planning brain of an autonomous "
        f"{cfg['domain']} agent system. Given a goal, compose the plan that achieves "
        "it, using ONLY capabilities from the catalog below. Each step names exactly "
        "one capability id, or the literal 'custom' when no catalog capability fits.\n\n"
        "SELECT — first choose only the capabilities THIS specific goal needs. You do "
        "NOT have to use all of them, and genuinely different goals should produce "
        "genuinely different plans; never fall back on a fixed template.\n\n"
        "DEPENDENCIES & SHAPE — wire `depends_on` to encode ONLY real data/ordering "
        "constraints: a step lists another only if it consumes that step's output. Steps "
        "that do not consume each other's output MUST have no dependency between them, so "
        "they run in PARALLEL. A step that needs several prior outputs lists ALL of them "
        "(fan-in). Prefer a wide, parallel plan (fan-out then fan-in) over a long linear "
        "chain — only chain steps that are genuinely sequential. Use as few steps as the "
        "goal needs, use the literal 'custom' when no capability fits, and never invent "
        "capability ids.\n\n"
        "CATALOG:\n" + catalog_text
    )
    msg = await _get_client().messages.create(
        model=MODEL,
        max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": f"Goal: {goal}\n\nCompose the plan via emit_plan."}],
        tools=[EMIT_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "emit_plan"},
        thinking={"type": "disabled"},          # fast, deterministic forced-tool output
        output_config={"effort": "low"},
    )
    for block in msg.content:
        if block.type == "tool_use" and block.name == "emit_plan":
            return block.input           # the whole emit_plan artifact (steps + rationale)
    raise ValueError("no emit_plan tool_use in response")


async def compose_plan(cfg, goal):
    """Returns (steps, meta). meta['source'] is 'live' or 'recorded'; meta always
    carries 'rationale'; on recorded, 'reason' explains why. Never raises."""
    catalog = cfg["catalog"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return cfg["recorded_plan"], {"source": "recorded", "reason": "no ANTHROPIC_API_KEY",
                                      "rationale": "recorded fallback plan"}
    try:
        data = await asyncio.wait_for(_call_model(cfg, goal), timeout=TIMEOUT_S)
        steps = data.get("steps", [])
        validate_plan(steps, catalog)
        return steps, {"source": "live", "rationale": data.get("rationale", "")}
    except asyncio.TimeoutError:
        return cfg["recorded_plan"], {"source": "recorded", "reason": "timeout",
                                      "rationale": "recorded fallback plan"}
    except Exception as e:  # API error, bad key, invalid/ cyclic plan, ...
        return cfg["recorded_plan"], {"source": "recorded", "reason": type(e).__name__,
                                      "rationale": "recorded fallback plan"}


# ---------------------------------------------------------------------------
# render the plan into the two artifacts the UI shows: the raw emit_plan JSON
# (what the model actually wrote) and a deterministic async-Python script
# (a faithful, runnable-looking rendering of the same plan).
# ---------------------------------------------------------------------------

def plan_to_json(steps, rationale):
    return json.dumps({"steps": steps, "rationale": rationale}, indent=2)


def _slug(text):
    out = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
    return (out[:24] or "step")


def plan_to_code(cfg, goal, steps):
    catalog = cfg["catalog"]
    order = topo_order(steps)
    fn = {st["id"]: f'step_{st["id"]}_{_slug(st["title"])}' for st in order}
    L = ['"""Auto-generated from Claude\'s plan — one function per step, wired by',
         'depends_on. Fake payloads; the composition is the model\'s."""',
         f"# goal: {goal}",
         "import asyncio",
         "",
         "board = {}          # shared blackboard",
         ""]
    for st in order:
        cap = st["capability"]
        L.append(f"async def {fn[st['id']]}():")
        if cap not in catalog:
            L.append("    # ⚠ custom step — no catalog capability; hand off to a human")
            L.append(f"    raise NotImplementedError({st['title']!r})")
        else:
            c = catalog[cap]
            L.append(f"    # capability: {cap} — {c['desc']}")
            L.append(f"    board[{st['id']!r}] = {c['payload']!r}")
        L.append("")
    L.append("PLAN = {          # step id -> (coroutine, depends_on)")
    for st in order:
        L.append(f"    {st['id']!r}: ({fn[st['id']]}, {st['depends_on']!r}),")
    L.append("}")
    L += ["",
          "async def run():",
          "    done = {}",
          "    async def launch(sid):",
          "        coro, deps = PLAN[sid]",
          "        if deps:",
          "            await asyncio.gather(*(done[d] for d in deps))",
          "        await coro()",
          "    for sid in PLAN:                    # topological order",
          "        done[sid] = asyncio.create_task(launch(sid))",
          "    await asyncio.gather(*done.values())",
          "",
          "asyncio.run(run())"]
    return "\n".join(L)
