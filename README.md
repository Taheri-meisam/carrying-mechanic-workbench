# Carrying Mechanic workbench

A selection instrument and workbench for traditional games. It is used to decide, before any
adaptation exists, which documented games are supported for a screen or hybrid interface, and to
record what an adaptation keeps, changes and gives up.

A language model drafts a structured record for each game from its written description, with a
quotation for every judgement. A person decides every field. Eligibility is then computed by rule.
Nothing is scored and nothing is ranked.

| | |
|---|---|
| Software release | 1.1.0 |
| Instrument | v0.5 |
| Licence | MIT |
| Authors | Meisam Taheri, University of Inland Norway; Hatice Büber Kaya, Kırklareli University |
| Funding | COST Action CA22145 GameTable (Computational Techniques for Tabletop Games Heritage) |

## Contents

| Path | What it is |
|---|---|
| `backend/instrument.md` | the instrument, which is also the prompt the model receives |
| `backend/instrument_v0.4.md` | the instrument as the nine-game pilot ran it |
| `backend/instrument_method_notes.md` | instrument documentation that is not sent to the model |
| `backend/schema.py` | the record schema and the consistency checks |
| `backend/evaluate.py` | the eligibility rule, extraction accuracy, Cohen's kappa |
| `backend/model.py` | model providers: local (Ollama or vLLM), Anthropic, mock |
| `backend/app.py` | the service |
| `backend/coding_sheet.py` | offline coding sheets: export, import, validate |
| `frontend/index.html` | the workbench, one self-contained page |
| `analysis/eval.js` | the accuracy table, computed from an exported pool |
| `analysis/kappa_pilot.json` | the inter-rater figures of the pilot, unrounded |
| `examples/corpus_sample_synthetic.json` | 24 synthetic game descriptions, for trying the system |
| `tests/` | the test suite |
| `INVARIANTS.md` | the seven design rules and the eligibility rule |

## What is not here

The encyclopaedia entries, the coded records and the model drafts that quote them. The entries are
the text of the *Encyclopedia of Traditional Sports and Games*, in English translations supplied by
the Ministry of Youth and Sports of the Republic of Türkiye, whose authorisation covers research use
and not redistribution. The Turkish entries are public at https://encyclopedia.worldethnosport.org.
See `NOTICE.md`.

## Install and test

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m pytest

The tests make no model calls. Those that need the coded records are skipped, and say so.

## Run

    ollama serve &
    ollama pull qwen3.5:35b
    .venv/bin/uvicorn backend.app:app --port 8813

Then open http://localhost:8813. The default provider is a local model and needs no API key. It
expects an OpenAI-compatible endpoint; set `LOCAL_BASE_URL` to use one on another machine. Without
any model, `MODEL_PROVIDER=mock` gives schema-valid placeholder drafts.

To draft a whole corpus:

    .venv/bin/python -m backend.batch examples/corpus_sample_synthetic.json --tier default

A corpus is a JSON list of `{"name": "...", "entry": "..."}`. Each draft is stored as a pending
record, with the raw draft kept beside it.

## Instrument v0.5

Version 0.5 changes one thing. The model is no longer asked for a distinctiveness level. It returns
a search hypothesis and leaves the level empty. A separate, logged retrieval step then supplies
candidate counterparts, and one is kept only if it carries a resolvable identifier: a Ludii game
ID, a BoardGameGeek ID or a Wikipedia URL. A person reviews the log and sets the level. Records made
under v0.1 to v0.4 load unchanged.

## What is logged for every extraction

Provider, model, tier, instrument version, prompt hash, time, and the raw draft.

## Cite

See `CITATION.cff`. Each release is archived on Zenodo with a DOI.
