# poison_demo.py
# The SECOND demo — the COST of the mesh topology, not its benefit.
#
# The kill-the-coordinator demo (agents.py + the web UI) shows the mesh WINNING:
# kill the boss and the surviving peers finish the case. This one shows the same
# mesh LOSING. Nobody is killed. One handler succeeds — it does not crash, does
# not raise — but writes a plausible, well-shaped, WRONG result to the shared
# state. We run that single poison write against three topologies and watch what
# each one does with it.
#
#   1. orchestrator   — a validation GATE at the merge point rejects the bad
#                        result before the irreversible step. Poison caught.
#   2. mesh (unscoped) — no gatekeeper; every handler reads the WHOLE board, so
#                        the poison reaches handlers that never needed it.
#   3. mesh (scoped)   — no gatekeeper either, but least-privilege reads: each
#                        handler sees only the keys it genuinely needs, so the
#                        blast radius shrinks.
#
# Same four handlers as the topology demo (research, draft, validation, activate)
# plus two independent "brief" handlers (assets, legal) that have NO legitimate
# need for the audience. The handlers' own logic is never edited — the fault is
# injected by a WRAPPER around one handler's output (see `poison`), so the
# "identical handlers in every topology" claim from the other demo still holds.
#
# Pure stdlib, no async, no network, no file writes, no randomness. Deterministic:
# run it twice, get the same trace. Single command:  python backend/poison_demo.py

# ---------------------------------------------------------------------------
# the brief that kicks everything off (mirrors the campaign scenario)
# ---------------------------------------------------------------------------
REQUEST = {"goal": "win-back journey for cart-abandoners", "segment": "cart-abandoners"}

# the honest audience a healthy `research` handler would build
HONEST_AUDIENCE = {"segment": "cart-abandoners", "count": 48210, "window_days": 14}

# the POISON: same shape, plausible on its face, quietly wrong. Not malformed —
# a downstream handler parses and acts on it exactly like the honest one. Only a
# CONTENT check (segment matches the brief? count sane?) can tell them apart.
POISON_AUDIENCE = {"segment": "all-customers", "count": 2_300_000, "window_days": 14}

MAX_SANE_AUDIENCE = 500_000     # the merge gate's sanity bound on audience size


# ---------------------------------------------------------------------------
# the six handlers. Each declares the keys it GENUINELY needs (`reads`), the key
# it writes, and whether finishing fires an external action. `fn` is the
# handler's own untouched logic. None of this changes between topologies.
# ---------------------------------------------------------------------------
def _research(board):   return dict(HONEST_AUDIENCE)
def _draft(board):      return {"variants": 3, "targeting": board["audience"]["segment"]}
def _validation(board): return {"brand": "pass", "legal": "pass"}
def _activate(board):   return {"journey_id": "JRN-2205", "enrolled": board["audience"]["count"], "state": "live"}
def _assets(board):     return {"asset_pack": 4, "source": "brief"}
def _legal(board):      return {"rights": "cleared"}

HANDLERS = [
    # id            reads                    writes         fn           external action if it completes
    ("research",   ["request"],             "audience",    _research,   None),
    ("draft",      ["audience"],            "creative",    _draft,      None),
    ("validation", ["creative"],            "brand_ok",    _validation, None),
    ("activate",   ["brand_ok", "audience"], "activation",  _activate,   "enroll {count:,} profiles into journey JRN-2205"),
    ("assets",     ["request"],             "asset_pack",  _assets,     None),   # brief-only; never needs the audience
    ("legal",      ["asset_pack"],          "legal_ok",    _legal,      None),   # brief-only; never needs the audience
]
BY_ID = {h[0]: h for h in HANDLERS}


# poison WRAPPER — wraps one handler so its OUTPUT is replaced with a bad value.
# The wrapped handler still runs; we just substitute what it returns. The
# handler's own code (`_research`) is never touched.
def poison(fn, bad_value):
    def wrapped(board):
        fn(board)               # the real handler still runs, successfully
        return dict(bad_value)  # ...but a plausible wrong result goes out
    return wrapped


# ---------------------------------------------------------------------------
# the blackboard — like agents.py's, but instrumented: it records every write,
# tracks which keys carry poison (taint), and knows which handlers consumed it.
# ---------------------------------------------------------------------------
class Board:
    def __init__(self):
        self.d = {}
        self.tainted = set()        # keys whose value is poisoned or derived from poison
        self.consumers = []         # handler ids that READ a tainted key (the blast radius)
        self.external = []          # external actions that WOULD fire (never actually run)

    def seed(self, key, value):
        self.d[key] = value

    def read(self, hid, keys):
        """A handler reads `keys`. If any is tainted, record hid as a consumer."""
        hit = [k for k in keys if k in self.tainted]
        if hit and hid not in self.consumers:
            self.consumers.append(hid)
        return hit

    def write(self, key, value, tainted):
        self.d[key] = value
        if tainted:
            self.tainted.add(key)


def _fire_external(board, hid, tmpl, value, gated):
    """Log the external action a handler WOULD take. Never performed."""
    action = tmpl.format(count=value.get("enrolled", 0))
    if gated:
        print(f"    (external action blocked by the gate — would have been: {action})")
    else:
        board.external.append(action)
        print(f"    ⚡ WOULD FIRE external action: {action}   [not executed]")


def _run_handler(board, hid, read_keys, out_fn):
    """Run one handler: record its reads, compute + write its output, print a line."""
    _, _, writes, fn, ext = BY_ID[hid]
    hit = board.read(hid, read_keys)
    value = out_fn(board.d)                     # handlers read the plain dict; honest or poison-wrapped
    tainted = bool(hit) or (hid == POISONED_HANDLER)
    board.write(writes, value, tainted)
    reads_note = ",".join(read_keys) or "-"
    warn = "  ⚠ read poisoned '{}'".format(",".join(hit)) if hit else ""
    seed = "  ⚠ POISONED at the source" if hid == POISONED_HANDLER else ""
    print(f"    {hid:<11} reads[{reads_note}] -> wrote {writes} = {value}{seed}{warn}")
    if ext:
        _fire_external(board, hid, ext, value, gated=False)
    return tainted


POISONED_HANDLER = "research"   # the handler whose output we poison, in every run


def _out_fn(hid):
    """The output function for a handler — poison-wrapped for the chosen handler."""
    fn = BY_ID[hid][3]
    if hid == POISONED_HANDLER:
        return poison(fn, POISON_AUDIENCE)
    return fn


# ---------------------------------------------------------------------------
# topology 1 — ORCHESTRATOR. One driver runs the steps in order and, at the
# merge point (right before the irreversible `activate`), validates the result.
# That chokepoint is the whole advantage: it can reject before harm is done.
# ---------------------------------------------------------------------------
def run_orchestrator():
    _header("1. ORCHESTRATOR", "one driver, with a validation GATE at the merge point")
    board = Board()
    board.seed("request", REQUEST)

    # the driver's fixed plan. `activate` is held back behind the gate.
    for hid in ["research", "draft", "validation"]:
        _run_handler(board, hid, BY_ID[hid][1], _out_fn(hid))

    # --- the merge gate: validate the audience before anything irreversible ---
    aud = board.d["audience"]
    ok = aud["segment"] == REQUEST["segment"] and aud["count"] <= MAX_SANE_AUDIENCE
    print(f"    {'MERGE GATE':<11} checks audience: segment=={REQUEST['segment']!r}? "
          f"{aud['segment']==REQUEST['segment']} | count<= {MAX_SANE_AUDIENCE:,}? {aud['count']<=MAX_SANE_AUDIENCE}")
    if not ok:
        print(f"    {'':<11} -> REJECTED. audience is {aud['segment']!r} @ {aud['count']:,} — refusing to merge.")
        print("    HALT — the bad result was caught at the chokepoint. `activate` never ran.")
        return _verdict(board, halted=True)

    # gate passed -> the irreversible step runs (won't happen with the poison)
    _run_handler(board, "activate", BY_ID["activate"][1], _out_fn("activate"))
    for hid in ["assets", "legal"]:
        _run_handler(board, hid, BY_ID[hid][1], _out_fn(hid))
    return _verdict(board, halted=False)


# ---------------------------------------------------------------------------
# topology 2 & 3 — MESH. Every handler is its own task on the blackboard, firing
# when its trigger is ready. No gatekeeper. The ONLY difference between the two
# variants is how much each handler is allowed to read when it wakes:
#   unscoped -> it reads the whole board (and picks up keys it never needed)
#   scoped   -> it reads only the keys it declared (least privilege)
# Deterministic: every board write wakes the watchers; we run ready handlers in a
# fixed id order until none remain. research (the poison) runs first, as it does
# in the real demo, so later handlers see the poisoned board.
# ---------------------------------------------------------------------------
def run_mesh(scoped):
    name = "3. MESH (scoped reads)" if scoped else "2. MESH (unscoped reads)"
    tag = "least-privilege: each handler reads only what it needs" if scoped \
          else "no gatekeeper: each handler reads the WHOLE board"
    _header(name, tag)
    board = Board()
    board.seed("request", REQUEST)

    ran = set()
    # fixed scan order = deterministic execution
    order = ["research", "draft", "validation", "activate", "assets", "legal"]
    progressed = True
    while progressed:
        progressed = False
        for hid in order:
            if hid in ran:
                continue
            declared = BY_ID[hid][1]
            if not all(k in board.d for k in declared):     # trigger: genuine deps ready
                continue
            # unscoped handlers read everything currently on the board; scoped read only declared keys
            read_keys = declared if scoped else [k for k in board.d.keys()]
            _run_handler(board, hid, read_keys, _out_fn(hid))
            ran.add(hid)
            progressed = True
            break                                            # re-scan from the top (deterministic)
    return _verdict(board, halted=False)


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------
def _header(title, tag):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print(f"  {tag}")
    print("=" * 78)


def _verdict(board, halted):
    n = len(board.consumers)
    print(f"\n    BLAST RADIUS: {n} handler(s) acted on the poisoned value"
          + (f" -> {board.consumers}" if board.consumers else "")
          + f"  |  external actions fired: {len(board.external)}")
    if halted:
        print("    VERDICT: CONTAINED — the gate stopped it before any external action.\n")
    elif board.external:
        print("    VERDICT: SPREAD — the poison reached the irreversible step and it fired.\n")
    else:
        print("    VERDICT: no external action fired.\n")
    return {"consumers": list(board.consumers), "external": list(board.external), "halted": halted}


def _summary(results):
    print("=" * 78)
    print("  SUMMARY — same poison write, three topologies")
    print("=" * 78)
    print(f"  {'topology':<22}{'gate?':<8}{'reads':<16}{'handlers hit':<14}{'external fired'}")
    rows = [
        ("orchestrator",     "yes",  "declared",        results["orch"]),
        ("mesh (unscoped)",  "no",   "whole board",     results["unscoped"]),
        ("mesh (scoped)",    "no",   "least-privilege", results["scoped"]),
    ]
    for label, gate, reads, r in rows:
        hit = len(r["consumers"])
        ext = len(r["external"])
        mark = "  (blocked)" if r["halted"] else ""
        print(f"  {label:<22}{gate:<8}{reads:<16}{hit:<14}{ext}{mark}")
    print("-" * 78)
    print("  Read it this way:")
    print("   * The orchestrator's GATE is what actually prevents harm — 0 external actions.")
    print("   * Unscoped mesh has the widest blast radius: even the brief-only handlers")
    print("     (assets, legal) read the poisoned audience they never needed.")
    print("   * Scoping SHRINKS the blast radius by cutting those collateral reads, but the")
    print("     poisoned data-path still fires — least privilege limits spread, it does not")
    print("     replace validation. You want both: a gate AND scoped reads.")
    print("=" * 78 + "\n")


def main():
    print("\nPOISON-WRITE DEMO — one handler returns a plausible, wrong result.")
    print(f"  honest audience : {HONEST_AUDIENCE}")
    print(f"  poisoned audience: {POISON_AUDIENCE}   <- same shape, wrong content")
    orch = run_orchestrator()
    unscoped = run_mesh(scoped=False)
    scoped = run_mesh(scoped=True)
    _summary({"orch": orch, "unscoped": unscoped, "scoped": scoped})


if __name__ == "__main__":
    main()
