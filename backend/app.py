"""Carrying Mechanic workbench backend: FastAPI + SQLite. Serves the frontend, calls the model, stores records, computes evaluation."""
import json, os, re, sqlite3, time, unicodedata
from pathlib import Path
from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import model as M
from . import schema as S
from . import corpus as C
from .evaluate import evaluate

HERE = Path(__file__).parent
DB = Path(os.environ.get("WORKBENCH_DB", HERE.parent / "data" / "workbench.sqlite"))
DB.parent.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Carrying Mechanic workbench")

def conn():
    c = sqlite3.connect(DB); c.execute("CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, json TEXT NOT NULL, updated REAL NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS extractions(id INTEGER PRIMARY KEY AUTOINCREMENT, game TEXT, provider TEXT, tier TEXT, instrument TEXT, prompt_hash TEXT, ts REAL, draft TEXT)")
    if "flags" not in {r[1] for r in c.execute("PRAGMA table_info(extractions)")}:
        c.execute("ALTER TABLE extractions ADD COLUMN flags TEXT")   # additive; older rows keep NULL
    return c

def slug(s): 
    s = unicodedata.normalize("NFKD", s or "game").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "game"

class ExtractIn(BaseModel):
    name: str = ""; entry: str; tier: str = "default"

TIER_UP = {"quick": "default", "default": "complex"}

@app.post("/api/extract")
def api_extract(inp: ExtractIn):
    """Draft one record. Invalid JSON is retried once at the next tier up. The draft is returned
    and stored exactly as the model produced it (design rule 3); schema, evidence and consistency
    problems ride alongside in `flags` — flagged, never corrected."""
    if not inp.entry.strip(): raise HTTPException(400, "entry is empty")
    tier = inp.tier
    try:
        draft, provider = M.extract(inp.name, inp.entry, tier)
    except json.JSONDecodeError as e:
        up = TIER_UP.get(tier)
        if not up: raise HTTPException(502, f"model did not return valid JSON: {e}")
        try:
            draft, provider = M.extract(inp.name, inp.entry, up); tier = up
        except json.JSONDecodeError as e2:
            raise HTTPException(502, f"model did not return valid JSON at tier {inp.tier} or {up}: {e2}")
        except Exception as e2:
            raise HTTPException(502, f"model call failed at tier {up}: {type(e2).__name__}: {e2}")
    except Exception as e:
        raise HTTPException(502, f"model call failed: {type(e).__name__}: {e}")
    flags = S.check(draft, inp.entry)
    ts = time.time()
    c = conn()
    c.execute("INSERT INTO extractions(game,provider,tier,instrument,prompt_hash,ts,draft,flags) VALUES(?,?,?,?,?,?,?,?)",
              (inp.name, provider, tier, M.INSTRUMENT_VERSION, M.PROMPT_HASH, ts,
               json.dumps(draft, ensure_ascii=False), json.dumps(flags, ensure_ascii=False)))
    # A repair pass fixes structural faults only. The first output is kept verbatim as the draft
    # (design rule 3); the repaired one rides alongside so the coder works from a valid record and
    # either can be scored later.
    repaired, addressed = (None, [])
    if provider.startswith("local") and M.repair_enabled():
        try: repaired, addressed = M.repair_local(draft, inp.entry, tier)
        except Exception: repaired, addressed = None, []
    if repaired is not None:
        flags_r = S.check(repaired, inp.entry)
        c.execute("INSERT INTO extractions(game,provider,tier,instrument,prompt_hash,ts,draft,flags) VALUES(?,?,?,?,?,?,?,?)",
                  (inp.name, provider + "+rep", tier, M.INSTRUMENT_VERSION, M.PROMPT_HASH, time.time(),
                   json.dumps(repaired, ensure_ascii=False), json.dumps(flags_r, ensure_ascii=False)))
    c.commit(); c.close()
    meta = {"provider": provider, "tier": tier, "requestedTier": inp.tier, "instrument": M.INSTRUMENT_VERSION,
            "promptHash": M.PROMPT_HASH, "ts": ts,
            "repair": {"applied": repaired is not None, "addressed": addressed} if addressed else None}
    out = {"draft": draft, "meta": meta, "flags": flags}
    if repaired is not None:
        out["repaired"] = repaired; out["repairedFlags"] = flags_r
    return out

class SearchIn(BaseModel):
    name: str; closest: str = ""; separatingRule: str = ""; searchQueries: list[str] = []

@app.post("/api/search")
def api_search(inp: SearchIn):
    """Distinctiveness retrieval (instrument v0.5; backend/instrument_method_notes.md). The search
    engine supplies the candidates and the model selects among them by index only, so every URL
    returned came from the search engine. The result carries a `searchLog` and a `counterpart`,
    which is null unless the chosen candidate has a resolvable identifier (Ludii, BoardGameGeek,
    Wikipedia). It never carries a level: `level` is always null and `verified` always false —
    the coder reviews the log and sets both (design rule 7)."""
    try: out = M.search(inp.name, inp.closest, inp.separatingRule, inp.searchQueries)
    except Exception as e: raise HTTPException(502, f"search failed: {type(e).__name__}: {e}")
    out["level"] = None; out["verified"] = False
    return out

@app.get("/api/records")
def list_records():
    c = conn(); rows = c.execute("SELECT json FROM records ORDER BY id").fetchall(); c.close()
    return [json.loads(r[0]) for r in rows]

@app.put("/api/records/{rid}")
def put_record(rid: str, rec: dict = Body(...)):
    rec["id"] = rid; rec.setdefault("review", {}); rec["review"]["updated"] = int(time.time() * 1000)
    c = conn(); c.execute("INSERT OR REPLACE INTO records(id,json,updated) VALUES(?,?,?)", (rid, json.dumps(rec, ensure_ascii=False), time.time())); c.commit(); c.close()
    return {"ok": True, "id": rid}

@app.delete("/api/records/{rid}")
def del_record(rid: str):
    c = conn(); c.execute("DELETE FROM records WHERE id=?", (rid,)); c.commit(); c.close(); return {"ok": True}

@app.post("/api/records/import")
def import_records(recs: list = Body(...)):
    c = conn(); n = 0
    for r in recs:
        rid = r.get("id") or slug(r.get("name")); r["id"] = rid
        c.execute("INSERT OR REPLACE INTO records(id,json,updated) VALUES(?,?,?)", (rid, json.dumps(r, ensure_ascii=False), time.time())); n += 1
    c.commit(); c.close(); return {"ok": True, "imported": n}

@app.post("/api/corpus/upload")
async def corpus_upload(file: UploadFile = File(...), style: str = Form("caps"), pattern: str = Form(""),
                        expected: int = Form(518), ocr: bool = Form(False), lang: str = Form("tur")):
    """Upload the encyclopaedia (PDF or UTF-8 text) and split it into entries.

    Parses only: it writes a corpus file and returns the split plus its warnings, so a coder can
    eyeball the headings before any extraction is run. It never drafts records — that stays an
    explicit `backend.batch` call, because 518 entries is a long job and a costly mistake to
    start by accident."""
    import tempfile
    name = (file.filename or "upload")
    suffix = Path(name).suffix.lower() or ".txt"
    if suffix not in (".pdf", ".txt", ".text", ".md"):
        raise HTTPException(400, f"unsupported file type {suffix!r}; give a PDF or UTF-8 text")
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "empty upload")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(blob); tmp = Path(fh.name)
    try:
        raw = C.pdf_to_text(tmp, ocr, lang) if suffix == ".pdf" else blob.decode("utf-8", "replace")
        entries = C.split_entries(C.clean(raw), style, pattern or None)
        rep = C.report(entries, expected)
    except SystemExit as e:                     # the loader exits on missing poppler/tesseract/lang
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(422, f"could not parse {name}: {type(e).__name__}: {e}")
    finally:
        tmp.unlink(missing_ok=True)
    if not entries:
        raise HTTPException(422, f"no entries matched the {style!r} heading style; try another --style or a custom pattern")
    out = DB.parent / f"corpus_{int(time.time())}.json"
    out.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"report": rep, "corpusFile": str(out),
            "preview": [{"name": e["name"], "words": len(e["entry"].split()),
                         "head": e["entry"][:300]} for e in entries],
            "next": f"python -m backend.batch {out} --tier default"}

@app.get("/api/extractions")
def extractions(game: str = "", limit: int = 100):
    """A record's drafting history: every model call that produced a draft for it, newest first.

    The table is append-only, so re-extracting under a new instrument version adds a row rather than
    replacing one. That is what makes an accuracy figure attributable: a draft can be traced to the
    model, tier, instrument version and prompt hash that produced it."""
    c = conn()
    sql = ("SELECT id,game,provider,tier,instrument,prompt_hash,ts,draft,flags FROM extractions"
           + (" WHERE game=?" if game else "") + " ORDER BY ts DESC LIMIT ?")
    rows = c.execute(sql, ((game, limit) if game else (limit,))).fetchall(); c.close()
    out = []
    for i, g, prov, tier, instr, ph, ts, draft, flags in rows:
        f = json.loads(flags) if flags else {}
        out.append({"id": i, "game": g, "provider": prov, "tier": tier, "instrument": instr,
                    "promptHash": ph, "ts": ts,
                    "stale": bool(instr) and instr != M.INSTRUMENT_VERSION,
                    "flagCount": sum(len(v) for k, v in f.items() if k != "evidenceCoverage"),
                    "draft": json.loads(draft) if draft else None})
    return {"instrument": M.INSTRUMENT_VERSION, "promptHash": M.PROMPT_HASH,
            "game": game or None, "count": len(out), "extractions": out}

@app.post("/api/records/{rid}/reextract")
def reextract(rid: str):
    """Re-draft a record under the current instrument, keeping everything that came before.

    The previous draft is pushed onto `review.priorRaw` and remains in the `extractions` table, so
    nothing is lost (design rule 3). The adjudicated values and the reviewed flags are deliberately
    NOT cleared: a coder's work is not ours to discard, and whether an adjudication survives an
    instrument change is a judgement for the researchers. The record is marked so they can see it
    needs revisiting."""
    c = conn()
    row = c.execute("SELECT json FROM records WHERE id=?", (rid,)).fetchone()
    if not row: c.close(); raise HTTPException(404, f"no record {rid}")
    rec = json.loads(row[0]); rev = rec.setdefault("review", {})
    entry = rev.get("entry") or ""
    if not entry.strip():
        c.close(); raise HTTPException(400, "this record has no stored source entry to re-extract from")
    try:
        draft, provider = M.extract(rec.get("name", ""), entry, (rev.get("meta") or {}).get("tier") or "default")
    except Exception as e:
        c.close(); raise HTTPException(502, f"model call failed: {type(e).__name__}: {e}")
    ts = time.time()
    flags = S.check(draft, entry)
    c.execute("INSERT INTO extractions(game,provider,tier,instrument,prompt_hash,ts,draft,flags) VALUES(?,?,?,?,?,?,?,?)",
              (rec.get("name", ""), provider, (rev.get("meta") or {}).get("tier") or "default",
               M.INSTRUMENT_VERSION, M.PROMPT_HASH, ts,
               json.dumps(draft, ensure_ascii=False), json.dumps(flags, ensure_ascii=False)))
    if rev.get("raw"):
        rev.setdefault("priorRaw", []).append({"raw": rev["raw"], "meta": rev.get("meta")})
    rev["raw"] = draft
    rev["flags"] = flags
    rev["meta"] = {"provider": provider, "tier": (rev.get("meta") or {}).get("tier") or "default",
                   "instrument": M.INSTRUMENT_VERSION, "promptHash": M.PROMPT_HASH, "ts": ts}
    rev["needsRecheck"] = True
    rev["updated"] = int(ts * 1000)
    c.execute("INSERT OR REPLACE INTO records(id,json,updated) VALUES(?,?,?)",
              (rid, json.dumps(rec, ensure_ascii=False), ts))
    c.commit(); c.close()
    return {"ok": True, "id": rid, "meta": rev["meta"], "flags": flags,
            "priorDrafts": len(rev.get("priorRaw", []))}

@app.get("/api/eval")
def api_eval():
    return evaluate(list_records())

@app.get("/api/export.csv")
def export_csv():
    """One row per record: the fields evaluate.F scores, plus who reviewed it and what the rules
    derived. This is the file the paper tables are built from, so draft and adjudicated values are
    both present and the model that produced the draft is named."""
    import csv, io
    from .evaluate import F, eligible, reviewed, SECTIONS
    rows = list_records()
    fields = list(F.keys())
    head = (["id", "name", "coder", "reviewedSections", "eligible", "hybrid", "fails",
             "Dverified", "provider", "tier", "instrument", "promptHash"]
            + [f"{f}_draft" for f in fields] + [f"{f}_adjudicated" for f in fields]
            + [f"{f}_coder2" for f in ("route", "MC", "LOM", "AGE", "CTa", "S", "D")])
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(head)
    for r in rows:
        rev, e = r.get("review") or {}, eligible(r)
        meta, raw, r2 = rev.get("meta") or {}, rev.get("raw"), r.get("review2") or {}
        def val(fn, rec):
            try: return fn(rec)
            except Exception: return ""
        w.writerow([r.get("id",""), r.get("name",""), rev.get("coder",""), reviewed(r),
                    e["ok"], e["hybrid"], ";".join(e["fails"]),
                    (r.get("distinctiveness") or {}).get("verified", False),
                    meta.get("provider",""), meta.get("tier",""), meta.get("instrument",""),
                    meta.get("promptHash","")]
                   + [val(F[f], raw) if raw else "" for f in fields]
                   + [val(F[f], r) for f in fields]
                   + [r2.get(f, "") for f in ("route","MC","LOM","AGE","CTa","S","D")])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="workbench_export.csv"'})

@app.get("/api/meta")
def meta():
    p = M.resolve_provider()
    out = {"instrument": M.INSTRUMENT_VERSION, "promptHash": M.PROMPT_HASH, "provider": p, "search": p != "mock"}
    if p == "local":
        out["model"] = M.local_model("default"); out["baseUrl"] = M.LOCAL_BASE_URL
        try:
            import urllib.request
            with urllib.request.urlopen(M.LOCAL_BASE_URL + "/models", timeout=5) as r:
                out["reachable"] = r.status == 200
        except Exception as e:
            out["reachable"] = False; out["error"] = f"{type(e).__name__}: {e}"
    return out

FRONT = HERE.parent / "frontend"
@app.get("/")
def index(): return FileResponse(FRONT / "index.html")
app.mount("/static", StaticFiles(directory=FRONT), name="static")
