"""Batch extraction over a corpus file. Usage: python -m backend.batch corpus.json [--tier default]
corpus.json: [{"name": "...", "entry": "..."}, ...]  Each result is stored as a pending record with its raw draft."""
import json, sys, time, unicodedata, re
from . import model as M
from . import schema as S
from .app import conn, slug

def main():
    path = sys.argv[1]; tier = sys.argv[sys.argv.index("--tier") + 1] if "--tier" in sys.argv else "default"
    corpus = json.load(open(path, encoding="utf-8")); c = conn(); done = 0; flagged = {}
    for item in corpus:
        name = item.get("name", ""); rid = slug(name)
        if c.execute("SELECT 1 FROM records WHERE id=?", (rid,)).fetchone(): print("skip", name); continue
        try:
            draft, provider = M.extract(name, item["entry"], tier)
        except Exception as e:
            print("FAILED", name, type(e).__name__, e); continue
        flags = S.check(draft, item["entry"])
        repaired, addressed = (None, [])
        if provider.startswith("local") and M.repair_enabled():
            try: repaired, addressed = M.repair_local(draft, item["entry"], tier)
            except Exception: repaired, addressed = None, []
        ts = time.time()
        # the coder adjudicates the structurally valid record; review.raw keeps the first output
        # v0.5: the body never carries a model-assigned D level (forced to null on a copy; the
        # verbatim output stays in review.raw and the fault is in flags.humanOnly)
        rec, _ = S.normalise_draft(repaired if repaired is not None else draft)
        rec["id"] = rid; rec["name"] = (repaired or draft).get("name") or name
        rec["review"] = {"coder": "", "sections": {}, "notes": f"Batch draft, {provider}, tier {tier}, instrument {M.INSTRUMENT_VERSION}, prompt {M.PROMPT_HASH}", "raw": draft, "flags": flags, "entry": item["entry"],
                         "repaired": repaired, "repairAddressed": addressed,
                         # structured provenance, not only the prose note: /api/extractions and the
                         # stale check read this, and the CSV export reports it
                         "meta": {"provider": provider, "tier": tier,
                                  "instrument": M.INSTRUMENT_VERSION, "promptHash": M.PROMPT_HASH,
                                  "ts": ts},
                         "updated": int(ts * 1000)}
        c.execute("INSERT OR REPLACE INTO records(id,json,updated) VALUES(?,?,?)", (rid, json.dumps(rec, ensure_ascii=False), ts))
        # design rule 3: the raw draft belongs in the extractions table too, not only in review.raw
        c.execute("INSERT INTO extractions(game,provider,tier,instrument,prompt_hash,ts,draft,flags) VALUES(?,?,?,?,?,?,?,?)",
                  (name, provider, tier, M.INSTRUMENT_VERSION, M.PROMPT_HASH, ts,
                   json.dumps(draft, ensure_ascii=False), json.dumps(flags, ensure_ascii=False)))
        if repaired is not None:
            c.execute("INSERT INTO extractions(game,provider,tier,instrument,prompt_hash,ts,draft,flags) VALUES(?,?,?,?,?,?,?,?)",
                      (name, provider + "+rep", tier, M.INSTRUMENT_VERSION, M.PROMPT_HASH, ts + 0.001,
                       json.dumps(repaired, ensure_ascii=False),
                       json.dumps(S.check(repaired, item["entry"]), ensure_ascii=False)))
        c.commit()
        n_flag = sum(len(v) for v in flags.values())
        for k, v in flags.items():
            if v: flagged[k] = flagged.get(k, 0) + 1
        done += 1
        print("ok", name, f"({n_flag} flags)" if n_flag else "")
    print(f"{done} drafts stored")
    if flagged:
        print("drafts carrying at least one flag, by kind:")
        for k, v in sorted(flagged.items(), key=lambda x: -x[1]): print(f"  {k:16} {v}/{done}")
        print("Flags are advisory. The drafts are stored exactly as the model produced them.")

if __name__ == "__main__": main()
