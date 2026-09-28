"""Model providers.

    local      any OpenAI-compatible server (Ollama, vLLM, llama.cpp). Default. No API key.
    anthropic  Anthropic Messages API. Optional escape hatch; only used if ANTHROPIC_API_KEY is set.
    mock       offline heuristic, schema-valid, for UI testing only. Never research data.

The instrument (the prompt) is identical across providers: only the transport differs, so
`promptHash` stays comparable between drafts made by different models.
"""
import json, os, re, hashlib, datetime, urllib.request, urllib.parse, urllib.error, html as htmllib
from pathlib import Path

HERE = Path(__file__).parent
INSTRUMENT = (HERE / "instrument.md").read_text(encoding="utf-8")
INSTRUMENT_VERSION = (HERE / "instrument_version.txt").read_text().strip()
PROMPT_HASH = hashlib.sha256(INSTRUMENT.encode()).hexdigest()[:12]

# ---------------------------------------------------------------- providers

LOCAL_BASE_URL = os.environ.get("LOCAL_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LOCAL_API_KEY = os.environ.get("LOCAL_API_KEY", "not-needed")  # Ollama ignores it; vLLM may require a match

# (model, extended-thinking budget) — Anthropic
TIERS = {"quick": ("claude-haiku-4-5-20251001", 0), "default": ("claude-sonnet-4-6", 0), "complex": ("claude-sonnet-4-6", 8000)}
# (model, max_tokens, thinking) — local. `complex` enables the model's own reasoning pass, the
# local analogue of extended thinking; thinking tokens are drawn from max_tokens, hence the budget.
# Measured on the nine pilot games, flags per run (lower is better):
#
#                        self-contradictions   evidence-is-prose   total   s/game
#   qwen3.5:35b                            6                   9      20       21
#   gemma4:31b + schema                    3                  21      31      150
#
# gemma4's runner honours grammar-constrained decoding, which halves the self-contradictions and
# makes every quotation it does give exactly verbatim (median coverage 1.00 vs 0.93). But forcing
# every evidence field to exist makes it write prose where it has nothing to quote, twice as often,
# and it is 7x slower. qwen3.5:35b is the default on the total; set MODEL_OVERRIDE=gemma4:31b to
# trade speed and fabricated evidence for structural guarantees. Which failure matters more is a
# research decision and cannot be settled before the adjudication study.
LOCAL_TIERS = {"quick": ("gemma3:12b", 6000, False), "default": ("qwen3.5:35b", 8000, False),
               "complex": ("qwen3.5:35b", 24000, True)}
LOCAL_SEARCH_MODEL = os.environ.get("LOCAL_SEARCH_MODEL", "qwen3.5:35b")


def resolve_provider() -> str:
    """Explicit MODEL_PROVIDER wins; otherwise a key means anthropic, no key means local."""
    p = (os.environ.get("MODEL_PROVIDER") or "").strip()
    if p:
        return p
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "local"


def local_model(tier: str) -> str:
    return os.environ.get("MODEL_OVERRIDE") or LOCAL_TIERS.get(tier, LOCAL_TIERS["default"])[0]


def build_prompt(name: str, entry: str, exemplar_block: str = "") -> str:
    """The instrument, then optional worked examples, then the game. With no exemplars the string
    is byte-identical to the zero-shot prompt, so promptHash stays comparable."""
    return (f"{INSTRUMENT}{exemplar_block}\n\nGAME NAME (as given): "
            f"{name or '(not given; take it from the entry)'}\n\nENTRY:\n{entry}")


def exemplar_context(name: str) -> tuple:
    """(prompt block, n used). Leave-one-out over fully adjudicated records; off unless EXEMPLARS>0."""
    from . import exemplars as X
    n = X.count()
    if not n:
        return "", 0
    picked = X.select(X.load_records(), name, n)
    return (X.block(picked), len(picked)) if picked else ("", 0)


# ---------------------------------------------------------------- JSON rescue

def _strip_fences(t: str) -> str:
    t = t.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t); t = re.sub(r"\s*```$", "", t)
    return t


def _json_obj(text: str) -> dict:
    """Parse the one JSON object out of a completion. Raises json.JSONDecodeError on failure,
    which app.py turns into a 502 so the coder can retry or raise the tier."""
    t = re.sub(r"(?s)<think>.*?</think>", "", text or "")   # reasoning models leak these
    t = _strip_fences(t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i == -1 or j <= i:
            raise json.JSONDecodeError("no JSON object in completion", t[:200] or "", 0)
        return json.loads(t[i:j + 1])


# ---------------------------------------------------------------- local (OpenAI-compatible)

def _http_json(url: str, payload: dict, timeout: int, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _chat_local(messages: list, model: str, max_tokens: int, json_mode: bool = True,
                thinking: bool = False, timeout: int = 1800) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if not thinking:
        # Reasoning models otherwise spend the whole budget thinking and return empty content.
        payload["reasoning_effort"] = "none"
    try:
        out = _http_json(f"{LOCAL_BASE_URL}/chat/completions", payload, timeout,
                         {"Authorization": f"Bearer {LOCAL_API_KEY}"})
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"local server {e.code} from {LOCAL_BASE_URL}: {e.read().decode('utf-8', 'replace')[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"no local model server at {LOCAL_BASE_URL} ({e.reason}). "
                           f"Start one: `ollama serve`, or set LOCAL_BASE_URL.") from None
    choice = out["choices"][0]
    text = choice["message"].get("content") or ""
    if not text.strip():
        why = ("the budget was spent on the reasoning pass — raise max_tokens for this tier"
               if choice.get("finish_reason") == "length" else f"finish_reason={choice.get('finish_reason')}")
        raise RuntimeError(f"{model} returned no content: {why}")
    return text


JSON_ONLY = "You return exactly one JSON object and nothing else: no prose, no explanation, no markdown fences."

# ---- Ollama native: schema-constrained decoding -----------------------------
# Ollama's OpenAI-compatible endpoint accepts `response_format: json_schema` and silently ignores
# it. Its native /api/chat honours `format: <schema>` — but only on models whose runner applies the
# grammar: the gemma family does, qwen3.5 does not (identical output with and without it). Where it
# works, a whole class of faults becomes impossible instead of merely flagged.
OLLAMA_NATIVE = LOCAL_BASE_URL.rstrip("/").removesuffix("/v1") + "/api/chat"
SCHEMA_MODELS = ("gemma",)                       # prefixes whose runners honour `format`
NUM_CTX = int(os.environ.get("LOCAL_NUM_CTX", "32768"))   # 262144 spills the KV cache to CPU

_SCHEMA_CACHE = {}
def draft_schema() -> dict:
    """The record schema, with pydantic's $defs inlined — Ollama needs it self-contained."""
    if "s" not in _SCHEMA_CACHE:
        from . import schema as S
        sch = S.Draft.model_json_schema(); defs = sch.pop("$defs", {})
        def inline(n):
            if isinstance(n, dict):
                if "$ref" in n:
                    return inline(json.loads(json.dumps(defs[n["$ref"].split("/")[-1]])))
                return {k: inline(v) for k, v in n.items()}
            return [inline(x) for x in n] if isinstance(n, list) else n
        sch = inline(sch)
        # Instrument v0.5: a draft is a search hypothesis. Where the runner honours the grammar,
        # a D level cannot be emitted at all, and the fields written by the retrieval step or by
        # the coder are not offered. (schema.Distinct itself stays wide: legacy records carry a
        # level and a searchNote and must keep validating.)
        dist = (sch.get("properties") or {}).get("distinctiveness") or {}
        props = dist.get("properties") or {}
        if "level" in props:
            props["level"] = {"type": "null"}
        for k in ("searchNote", "counterpart", "searchLog", "verified"):
            props.pop(k, None)
        _SCHEMA_CACHE["s"] = sch
    return _SCHEMA_CACHE["s"]


def schema_capable(model: str) -> bool:
    return (os.environ.get("LOCAL_SCHEMA", "1") not in ("0", "false", "no")
            and any(model.startswith(x) for x in SCHEMA_MODELS))


def _chat_ollama(messages: list, model: str, max_tokens: int, thinking: bool = False,
                 schema: dict | None = None, timeout: int = 1800) -> str:
    body = {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": NUM_CTX}}
    if schema: body["format"] = schema
    if not thinking: body["think"] = False
    try:
        out = _http_json(OLLAMA_NATIVE, body, timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ollama {e.code}: {e.read().decode('utf-8','replace')[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"no ollama at {OLLAMA_NATIVE} ({e.reason})") from None
    text = (out.get("message") or {}).get("content") or ""
    if not text.strip():
        raise RuntimeError(f"{model} returned no content (done_reason={out.get('done_reason')})")
    return text


def extract_local(name: str, entry: str, tier: str = "default") -> dict:
    """Draft a record with a local model. One stricter retry on unparseable output, same tier."""
    model = local_model(tier)
    _, max_tokens, thinking = LOCAL_TIERS.get(tier, LOCAL_TIERS["default"])
    block, n_fs = exemplar_context(name)
    msgs = [{"role": "system", "content": JSON_ONLY},
            {"role": "user", "content": build_prompt(name, entry, block)}]
    if schema_capable(model):
        text = _chat_ollama(msgs, model, max_tokens, thinking=thinking, schema=draft_schema())
    else:
        text = _chat_local(msgs, model, max_tokens, thinking=thinking)
    try:
        return _json_obj(text)
    except json.JSONDecodeError:
        retry = msgs + [{"role": "assistant", "content": text[:2000]},
                        {"role": "user", "content": "That was not one valid JSON object. Return the same content "
                                                    "as a single valid JSON object, starting with { and ending with }."}]
        return _json_obj(_chat_local(retry, model, max_tokens, thinking=thinking))


# ---------------------------------------------------------------- repair pass

REPAIR_RULE = (
    "You returned a record with structural errors. Return the SAME record as one JSON object with "
    "ONLY those errors fixed.\n"
    "Change nothing else. Do not alter any level, op, pass value, route, descriptor, note, evidence "
    "or free text except where an error names it. Do not add or remove judgements. "
    "If a section is missing because it was nested inside another, move it to the top level unchanged.")


def repair_local(draft: dict, entry: str, tier: str = "default") -> tuple:
    """One bounded attempt to fix *structural* faults the checker found — a list where a string
    belongs, an op outside the four allowed values, a section nested inside `gates`.

    It never touches judgements, and it is a second model output, not a rewrite by us: the original
    is kept and stored either way (design rule 3). Returns (repaired_or_None, errors_addressed).
    """
    from . import schema as S
    flags = S.check(draft, entry)
    errs = list(flags.get("schema") or [])      # mechanical faults only
    if not errs:
        return None, []
    model = local_model(tier)
    _, max_tokens, thinking = LOCAL_TIERS.get(tier, LOCAL_TIERS["default"])
    msgs = [{"role": "system", "content": JSON_ONLY},
            {"role": "user", "content": f"RECORD:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
                                        f"ERRORS:\n- " + "\n- ".join(errs) + f"\n\n{REPAIR_RULE}"}]
    try:
        fixed = _json_obj(_chat_local(msgs, model, max_tokens, thinking=thinking))
    except Exception:
        return None, errs
    after = S.check(fixed, entry)
    n_before = len(errs)
    n_after = len(after.get("schema") or [])
    # only accept a repair that actually reduces structural faults
    return (fixed, errs) if n_after < n_before else (None, errs)


def repair_enabled() -> bool:
    return (os.environ.get("REPAIR", "1") or "1").strip() not in ("0", "false", "no", "")


# ---------------------------------------------------------------- anthropic

def extract_anthropic(name: str, entry: str, tier: str = "default") -> dict:
    import anthropic
    model, thinking = TIERS.get(tier, TIERS["default"])
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    block, _ = exemplar_context(name)
    kwargs = dict(model=os.environ.get("MODEL_OVERRIDE", model), max_tokens=6000,
                  messages=[{"role": "user", "content": build_prompt(name, entry, block)}])
    if thinking:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking}; kwargs["max_tokens"] = 6000 + thinking
    resp = client.messages.create(**kwargs)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return _json_obj(text)


# ---------------------------------------------------------------- distinctiveness retrieval (v0.5)
# A separate, logged step (backend/instrument_method_notes.md). The search engine supplies the
# candidates; the model selects among them BY INDEX ONLY, so every URL in the result came from
# the search engine. The result never carries a D level: the coder assigns it after reviewing the
# log. A counterpart is retained only when its URL carries a resolvable identifier.

SEARCH_LANGUAGES = ["en", "tr"]


def search_queries(name: str, closest: str = "", hypothesis: list | None = None) -> list:
    """Query terms for the retrieval step: fixed English and Turkish templates around the game's
    name, the hypothesised counterpart, then the draft's own `searchQueries`. Order-preserving,
    de-duplicated."""
    qs = [f'"{name}" traditional game rules', f'{name} game Turkey traditional children',
          f'"{name}" geleneksel oyun nasıl oynanır']
    if closest:
        qs += [f'{closest} game rules', f'"{name}" "{closest}" similar game']
    qs += [str(q).strip() for q in (hypothesis or []) if str(q or "").strip()]
    out, seen = [], set()
    for q in qs:
        if q.casefold() not in seen:
            seen.add(q.casefold()); out.append(q)
    return out


def _index(v, hits):
    """The hit at index `v`, or None. bool is rejected: True would otherwise read as 1."""
    if isinstance(v, bool):
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return hits[i] if 0 <= i < len(hits) else None     # never a negative index either


def search_prompt(name: str, closest: str, separating: str, hits: list) -> str:
    listing = "\n".join(f'[{i}] {h["title"]} — {h["url"]}\n    {h.get("snippet", "")}' for i, h in enumerate(hits))
    return (
        f"You are checking whether the traditional game '{name}' has a rule-for-rule international counterpart.\n"
        f"A prior hypothesis names '{closest or '(none)'}' with separating rule '{separating or '(none)'}'.\n\n"
        f"These are the ONLY search results available. Judge from them alone. Do not use or invent any other source.\n"
        f"If they are insufficient, say so in `reasoning`.\n\n"
        f"{listing}\n\n"
        "Do NOT assign a distinctiveness level (D0, D1 or D2): a human coder assigns it after reviewing this log. "
        "Refer to results only by their index.\n\n"
        'Return ONLY this JSON object: {"closestMatch":"","separatingRule":"","counterpart":null,'
        '"sources":[{"i":0,"whatItShows":""}],"reasoning":""}  — `i` is the index of a result above; '
        "`sources` lists every result you inspected; `counterpart` is the index of the ONE result that documents "
        "the closest counterpart game, or null if none of them does; `separatingRule` is the core rule that "
        "separates the two games, or empty.")


def build_search_result(name: str, closest: str, separating: str, queries: list, hits: list,
                        raw: dict | None, engine: str, date: str | None = None) -> dict:
    """Assemble the retrieval result from the search hits and the model's selection. Pure.

    `raw` is the model's JSON (None when there was nothing to read). Everything the model says
    about a source is attached to a hit looked up by index; an index we did not hand out is
    dropped, never repaired. `level` is always None and `verified` always False, whatever `raw`
    contains. `counterpart` is an identifier dict only when the chosen hit's URL resolves to a
    Ludii, BoardGameGeek or Wikipedia identifier; otherwise None."""
    from .schema import resolvable_identifier
    from urllib.parse import urlparse
    raw = raw if isinstance(raw, dict) else {}
    today = date or datetime.date.today().isoformat()
    inspected, seen = [], set()
    for s in (raw.get("sources") or []):
        h = _index(s.get("i") if isinstance(s, dict) else s, hits)
        if h is None or h["url"] in seen:
            continue
        seen.add(h["url"])
        inspected.append({"title": h["title"], "url": h["url"],
                          "whatItShows": str((s.get("whatItShows", "") if isinstance(s, dict) else ""))[:400],
                          "identifier": resolvable_identifier(h["url"])})
    pick = raw.get("counterpart")
    chosen = _index(pick.get("i") if isinstance(pick, dict) else pick, hits)
    counterpart = resolvable_identifier(chosen["url"]) if chosen else None
    if chosen and chosen["url"] not in seen:
        inspected.append({"title": chosen["title"], "url": chosen["url"], "whatItShows": "",
                          "identifier": counterpart})
    hosts = []
    for h in hits:
        host = (urlparse(h["url"]).hostname or "").removeprefix("www.")
        if host and host not in hosts:
            hosts.append(host)
    if not hits:
        reasoning = ("No web results were retrieved (search unreachable or blocked). Nothing was inferred; "
                     "no counterpart is retained.")
    else:
        reasoning = str(raw.get("reasoning", ""))
        if chosen and counterpart is None:
            reasoning = (reasoning + " [The selected candidate carries no resolvable identifier (Ludii, "
                         "BoardGameGeek or Wikipedia), so no counterpart is retained.]").strip()
    closest_match = str(raw.get("closestMatch") or raw.get("closest") or "")
    sep = str(raw.get("separatingRule") or "")
    log = {"date": today, "languages": list(SEARCH_LANGUAGES), "sources": hosts, "queries": list(queries),
           "candidatesInspected": inspected, "closestMatch": closest_match, "separatingRule": sep}
    return {"level": None, "verified": False,
            "closest": closest, "separatingRule": separating,          # the hypothesis, as sent
            "counterpart": counterpart, "searchLog": log,
            "sources": [{k: c[k] for k in ("title", "url", "whatItShows")} for c in inspected],
            "queries": list(queries), "reasoning": reasoning, "date": today, "engine": engine}


# ---------------------------------------------------------------- anthropic search

def _anthropic_hits(resp) -> list:
    """[{title, url, snippet}] from the web-search result blocks of a Messages response. These
    are what the search tool returned, not what the model wrote. A failed search carries an
    error object instead of a list and contributes nothing."""
    hits, seen = [], set()
    for b in getattr(resp, "content", None) or []:
        if getattr(b, "type", "") != "web_search_tool_result":
            continue
        items = getattr(b, "content", None)
        if not isinstance(items, list):
            continue
        for it in items:
            url = getattr(it, "url", None) or (it.get("url") if isinstance(it, dict) else None)
            title = getattr(it, "title", None) or (it.get("title") if isinstance(it, dict) else None)
            if url and url not in seen:
                seen.add(url); hits.append({"title": str(title or "")[:200], "url": url, "snippet": ""})
    return hits


def search_anthropic(name: str, closest: str, separating: str, hypothesis: list | None = None) -> dict:
    """Distinctiveness retrieval with the Messages API web-search tool, in two calls so that the
    index-only rule holds here too: the first call runs the searches and only its result blocks
    are kept; the second call, without tools, selects among those results by index."""
    import anthropic
    client = anthropic.Anthropic()
    queries = search_queries(name, closest, hypothesis)
    q = (f"Search the web for counterparts of the traditional game '{name}'"
         + (f" (hypothesised counterpart: '{closest}')" if closest else "")
         + ". Include Wikipedia EN and TR, BoardGameGeek and the Ludii Games Database. Run these searches:\n- "
         + "\n- ".join(queries) + "\nThen reply with the single word DONE.")
    resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": len(queries)}],
        messages=[{"role": "user", "content": q}])
    hits = _anthropic_hits(resp)
    raw = None
    if hits:
        sel = client.messages.create(model="claude-sonnet-4-6", max_tokens=3000, system=JSON_ONLY,
            messages=[{"role": "user", "content": search_prompt(name, closest, separating, hits)}])
        raw = _json_obj("".join(b.text for b in sel.content if getattr(b, "type", "") == "text"))
    return build_search_result(name, closest, separating, queries, hits, raw, "anthropic web_search")


# ---------------------------------------------------------------- local distinctiveness search

DDG_URL = "https://html.duckduckgo.com/html/"
UA = "Mozilla/5.0 (X11; Linux x86_64) carrying-mechanic-workbench/1.0"


def ddg(query: str, limit: int = 6) -> list:
    """Real DuckDuckGo hits: [{title, url, snippet}]. Network failures return [] — never a guess."""
    try:
        req = urllib.request.Request(DDG_URL, data=urllib.parse.urlencode({"q": query}).encode(),
                                     headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r'(?s)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|\Z)', page):
        href, title, tail = m.group(1), m.group(2), m.group(3)
        if href.startswith("//"):
            href = "https:" + href
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        url = q[0] if q else href
        if not url.startswith("http") or url in seen:
            continue
        sn = re.search(r'(?s)class="result__snippet"[^>]*>(.*?)</a>', tail)
        clean = lambda s: htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        seen.add(url)
        out.append({"title": clean(title)[:200], "url": url, "snippet": clean(sn.group(1))[:400] if sn else ""})
        if len(out) >= limit:
            break
    return out


def search_local(name: str, closest: str, separating: str, hypothesis: list | None = None) -> dict:
    """Distinctiveness retrieval without an API key: DuckDuckGo supplies the candidates, the local
    model only reads them and selects by index, so every URL in the result came from the search
    engine — it cannot invent one. No level is returned or defaulted; `verified` stays False."""
    queries = search_queries(name, closest, hypothesis)
    hits, seen = [], set()
    for q in queries:
        for h in ddg(q):
            if h["url"] not in seen:
                seen.add(h["url"]); hits.append(h)
    raw = None
    if hits:
        raw = _json_obj(_chat_local([{"role": "system", "content": JSON_ONLY},
                                     {"role": "user", "content": search_prompt(name, closest, separating, hits)}],
                                    os.environ.get("MODEL_OVERRIDE") or LOCAL_SEARCH_MODEL, 3000))
    return build_search_result(name, closest, separating, queries, hits, raw, "duckduckgo")


def search(name: str, closest: str = "", separating: str = "", hypothesis: list | None = None) -> dict:
    fn = search_anthropic if resolve_provider() == "anthropic" else search_local
    out = fn(name, closest, separating, hypothesis)
    out["level"] = None; out["verified"] = False        # human-only, whatever happened upstream
    return out


# ---------------------------------------------------------------- mock

def extract_mock(name: str, entry: str, tier: str = "default") -> dict:
    """Heuristic stand-in so the interface and evaluation can be exercised without any model. Not a model."""
    low = entry.lower()
    bodily = any(w in low for w in ["running", "chas", "wrestl", "horse", "jump", "hop", "sprint"])
    board = any(w in low for w in ["board", "stones", "pits", "pieces", "dice", "sticks"])
    opp = any(w in low for w in ["opponent", "team", "wins", "winner"])
    route = "R1" if opp else "R2"
    S = "Substituted" if bodily and not board else ("Kept" if board else "Transformed")
    quote = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", entry) if len(s) > 40), "")[:160]
    return {"name": name or "Untitled", "altNames": "", "source": {"region": "", "players": "", "session": ""},
      "carryingMechanic": {"text": "MOCK: first rule-like sentence of the entry.", "evidence": quote},
      "gates": {"G1": {"pass": True, "reason": "MOCK", "evidence": quote}, "G2": {"pass": "horse" not in low, "reason": "MOCK: fails if horses are required", "evidence": ""},
                "G3": {"pass": True, "reason": "MOCK", "classes": ["S"] if board else ["L"], "evidence": ""}},
      "route": {"primary": route, "secondary": "", "test": "MOCK keyword heuristic; replace by test."},
      "dimensions": {"MC": {"level": 1, "descriptor": "MOCK", "evidence": ""}, "LOM": {"level": 1, "objective": "MOCK", "counterfactual": "", "evidence": ""},
                     "AGE": {"level": 1, "competence": "", "parameter": "", "evidence": ""}, "CTa": {"level": 2, "evidence": ""}, "CTb": {"level": 1, "note": ""}, "CTc": {"level": 1, "note": ""}},
      "dig": {"S": {"op": S, "note": "MOCK"}, "M": {"op": "Transformed", "note": ""}, "L": {"op": "Kept", "note": ""}},
      "ledger": [{"element": "Carrying rule", "type": "Rule (carrying)", "original": quote[:60], "underS": "", "operation": S, "note": "MOCK"}],
      "distinctiveness": {"level": None, "closest": "MOCK", "separatingRule": "", "searchQueries": [name or "MOCK"],
                          "counterpart": {"idType": "", "id": "", "url": ""},
                          "searchLog": {"date": "", "languages": [], "sources": [], "queries": [], "candidatesInspected": [],
                                        "closestMatch": "", "separatingRule": ""},
                          "verified": False},
      "ambiguities": ["MOCK provider: every field must be adjudicated"], "confidence": {"overall": 0.2, "low": ["all"]}}


# ---------------------------------------------------------------- entry point

def extract(name, entry, tier="default"):
    """Returns (draft, provider_label). The label records the exact model and, when worked examples
    were in the prompt, a `+fs<n>` suffix — so a pool mixing zero-shot and few-shot drafts stays
    separable when the accuracy table is built. See backend/exemplars.py."""
    provider = resolve_provider()
    _, n_fs = exemplar_context(name)
    fs = f"+fs{n_fs}" if n_fs else ""
    if provider == "anthropic":
        return extract_anthropic(name, entry, tier), f"anthropic:{os.environ.get('MODEL_OVERRIDE', TIERS.get(tier, TIERS['default'])[0])}{fs}"
    if provider == "mock":
        return extract_mock(name, entry, tier), f"mock{fs}"
    return extract_local(name, entry, tier), f"local:{local_model(tier)}{fs}"
