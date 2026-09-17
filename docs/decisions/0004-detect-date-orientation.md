# 4. Detect date orientation instead of trusting a parser flag

Date: 2026-09-18

## Status

Accepted

## Context

Real exports mix date formats inside a single column. The sample file carries
`2024-11-02`, `20/05/2024` and `Jun 03, 2025` in `Order Date`.

The dangerous value is `03/05/2024`. It is 3 May under day-first and 5 March under
month-first, and **both readings succeed on 100% of rows**. There is no exception to
catch and no malformed value to report. A parser picks one, and every monthly trend built
on that column is quietly wrong by an amount nobody notices.

Pandas offers a `dayfirst` flag, but it applies to the whole column. Setting
`dayfirst=True` to fix `20/05/2024` also reinterprets `2024-11-02` as 11 February. One
global flag cannot describe a column holding both.

## Decision

Infer the orientation from the rows that *cannot* be ambiguous: if any leading component
in a short slash-or-dash date exceeds 12, it must be a day, so the column is day-first.
Symmetrically for the second component.

Parse in two passes. Short ambiguous dates get the inferred orientation; everything else
is parsed on its own terms, so ISO values are never touched by the flag. Report the
conclusion in the sidebar as a cleaning note.

Where a column is genuinely ambiguous — every date could go either way — say so
("ambiguous D/M vs M/D dates, assumed month-first") rather than choosing silently.

## Consequences

The sample file's dates parse correctly and the app states that it detected day-first
ordering, so the reading is visible rather than assumed.

The inference needs at least one unambiguous row. A column where every day is ≤ 12 stays
genuinely ambiguous, and the honest output there is the warning, not a guess.

This is the most easily-missed correctness bug in the project and the reason type
recovery reports everything it changed.
