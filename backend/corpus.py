"""Corpus loader: encyclopaedia PDF (or plain text) -> corpus.json for `backend.batch`.

    python -m backend.corpus book.pdf --dry-run                 # show detected headings, change nothing
    python -m backend.corpus book.pdf -o examples/corpus.json
    python -m backend.corpus book.pdf --style caps --ocr --lang tur

Turkish names are preserved exactly; nothing here ASCII-folds. The heading style is a guess about
someone else's typography, so `--dry-run` exists to be run first and the pattern tuned before any
corpus.json is written. Entries under 150 words are reported, never dropped.
"""
import argparse, json, re, shutil, subprocess, sys, unicodedata
from collections import Counter
from pathlib import Path

MIN_WORDS = 150

# Heading styles. Each matches a whole stripped line that stands alone as an entry title.
STYLES = {
    # ALL CAPS, Turkish-aware (İ, Ş, Ğ, Ü, Ö, Ç); allows a trailing entry number
    "caps": r"^[0-9]{0,4}[.\)]?\s*([A-ZÇĞİIÖŞÜ][A-ZÇĞİIÖŞÜ\s\-'’\.]{2,60})\s*$",
    # "12. Birdir Bir" / "12 Birdir Bir"
    "numbered": r"^\s*[0-9]{1,4}\s*[.\)]\s+(\S.{2,60})\s*$",
    # Title Case line with no sentence punctuation
    "titlecase": r"^([A-ZÇĞİIÖŞÜ][\wÇĞİIÖŞÜçğıiöşü'’\-]*(?:\s+[A-ZÇĞİIÖŞÜ][\wÇĞİIÖŞÜçğıiöşü'’\-]*){0,6})\s*$",
}


# ---------------------------------------------------------------- extraction

def pdf_to_text(path: Path, ocr: bool = False, lang: str = "tur", layout: bool = True) -> str:
    """`-layout` preserves the page grid, which is right for single-column books. A two-column
    book needs reading order instead (`layout=False`), or the two columns interleave line by line
    and every sentence is cut in half."""
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found (install poppler-utils), or pass a .txt file instead")
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + ["-enc", "UTF-8", str(path), "-"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"pdftotext failed: {out.stderr[:300]}")
    text = out.stdout
    if ocr or len(text.strip()) < 200:
        if not ocr:
            print("! almost no embedded text — the PDF looks scanned. Re-run with --ocr", file=sys.stderr)
            return text
        text = ocr_pdf(path, lang)
    return text


def ocr_pdf(path: Path, lang: str) -> str:
    if not shutil.which("tesseract"):
        sys.exit("tesseract not found; install tesseract-ocr and the language data")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    if lang not in langs.split():
        sys.exit(f"tesseract has no '{lang}' data (installed: {' '.join(langs.split()[1:]) or 'none'}).\n"
                 f"  sudo apt install tesseract-ocr-{lang}\n"
                 f"OCRing Turkish with English data mangles ı/ş/ğ/İ and is not worth doing.")
    if not shutil.which("pdftoppm"):
        sys.exit("pdftoppm not found (poppler-utils) — needed to rasterise pages for OCR")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(path), f"{td}/p"], check=True)
        pages = sorted(Path(td).glob("p*.png"))
        print(f"OCR: {len(pages)} pages at 300dpi, lang={lang}", file=sys.stderr)
        chunks = []
        for i, img in enumerate(pages, 1):
            r = subprocess.run(["tesseract", str(img), "-", "-l", lang, "--psm", "6"],
                               capture_output=True, text=True)
            chunks.append(r.stdout)
            if i % 25 == 0: print(f"  ...{i}/{len(pages)}", file=sys.stderr)
    return "\n".join(chunks)


# ---------------------------------------------------------------- broken glyph recovery

TR = "A-Za-zçğıöşüÇĞİÖŞÜâîû"

# Some PDFs carry a font whose ToUnicode map is wrong, so correct-looking glyphs extract as
# punctuation. In the GameTable encyclopaedia (Quartz PDFContext) `!` and `"` both stand for `i`
# and `#` stands for *either* `ğ` or `ş` — `İnsanlı#ın` is İnsanlığın but `ba#langıç` is başlangıç.
# The first two are substituted; the third is resolved only where the evidence is decisive, and
# reported otherwise. Guessing it would corrupt a research corpus silently.
GLYPH_I = ('!', '"')

# Suffixes where Turkish morphology fixes the letter: -dığı/-diği/-duğu/-düğü and -lığı/-liği and
# -acağı/-eceği take ğ; -mış/-miş/-muş/-müş and -ış/-iş/-uş/-üş take ş.
G_PATTERNS = [r"[dt][ıiuü]#[ıiuü]", r"l[ıiuü]#[ıiuü]", r"[ae]c[ae]#[ıi]", r"[oöuü]#l", r"[aeıioöuü]#[aeıioöuü]?n?d[ae]"]
S_PATTERNS = [r"m[ıiuü]#$", r"[ıiuü]#$", r"^#", r"#[ktpç]"]


def recover_glyphs(text: str) -> tuple:
    """Return (repaired text, report). Report lists every `#` word left unresolved, with counts."""
    import collections
    out = text
    # `!` and `"` mean `i` only between letters; a real quote or exclamation is left alone
    n_i = 0
    # between two letters: der!ves, gerekmekted"r
    for ch in GLYPH_I:
        out, k = re.subn(f"(?<=[{TR}]){re.escape(ch)}(?=[{TR}])", "i", out); n_i += k
    # word-initial `!` glued to a lowercase letter: !nner, !ons, !şlevle. A real exclamation mark is
    # followed by a space, and a real opening quote is excluded by requiring `!` rather than `"`.
    out, k = re.subn(f"(?<![{TR}])!(?=[a-zçğıöşü])", "i", out); n_i += k

    lex = {w.lower() for w in re.findall(f"[{TR}]{{2,}}", out) if "#" not in w}
    words = collections.Counter(w for w in re.findall(f"[{TR}]*#[{TR}]*", out) if len(w) > 1)

    fixed, unresolved, how = {}, collections.Counter(), collections.Counter()
    for w in words:
        cands = {c: w.replace("#", c) for c in ("ğ", "ş")}
        hits = [c for c, v in cands.items() if v.lower() in lex]
        if len(hits) == 1:
            fixed[w] = cands[hits[0]]; how["same word spelled correctly elsewhere"] += words[w]
        elif any(re.search(p, w) for p in G_PATTERNS):
            fixed[w] = cands["ğ"]; how["Turkish suffix implies ğ"] += words[w]
        elif any(re.search(p, w) for p in S_PATTERNS):
            fixed[w] = cands["ş"]; how["Turkish rule implies ş"] += words[w]
        else:
            unresolved[w] = words[w]
    for w, v in sorted(fixed.items(), key=lambda x: -len(x[0])):
        out = re.sub(rf"(?<![{TR}]){re.escape(w)}(?![{TR}])", v, out)

    return out, {"i_substitutions": n_i, "resolved": sum(how.values()), "how": dict(how),
                 "unresolved_words": sum(unresolved.values()),
                 "unresolved": unresolved.most_common()}


# ---------------------------------------------------------------- cleanup

def clean(text: str) -> list:
    """Normalise to a list of lines: de-hyphenate across line breaks, drop running heads and page
    numbers. Composed Unicode so 'İ' and 'ş' compare predictably."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\f", "\n"))
    text = re.sub(r"(\w)[-‐‑]\n\s*(\w)", r"\1\2", text)      # word-split across lines
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    # a short line repeated on many pages is a running head, not content
    counts = Counter(l for l in lines if 0 < len(l) < 60)
    heads = {l for l, n in counts.items() if n > 8 and not re.search(r"[.!?]$", l)}
    out = []
    for l in lines:
        if not l or l in heads: 
            out.append("") if not l else None
            continue
        if re.fullmatch(r"[\-–—\s]*\d{1,4}[\-–—\s]*", l):  # bare page number
            continue
        out.append(l)
    return out


# ---------------------------------------------------------------- splitting

def split_entries(lines: list, style: str = "caps", pattern: str = None) -> list:
    rx = re.compile(pattern or STYLES[style])
    heads = []
    for i, l in enumerate(lines):
        if not l:
            continue
        m = rx.match(l)
        if not m:
            continue
        # a heading is followed by prose, not by another heading or a blank run
        nxt = next((x for x in lines[i + 1:i + 4] if x), "")
        if not nxt or rx.match(nxt):
            continue
        heads.append((i, (m.group(1) if m.groups() else l).strip()))
    entries = []
    for k, (i, name) in enumerate(heads):
        j = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = " ".join(x for x in lines[i + 1:j] if x).strip()
        entries.append({"name": name, "entry": body})
    return entries


def report(entries: list, expected: int = 518) -> dict:
    short = [e["name"] for e in entries if len(e["entry"].split()) < MIN_WORDS]
    empty = [e["name"] for e in entries if not e["entry"].strip()]
    names = Counter(e["name"] for e in entries)
    return {"count": len(entries), "expected": expected,
            "short": short, "empty": empty,
            "duplicates": [n for n, c in names.items() if c > 1],
            "medianWords": sorted(len(e["entry"].split()) for e in entries)[len(entries) // 2] if entries else 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF or UTF-8 text file")
    ap.add_argument("-o", "--out", help="write corpus.json here")
    ap.add_argument("--style", choices=sorted(STYLES), default="caps")
    ap.add_argument("--pattern", help="custom heading regex; group 1 is the name")
    ap.add_argument("--ocr", action="store_true", help="rasterise and OCR (scanned PDFs)")
    ap.add_argument("--lang", default="tur", help="tesseract language (default tur)")
    ap.add_argument("--expected", type=int, default=518)
    ap.add_argument("--dry-run", action="store_true", help="show detected headings, write nothing")
    ap.add_argument("--reading-order", action="store_true",
                    help="two-column books: take reading order instead of the page grid")
    ap.add_argument("--fix-glyphs", action="store_true",
                    help="repair a broken font map (! and \" -> i; # -> ğ/ş where decisive, rest reported)")
    a = ap.parse_args()

    src = Path(a.input)
    raw = pdf_to_text(src, a.ocr, a.lang, layout=not a.reading_order) if src.suffix.lower() == ".pdf" else src.read_text(encoding="utf-8")
    if a.fix_glyphs:
        raw, rep = recover_glyphs(raw)
        print(f"glyph recovery: {rep['i_substitutions']} '!'/'\"' -> i; "
              f"{rep['resolved']} '#' resolved, {rep['unresolved_words']} left")
        for k, v in rep["how"].items(): print(f"    {v:>5}  {k}")
        if rep["unresolved"]:
            outp = (Path(a.out).with_suffix(".unresolved.txt") if a.out
                    else Path(src).with_suffix(".unresolved.txt"))   # beside the source, not the repo root
            outp.write_text("\n".join(f"{n}\t{w}" for w, n in rep["unresolved"]), encoding="utf-8")
            print(f"    {len(rep['unresolved'])} distinct words need a human: {outp}")
    lines = clean(raw)
    entries = split_entries(lines, a.style, a.pattern)
    r = report(entries, a.expected)

    print(f"{r['count']} entries detected (expected {r['expected']}), median {r['medianWords']} words")
    if r["count"] != r["expected"]:
        print(f"! count differs from {r['expected']} — check the heading style before trusting the split")
    if r["duplicates"]: print(f"! duplicate names: {', '.join(r['duplicates'][:10])}")
    if r["empty"]:      print(f"! {len(r['empty'])} entries with no body: {', '.join(r['empty'][:10])}")
    if r["short"]:      print(f"! {len(r['short'])} entries under {MIN_WORDS} words, for manual check:")
    for n in r["short"][:20]: print("   ", n)

    if a.dry_run or not a.out:
        print("\nfirst 15 detected headings:")
        for e in entries[:15]: print(f"   {e['name']}  ({len(e['entry'].split())} words)")
        if not a.out: print("\n(no -o given; nothing written)")
        return
    Path(a.out).write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
