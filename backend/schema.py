"""Server-side schema enforcement for model drafts. Mirrors the frontend `normalise()`.

Everything here *flags*; nothing rewrites or drops the model's answers. The drafts are the
evaluation data for Paper 2 (design rule 3), so `/api/extract` still returns and stores the raw
object exactly as the model produced it — these checks ride alongside it in `flags`.

Instrument v0.5: `normalise_draft()` builds the record *body* from a fresh draft with the D level
forced to null. It works on a copy; the raw draft is still stored verbatim.
"""
import re
from typing import Any, List, Literal
from pydantic import BaseModel, Field, ValidationError

OPS = ("Kept", "Transformed", "Substituted", "Removed")
SURVIVES = {"Kept", "Transformed"}
CLASSES = ("S", "M", "L")
Level = int


# ---- pydantic mirror of frontend normalise() ---------------------------------

class Source(BaseModel):
    region: str = ""; players: str = ""; session: str = ""

class Mechanic(BaseModel):
    text: str = ""; evidence: str = ""

class Gate(BaseModel):
    pass_: bool = Field(True, alias="pass"); reason: str = ""; evidence: str = ""
    model_config = {"populate_by_name": True}

class Gate3(Gate):
    classes: List[str] = []

class Gates(BaseModel):
    G1: Gate = Gate(); G2: Gate = Gate(); G3: Gate3 = Gate3()
    model_config = {"extra": "forbid"}      # a stray key here means the model never closed `gates`

class Route(BaseModel):
    primary: Literal["R1", "R2", "R3"] = "R1"; secondary: str = ""; test: str = ""

class MCDim(BaseModel):
    level: Level = 1; descriptor: str = ""; evidence: str = ""

class LOMDim(BaseModel):
    level: Level = 1; objective: str = ""; counterfactual: str = ""; evidence: str = ""

class AGEDim(BaseModel):
    level: Level = 1; competence: str = ""; parameter: str = ""; evidence: str = ""

class CTaDim(BaseModel):
    level: Level = 1; evidence: str = ""

class CTx(BaseModel):
    level: Level = 1; note: str = ""

class Dimensions(BaseModel):
    MC: MCDim = MCDim(); LOM: LOMDim = LOMDim(); AGE: AGEDim = AGEDim()
    CTa: CTaDim = CTaDim(); CTb: CTx = CTx(); CTc: CTx = CTx()
    model_config = {"extra": "forbid"}

class SubstitutionTest(BaseModel):
    """Instrument v0.2, part 5a. Only meaningful when op == "Substituted"; only S affects
    eligibility. `pass` None means the test has not been run."""
    pass_: bool | None = Field(None, alias="pass"); reason: str = ""
    model_config = {"populate_by_name": True}

class DigOp(BaseModel):
    op: Literal["Kept", "Transformed", "Substituted", "Removed"] = "Transformed"
    note: str = ""
    substitutionTest: SubstitutionTest = SubstitutionTest()

class Dig(BaseModel):
    S: DigOp = DigOp(); M: DigOp = DigOp(); L: DigOp = DigOp(op="Kept")
    model_config = {"extra": "forbid"}

class LedgerRow(BaseModel):
    element: str = ""; type: str = "Rule"; original: str = ""; underS: str = ""
    operation: Literal["Kept", "Transformed", "Substituted", "Removed", "Added"] = "Kept"; note: str = ""

class Counterpart(BaseModel):
    """A retained counterpart. Only kept when it carries a resolvable identifier (a Ludii game ID,
    a BoardGameGeek ID or a Wikipedia URL); see `resolvable_identifier`. The draft skeleton prints
    it empty, the retrieval step sets it to an identifier or to null."""
    idType: str = ""; id: str = ""; url: str = ""

class SearchLog(BaseModel):
    """Instrument v0.5 retrieval log. Written by the retrieval step, reviewed by the coder."""
    date: str = ""; languages: List[str] = []; sources: List[Any] = []; queries: List[str] = []
    candidatesInspected: List[Any] = []; closestMatch: str = ""; separatingRule: str = ""

class Distinct(BaseModel):
    """Accepts both shapes. v0.1-v0.4 records carry a model-assigned `level` and a `searchNote`
    string; they must keep loading unchanged. From v0.5 the draft is a search hypothesis only:
    `level` is null until a coder assigns it after reviewing the search log."""
    level: Literal["D0", "D1", "D2"] | None = None
    closest: str = ""; separatingRule: str = ""
    searchQueries: List[str] = []
    searchNote: str | None = ""                     # legacy (v0.1-v0.4); tolerated, never required
    counterpart: Counterpart | None = Counterpart()
    searchLog: SearchLog | None = SearchLog()
    verified: bool = False

class Confidence(BaseModel):
    overall: float = 0.5; low: List[str] = []

class Draft(BaseModel):
    name: str = "Untitled"
    altNames: str | List[str] = ""      # models return either; normalise() joins a list with "; "
    source: Source = Source(); carryingMechanic: Mechanic = Mechanic()
    gates: Gates = Gates(); route: Route = Route(); dimensions: Dimensions = Dimensions()
    dig: Dig = Dig(); ledger: List[LedgerRow] = []; distinctiveness: Distinct = Distinct()
    ambiguities: List[str] = []; confidence: Confidence = Confidence()


REQUIRED = ("name", "carryingMechanic", "gates", "route", "dimensions", "dig", "distinctiveness")

def missing_sections(draft: dict) -> List[str]:
    """Top-level sections absent from the draft.

    Every field in `Draft` has a default, so a draft that simply lacks `route` or `dig` validates
    clean and arrives with defaults that look like real judgements. That is exactly the failure the
    complex tier produced once — the model did not close `gates` and nested five sections inside
    it — so presence is checked separately from shape."""
    return [k for k in REQUIRED if k not in draft]


# ---- evidence: does the quotation actually occur in the entry? ----------------

_QUOTES = '"\u201c\u201d\u2018\u2019\u00ab\u00bb\'`'
COVERAGE_MIN = 0.4   # below this a quotation is prose about the entry, not a quotation from it

def _words(s: str):
    return re.sub(r"[^\w\s]", " ", s or "").split()

def coverage(quote: str, entry: str) -> float:
    """How much of `quote` occurs verbatim in `entry`, as a 0-1 ratio.

    Model quotations are usually faithful but *stitched*: fragments joined by an ellipsis, with
    editorial insertions in brackets and sometimes an attribution prefix. Requiring one contiguous
    span flags almost everything, so each fragment is scored on its own with a short n-gram. A
    genuine quotation lands near 1.0; prose written *about* the entry lands at 0.0.
    """
    q = re.sub(r"\[[^\]]*\]", " ", quote or "")
    q = re.sub(r"^[^:]{0,40}(?:documents|writes|states|notes|records)\s*:", " ", q, flags=re.I)
    hay = " ".join(_words(entry)).casefold()
    parts = [p for p in re.split(r"\s*(?:\.\.\.|\u2026)\s*", q) if _words(p)]
    if not parts:
        return 1.0
    num = den = 0
    for part in parts:
        pw = _words(part); n = min(4, len(pw))
        if not n:
            continue
        runs = [" ".join(pw[i:i + n]).casefold() for i in range(len(pw) - n + 1)] or [" ".join(pw).casefold()]
        num += sum(r in hay for r in runs); den += len(runs)
    return num / den if den else 1.0


def _evidence_fields(d: dict):
    g = d.get("gates") or {}
    yield "carryingMechanic", ((d.get("carryingMechanic") or {}).get("evidence") or "")
    for k in ("G1", "G2", "G3"):
        yield f"gates.{k}", ((g.get(k) or {}).get("evidence") or "")
    dim = d.get("dimensions") or {}
    for k in ("MC", "LOM", "AGE", "CTa"):
        yield f"dimensions.{k}", ((dim.get(k) or {}).get("evidence") or "")


def evidence_coverage(draft: dict, entry: str) -> dict:
    """Per-field verbatim coverage, for every field carrying a non-empty quotation."""
    return {p: round(coverage(v, entry), 2) for p, v in _evidence_fields(draft) if (v or "").strip()}


def evidence_missing(draft: dict, entry: str) -> List[str]:
    """Fields whose `evidence` is prose about the entry rather than a quotation from it."""
    return [f"{p} (coverage {c:.2f})" for p, c in evidence_coverage(draft, entry).items() if c < COVERAGE_MIN]


def missing_evidence(draft: dict) -> List[str]:
    """Field paths carrying no quotation at all (design rule 4 expects one on every judgement)."""
    return [p for p, v in _evidence_fields(draft) if not (v or "").strip()]


# ---- internal consistency ----------------------------------------------------

def inconsistencies(draft: dict) -> List[str]:
    """(see below; v0.2 adds the untested-substitution check)"""
    """Contradictions inside a single draft. The instrument defines G3 as survival under at
    least one interface class, so G3 is derivable from the draft's own dig ops."""
    out = []
    dig = draft.get("dig") or {}
    ops = {k: ((dig.get(k) or {}).get("op")) for k in CLASSES}
    g3 = (draft.get("gates") or {}).get("G3") or {}
    implied = any(ops.get(k) in SURVIVES for k in CLASSES)
    stated = bool(g3.get("pass", True))
    if implied != stated:
        surv = [k for k in CLASSES if ops.get(k) in SURVIVES]
        out.append(f"gates.G3.pass={stated} but dig implies {implied} "
                   f"(survives under {surv or 'no class'}: S={ops.get('S')}, M={ops.get('M')}, L={ops.get('L')})")
    s = (dig.get("S") or {})
    if s.get("op") == "Substituted" and ((s.get("substitutionTest") or {}).get("pass")) is None:
        out.append("dig.S.op=Substituted but the substitution test has not been run "
                   "(instrument v0.2 part 5a); eligibility cannot be decided without it")
    cls = g3.get("classes") or []
    if stated and not cls:
        out.append("gates.G3.pass=True but gates.G3.classes is empty; the instrument asks which classes survive")
    for c in cls:
        if c not in CLASSES:
            out.append(f"gates.G3.classes contains {c!r}, not one of S/M/L")
        elif ops.get(c) not in SURVIVES:
            out.append(f"gates.G3.classes lists {c} but dig.{c}.op={ops.get(c)!r} is not Kept/Transformed")
    return out


def human_only_fields(draft: dict) -> List[str]:
    """Design rule 2: the model never sets these. Flag if the draft came back carrying them.

    Instrument v0.5 adds `distinctiveness.level`: the draft is a search hypothesis and the D level
    is assigned by the coder after reviewing the search log. A level in a model draft is exactly
    the defect v0.5 removes (the v0.1-v0.4 skeleton printed "D1" and every draft echoed it)."""
    out = []
    dist = draft.get("distinctiveness")
    dist = dist if isinstance(dist, dict) else {}
    if dist.get("level") not in (None, ""):
        out.append("distinctiveness.level")
    if dist.get("verified"):
        out.append("distinctiveness.verified")
    rev = draft.get("review") or {}
    if rev.get("sections"):
        out.append("review.sections")
    if rev.get("coder"):
        out.append("review.coder")
    return out


# ---- instrument v0.5: distinctiveness is a hypothesis, not a classification ---

LEVEL_FLAG = "distinctiveness.level"

def normalise_draft(draft: dict) -> tuple:
    """The record body built from a FRESH model draft: a deep copy with `distinctiveness.level`
    forced to null and `verified` forced to false, whatever the model emitted.

    Returns (body, warnings). The draft passed in is never modified: the verbatim model output is
    still what goes into `review.raw` and the `extractions` table (design rule 3), and `check()`
    reports the same fault under `humanOnly`. This is for fresh drafts only. Stored records made
    under v0.1-v0.4 carry a level and are not passed through here."""
    import copy
    body = copy.deepcopy(draft) if isinstance(draft, dict) else {}
    warnings = []
    dist = body.get("distinctiveness")
    if not isinstance(dist, dict):
        dist = {}
    had = dist.get("level")
    if had not in (None, ""):
        warnings.append(f"{LEVEL_FLAG}: the model assigned {had!r}; set to null "
                        "(instrument v0.5: the coder assigns the level after the search log)")
    if dist.get("verified"):
        warnings.append("distinctiveness.verified: the model set a human-only flag; set to false")
    dist["level"] = None
    dist["verified"] = False
    body["distinctiveness"] = dist
    return body, warnings


_LUDII = re.compile(r"^(?:www\.)?ludii\.games$", re.I)
_BGG = re.compile(r"^(?:www\.)?boardgamegeek\.com$", re.I)
_WIKI = re.compile(r"^(?:[a-z0-9-]+\.)?(?:m\.)?wikipedia\.org$", re.I)

def resolvable_identifier(url: str):
    """{"idType", "id", "url"} when `url` carries a resolvable identifier, else None.

        ludii      ludii.games/details.php?keyword=<name>  (also ?id=<n>, ?gameId=<n>)
        bgg        boardgamegeek.com/boardgame/<digits>[/slug]   -> the numeric id
        wikipedia  <lang>.wikipedia.org/wiki/<Title>             -> "<lang>:<Title>"

    Pure: no network, no guessing. A search-results page, a blog, a video, a Wikipedia URL that
    is not an article (Special:, File:, ...) or a BoardGameGeek page that is not a game all
    return None, and the retrieval step then records the counterpart as null."""
    from urllib.parse import urlparse, parse_qs, unquote
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    host, path = u.hostname, u.path or ""
    if _LUDII.match(host):
        q = parse_qs(u.query)
        if path.rstrip("/").lower().endswith("/details.php"):
            for k in ("keyword", "id", "gameId", "gameid"):
                v = (q.get(k) or [""])[0].strip()
                if v:
                    return {"idType": "ludii", "id": v, "url": url}
        return None
    if _BGG.match(host):
        m = re.match(r"^/boardgame(?:expansion)?/(\d+)(?:/|$)", path)
        return {"idType": "bgg", "id": m.group(1), "url": url} if m else None
    if _WIKI.match(host):
        m = re.match(r"^/wiki/(.+)$", path)
        if not m:
            return None
        title = unquote(m.group(1)).strip("/")
        if not title or re.match(r"^(Special|File|Help|Talk|User|Category|Template|Portal|"
                                 r"Wikipedia|Özel|Dosya|Kategori|Yardım|Şablon|Vikipedi)\s*:", title, re.I):
            return None
        lang = host.lower().split(".")[0]
        lang = "en" if lang in ("wikipedia", "www") else lang
        return {"idType": "wikipedia", "id": f"{lang}:{title}", "url": url}
    return None


# ---- entry point -------------------------------------------------------------

def check(draft: dict, entry: str = "") -> dict:
    """All flags for one draft. Never mutates `draft`."""
    problems = [f"{k}: section missing from the draft" for k in missing_sections(draft)]
    try:
        Draft.model_validate(draft)
    except ValidationError as e:
        for err in e.errors():
            problems.append(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
    return {"schema": problems,
            "evidenceMissing": evidence_missing(draft, entry) if entry else [],
            "evidenceAbsent": missing_evidence(draft),
            "inconsistent": inconsistencies(draft),
            "humanOnly": human_only_fields(draft),
            "evidenceCoverage": evidence_coverage(draft, entry) if entry else {}}
