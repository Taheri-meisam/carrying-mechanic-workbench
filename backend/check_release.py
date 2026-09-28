"""Refuse to ship the encyclopaedia text.

The source entries are the Ministry's, not ours. They live in the working pool
(`review.entry`) because the workbench displays them, and they must not reach a public
deposit, a released repository, or anything licensed CC-BY/CC0. `pool_public.json` is the
redistributable form: identical except that the entry text is replaced by a note, and it
reproduces every published figure.

    python -m backend.check_release <path>...    # exits non-zero if any file carries entry text
"""
import json, pathlib, sys

THRESHOLD = 400          # a quotation is short; an entry is thousands of characters


def entry_text(path: pathlib.Path):
    """Return (field, length) for any record field holding verbatim entry prose."""
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    found = []
    for rec in blob if isinstance(blob, list) else [blob]:
        if not isinstance(rec, dict):
            continue
        e = (rec.get("review") or {}).get("entry") or ""
        if isinstance(e, str) and len(e) > THRESHOLD and not e.lstrip().startswith("[Source entry withheld"):
            found.append((rec.get("name", "?"), len(e)))
    return found


def main(paths):
    bad = 0
    for p in (pathlib.Path(x) for x in paths):
        hits = entry_text(p)
        if hits:
            bad += 1
            total = sum(n for _, n in hits)
            print(f"  *** {p}: {len(hits)} records carry source-entry text ({total:,} chars)")
            print(f"      Use pool_public.json, or strip review.entry before releasing.")
        else:
            print(f"  ok  {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["analysis/pool.json"]))
