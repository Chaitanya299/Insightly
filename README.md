# Ask Your Data

Upload CSV/Excel files, ask analytical questions in plain English, get answers you can verify.

The model writes SQL. **DuckDB computes the numbers.** The model sees the schema and a
few sample values, never the rows, and results are never sent back to it — so it cannot
invent a figure, and the query behind every answer is shown, editable, and re-runnable.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add a free key from https://console.groq.com/keys
streamlit run src/app.py
```

Open http://localhost:8501 and click **Load sample files** in the sidebar, or upload your own.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Model | **gpt-oss-120b** (Apache-2.0 open weights) via Groq | Strong SQL generation, free tier, ~1s latency. Swap with `GROQ_MODEL` (`qwen/qwen3.8-27b` also works), or point at Ollama — the model only ever returns JSON. |
| Compute | **DuckDB** in-memory | Real SQL over the uploaded files. Cross-file questions are joins, not prompt engineering. Handles millions of rows. |
| Ingestion | **pandas** + openpyxl | Type recovery from messy real-world files. |
| UI | **Streamlit** + Plotly | Upload, chat and charts without spending the budget on plumbing. |

~870 lines across three source files.

## How it works

```
upload ─► pandas: clean headers, recover types ─► DuckDB tables (one per file/sheet)
                                                        │
                                            schema card + detected join keys
                                                        │
question ───────────────────────────────────────────────┴──► gpt-oss-120b
                                                        │
                                          {sql, chart, explanation}
                                                        │
                                   guard ─► execute ─► error? one repair retry
                                                        │
                                     DataFrame ─► chart picker ─► answer + editable SQL
```

The model receives a **schema card** — table names, column names, types, null rates,
distinct counts, three sample values — and never the rows themselves. (Those sample values
are real data and do reach the model provider; for confidential data that is the part to
switch off.) This is what makes
the app work on a 5-million-row file and what makes hallucinated figures structurally
impossible: every number is the output of a query you can read.

## What this does beyond calling an LLM

**1. Verifiable answers.** Text-to-SQL instead of rows-in-the-prompt. The SQL appears
under every answer in an editable box with a Re-run button — when the model misreads a
question, you see exactly where and fix it in place instead of arguing with a chatbot.

**2. Messy-data handling.** Real CSVs are where naive versions break. On import the app
normalises headers (`Order Date ` → `order_date`), recovers numbers from `$1,234.50`,
`15%` and `(99)` → `-99`, and parses dates. Everything it changed is listed in the sidebar.

The date handling is the one worth calling out. `data/samples/sales.csv` mixes
`2024-11-02`, `20/05/2024` and `Jun 03, 2025` in one column. `03/05/2024` parses as either
3 May or 5 March and **both readings succeed on 100% of rows** — a naive parser picks one
silently and every monthly trend is quietly wrong. The app infers orientation from the
rows that *can't* be ambiguous (any leading value > 12 means the column is day-first),
applies it only to the short ambiguous dates so ISO rows aren't corrupted, and says what
it concluded. If a column is genuinely ambiguous it says that too.

**3. Cross-file join discovery.** Before any question is asked, the app scores column
pairs across files by **containment** — overlap over the smaller side, not Jaccard,
because a foreign key is many-to-one and 900 orders against 120 customers has near-zero
Jaccard. Detected keys go into the prompt as explicit hints.

Measured honestly: on the sample data the hints made **no difference** — 20/20 with and
without them — because the key names are obvious (`customer_id` → `id`, `sku` → `sku`) and
the model finds them unaided. Hints exist for the case these files don't contain: keys
whose names don't line up (`cust_ref` → `id`), where a wrong guess returns zero rows that
look like a real answer. The eval set needs a file like that before this component is
proven.

**4. Guardrails and self-repair.** Generated SQL is untrusted input: single statement,
`SELECT`/`WITH` only, DDL and file-reading functions rejected, results bounded. The
DuckDB connection itself runs with `enable_external_access=false`, so
`SELECT * FROM read_csv('/etc/passwd')` fails at the engine, not just at the regex.
When a query errors, the engine feeds DuckDB's own error message back to the model for
exactly one retry and labels the answer as repaired.

**5. A picture of what you uploaded.** Before any question, every column is profiled into
a mini chart: where the numbers cluster, whether the dates have a hole in them, which
categories dominate, what share is empty. It is the fastest way to see that a file was
read correctly — and in the sample data the `status` column's two bars show the refund
proportion at a glance, which is exactly the thing that makes "total revenue" ambiguous.

Columns that would produce a meaningless picture are left blank rather than filled: an
`id` has no distribution worth drawing, and twenty product names in twenty rows would be
twenty bars of height one. Profiling samples at 50,000 rows, so a 1M-row file costs the
same as a small one.

**6. Survives bad files.** Each upload is isolated, so one unreadable file doesn't take
the session down with it — drop four good CSVs and one truncated export and you get four
tables plus a plain-English note about the fifth ("malformed CSV (unclosed quote, or rows
with differing column counts)"), not a traceback that loses all five.

**7. Charts from result shape.** The chart type is decided from what came back — one
number is a metric, date + numeric is a line, categories are a bar. The model may suggest
a chart, but it saw the schema, not the result, so its suggestion goes through the same
checks as the fallback: a suggested bar chart of 108 named customers becomes a ranked
horizontal bar of the top 25 with a caption saying so; a 20-slice pie becomes a bar; and
an `id` column never becomes an axis, because an id is a number but not a quantity.

Bars are sorted by value, labelled with their values (a zero baseline is honest but makes
$2,481 and $2,197 look identical), and flipped horizontal when the labels are long. Money
columns are detected by name and formatted as currency in the axis, the labels and the
table. The chart and the table under it share one ordering — the same numbers in two
different orders on one screen reads as a bug.

**8. Declining.** Asked something the data can't answer, the app says what's missing
rather than inventing a column.

## Measuring the delta

`python tests/evals.py` asks 20 questions whose answers are computed independently in
pandas, first against the full system, then with one component switched off at a time.
Full results: [`docs/evals.md`](docs/evals.md).

| Configuration | Correct |
|---|---|
| Full system | **20 / 20** |
| Without type recovery | **16 / 20** — all four monthly questions fail |
| Without date-orientation detection | **16 / 20** — the same four, now silently wrong |
| Without join hints | 20 / 20 |
| Without agreed definitions | 20 / 20 |
| Privacy mode (no sample values sent) | 20 / 20 |

**What this proves:** type recovery and date detection each carry four questions. The
date result is the one to note. Without detection the model still *answers* all four
monthly questions, with confident, wrong totals, because `03/05/2024` was read
as 5 March. No error, nothing on screen to suggest a problem.

**What it doesn't prove:** join hints, definitions and privacy mode made no difference
here. The sample files have obvious key names, the model's default reading of revenue
matches the definition, and the category values are spelled the obvious way. Those
components guard against data this set doesn't contain yet. That is a gap in the eval set,
reported rather than hidden.

**The naive baseline** (CSV text pasted into the prompt) can't run on these files at all:
Groq rejects the request as too large (`413`). A run on a 100-row subset exhausted the free
tier's daily token quota before it finished; the harness records those as *not run*, never
as wrong. Re-run with `python tests/evals.py --subset-only --merge` once the quota resets.

## Running it for a customer

Three things a real deployment needs that a demo does not:

**Agreed definitions.** `config/metrics.toml` holds the customer's meaning of terms like
"revenue": the exact SQL expression and a plain-English description. The model is told to
use them verbatim, and each answer names the definition it applied (*"Uses the agreed
definition of **revenue**: net of refunds"*). A definition is only offered when its table
and columns exist in the upload. The meaning of "revenue" becomes something the customer
sets once, in a file, instead of something the model re-decides every question.
Measured: 20/20 with or without the file on the sample data, because the model's own
default (exclude refunds) happens to match this definition. Its value is control, not
accuracy here — it would show up in the evals the day a customer's definition differs from
the model's instinct. See [ADR 0007](docs/decisions/0007-business-definitions-as-configuration.md).

**Privacy mode.** `SEND_SAMPLES=false` strips sample values from the schema card, so
only column names and types leave the machine. Measured cost on the sample data: **none**
(20/20 — [`docs/evals.md`](docs/evals.md)), because the category values here are spelled the
obvious way. On data with codes the model can't guess (`'CMP'` for completed) it would cost
accuracy; the eval is how you'd find out for a given customer before switching it on.

**A trace of every question.** One JSONL line per question in `logs/queries.jsonl`: the
SQL that ran, status, row count, definitions used, tokens and latency — never the result
rows. A "Recent queries" panel shows it in the app. When a customer says yesterday's total
was wrong, you read the SQL that produced it instead of guessing.

## Verifying the answers

```bash
python tests/test_engine.py     # 18 assertions, no API key: guard, coercion, joins, charts,
                                #   repair, bad files, definitions, privacy, trace, eval harness
python tests/test_live.py       # 5 assertions against the real model (skips without a key)
python tests/ground_truth.py    # the demo answers, recomputed in pandas via a different path
python tests/evals.py           # the scored ablation study -> docs/evals.md (~30 min on free tier)
```

`ground_truth.py` exists so the demo can be checked rather than trusted. It prints two
columns, because **"revenue" is not one number**: the sample data contains 62 refunded
orders. `config/metrics.toml` defines revenue as net of refunds, so that is what the app
returns and what the table below shows; gross figures are in the script.

| Question | Expected (net of refunds) | Gross |
|---|---|---|
| What's the total revenue? | $1,962,664.00 | $2,109,620.72 |
| Average order value by region | North $2,481.03 · South $2,437.13 · East $2,221.77 · West $2,196.96 | 2,497.39 / 2,389.29 / 2,234.18 / 2,228.26 |
| Revenue by month in 2024 | Jan $63,622.72 · Feb $73,209.29 · Mar $108,916.78 | 66,805.26 / 78,527.01 / 112,328.91 |
| Top 5 product categories | Docks $596,094.98, Monitors, Laptops, Headsets, Keyboards | Docks $630,789.81 (Laptops and Monitors swap) |
| Compare North vs South | North $503,649.95 · South $599,533.05 | 566,907.69 / 621,216.24 |
| Customers with >3 orders | 108 | 110 |
| What's our headcount? | declines — no employee data in these files | — |

All six verified matching on a live run. That the gross and net answers differ — and that
the difference is *visible* in the SQL rather than hidden in a number — is the whole
argument for this design.

## Does it actually scale?

The design claim is that the prompt doesn't grow with the data. Measured, not asserted:

```bash
python tests/benchmark.py 1000000
```

| | 1,000,000 rows (55 MB) |
|---|---|
| Ingest, clean, type-recover, profile | **~9s** |
| Query (total / trend / group-by) | **0.00–0.01s** |
| Prompt sent to the model | **561 chars, ~140 tokens** |
| The same data as rows in a prompt | ~13,600,000 tokens — **97,476x larger**, and past every context window |

The schema card is the same 561 characters at 1,000,000 rows as at 900. Row count changes
the answer, never the prompt. The rows-in-the-prompt approach would need a prompt about a
hundred times larger than this model's 131k-token context window. It cannot even run the
900-row sample: on Groq's free tier the request is rejected before the model sees it
(`413 Request too large`).

## Sample data

`data/samples/` holds three deliberately messy related files: `sales.csv` (900 orders,
money as `"$1,234.50"`, three date formats in one column), `customers.xlsx` (120
customers, spaces in headers), `products.csv` (20 SKUs). They join
`sales.customer_id → customers.id` and `sales.sku → products.sku`.

## Why it is built this way

The decisions that would otherwise look arbitrary are recorded in
[`docs/decisions/`](docs/decisions/), Nygard-format, one file each:

| | |
|---|---|
| [0002](docs/decisions/0002-text-to-sql-over-rows-in-prompt.md) | Generated SQL instead of rows in the prompt, and what that costs |
| [0003](docs/decisions/0003-containment-not-jaccard-for-join-keys.md) | Why Jaccard is the instinctive scoring choice and the wrong one |
| [0004](docs/decisions/0004-detect-date-orientation.md) | The date bug where both readings succeed on 100% of rows |
| [0005](docs/decisions/0005-two-layer-sql-safety.md) | Treating generated SQL as untrusted input, in two layers |
| [0006](docs/decisions/0006-live-tests-for-prompt-regressions.md) | Why a stubbed model proved nothing about the prompt |
| [0007](docs/decisions/0007-business-definitions-as-configuration.md) | Why "revenue" is the customer's decision, not the model's |

[`docs/architecture.md`](docs/architecture.md) covers module boundaries and the four
critical paths.

## Limits

In-memory and single-session — nothing persists across restarts, and there's no auth, so
run it locally or behind one. On Groq's free tier the whole app shares 8,000 tokens a
minute; at ~1,500 tokens a question that is about five questions a minute across all
users, which is the real concurrency ceiling. Uploads are capped at 500MB by Streamlit (DuckDB itself
would handle far more). Excel formulas are read as their cached values.
