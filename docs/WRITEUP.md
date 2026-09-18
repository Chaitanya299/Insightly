# Approach, decisions, and what I'd build next

## The decision everything else follows from

The obvious build is: read the files into pandas, stuff rows into the prompt, let the
model answer. It demos well on 50 rows and fails on everything real — it hallucinates
figures, blows the context window, and cannot join two files reliably.

So: **the model writes SQL, DuckDB computes the numbers, and the model never sees the
rows.** It gets a schema card — names, types, null rates, distinct counts, three sample
values per column — and returns a query.

Three things fall out of that, which is why it was worth deciding first:

- **Correctness becomes structural, not statistical.** A number on screen is the output
  of a query, so it can't be invented. The failure mode moves from "wrong number stated
  confidently" to "wrong question answered" — visible, because the SQL is shown.
- **Scale stops mattering.** 900 rows and 9 million rows produce the same prompt.
- **Cross-file analysis is just a join**, not a prompt-engineering problem.

The cost is that questions SQL can't express (fuzzy matching, "why did this happen") are
out of reach. For an analytical Q&A tool that's the right trade.

## What I built on top of the model

A thin LLM wrapper would have failed the acceptance criteria on real files. Four pieces
of engineering carry it, in the order they mattered:

**Type recovery.** Real CSVs carry money as `"$1,234.50"` and dates in three formats in
one column. Sum a text column and you get a concatenation or an error. The app coerces
numbers and dates on import, using a 90%-parse threshold so a few junk rows don't veto a
column, and shows every change it made.

The date case is the one I'd point a panel at. In the sample file `03/05/2024` parses as
either 3 May or 5 March, and **both readings succeed on 100% of rows** — there is no error
to catch. A naive parser picks one silently and every monthly trend is wrong by a number
nobody notices. The app decides orientation from the rows that can't be ambiguous (a
leading component > 12 must be a day), applies it only to the short ambiguous dates so
ISO rows aren't corrupted by the same flag, and reports what it concluded. Where a column
is genuinely ambiguous, it says so rather than choosing.

**Join discovery.** Before any question, the app scores cross-file column pairs by
containment — intersection over the *smaller* side. Jaccard is the instinctive choice and
it's wrong here: a foreign key is many-to-one, so 900 orders against 120 customers scores
near zero on Jaccard and 1.0 on containment. Detected keys go into the prompt as hints.
Without them the model guesses key names and the join silently returns zero rows — the
worst failure mode available, because an empty result looks like a real answer.

**Guardrails.** Text-to-SQL means model output reaches a SQL engine, so it's untrusted
input. Single statement, `SELECT`/`WITH` only, DDL and file-reading functions rejected,
results bounded. The regex is the friendly error; the actual enforcement is the DuckDB
connection running with `enable_external_access=false`, so `read_csv('/etc/passwd')` fails
at the engine. Defence in depth, because a regex over SQL is not a parser.

**Surviving bad input.** Every file is loaded in isolation. The failure that ends a live
demo is somebody dropping in their own truncated export and getting a traceback that takes
the other four files with it, so a bad file produces a plain-English note naming that file
and the rest still load. pandas exceptions get translated on the way out: `ParserError`
becomes "malformed CSV (unclosed quote, or rows with differing column counts)".

**Self-repair.** On a query error the engine feeds DuckDB's own message back to the model
for exactly one retry, and labels the answer as repaired. One retry, not a loop — a second
failure is a signal to show the human the error, not to keep spending tokens.

And the small one that matters most in a live demo: the **SQL is editable and re-runnable**
under every answer. It turns the app from something you trust into something you check.

## The number that makes the case

A million rows of the same messy shape: about 9s to ingest, clean and profile; 0.01s per query;
and a prompt of **561 characters** — the same 561 characters the 900-row sample produces.
Putting those rows in a prompt instead would be roughly 13.6 million tokens, about
97,000x more, and past every context window at any price. `tests/benchmark.py` reproduces it.

I mention it because it settles the architecture argument with a measurement rather than a
preference. Rows-in-the-prompt isn't a worse version of this design; for a file like that
it is not a version that runs.

## What I'd build next

1. **A semantic layer.** "Revenue" means net of refunds at one company and gross at
   another; the sample data has a `Refunded` status the model ignores unless asked. A small
   YAML of metric definitions injected into the prompt is the difference between a demo and
   something a finance team can use. This is the highest-value next thing by a distance.
2. **A verification pass.** A second cheap model call that reads the question and the
   generated SQL and flags disagreement — catching the remaining failure mode, right SQL
   for the wrong question. Surface it as a confidence note, not a blocker.
3. **Persistence.** Saved questions, pinned answers, a lightweight dashboard. Every user
   who likes an answer immediately wants it again tomorrow.
4. **Warehouse connectors.** File upload is the take-home framing; the same schema-card →
   SQL → verify loop points at Postgres, Snowflake or BigQuery unchanged, which is where
   the questions actually live.
5. **Cost and caching.** Schema cards are stable, so prompt caching plus a
   question→SQL cache would cut both latency and spend before scale makes it urgent.

## Where it's weak

Single-session and in-memory, no auth. Very wide tables (200+ columns) would need schema
cards trimmed to the columns a question plausibly touches. Questions requiring window
functions or statistical tests work only as well as the model's SQL. And the app inherits
whatever the source files get wrong — it surfaces its assumptions clearly, but it cannot
tell you the underlying data is bad.
