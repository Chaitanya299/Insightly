# Insightly

AI-powered data Q&A for CSV and Excel files. Every number comes from SQL you can read.

> **Try it live:** https://insightly0.streamlit.app · click **Load sample files**, then ask a question.
>
> **Demo video:** [add link] · **Write-up:** [`docs/WRITEUP.md`](docs/WRITEUP.md) · **Demo script:** [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)

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

A sidebar walks through the whole journey, **01 Upload → 02 Ask → 03 Dashboard → 04 Data → 05 Quality**. The visual system (Satoshi + JetBrains Mono, an ink sidebar, a green "verified" stamp on every number DuckDB computed) is specified in [`DESIGN.md`](DESIGN.md).

- **Upload:** drop files or load the samples, then see what was loaded, what was cleaned and how the files link.

- **Ask:** plain-English questions across several files. Each answer comes back as a chart, a table and editable SQL.
- **Dashboard:** KPIs, the monthly trend and a cross-file breakdown, built from rules, with no model call.
- **Data:** every column profiled as a labelled mini chart (rows per month, spread, most common values with shares), what was cleaned on import, and how the files connect.
- **Agreed definitions:** the organisation's formula for words like *revenue*, used by both the chat and the dashboard. You can edit them in the app, and each formula is test-run against the data before it's saved.
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

We tested each part by switching it off and counting how many questions went wrong.

| Part | The problem it solves | Wrong answers without it |
|---|---|---|
| **The model writes a query; the database does the maths** | AI models get arithmetic wrong and can't read big files | Half the answers wrong (10 of 20) |
| **Fixing number formats** | `"$1,234.50"` is read as text, so totals fail | 4 more wrong |
| **Reading dates correctly** | `03/05/2024` could be 3 May or 5 March | 4 more wrong, with no error shown |
| **Agreed definitions** | "Revenue" should mean what your company means | 5 more wrong |
| **Finding links between files** | Matching columns can have different names in each file | 3 more wrong in privacy mode |

## Evaluation

We asked questions whose correct answers we worked out separately, then checked
Insightly's answers against them.

- **Everyday questions (20):** 20 of 20 correct.
- **Tricky questions (17):** 17 of 17 correct. These use files built to cause mistakes:
  confusing column names, company codes like `EMEA`, and a financial year starting in April.

Details: [`docs/evals.md`](docs/evals.md) and [`docs/evals-hard.md`](docs/evals-hard.md).
To check it yourself:

```bash
python tests/test_engine.py   # quick checks, no API key needed
python tests/evals.py         # the full test (needs an API key)
```

## Security

- **Read-only.** Insightly can only *read* your data. Any query that tries to change or
  delete anything, or open other files on your computer, is blocked, and blocked again by
  the database itself.
- **Your rows stay here.** The AI sees column names, types and a few example values,
  never your full data. Turn on privacy mode (`SEND_SAMPLES=false`) to hide the examples too.
- **The history log keeps questions and queries, never results.** API keys stay in a
  local `.env` file that is never uploaded.

## Known Limitations

- **A few example values are sent to the AI** unless privacy mode is on, and privacy mode
  makes questions about coded values (like `EMEA`) less accurate.
- **The AI is told to use your definitions but isn't forced to.** Our tests catch it when
  it doesn't.
- **One person at a time.** No logins, and nothing is saved when you close it.
- **About 5 questions a minute** on the free AI plan.
- **It answers "how much" and "how many" questions,** not "why did this happen".
- **The tests are small** (37 questions on sample data), so treat one-question
  differences as noise.

## Roadmap

1. An eval set built with the customer, on their data
2. Definitions compiled into SQL (a semantic layer)
3. A verification pass: does the SQL answer the question asked?
4. Persistence, auth, saved dashboards, warehouse connectors
