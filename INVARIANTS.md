# Design rules

Seven rules hold the system together. Each exists because breaking it would invalidate a
measurement the papers report, not because of a coding preference. Tests in `tests/` pin all
seven; `tests/test_pipeline.py` covers rules 5 and 6 directly.

1. **No total score, anywhere.** Eligibility is a non-compensatory rule (below). A weighted sum,
   a ranking or a "score" column would let a strong field compensate for a failed one, which is
   the decision the instrument is built to refuse.

2. **The model never sets `review.sections[*]`, `distinctiveness.verified` or, from instrument
   v0.5, `distinctiveness.level`.** Those are human-only. Eligibility requires all seven sections to have been reviewed by a person.

3. **The raw draft is stored unchanged** in `record.review.raw` and in the `extractions` table.
   Editing a record never overwrites it. Extraction-accuracy figures are computed against it, so
   a silent repair would inflate them.

4. **Every model judgement carries an `evidence` quotation** from the source entry. If the prompt
   changes, that requirement stays: rule 4 is what makes a human check of a field possible without
   re-reading the whole entry.

5. **The prompt lives only in `backend/instrument.md`.** The frontend holds an inline copy
   (`const INSTRUMENT`) used when the page runs without the backend. Keep the two identical, and
   bump `backend/instrument_version.txt` when either changes.

6. **The evaluation definitions must stay identical** in three places: `backend/evaluate.py`,
   `renderEval()` in `frontend/index.html`, and `analysis/eval.js`. Exact match per
   field, Cohen's kappa as implemented. Algorithm 1 in the SELECTION paper is a fourth copy in
   print; it has to agree too.

7. **Distinctiveness is a hypothesis until a person has reviewed the search log.** From instrument
   v0.5 a draft carries no level: `level` is null, beside `closest`, `separatingRule` and
   `searchQueries`. `/api/search` writes into `searchLog` and `counterpart`, and keeps a counterpart
   only if it has a resolvable identifier (a Ludii game ID, a BoardGameGeek ID or a Wikipedia URL).
   The level and the `verified` flag are set by a person. Records made under v0.1 to v0.4 carry a
   level assigned in the draft and a `searchNote` string; they load unchanged.

## The eligibility rule

Do not change this without a decision from the researchers. It is stated in prose in the
instrument, implemented in `backend/evaluate.py`, and printed as Algorithm 1 in the SELECTION
paper.

```
fails = []
if not (G1 and G2 and G3):   fails += gate
if MC  < 1:                  fails += MC
if not survives(dig.S):      fails += DIG(S)
if CTa < 1:                  fails += CT-a

eligible       = fails empty and all 7 sections reviewed
hybrid         = not survives(S) and survives(M)
educationallyOk = eligible and LOM >= 1        # reported separately, see below

survives(d) = ord(d) >= 2                                    # Kept or Transformed
              or (d == Substituted and d.substitutionTest.pass)
```

Two points of the rule are easy to get wrong:

- **LOM is not part of core eligibility.** LOM is the relation between a game and a *stated*
  learning objective, so it cannot gate a general adaptation decision. Where an objective is
  supplied it applies as an additional pedagogical filter and is reported separately, as
  `educationalOk` / `educationalFails`.
- **L (co-located support) is neither a gate nor a hybrid trigger.** Under a co-located setup the
  physical game is by definition unchanged, so L cannot show that anything survives digitisation.
  It is recorded descriptively: a proposed setup plus a feasibility judgement.

Cohen's kappa is **undefined**, not 1.0, when expected agreement is 1 — that is, when every
observation falls in one category. `kappa()` returns `None` there, and the papers print a dash.
