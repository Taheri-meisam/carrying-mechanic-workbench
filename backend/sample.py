"""Draw the adjudication sample, reproducibly.

    python -m backend.sample --n 40 --seed 20260916 --out examples/sample_40.json

The sample must be fixed before coding starts, or it drifts toward games that are easy to judge and
the reliability figure is flattered. So this writes a manifest next to the sample recording the
seed, the source corpus and its sha256, the stratification, and every id drawn: the draw can be
reproduced exactly, and a reviewer can check that it was not adjusted afterwards.

Stratification is by adaptation route when the pool is already drafted (routes have different
architectures and should not be left to chance in a small sample); otherwise the draw is uniform.

This is a general utility. It is NOT the sampler of the 75-game reliability study: that study may
use no output of the instrument for selection, drafted routes included, and draws on descriptive
proxies read from the entry text instead. That sampler is not part of this module.
"""
import argparse, hashlib, json, random, sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def load_corpus(path: Path) -> list:
    d = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(d, list) or not d:
        sys.exit(f"{path} is not a non-empty JSON array")
    return d


def drafted_routes(db: Path) -> dict:
    """name -> route, for entries already drafted, so the draw can be stratified."""
    if not db.exists():
        return {}
    out = {}
    for (j,) in sqlite3.connect(db).execute("SELECT json FROM records"):
        r = json.loads(j)
        out[r.get("name", "")] = ((r.get("route") or {}).get("primary")) or "?"
    return out


def draw(corpus: list, n: int, seed: int, routes: dict) -> tuple:
    rng = random.Random(seed)
    names = [g.get("name", "") for g in corpus]
    if n >= len(corpus):
        return list(corpus), "none (n >= corpus size; the whole corpus is the sample)"
    if not routes:
        idx = sorted(rng.sample(range(len(corpus)), n))
        return [corpus[i] for i in idx], "uniform (pool not drafted, so no route is known)"
    # proportional allocation over known routes, remainder to the largest strata
    strata = defaultdict(list)
    for g in corpus:
        strata[routes.get(g.get("name", ""), "?")].append(g)
    total = len(corpus)
    quota = {k: int(round(n * len(v) / total)) for k, v in strata.items()}
    while sum(quota.values()) != n:
        k = max(quota, key=lambda x: len(strata[x]) - quota[x]) if sum(quota.values()) < n \
            else max(quota, key=lambda x: quota[x])
        quota[k] += 1 if sum(quota.values()) < n else -1
    picked = []
    for k in sorted(strata):
        pool = sorted(strata[k], key=lambda g: g.get("name", ""))
        picked += rng.sample(pool, min(quota[k], len(pool)))
    return picked, "proportional by route: " + ", ".join(f"{k}={quota[k]}" for k in sorted(quota))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="examples/corpus_nine_games.json")
    ap.add_argument("--n", type=int, required=True, help="sample size")
    ap.add_argument("--seed", type=int, required=True, help="record this in the paper")
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=str(HERE.parent / "data" / "workbench.sqlite"))
    a = ap.parse_args()

    src = Path(a.corpus)
    corpus = load_corpus(src)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    routes = drafted_routes(Path(a.db))
    picked, how = draw(corpus, a.n, a.seed, routes)

    if a.n > len(corpus):
        print(f"! asked for {a.n} but the corpus holds {len(corpus)}. "
              f"A reliability sample smaller than the corpus is the point; load more entries first.",
              file=sys.stderr)

    out = Path(a.out); out.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    man = out.with_suffix(".manifest.json")
    man.write_text(json.dumps({
        "drawn": time.strftime("%Y-%m-%d %H:%M:%S"), "n_requested": a.n, "n_drawn": len(picked),
        "seed": a.seed, "corpus": str(src), "corpus_sha256_16": sha, "corpus_size": len(corpus),
        "stratification": how, "names": [g.get("name", "") for g in picked],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(picked)} of {len(corpus)} drawn -> {out}")
    print(f"  seed {a.seed}, corpus sha256 {sha}")
    print(f"  stratification: {how}")
    if routes:
        print("  routes in sample:", dict(Counter(routes.get(g.get('name',''), '?') for g in picked)))
    print(f"  manifest -> {man}")
    print("\nRecord the seed and the corpus hash in the paper; re-running reproduces this exact draw.")


if __name__ == "__main__":
    main()
