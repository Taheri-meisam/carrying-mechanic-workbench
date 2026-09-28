# Instrument method notes

Documentation that belongs to the instrument but is NOT part of the prompt. `instrument.md` is sent
to the model verbatim (and hashed into `promptHash`); nothing in this file is.

## Distinctiveness retrieval (instrument v0.5)

Distinctiveness retrieval (separate, logged step; instrument v0.5). A web search supplies candidate sources for the search hypothesis; the model selects among them by index only. A candidate counterpart is retained only if it carries a resolvable identifier (a Ludii game ID, a BoardGameGeek ID or a Wikipedia URL) and is set to null otherwise. The search log records the search date, languages, sources, query terms, candidates inspected, closest match and any separating core rule. The coder reviews the log, confirms or overrides the retained counterpart, assigns D0, D1 or D2 and sets "verified". The coder is never asked to run the search. Retrieval runs only after the evaluation sample is frozen, and its outcomes are not used for sample selection.
