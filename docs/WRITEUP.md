# Insightly: approach, decisions and what I'd build next

Live: https://insightly0.streamlit.app · Code: https://github.com/Chaitanya299/Insightly

## The problem, and the one decision everything follows from

The brief was an AI app that answers plain-English questions over several uploaded CSV
and Excel files. The obvious build pastes the rows into the prompt and asks the model.
That fails in three ways: the model does arithmetic in its head, a real file doesn't fit
in the context window, and joining two files becomes guesswork.

So Insightly splits the job. **The model writes SQL; DuckDB computes the answer.** The
model receives a *schema card*: column names, types, null rates and a few sample values.
It never sees the rows or its own results. On the same 20 questions over the same 100 rows,
pasting rows scored **10/20** at 6,040 tokens a question; Insightly scored **20/20** at
1,504. On the full 900-row sample the naive request is rejected as too large, while
Insightly's prompt stays 564 characters at 900 rows and at 1,000,000.

## What I built on top of the model (the delta)

- **Type recovery.** `$1,234.50`, `15%` and `(99)` become numbers on import.
- **Date-order detection.** `03/05/2024` parses as March *or* May without an error. The
  app infers day-first or month-first from the dates that can't be ambiguous.
- **Join discovery.** Links between files are found by value overlap (containment, not
  Jaccard, because a foreign key is many-to-one), so keys with different names still join.
- **Agreed definitions.** `config/metrics.toml` fixes what "revenue" means. The chat and
  the rule-built dashboard both use it, and it can be edited in the app. Each formula is
  test-run before it's saved.
- **Trust surface.** A "verified by DuckDB" stamp, editable SQL under every answer, a
  decline when the data can't answer, and one repair retry fed the database's exact error.

## How I know each part matters

I wrote questions whose answers I computed separately in pandas, then switched off one
component at a time:

| Switched off | Sample set (20) | Hard set (17) |
|---|---|---|
| Nothing (full system) | 20 | 17 |
| Type recovery / date detection | 16 / 16 | |
| Agreed definitions | 20 | 12 |
| Privacy mode, without join hints | 20 | 10 |

The sample data turned out too easy to test three components, so I built a hard set
designed to break them: decoy keys, codes like `EMEA`, and an April fiscal year. Building
it found two real bugs before any model ran. I report the components that made no
difference instead of hiding them.

## Safety

Generated SQL is untrusted input. A guard allows one read-only `SELECT`/`WITH` statement
and blocks file functions, and DuckDB runs with external access off, so a bypass still
can't read the disk. Privacy mode sends names and types only.

## What went wrong, and what it taught me

A stubbed test for "decline what you can't answer" passed while the real model answered
*headcount* with a count of customers. A stub tests the code around a model, never the
model, so there is now a live suite. Later my eval harness scored API quota errors as wrong
answers and reported the naive baseline at 0/20 when it hadn't run. It now records *not
run*. Both were the same bug: treating silence as a result.

## Limits and next steps

It's single-user with no auth, the free API allows about 5 questions a minute, and
definitions are a prompt instruction, not compiled SQL. Next: an eval set built **with the
customer on their data**, definitions compiled into SQL, a second pass that checks the SQL
answers the question asked, and persistence with auth.
