# Insightly

AI-powered data Q&A for CSV and Excel files. Every number comes from SQL you can read.

> **Demo:** [Demo GIF/video]

## Why Insightly?

```
┌──────────────────────────────┐          ┌──────────────────────────────┐
│      Naive LLM approach      │          │          Insightly           │
│                              │          │                              │
│   Rows ─► LLM ─► Answer      │    VS    │   Schema ─► LLM ─► SQL       │
│                              │          │                 ↓            │
│                              │          │              DuckDB          │
│                              │          │                 ↓            │
│                              │          │         Verified result      │
│                              │          │                              │
│   10 / 20 correct            │          │   20 / 20 correct            │
│   6,040 tokens / question    │          │   1,504 tokens / question    │
└──────────────────────────────┘          └──────────────────────────────┘
```

Both approaches got the same 20 questions on the same 100 rows, and every answer was
checked against one computed independently in pandas
([`docs/evals.md`](docs/evals.md)). The naive approach can't run on the full 900-row
files at all (`413: request too large`). Insightly's prompt is 564 characters at 900 rows
and at 1,000,000.

The model writes the query; **DuckDB computes the answer**. The model never sees the rows,
so it can't invent a figure, and the SQL is shown, editable and re-runnable under every
answer. The same comparison is in the app, on the start screen and in the **Quality** view.

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add a free key from https://console.groq.com/keys
streamlit run src/app.py
```

Click **Load sample files**, then ask *"average order value by region"* or *"what's our
headcount?"* (it should decline).

## Features

- **Ask:** plain-English questions across several files. Each answer comes back as a chart, a table and editable SQL.
- **Dashboard:** KPIs, the monthly trend and a cross-file breakdown, built from rules, with no model call.
- **Data:** every column profiled as a mini chart, what was cleaned on import, and how the files connect.
- **Quality:** the eval evidence and live usage analytics (answer rate, latency, tokens).

## Architecture

```
upload ─► profiling.py ─► DuckDB (one table per file/sheet)
            clean · recover types · detect date order · find joins
question ─► engine.py ─► LLM ─► SQL ─► guard ─► DuckDB ─► chart · table · SQL
            schema card + join hints + agreed definitions      (one repair retry)
```

`profiling.py` (ingest), `engine.py` (model, guard, execution, charts), `dashboard.py`
(rule-built SQL) and `app.py` (Streamlit UI). Stack: Python · Streamlit · DuckDB · pandas ·
Plotly · open-weight `gpt-oss` via Groq. See [`docs/architecture.md`](docs/architecture.md)
and the ADRs in [`docs/decisions/`](docs/decisions/).

## Engineering Delta

What each component is worth. "Without it" is the score with that one component switched off.

| Component | What it fixes | Without it |
|---|---|---|
| Text-to-SQL over DuckDB | Hallucinated arithmetic; files too big for a prompt | 10 / 20 (naive) |
| Type recovery | `"$1,234.50"` stored as text | −4 answers |
| Date-order detection | `03/05/2024` silently read as the wrong month | −4 answers |
| Agreed definitions (`config/metrics.toml`) | "Revenue" means what the customer says | −5 answers (hard suite) |
| Join discovery | Keys whose names don't match | −3 answers (hard suite, privacy mode) |

## Evaluation

| Suite | Full system | Details |
|---|---|---|
| Sample data, 20 questions | **20 / 20** (gpt-oss-120b and 20b) | [`docs/evals.md`](docs/evals.md) |
| Hard data, 17 questions (decoy keys, coded categories, fiscal year) | **17 / 17** (gpt-oss-20b) | [`docs/evals-hard.md`](docs/evals-hard.md) |

```bash
python tests/test_engine.py   # 22 tests, no API key, runs in CI
python tests/test_live.py     # 5 tests against the real model
python tests/evals.py         # ablation study (--suite hard for the hard set)
```

## Security

- Generated SQL is treated as untrusted. It must be a single `SELECT`/`WITH` statement: no writes, no file functions, and at most 5,000 rows back.
- DuckDB runs with `enable_external_access=false`, so file reads are blocked even if the regex check is bypassed.
- The model receives the schema card only. Privacy mode (`SEND_SAMPLES=false`) removes the sample values too.
- The trace log holds questions and SQL, never result rows. `.env` is gitignored.

## Known Limitations

- **Sample values reach the model provider** unless privacy mode is on, and privacy mode costs accuracy on coded categories (13/17 on the hard suite).
- **Definitions are a prompt instruction, not compiled SQL.** The model can ignore one; the evals catch it.
- **Single session, in-memory, no auth, no persistence.**
- **Throughput is bounded by the API.** The free tier allows about 5 questions a minute across all users.
- **SQL-shaped questions only.** No fuzzy matching and no "why did this happen".
- **Small evals.** One run per question on synthetic data, so a one-question gap is noise.

## Roadmap

1. An eval set built with the customer, on their data
2. Definitions compiled into SQL (a semantic layer)
3. A verification pass: does the SQL answer the question asked?
4. Persistence, auth, saved dashboards, warehouse connectors
