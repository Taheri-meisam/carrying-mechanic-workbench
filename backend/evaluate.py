"""Evaluation over the pool: extraction accuracy (draft vs adjudicated), rubric reliability (coder1 vs coder2, Cohen's kappa), corpus summary.
Same definitions as the frontend's Evaluation tab and eval.js."""
from collections import Counter
SECTIONS = ["mechanic", "gates", "route", "dimensions", "dig", "ledger", "distinct"]
ORDER = {"Kept": 3, "Transformed": 2, "Substituted": 1, "Removed": 0}
F = {"route": lambda r: r["route"]["primary"], "MC": lambda r: str(r["dimensions"]["MC"]["level"]), "LOM": lambda r: str(r["dimensions"]["LOM"]["level"]),
     "AGE": lambda r: str(r["dimensions"]["AGE"]["level"]), "CTa": lambda r: str(r["dimensions"]["CTa"]["level"]), "S": lambda r: r["dig"]["S"]["op"],
     "M": lambda r: r["dig"]["M"]["op"], "L": lambda r: r["dig"]["L"]["op"],
     # v0.5: the level is None until a coder assigns it (and absent from a draft that omitted it)
     "D": lambda r: r["distinctiveness"].get("level"),
     "G1": lambda r: str(r["gates"]["G1"]["pass"]), "G2": lambda r: str(r["gates"]["G2"]["pass"]), "G3": lambda r: str(r["gates"]["G3"]["pass"])}

def reviewed(r): return sum(1 for s in SECTIONS if r.get("review", {}).get("sections", {}).get(s))
def kappa(a, b):
    n = len(a)
    if not n: return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b); pe = sum((ca[c] / n) * (cb[c] / n) for c in set(a) | set(b))
    # pe == 1 means both coders used a single, identical category: there was no variance to agree
    # about, so kappa divides by zero and is undefined. Returning 1.0 here would print a perfect
    # score for a field that carries no information. None renders as an em dash.
    return None if pe >= 1 else (po - pe) / (1 - pe)
def survives(d):
    """Does the carrying mechanic survive under this interface class?

    Instrument v0.2: Kept and Transformed survive; Removed does not; Substituted is conditional on
    the substitution test (same game-defining problem AND the carryingMechanic competence plausibly
    transfers). Before v0.2 Substituted always failed, which made the class a two-value field; after
    it, Substituted-with-a-failed-test is what does the rejecting, and Removed is not asked to carry
    that burden alone."""
    op = (d or {}).get("op")
    if ORDER.get(op, 0) >= 2: return True
    if op != "Substituted": return False
    return bool(((d or {}).get("substitutionTest") or {}).get("pass"))


def eligible(r):
    fails = []
    g = r["gates"]
    if not (g["G1"]["pass"] and g["G2"]["pass"] and g["G3"]["pass"]): fails.append("gate")
    if int(r["dimensions"]["MC"]["level"]) < 1: fails.append("MC")
    if not survives(r["dig"]["S"]): fails.append("DIG(S)")
    if int(r["dimensions"]["CTa"]["level"]) < 1: fails.append("CTa")
    # Core eligibility does not read LOM. LOM is the relation between a game and a *stated*
    # educational objective, so it cannot gate a general adaptation decision; where an objective is
    # supplied it applies as an additional pedagogical filter, reported separately. On the nine pilot
    # games this changes no verdict: neither coder rated any game below 1.
    edu_fails = fails + (["LOM"] if int(r["dimensions"]["LOM"]["level"]) < 1 else [])
    # v0.3: L is out of the gate and out of the hybrid flag. Under a co-located setup the physical
    # game is unchanged by definition, so L cannot show that a game survives digitisation; it could
    # not discriminate. L is now descriptive only -- a proposed setup with a feasibility judgement.
    hyb = not survives(r["dig"]["S"]) and survives(r["dig"]["M"])
    done = reviewed(r) == len(SECTIONS)
    return {"ok": not fails and done, "fails": fails, "hybrid": hyb,
            "educationalOk": not edu_fails and done, "educationalFails": edu_fails}

def evaluate(records):
    done = [r for r in records if r.get("review", {}).get("raw") and reviewed(r) == len(SECTIONS)]
    acc = {}
    for f, fn in F.items():
        try: pairs = [(fn(r["review"]["raw"]), fn(r)) for r in done]
        except KeyError: pairs = []
        ok = sum(a == b for a, b in pairs)
        acc[f] = {"agree": ok, "n": len(pairs), "accuracy": ok / len(pairs) if pairs else None,
                  "disagreements": [f"{r['name']}: {a} -> {b}" for r, (a, b) in zip(done, pairs) if a != b]}
    two = [r for r in records if r.get("review2", {}).get("coder") and reviewed(r) == len(SECTIONS)]
    rel = {}
    for f in ["route", "MC", "LOM", "AGE", "CTa", "S", "D"]:
        rows = [r for r in two if r["review2"].get(f) not in (None, "")]
        a = [str(F[f](r)) for r in rows]; b = [str(r["review2"][f]) for r in rows]
        rel[f] = {"agree": sum(x == y for x, y in zip(a, b)), "n": len(rows), "kappa": kappa(a, b),
                  "disagreements": [f"{r['name']}: {x} / {y}" for r, x, y in zip(rows, a, b) if x != y]}
    rev = [r for r in records if reviewed(r) == len(SECTIONS)]
    el = [(r, eligible(r)) for r in rev]
    summary = {"n": len(records), "reviewed": len(rev),
      "gateRejections": [r["name"] for r, e in el if "gate" in e["fails"]],
      "lostOnScreen": [r["name"] for r in rev if ORDER.get(r["dig"]["S"]["op"], 0) < 2],
      "hybrid": [r["name"] for r, e in el if e["hybrid"]],
      "lom0": [r["name"] for r in rev if int(r["dimensions"]["LOM"]["level"]) < 1],
      "age0": [r["name"] for r in rev if int(r["dimensions"]["AGE"]["level"]) < 1],
      "eligible": [r["name"] for r, e in el if e["ok"]],
      "routes": dict(Counter(r["route"]["primary"] for r in rev)), "D": dict(Counter(((r.get("distinctiveness") or {}).get("level") or "unset") for r in rev)),
      "S": dict(Counter(r["dig"]["S"]["op"] for r in rev))}
    return {"accuracy": acc, "reliability": rel, "summary": summary}
