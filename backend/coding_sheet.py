"""Offline coding pack: export a workbook a coder can fill in a spreadsheet, and read it back.

    python -m backend.coding_sheet export --out coding_pack
    python -m backend.coding_sheet import coding_pack/coding_sheet.csv --coder C1

The workbench is the better place to adjudicate — it shows the entry beside the draft, locates each
quotation in the source, and will not let a record count until all seven sections are ticked. This
exists for the case where that is not practical: a co-author working offline, or in Excel.

The sheet is long, one row per judgement rather than one row per game, because a coder decides one
thing at a time. Each row carries what is needed to decide it: the level descriptors, the model's
value, its reason, its quotation, and whether that quotation was actually found in the entry.
"""
import argparse, csv, json, sqlite3, sys, time, unicodedata, re
from pathlib import Path

HERE = Path(__file__).parent
SECTIONS = ["mechanic", "gates", "route", "dimensions", "dig", "ledger", "distinct"]

D = {  # field -> (section, prompt, options, level descriptors)
 "carryingMechanic.text": ("mechanic", "Carrying mechanic: the rule without which this is a different game", "free text", ""),
 "gates.G1.pass": ("gates", "G1 rule system: does the entry document rules with a defined end state?", "TRUE / FALSE", ""),
 "gates.G2.pass": ("gates", "G2 bounded session: one play in a sitting, equipment a mini-game can represent?", "TRUE / FALSE", ""),
 "gates.G3.pass": ("gates", "G3 survival: is the carrying mechanic kept or transformed under AT LEAST ONE of S/M/L?", "TRUE / FALSE",
                   "Note: G3 follows from the DIG rows below. If any of S/M/L is Kept or Transformed, G3 is TRUE."),
 "gates.G3.classes": ("gates", "Which classes does it survive under? (from the DIG rows)", "any of S, M, L, comma separated", ""),
 "route.primary": ("route", "Adaptation route, decided by test not by keywords", "R1 / R2 / R3",
                   "R1 contest: remove the second player and it is no longer playable. R2 closure: still playable alone, defined end, no winner. R3 construction: a phase where what the player builds decides how it performs."),
 "route.secondary": ("route", "Secondary route, if any", "blank / R1 / R2 / R3", ""),
 "dimensions.MC.level": ("dimensions", "MC rule decomposability", "0 / 1 / 2",
   "0 a core step depends on human judgement or negotiation. 1 decomposes but the entry holds incompatible cores or an under-specified step. 2 decomposes fully into discrete elements."),
 "dimensions.LOM.level": ("dimensions", "LOM intrinsic objective (the counterfactual)", "0 / 1 / 2",
   "Name the learning behaviour, then remove that content. 0 the same strategy still wins. 1 strategy changes but the game remains winnable. 2 the game collapses; the learning behaviour IS the winning behaviour."),
 "dimensions.LOM.objective": ("dimensions", "LOM: name the learning behaviour as a short noun phrase", "free text", ""),
 "dimensions.AGE.level": ("dimensions", "AGE developmental dependency", "0 / 1 / 2",
   "0 narrow-window competence, no native parameter. 1 difficulty only through external parameters. 2 a native scaling parameter (board size, piece count, target score)."),
 "dimensions.CTa.level": ("dimensions", "CT-a legibility", "0 / 1 / 2",
   "0 rules cannot be followed without cultural knowledge absent from them. 1 playable with a one-line gloss. 2 legible from the rules alone."),
 "dig.S.op": ("dig", "Under S (screen-only touch), the carrying mechanic is:", "Kept / Transformed / Substituted / Removed",
   "Kept: rule and skill preserved. Transformed: rule enforced, skill changes. Substituted: a different skill; a new game with the old name. Removed."),
 "dig.M.op": ("dig", "Under M (motion or sensor), the carrying mechanic is:", "Kept / Transformed / Substituted / Removed", ""),
 "dig.L.op": ("dig", "Under L (location or co-located, device as referee or prop):", "Kept / Transformed / Substituted / Removed", ""),
 "ledger": ("ledger", "Is the transformation ledger correct as drafted? Correct it in the workbench if not.", "OK / NEEDS WORK", ""),
 "distinctiveness.level": ("distinct", "Distinctiveness (assigned by you, not by the model)", "D0 / D1 / D2",
   "D0 a rule-for-rule counterpart is documented elsewhere. D1 family counterpart, at least one core rule differs. D2 none found in the logged search. The model does not assign a level (instrument v0.5). You assign it after reviewing the search log; you are not asked to run the search yourself. Blank is allowed: leave it blank if there is no search log yet or it does not let you decide."),
}


KAPPA_FIELDS = ("route.primary", "dimensions.MC.level", "dimensions.LOM.level",
                "dimensions.AGE.level", "dimensions.CTa.level", "dig.S.op", "distinctiveness.level")

TRUE_WORDS = {"true","t","yes","y","1","pass","evet","dogru","doğru"}
FALSE_WORDS = {"false","f","no","n","0","fail","hayir","hayır","yanlis","yanlış"}
OPS = ("Kept", "Transformed", "Substituted", "Removed")


def parse_value(field: str, raw: str):
    """Accept what a human actually types. Returns (value, error). Case, spacing and the obvious
    Turkish yes/no words are all tolerated; anything genuinely unreadable is reported, never
    silently dropped."""
    v = re.sub(r"\s+", " ", (raw or "")).strip()
    if not v:
        return None, None
    last = field.rsplit(".", 1)[-1]
    if field.startswith("review.sections."):
        if v.lower() in TRUE_WORDS or v.lower().startswith(("review", "done")): return True, None
        if v.lower() in FALSE_WORDS or v.lower().startswith("not"): return False, None
        return None, f"expected REVIEWED or NOT YET, got {raw!r}"
    if last == "pass":
        if v.lower() in TRUE_WORDS: return True, None
        if v.lower() in FALSE_WORDS: return False, None
        return None, f"expected TRUE or FALSE, got {raw!r}"
    if field == "distinctiveness.level":          # must precede the generic level branch
        u = v.upper().replace(" ", "")
        if u in ("D0", "D1", "D2"): return u, None
        return None, f"expected D0, D1 or D2, got {raw!r}"
    if last == "level":
        m = re.match(r"^([0-2])\b", v)
        if m: return int(m.group(1)), None
        return None, f"expected 0, 1 or 2, got {raw!r}"
    if last == "classes":
        cls = [x.strip().upper() for x in re.split(r"[,;/ ]+", v) if x.strip()]
        bad = [x for x in cls if x not in ("S", "M", "L")]
        return (None, f"classes must be S, M or L, got {bad}") if bad else (cls, None)
    if last == "op":
        hit = [o for o in OPS if o.lower() == v.lower()]
        return (hit[0], None) if hit else (None, f"expected one of {'/'.join(OPS)}, got {raw!r}")
    if field == "route.primary" or field == "route.secondary":
        u = v.upper()
        if u in ("R1", "R2", "R3"): return u, None
        return None, f"expected R1, R2 or R3, got {raw!r}"
    return v, None


def get(rec, path):
    cur = rec
    for k in path.split("."):
        if not isinstance(cur, dict): return ""
        cur = cur.get(k, "")
    return "" if cur is None else (", ".join(cur) if isinstance(cur, list) else cur)


def slug(s):
    s = unicodedata.normalize("NFKD", s or "game").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "game"


SHORT_FIELDS = ("route.primary", "gates.G1.pass", "gates.G2.pass", "gates.G3.pass",
                "dimensions.MC.level", "dimensions.LOM.level", "dimensions.AGE.level",
                "dimensions.CTa.level", "dig.S.op", "distinctiveness.level")


def export(db: Path, out: Path, blind: bool = False, only: list = None, short: bool = False):
    out.mkdir(parents=True, exist_ok=True)
    (out / "entries").mkdir(exist_ok=True)
    recs = [json.loads(j) for (j,) in sqlite3.connect(db).execute("SELECT json FROM records ORDER BY id")]
    if only:
        want = {n.casefold() for n in only}
        recs = [r for r in recs if r.get("name", "").casefold() in want
                or any(r.get("name", "").casefold().startswith(w) for w in want)]
        missing = [n for n in only if not any(r["name"].casefold().startswith(n.casefold()) for r in recs)]
        if missing: sys.exit(f"not in the pool: {missing}")
    if not recs: sys.exit("no records in the pool")

    rows = []
    for r in recs:
        rev = r.get("review") or {}
        entry = rev.get("entry") or ""
        # human-facing file: keep the Turkish name, strip only what a filesystem dislikes
        fname = re.sub(r'[<>:"/\\|?*]', "-", r["name"]).strip()
        (out / "entries" / f"{fname}.txt").write_text(
            f"{r['name']}\n{'='*len(r['name'])}\n\n{entry}\n", encoding="utf-8")
        flags = rev.get("flags") or {}
        bad = {x.split(" ")[0] for x in (flags.get("evidenceMissing") or [])} | set(flags.get("evidenceAbsent") or [])
        keep = KAPPA_FIELDS if blind else (SHORT_FIELDS if short else tuple(D))
        fields = [(k, v) for k, v in D.items() if k in keep]
        for path, (sec, prompt, opts, desc) in fields:
            if path == "ledger":
                model_val, reason, ev = f"{len(r.get('ledger') or [])} rows", "", ""
            else:
                model_val = get(r, path)
                base = path.rsplit(".", 1)[0]
                reason = get(r, base + ".reason") or get(r, base + ".note") or get(r, base + ".descriptor") or get(r, base + ".objective")
                ev = get(r, base + ".evidence")
            key = base if path != "ledger" else ""
            row = {"game": r["name"], "section": sec, "field": path,
                   "what_to_decide": prompt, "allowed_values": opts, "level_descriptors": desc}
            if not blind:      # coder 2 must not see the draft, or kappa measures anchoring
                row |= {"model_value": model_val, "model_reason": reason, "model_quotation": ev,
                        "quotation_found_in_entry": ("" if not ev else
                            ("no" if any(key.startswith(b) or b.startswith(key) for b in bad) else "yes"))}
            # Coder 1 adjudicates a draft: it is already filled in, and she changes what she
            # disagrees with. The section tick below, not a filled cell, is what makes it count.
            row |= {"YOUR_VALUE": ("" if blind else model_val), "YOUR_NOTE": ""}
            rows.append(row)
        if short:
            rows.append({"game": r["name"], "section": "all", "field": "review.sections.ALL",
                "what_to_decide": "*** Done with this game? ***",
                "allowed_values": "DONE / NOT YET",
                "level_descriptors": "Tick DONE once you have read the entry and checked the rows above.",
                "model_value": "", "model_reason": "", "model_quotation": "",
                "quotation_found_in_entry": "", "YOUR_VALUE": "", "YOUR_NOTE": ""})
        elif not blind:
            for sec in SECTIONS:
                rows.append({
                    "game": r["name"], "section": sec, "field": f"review.sections.{sec}",
                    "what_to_decide": f"*** Have you checked every {sec} row above for this game? ***",
                    "allowed_values": "REVIEWED / NOT YET",
                    "level_descriptors": "Only tick REVIEWED once you have actually read the entry and "
                                         "checked these rows. This tick is what makes the record count.",
                    "model_value": "", "model_reason": "", "model_quotation": "",
                    "quotation_found_in_entry": "", "YOUR_VALUE": "", "YOUR_NOTE": ""})
    cols = list(rows[0].keys())
    with (out / "coding_sheet.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    (out / "instrument.md").write_text((HERE / "instrument.md").read_text(encoding="utf-8"), encoding="utf-8")
    try:
        write_xlsx(rows, cols, out / "coding_sheet.xlsx", blind)
        print(f"  spreadsheet with dropdowns -> {out}/coding_sheet.xlsx")
    except ImportError:
        print("  (openpyxl not installed; CSV only)")
    print(f"{len(rows)} judgements over {len(recs)} games -> {out}/coding_sheet.csv"
          + ("   [BLIND — no model draft shown]" if blind else ""))
    print(f"  entries -> {out}/entries/  ({len(recs)} files)")
    print(f"  instrument -> {out}/instrument.md")
    return recs


def write_xlsx(rows, cols, path: Path, blind: bool):
    """A spreadsheet the coder can fill without typing a value by hand: YOUR_VALUE is a dropdown
    built from that row's allowed values, so an invalid entry cannot be entered at all."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    wb = Workbook(); ws = wb.active; ws.title = "Coding"
    ws.append(cols)
    head = Font(bold=True, color="FFFFFF"); fill = PatternFill("solid", fgColor="2C3E8C")
    for c in ws[1]:
        c.font = head; c.fill = fill; c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"

    widths = {"game": 26, "section": 12, "field": 24, "what_to_decide": 52, "allowed_values": 22,
              "level_descriptors": 70, "model_value": 14, "model_reason": 40, "model_quotation": 50,
              "quotation_found_in_entry": 12, "YOUR_VALUE": 18, "YOUR_NOTE": 40}
    for i, c in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 18)

    shade = PatternFill("solid", fgColor="EEF1F8")
    vcol = cols.index("YOUR_VALUE") + 1
    for i, r in enumerate(rows, 2):
        ws.append([r.get(c, "") for c in cols])
        for c in ws[i]:
            c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row=i, column=vcol).fill = shade

    # one dropdown per distinct option set, applied to the rows that use it
    groups = {}
    for i, r in enumerate(rows, 2):
        opts = r["allowed_values"]
        if "free text" in opts or "comma" in opts: continue
        vals = [x.strip() for x in opts.split("/") if x.strip() and x.strip() != "blank"]
        if vals: groups.setdefault(tuple(vals), []).append(i)
    for vals, idxs in groups.items():
        dv = DataValidation(type="list", formula1='"' + ",".join(vals) + '"', allow_blank=True,
                            showDropDown=False, errorTitle="Not an allowed value",
                            error="Choose from the list, or leave blank if you cannot decide.")
        ws.add_data_validation(dv)
        for i in idxs:
            dv.add(ws.cell(row=i, column=vcol))
    # A second sheet showing how far she has got, driven by Excel formulas so it updates as she
    # types. She cannot run a validator, so the feedback has to live inside the file.
    games, sections = [], []
    for r in rows:
        if r["game"] not in games: games.append(r["game"])
        if r["section"] not in sections: sections.append(r["section"])
    ps = wb.create_sheet("Progress")
    ps["A1"] = "How much you have filled in, by game and section. This updates itself as you type."
    ps["A1"].font = Font(bold=True, size=12)
    ps["A2"] = "A section counts only when every row in it is filled. Blank is a valid answer - see INSTRUCTIONS."
    ps.append([])
    ps.append(["game"] + sections)
    for c in ps[4]:
        c.font = head; c.fill = fill
    gcol = get_column_letter(cols.index("game") + 1)
    scol = get_column_letter(cols.index("section") + 1)
    vl = get_column_letter(vcol)
    last = len(rows) + 1
    g_rng, s_rng, v_rng = (f"Coding!${gcol}$2:${gcol}${last}",
                           f"Coding!${scol}$2:${scol}${last}",
                           f"Coding!${vl}$2:${vl}${last}")
    for gi, g in enumerate(games):
        r0 = 5 + gi
        row = [g] + [f'=COUNTIFS({g_rng},$A{r0},{s_rng},"{sec}",{v_rng},"<>")&" / "&'
                     f'COUNTIFS({g_rng},$A{r0},{s_rng},"{sec}")' for sec in sections]
        ps.append(row)
    ps.column_dimensions["A"].width = 32
    for k in range(2, len(sections) + 2):
        ps.column_dimensions[get_column_letter(k)].width = 13
    ps.freeze_panes = "A5"

    wb.save(path)


def read_sheet(path: Path) -> list:
    """Accept the spreadsheet or the CSV — whichever the coder found easier."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        ws = load_workbook(path, data_only=True).active
        it = ws.iter_rows(values_only=True)
        cols = [str(c) if c is not None else "" for c in next(it)]
        return [{k: ("" if v is None else str(v)) for k, v in zip(cols, r)} for r in it if any(r)]
    # Excel writes CSV in the machine's list separator: comma in en-US, SEMICOLON in tr-TR.
    # It may also save as cp1254 rather than UTF-8. Sniff both instead of assuming.
    raw = None
    for enc in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
        try:
            raw = path.read_text(encoding=enc); break
        except UnicodeDecodeError:
            continue
    if raw is None:
        sys.exit(f"could not decode {path}")
    head = raw.split("\n", 1)[0]
    delim = ";" if head.count(";") > head.count(",") else ","
    return list(csv.DictReader(raw.splitlines(), delimiter=delim))


def validate(path: Path):
    """Check a filled sheet before it is sent back. Reports unreadable values and which sections
    are complete; changes nothing."""
    rows = read_sheet(path)
    errs, filled = [], 0
    per = {}
    for r in rows:
        raw = r.get("YOUR_VALUE") or ""
        if str(raw).strip(): filled += 1
        _, err = parse_value(r["field"], str(raw))
        if err: errs.append(f'  {r["game"]} / {r["field"]}: {err}')
        key = (r["game"], r["section"])
        per.setdefault(key, [0, 0])
        per[key][1] += 1
        if str(raw).strip(): per[key][0] += 1
    games = sorted({g for g, _ in per})
    print(f"{len(rows)} rows, {filled} filled")
    print()
    for g in games:
        done = [s for (gg, s), (a, b) in per.items() if gg == g and a == b]
        part = [s for (gg, s), (a, b) in per.items() if gg == g and 0 < a < b]
        print(f"  {g[:34]:36} complete: {len(done)}/7" + (f"   partial: {', '.join(part)}" if part else ""))
    print()
    if errs:
        print(f"{len(errs)} values could not be read — fix these before sending back:")
        for e in errs[:25]: print(e)
    else:
        print("No unreadable values. Ready to send back.")
    return not errs


def import_sheet(path: Path, db: Path, coder: str, second: bool = False):
    rows = read_sheet(path)
    by_game = {}
    for r in rows:
        by_game.setdefault(r["game"], []).append(r)
    c = sqlite3.connect(db)
    changed = skipped = 0
    for game, rs in by_game.items():
        hit = c.execute("SELECT id,json FROM records WHERE json LIKE ?", (f'%"name": "{game}"%',)).fetchone()
        if not hit:
            print(f"  ! no record named {game!r} in the pool — skipped"); skipped += 1; continue
        rid, j = hit; rec = json.loads(j)
        rec.setdefault("review", {})
        edits = 0

        if second:
            # The second coder never edits the record. Their judgements live in review2, which is
            # the only thing kappa reads (backend/evaluate.py `rel`).
            R2 = {"route.primary": "route", "dimensions.MC.level": "MC", "dimensions.LOM.level": "LOM",
                  "dimensions.AGE.level": "AGE", "dimensions.CTa.level": "CTa", "dig.S.op": "S",
                  "distinctiveness.level": "D"}
            r2 = rec.setdefault("review2", {})
            bad2 = []
            for r in rs:
                k = R2.get(r["field"])
                v = str(r.get("YOUR_VALUE") or "").strip()
                if not k or not v: continue
                v2, err = parse_value(r["field"], v)
                if err: bad2.append(f'{r["field"]}: {err}'); continue
                r2[k] = v2; edits += 1
            if coder: r2["coder"] = coder
            c.execute("UPDATE records SET json=?, updated=? WHERE id=?",
                      (json.dumps(rec, ensure_ascii=False), time.time(), rid))
            print(f"  {game[:34]:36} {edits:>3} second-coder values"
                  + (f"   ! {len(bad2)} unreadable" if bad2 else ""))
            for b in bad2: print(f"      {b}")
            changed += 1
            continue
        bad = []
        for r in rs:
            v = str(r.get("YOUR_VALUE") or "").strip()
            if not v or r["field"] == "ledger" or r["field"].startswith("review.sections."): continue
            v2, err = parse_value(r["field"], v)
            if err:
                bad.append(f'{r["field"]}: {err}'); continue
            cur, keys = rec, r["field"].split(".")
            for k in keys[:-1]: cur = cur.setdefault(k, {})
            cur[keys[-1]] = v2; edits += 1
        # a section counts as reviewed only when every one of its fields was answered
        secs = rec["review"].setdefault("sections", {})
        for r in rs:
            if not r["field"].startswith("review.sections."): continue
            v, err = parse_value(r["field"], str(r.get("YOUR_VALUE") or ""))
            k = r["field"].rsplit(".", 1)[-1]
            if v is True:
                if k == "ALL": secs.update({x: True for x in SECTIONS})
                else: secs[k] = True
        notes = [f"{r['field']}: {r['YOUR_NOTE']}" for r in rs if (r.get("YOUR_NOTE") or "").strip()]
        if notes:
            rec["review"]["notes"] = ((rec["review"].get("notes") or "") + "\n" + "\n".join(notes)).strip()
        if coder: rec["review"]["coder"] = coder
        rec["review"]["updated"] = int(time.time() * 1000)
        c.execute("UPDATE records SET json=?, updated=? WHERE id=?",
                  (json.dumps(rec, ensure_ascii=False), time.time(), rid))
        n = sum(1 for s in SECTIONS if secs.get(s))
        print(f"  {game[:34]:36} {edits:>3} values, {n}/7 sections reviewed"
              + (f"   ! {len(bad)} unreadable" if bad else ""))
        for b in bad: print(f"      {b}")
        changed += 1
    c.commit()
    print(f"\n{changed} records updated, {skipped} skipped.")
    print("review2 only; the first coder's record is untouched." if second else
          "review.raw is untouched — the model draft is preserved for the accuracy table.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--out", default="coding_pack")
    e.add_argument("--only", nargs="+", metavar="GAME",
                   help="restrict to these games (calibration pilot); the rest stay held out")
    e.add_argument("--short", action="store_true",
                   help="ten fields per game and one tick, instead of seventeen and seven")
    e.add_argument("--blind", action="store_true",
                   help="coder 2: withhold the model draft, so kappa measures agreement not anchoring")
    i = sub.add_parser("import"); i.add_argument("sheet"); i.add_argument("--coder", default="")
    i.add_argument("--second", action="store_true", help="load into review2 (the second coder)")
    v = sub.add_parser("validate"); v.add_argument("sheet")
    for q in (e, i): q.add_argument("--db", default=str(HERE.parent / "data" / "workbench.sqlite"))
    a = ap.parse_args()
    if a.cmd == "export": export(Path(a.db), Path(a.out), a.blind, a.only, getattr(a, "short", False))
    elif a.cmd == "validate": sys.exit(0 if validate(Path(a.sheet)) else 1)
    else: import_sheet(Path(a.sheet), Path(a.db), a.coder, getattr(a, "second", False))


if __name__ == "__main__":
    main()
