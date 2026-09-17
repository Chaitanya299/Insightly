# 5. Guard generated SQL in two independent layers

Date: 2026-09-18

## Status

Accepted

## Context

Text-to-SQL means model output reaches a SQL engine. That output is untrusted input: it
is shaped by the user's question and by the column names inside uploaded files, both of
which are attacker-controllable in any real deployment.

DuckDB is more capable than the threat model usually assumes. Beyond DDL, it reads the
filesystem: `SELECT * FROM read_csv('/etc/passwd')` is a valid `SELECT` and would sail
past a guard that only checks the leading keyword.

A regex over SQL is not a parser, and anyone claiming otherwise has not thought about
string literals and comments.

## Decision

Two layers that fail independently.

`engine.guard` strips comments, rejects multiple statements, requires a leading `SELECT`
or `WITH`, rejects DDL and file-reading functions by name, and wraps the query to bound
the result. This produces the readable error the user sees.

`engine.connect` opens DuckDB with `enable_external_access=false`. Uploads are registered
from pandas rather than read from disk by DuckDB, so nothing legitimate needs filesystem
access. This is the layer that actually enforces.

## Consequences

Bypassing the regex is not sufficient to reach the disk — the engine refuses. The regex
is free to be a friendly error message rather than a security boundary it cannot be.

Stripping comments before splitting statements has a pleasant side effect:
`SELECT 1 -- ; DROP TABLE sales` is correctly allowed, because the DROP is inside a
comment and was never a statement.

False positives are possible — a column legitimately named `copy` would be rejected. That
trade is deliberate: the guard errs toward refusing and saying so.
