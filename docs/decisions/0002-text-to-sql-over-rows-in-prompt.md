# 2. Answer with generated SQL, not with rows in the prompt

Date: 2026-09-18

## Status

Accepted

## Context

The obvious way to build natural-language Q&A over an uploaded spreadsheet is to read it
into pandas, put the rows in the prompt, and let the model answer. It demos well on fifty
rows.

It has three failures that are not fixable by prompting. The model states figures it
computed in its head, and a wrong number arrives with the same confidence as a right one.
The context window caps the file size. And joining two uploaded files becomes a prompt
engineering problem rather than a query.

## Decision

The model writes DuckDB SQL. DuckDB computes the numbers. The model receives a *schema
card* — table and column names, types, null rates, distinct counts, three sample values
per column — and never the rows. Query results go to the user, never back to the model.

The three sample values are real data and do reach the model provider. That is a
deliberate trade: they are what let the model write `status = 'Completed'` instead of
guessing the spelling. For confidential data they are the thing to turn off.

## Consequences

Correctness becomes structural rather than statistical. A displayed number is the output
of a query, so it cannot be invented. The failure mode moves from "wrong number, stated
confidently" to "right SQL for the wrong question" — which is visible, because the SQL is
shown under every answer.

Scale stops mattering. Measured at 1,000,000 rows: the schema card is 564 characters,
the same 564 characters the 900-row sample produces. The equivalent rows would be roughly
13.6M tokens. The row count changes the answer, never the prompt.

Cross-file analysis is a join, and therefore mostly a question of finding the keys
(see [ADR 0003](0003-containment-not-jaccard-for-join-keys.md)).

The cost: questions SQL cannot express are out of reach — fuzzy matching, "why did this
happen", anything needing a model to read the data itself.

A subtler cost surfaced in live testing. Asked for "total revenue", the model filters
`status = 'Completed'`, excluding refunds. That is a defensible reading and it states the
assumption, but nothing pins the definition down. A semantic layer of metric definitions
is the missing piece, and the first thing to build next.
