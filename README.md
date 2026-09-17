# Ask Your Data

Upload CSV/Excel files, ask analytical questions in plain English, get answers you can verify.

The model writes SQL. **DuckDB computes the numbers.** The model never sees a single
data value, so it cannot invent one — and the query behind every answer is shown,
editable, and re-runnable.

<img width="900" alt="" src="docs/screenshot.png">

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
| Model | **Llama 3.3 70B** (open weights) via Groq | Strong SQL generation, free tier, ~1s latency. Swap with `GROQ_MODEL`, or point at Ollama — the model only ever returns JSON. |
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
question ───────────────────────────────────────────────┴──► Llama 3.3 70B
                                                        │
                                          {sql, chart, explanation}
                                                        │
                                   guard ─► execute ─► error? one repair retry
                                                        │
                                     DataFrame ─► chart picker ─► answer + editable SQL
```

The model receives a **schema card** — table names, column names, types, null rates,
distinct counts, three sample values — and never the rows themselves. This is what makes
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
Jaccard. Detected keys go into the prompt as explicit hints. Without this the model
invents key names and the join silently returns zero rows.

**4. Guardrails and self-repair.** Generated SQL is untrusted input: single statement,
`SELECT`/`WITH` only, DDL and file-reading functions rejected, results bounded. The
DuckDB connection itself runs with `enable_external_access=false`, so
`SELECT * FROM read_csv('/etc/passwd')` fails at the engine, not just at the regex.
When a query errors, the engine feeds DuckDB's own error message back to the model for
exactly one retry and labels the answer as repaired.

**5. Charts from result shape.** The chart type is decided from what came back — one
number is a metric, date + numeric is a line, few categories is a bar. The model's
suggestion is used only if it names columns that actually exist in the result.

**6. Declining.** Asked something the data can't answer, the app says what's missing
rather than inventing a column.

## Verifying the answers

```bash
python tests/test_engine.py     # 10 assertions: guard, coercion, joins, repair, end-to-end
python tests/ground_truth.py    # the demo answers, recomputed in pandas via a different path
```

`ground_truth.py` exists so the demo can be checked rather than trusted. Ask the app these
and compare:

| Question | Expected |
|---|---|
| What's the total revenue? | $2,109,620.72 |
| Average order value by region | North $2,497.39, South $2,389.29, East $2,234.18, West $2,228.26 |
| Revenue by month in 2024 | Jan $66,805.26 · Feb $78,527.01 · Mar $112,328.91 |
| Top 5 product categories by revenue | Docks $630,789.81, then Laptops, Monitors, Headsets, Keyboards |
| Compare North vs South revenue | North $566,907.69 · South $621,216.24 |
| Which customers ordered more than 3 times? | 110 customers |
| What's our headcount? | declines — not in the data |

## Sample data

`data/samples/` holds three deliberately messy related files: `sales.csv` (900 orders,
money as `"$1,234.50"`, three date formats in one column), `customers.xlsx` (120
customers, spaces in headers), `products.csv` (20 SKUs). They join
`sales.customer_id → customers.id` and `sales.sku → products.sku`.

## Limits

In-memory and single-session — nothing persists across restarts, and there's no auth, so
run it locally or behind one. Uploads are capped at 500MB by Streamlit (DuckDB itself
would handle far more). Excel formulas are read as their cached values.
