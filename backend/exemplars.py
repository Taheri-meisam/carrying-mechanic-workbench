"""Few-shot exemplars: human-adjudicated records used as interpretation anchors in the prompt.

Why this exists: the nine-game run showed the drift is interpretive, not informational — G3 read as
"does the mechanic survive intact" rather than "does any interface class carry it", and S pushed to
Substituted wherever a game is physical. Worked examples fix that far better than more source text.

Why it is off by default, and loud when on:

    A draft written with adjudicated answers in its prompt has seen a coder's judgements. Measuring
    "model-human accuracy" on such a draft measures the exemplars as much as the model. So
    selection is strictly leave-one-out (never the target game), only fully-adjudicated records
    qualify, and every draft made this way is labelled `+fs<n>` in its provider string, which lands
    in review.meta and the extractions table. A mixed pool stays separable at analysis time.

Set EXEMPLARS=2 to enable. Default 0.
"""
import json, os
from typing import List

SECTIONS = ["mechanic", "gates", "route", "dimensions", "dig", "ledger", "distinct"]
ENTRY_CHARS = 1200          # enough to ground the judgements, not the whole entry


def adjudicated(records: List[dict]) -> List[dict]:
    """Records a human has signed off on every section of."""
    return [r for r in records
            if sum(1 for s in SECTIONS if (r.get("review") or {}).get("sections", {}).get(s)) == len(SECTIONS)]


def select(records: List[dict], exclude_name: str = "", n: int = 2) -> List[dict]:
    """Deterministic leave-one-out choice, spread across routes so the anchors are not all R1."""
    pool = [r for r in adjudicated(records)
            if (r.get("name") or "").casefold() != (exclude_name or "").casefold()]
    pool.sort(key=lambda r: r.get("id") or r.get("name", ""))
    picked, seen = [], set()
    for r in pool:                                   # one per route first
        route = ((r.get("route") or {}).get("primary")) or "?"
        if route not in seen:
            picked.append(r); seen.add(route)
        if len(picked) == n: return picked
    for r in pool:                                   # then fill in order
        if r not in picked:
            picked.append(r)
        if len(picked) == n: break
    return picked


def _compact(r: dict) -> dict:
    """The adjudicated judgements only — not the model's raw draft, not the review metadata."""
    g, dim, dig = r.get("gates") or {}, r.get("dimensions") or {}, r.get("dig") or {}
    keep = lambda d, ks: {k: d.get(k) for k in ks if d.get(k) not in (None, "")}
    return {
        "name": r.get("name"),
        "carryingMechanic": keep(r.get("carryingMechanic") or {}, ["text", "evidence"]),
        "gates": {k: keep(g.get(k) or {}, ["pass", "reason", "classes", "evidence"]) for k in ("G1", "G2", "G3")},
        "route": keep(r.get("route") or {}, ["primary", "secondary", "test"]),
        "dimensions": {k: keep(dim.get(k) or {}, ["level", "descriptor", "objective", "counterfactual",
                                                  "competence", "parameter", "evidence"])
                       for k in ("MC", "LOM", "AGE", "CTa")},
        "dig": {k: keep(dig.get(k) or {}, ["op", "note"]) for k in ("S", "M", "L")},
        # v0.5: no D level in a worked example. The prompt tells the model not to assign one, and
        # an adjudicated level shown here would teach it to.
        "distinctiveness": keep(r.get("distinctiveness") or {}, ["closest", "separatingRule", "searchQueries"]),
    }


def block(picked: List[dict]) -> str:
    """The prompt section, built from already-selected records. Empty string when nothing
    qualifies, so the prompt is byte-identical to the zero-shot one and promptHash stays
    comparable."""
    if not picked:
        return ""
    parts = ["\n\n--- WORKED EXAMPLES ---",
             "These records were adjudicated by a human coder. They show how the levels and the gates "
             "were applied to other games; they are not the game you are judging. Apply the same "
             "reading of each descriptor. Note in particular that G3 asks whether the mechanic "
             "survives under AT LEAST ONE interface class, not whether it survives unchanged."]
    for r in picked:
        entry = (r.get("review") or {}).get("entry") or ""
        if entry:
            parts.append(f"\nENTRY (excerpt): {entry[:ENTRY_CHARS]}")
        parts.append(f"ADJUDICATED RECORD: {json.dumps(_compact(r), ensure_ascii=False)}")
    parts.append("--- END WORKED EXAMPLES ---\n")
    return "\n".join(parts)


def count() -> int:
    """How many exemplars the operator asked for. 0 disables the feature."""
    try:
        return max(0, int(os.environ.get("EXEMPLARS", "0")))
    except ValueError:
        return 0


def load_records() -> List[dict]:
    """Adjudicated records from the pool. Imported lazily to keep model.py free of the DB."""
    try:
        from .app import list_records
        return list_records()
    except Exception:
        return []
