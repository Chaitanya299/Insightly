# Insightly

AI-powered data Q&A for CSV and Excel files.

Upload several related files, ask questions in plain English, and get answers you can
check: every number on screen comes from a SQL query you can read, edit and re-run.

> **Demo:** [Demo GIF/video]

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add a free key from https://console.groq.com/keys
streamlit run src/app.py
```

Open http://localhost:8501 and click **Load sample files**, or upload your own.
Try *"average order value by region"*, *"revenue by month in 2024"*, or *"what's our
headcount?"* (it should decline: there is no employee data).

## What makes it different

```
LLM    → writes SQL          (sees the schema, never the rows)
DuckDB → computes the answer (deterministic, exact, any size)
```

The obvious build pastes the spreadsheet into the prompt and lets the model do arithmetic
in its head. It can't hold a real file, can't reliably join two, and states wrong numbers
with confidence. Here the model only writes a query. It never sees the rows or its own
results, so it cannot invent a figure.

Measured on the same 100 rows, with answers computed independently in pandas:

| | Correct | Tokens / question |
|---|---|---|
| **Insightly** (LLM → SQL → DuckDB) | **20 / 20** | 1,504 |
| Naive (rows pasted into the prompt) | 10 / 20 | 6,040 |

On the full 900-row sample the naive approach can't run at all: the API rejects the
request as too large (`413`). Insightly's prompt is **564 characters at 900 rows and at
1,000,000 rows**. The row count changes the answer, never the prompt.

## Features

**Four views**
- **💬 Ask**: chat with your data. Each answer shows a chart, the table, and the SQL behind it in an editable box with **Re-run** and **Download CSV**.
- **📊 Dashboard**: a no-LLM dashboard built automatically from the files: headline KPIs with a sparkline, latest month versus the previous one, best month, top category, a monthly trend, and a breakdown that can reach across files through a detected join. The SQL behind it is one click away.
- **🗂 Data**: every column profiled as a mini chart (distribution, time coverage, top values), plus what was cleaned on import, the agreed definitions, and how the files connect.
- **✅ Quality**: the eval evidence (what each component is worth) and live usage analytics: answer rate, latency, tokens per question, and every question with its SQL.

**Under the hood**
- **Multi-file upload**, CSV and Excel. Each sheet becomes a table, and one broken file never takes down the others.
- **Messy-data repair**: `$1,234.50`, `15%` and `(99)` become numbers, and headers are normalised. Mixed date formats become real dates, with **day-first/month-first detected** instead of guessed.
- **Cross-file joins found automatically**, scored by containment. This works even when key names don't match (`orders.customer` ↔ `clients.legacy_ref`).
- **Agreed business definitions** in `config/metrics.toml` (e.g. *revenue = completed orders only*). The chat and the dashboard both use them, and every answer that relies on one says so.
- **Declines** questions the data can't answer instead of inventing a column.
- **Self-repair**: a failing query is retried once with DuckDB's exact error.
- **Charts chosen from the result's shape**, with the model's suggestion sanity-checked. There are no pies with twenty slices and no ids used as measures. Bars are ranked and labelled, and money is formatted as currency.
- **Privacy mode** (`SEND_SAMPLES=false`): only column names and types leave the machine.
- **Trace log** (`logs/queries.jsonl`): the question, SQL, status, tokens and latency. Never the result rows.
- **Provider as configuration**: any OpenAI-compatible endpoint (Groq by default, `gpt-oss-120b` / `gpt-oss-20b`).

## Architecture

```
upload ─► profiling.py: clean headers · recover types · detect date orientation
          · profile columns · discover joins
                │
                ▼
          DuckDB (in-memory, one table per file/sheet, external file access off)
                │
question ─► engine.py: schema card + join hints + agreed definitions + last 3 Q→SQL
                │
                ▼
          LLM (JSON mode) ─► {sql, chart, explanation, metrics_used}
                │                    sql = null ─► "declined"
                ▼
          guard ─► execute ─► error? one repair retry with the verbatim error
                │
                ▼
          result ─► chart picker ─► app.py: chart · table · editable SQL · trace
```

| Module | Owns | Never does |
|---|---|---|
| `src/profiling.py` | Reading, cleaning, type recovery, profiling, join discovery, the schema card | Talks to the model |
| `src/engine.py` | Prompt, model call, SQL guard, execution, repair, chart choice, definitions, trace | Reads files, touches the UI |
| `src/dashboard.py` | Rule-built KPI, trend and breakdown SQL, using the agreed definitions | Calls the model |
| `src/app.py` | Streamlit UI: the four views, session state | Holds analysis logic |
| `config/metrics.toml` | The customer's definitions | Needs code changes per customer |

**Stack:** Python 3.12 · Streamlit · DuckDB · pandas · Plotly · OpenAI SDK against Groq.
Chart colours use a colour-vision-deficiency-validated categorical palette in a fixed
order. More detail in [`docs/architecture.md`](docs/architecture.md); every non-obvious
decision is an ADR in [`docs/decisions/`](docs/decisions/).

## Engineering Delta

What I built on top of "call the model":

| Component | Problem it solves | Proof |
|---|---|---|
| Text-to-SQL over DuckDB | Hallucinated arithmetic; files too big for a prompt | Naive 10/20 vs 20/20; prompt flat at 1M rows |
| Type recovery | `"$1,234.50"` is text, so sums fail | −4 answers without it |
| Date-orientation detection | `03/05/2024` parses as March *and* May with no error | −4 answers without it, all confidently wrong |
| Join discovery (containment) | Keys whose names don't match; Jaccard misses every FK | −3 answers in privacy mode without it |
| Agreed definitions | "Revenue" means what the customer says, not what the model guesses | −5 answers on the hard suite without them |
| Two-layer SQL safety | Generated SQL is untrusted input | Tests: writes, multi-statements, file reads all blocked |
| Editable SQL | Users check the machine instead of trusting it | Every answer, plus the dashboard |
| Eval harness with ablation | "Does it work?" answered with numbers | `docs/evals*.md` |

## Evaluation

`tests/evals.py` asks questions whose answers are **computed independently in pandas**,
then switches off one component at a time. API quota refusals are recorded as *not run*,
never as wrong.

**Sample suite:** 20 questions on the demo data ([`docs/evals.md`](docs/evals.md), [`docs/evals-current.md`](docs/evals-current.md))

| Configuration | gpt-oss-120b | gpt-oss-20b |
|---|---|---|
| Full system | **20 / 20** | **20 / 20** |
| Without type recovery | 16 / 20 | 16 / 20 |
| Without date detection | 16 / 20 | 16 / 20 |
| Without join hints | 20 / 20 | 20 / 20 |
| Without definitions | 20 / 20 | 19 / 20 |
| Privacy mode | 20 / 20 | 20 / 20 |

**Hard suite:** 17 questions on data built so each component is the only thing between
the model and a wrong answer: mismatched keys with a decoy, revenue net of discount with an
April fiscal year, and coded categories (`CXL`, `EMEA`) ([`docs/evals-hard.md`](docs/evals-hard.md)).

| Configuration (gpt-oss-20b) | Correct | What it lost |
|---|---|---|
| Full system | **17 / 17** | |
| Without join hints | 17 / 17 | nothing: the model matches `A-7342` in both sample lists |
| Without definitions | 12 / 17 | calendar- and fiscal-year revenue, tier, Europe |
| Privacy mode | 13 / 17 | the coded filters: cancelled, pending, refunded, Europe |
| Privacy mode, no join hints | 10 / 17 | the above plus three questions needing the client join |

Building the hard suite found two real bugs before any model ran (join discovery ignored
foreign keys not *named* like keys; three sample values hid the fourth status code). Both
are fixed and tested.

**Tests**

```bash
python tests/test_engine.py     # 22 tests, no API key (runs in CI on every push)
python tests/test_live.py       # 5 tests against the real model
python tests/ground_truth.py    # demo answers recomputed in pandas via a different path
python tests/benchmark.py 1000000
python tests/evals.py           # sample suite;  --suite hard for the hard one
```

| Demo question | Expected (net of refunds) |
|---|---|
| Total revenue | $1,962,664.00 (gross $2,109,620.72) |
| Average order value by region | North $2,481.03 · South $2,437.13 · East $2,221.77 · West $2,196.96 |
| Top product category | Docks, $596,094.98 |
| Customers with more than 3 orders | 108 |
| What's our headcount? | declines: no employee data |

At 1,000,000 rows, ingest takes about 9s, queries take 0.01s, and the prompt stays 564
characters. The same rows pasted into a prompt would be about 13.6M tokens.

## Security

- **Generated SQL is untrusted input.** It is checked twice:
  1. `guard()` strips comments and allows a single `SELECT`/`WITH` statement only. It blocks DDL/DML, `ATTACH`, `COPY`, `INSTALL`, `PRAGMA`, `read_csv`, `glob`, `getenv` and similar, and caps results at 5,000 rows.
  2. The DuckDB connection runs with `enable_external_access=false`, so `read_csv('/etc/passwd')` fails at the engine even if the regex were bypassed.
- **Data exposure is explicit.** The model gets the schema card only: names, types, null rates, three sample values or the full list for small category columns. Privacy mode removes the values.
- **The trace log holds questions and SQL, never result rows.** It is gitignored.
- **Secrets stay local.** Keys live in `.env` (gitignored), and org ids are redacted from stored eval errors.

## Known Limitations

- Sample values reach the model provider unless privacy mode is on, and privacy mode costs accuracy on coded categories (13/17 on the hard suite).
- Definitions are a prompt instruction, not compiled SQL. The model can ignore one; the evals catch it when it does.
- Single session, in-memory, no auth, no persistence.
- Throughput is bounded by the API. The free tier allows 8,000 tokens a minute, about 5 questions a minute for the whole app.
- Only questions SQL can express: no fuzzy matching and no "why did this happen".
- Tables with more than 200 columns would need a trimmed schema card. Excel formulas are read as cached values. Uploads are capped at 500 MB.
- The evals are one run per question on synthetic data, so a one-question gap is within noise.

## Roadmap

1. **An eval set built with the customer, on their data.** It's the only honest answer to "does it work on ours?"
2. **Compile definitions into SQL** (a small semantic layer) instead of asking the model to copy them.
3. **A verification pass** that checks the generated SQL answers the question actually asked.
4. **Persistence, auth, saved dashboards and a paid API tier.** The rate limit is the first wall a second user hits.
5. **Warehouse connectors** (Postgres, BigQuery, Snowflake) alongside file upload.
