# Architecture

A question in English becomes SQL, and DuckDB computes the answer. The invariant that
shapes every module: **the model sees the schema, never the rows.**

## Entry point

`src/app.py`, run by Streamlit (`streamlit run src/app.py`). It owns all session state
and is the only module that imports the other two. There is no server, no database
process, no build step.

## Module boundaries

| Module | Owns | Never does |
|---|---|---|
| `src/profiling.py` | Reading files, cleaning headers, recovering types, profiling columns, discovering join keys, rendering the schema card | Talk to the model. Execute a generated query. |
| `src/engine.py` | Prompt construction, the model call, the SQL guard, execution, the repair retry, chart selection | Read a file. Touch Streamlit. |
| `src/app.py` | Session state, layout, rendering, the editable-SQL loop | Contain analysis logic. |
| `config/metrics.toml` | The customer's definitions ("revenue is net of refunds") | Change per deployment without code |

The split matters because `profiling` and `engine` are both importable and testable
without Streamlit — which is why `tests/` can exercise the whole pipeline headlessly
and `tests/benchmark.py` can drive 1M rows with no UI at all.

## Critical paths

**1. Upload → queryable table.** `app.rebuild` → `profiling.load_files` → per file:
`_frames` (one frame per Excel sheet) → `clean_frame` (header normalisation, numeric
then datetime coercion at a 90% parse threshold) → `CREATE TABLE` in DuckDB →
`profile` → `Table`. Each file is wrapped in its own try/except: one unreadable upload
yields a plain-English problem string and the others still load.

**2. Question → answer.** `app` → `engine.ask` → `_user_prompt` (schema cards + join
hints + applicable business definitions + last 3 Q/SQL pairs) → Groq JSON mode →
`{sql, chart, explanation, metrics_used}` →
`engine.guard` → DuckDB → `pick_chart` → `Answer`. A null `sql` is a valid outcome:
the model declining to answer is correct behavior, not a failure.

**3. Query error → repair.** A DuckDB exception inside `ask` calls `_repair`, which
resends the conversation plus the failed SQL and the verbatim engine error. Exactly one
retry. A second failure surfaces the error and the SQL to the user.

**4. Edited SQL → rerun.** `render_answer` puts the query in a text area. The Re-run
button calls `engine.run_sql` directly, bypassing the model entirely, and re-picks the
chart from the new result shape. This is the trust mechanism: the user can always check
and correct the machine.

## Configuration

All optional, all environment variables:

| Variable | Default | Effect |
|---|---|---|
| `SEND_SAMPLES` | `true` | `false` is privacy mode: the schema card carries names and types only |
| `METRICS_PATH` | `config/metrics.toml` | Business definitions; each offered only if its table and columns exist |
| `TRACE_PATH` | `logs/queries.jsonl` | One line per question: SQL, status, tokens, latency. Never result rows |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Any open-weight model Groq serves |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Groq | Any OpenAI-compatible endpoint instead |

The model client is the OpenAI SDK pointed at a base URL, so the provider is configuration.
Production uses Groq; the eval harness can target a local FreeLLMAPI router
(`--provider freellmapi`) and records which model served each answer, since a router may
fail over between models.

## Where the safety lives

Two independent layers, because a regex over SQL is not a parser:
- `engine.guard` — single statement, `SELECT`/`WITH` only, DDL and file-reading
  functions rejected, result bounded to 5000 rows. Produces the readable error.
- `engine.connect` — the DuckDB connection runs with `enable_external_access=false`,
  so `read_csv('/etc/passwd')` fails at the engine even if the regex were bypassed.

## Capacity

The binding limit is the model API, not the server. Groq's free tier allows 8,000 tokens
a minute per account and a question costs about 1,500, so the whole app, across all
users, answers roughly five questions a minute. Per session, memory is about 3.5 MB for
the sample data and about 260 MB for a 1M-row file, held until the session ends. Session
state lives in one process's memory, so scaling out needs sticky sessions.

## Testing

`tests/test_engine.py` (21 tests) runs with a stubbed model and needs no API key.
`tests/test_live.py` (5 assertions) runs against the real model and skips without one —
it exists because a stub tests the plumbing and only the real model tests the prompt.
`tests/ground_truth.py` recomputes the demo answers in pandas through a different code
path. `tests/benchmark.py` measures the scaling claim. `tests/evals.py` scores 20
questions against the full system, the system with each component switched off, and the
naive rows-in-prompt approach, and writes `docs/evals.md`; `--suite hard` runs 17
questions on data built to break each component (`docs/evals-hard.md`). CI runs the stubbed suite on
every push.
