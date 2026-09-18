# provenance_demo.py
# Backs the README's "What about a crash before the gate?" note with something
# runnable. The poison demo shows a bad write being caught or spreading; this
# shows the OTHER failure a reader asked about — a worker that writes to shared
# state and then DIES before the gate can judge it. The question: what evidence
# survives the crash?
#
# Same crash, two boards:
#   1. PlainBoard  — a last-write-wins dict (what the sandbox actually uses).
#                    After the crash you have values and no idea which are
#                    trustworthy, who wrote them, or who already read them.
#   2. ProvBoard   — every write carries provenance (writer, causal inputs, a
#                    `validated` flag defaulting to false) and every read is
#                    logged. The crash can't erase an append-only log, so you can
#                    prove what was never validated, trace what derived from it,
#                    find who read it, and recompute.
#
# Deterministic, stdlib only, no async. Single command:
#   python3 backend/provenance_demo.py

HONEST_AUDIENCE = {"segment": "cart-abandoners", "count": 48_210}
POISON_AUDIENCE = {"segment": "all-customers", "count": 2_300_000}   # plausible, wrong


class PlainBoard:
    """The sandbox's board: a mutable dict. Keeps only the latest value."""
    def __init__(self): self.d = {}
    def set(self, writer, key, value, inputs): self.d[key] = value
    def get(self, reader, key): return self.d[key]


class ProvBoard:
    """An append-only board. Every write and every read is a durable event, so
    the record outlives the worker that made it."""
    def __init__(self):
        self.d = {}
        self.writes = []          # {seq, writer, key, value, inputs, validated}
        self.reads = []           # {seq, reader, key}
        self.gate_passes = set()
        self._seq = 0

    def _next(self):
        self._seq += 1
        return self._seq

    def set(self, writer, key, value, inputs):
        self.d[key] = value
        self.writes.append({"seq": self._next(), "writer": writer, "key": key,
                            "value": value, "inputs": list(inputs), "validated": False})

    def get(self, reader, key):
        self.reads.append({"seq": self._next(), "reader": reader, "key": key})
        return self.d[key]

    def gate_pass(self, key):                       # a gate stamps a value good
        self.gate_passes.add(key)
        for w in self.writes:
            if w["key"] == key:
                w["validated"] = True

    # --- forensics, computed purely from the logs (no live worker needed) ---
    def unvalidated(self):
        return [w for w in self.writes if not w["validated"]]

    def tainted_from(self, bad_key):
        """Keys derived (transitively) from bad_key, via each write's inputs."""
        tainted, changed = {bad_key}, True
        while changed:
            changed = False
            for w in self.writes:
                if w["key"] not in tainted and any(i in tainted for i in w["inputs"]):
                    tainted.add(w["key"]); changed = True
        return tainted

    def consumers_of(self, keys):
        return sorted({r["reader"] for r in self.reads if r["key"] in keys})


def run_crash(board):
    """Same sequence on either board: research writes a poison audience, two
    peers read it and act, then the coordinator DIES before the gate runs."""
    board.set("research", "audience", POISON_AUDIENCE, inputs=["request"])
    aud = board.get("draft", "audience")
    board.set("draft", "creative", {"variants": 3, "targeting": aud["segment"]}, inputs=["audience"])
    aud = board.get("measure", "audience")
    board.set("measure", "spend_plan", {"budget_for": aud["count"]}, inputs=["audience"])
    # >>> the coordinator that runs the merge gate dies here. the gate never runs. <<<


def show_plain(board):
    print("  final state — all it keeps:")
    for k, v in board.d.items():
        print(f"    {k:<11} = {v}")
    print("  → who wrote these? which passed a gate? who already read them?  unknown.\n")


def show_prov(board):
    print("  WRITE LOG (append-only — survives the crash):")
    for w in board.writes:
        mark = "yes" if w["validated"] else "NO "
        print(f"    #{w['seq']}  {w['writer']:<9} wrote {w['key']:<11} validated={mark}  from={w['inputs']}  {w['value']}")
    print("  READ LOG (who consumed what, before the crash):")
    for r in board.reads:
        print(f"    #{r['seq']}  {r['reader']:<9} read  {r['key']}")
    print(f"  GATE LOG: {sorted(board.gate_passes) or '(empty — the gate never ran)'}")

    bad = "audience"
    tainted = board.tainted_from(bad)
    print("\n  EVIDENCE AFTER THE CRASH — reconstructed from the logs alone:")
    for w in board.unvalidated():
        why = "written, never validated (no gate-pass event)" if w["key"] == bad \
              else f"derived from {w['inputs']}, never validated"
        print(f"    {w['key']:<11} ⚠ {why}")
    print(f"    tainted set : {sorted(tainted)}")
    print(f"    consumers   : {board.consumers_of(tainted)}   ← notify / unwind these")
    print(f"    recovery    : quarantine {sorted(tainted)}, then recompute from the last validated input.\n")


def main():
    print("\nCRASH BEFORE THE GATE — what evidence survives?\n")
    print("=" * 78)
    print("  1. Plain dict board  (what the sandbox actually uses)")
    print("=" * 78)
    pb = PlainBoard(); run_crash(pb); show_plain(pb)

    print("=" * 78)
    print("  2. Provenance board  (write-ahead log + read log + validated flag)")
    print("=" * 78)
    gb = ProvBoard(); run_crash(gb); show_prov(gb)

    print("Same crash both times. The plain board loses the case; the provenance board")
    print("still knows what was never judged, what derived from it, and who read it.")


if __name__ == "__main__":
    main()
