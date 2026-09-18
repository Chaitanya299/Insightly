"""Question -> SQL -> answer.

The model writes SQL. DuckDB computes the numbers. The model sees the schema
and three sample values per column, never the rows, and query results are never
sent back to it -- so every figure on screen is the output of a query the user
can read, edit and re-run, not something the model worked out.
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

# Any OpenAI-compatible endpoint. Production defaults to Groq; the eval harness can
# point elsewhere (e.g. a local FreeLLMAPI router) without touching this module.
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.getenv("LLM_MODEL") or os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"
MAX_ROWS = 5000

SYSTEM = """You are a careful data analyst who answers questions by writing DuckDB SQL.

You are given the SCHEMA of tables loaded from the user's uploaded files, and
detected join keys between them. You see column names, types and at most a few
sample values -- never the full rows, and never the result of your query.

Return ONLY a JSON object:
{
  "sql": "<a single DuckDB SELECT statement, or null if unanswerable>",
  "chart": {"type": "bar|line|scatter|pie|metric", "x": "<column>", "y": "<column>"} or null,
  "explanation": "<one or two plain sentences about what the query does>",
  "metrics_used": ["<name of each BUSINESS DEFINITION you applied>"]
}

Rules:
- If BUSINESS DEFINITIONS are given and the question uses one of those terms, use
  that exact expression -- it is the organisation's agreed meaning, and it overrides
  your own judgement about filters. List each one you used in "metrics_used".
- Use ONLY the tables and columns listed in the schema. Never invent a column.
- Answer the question that was asked, not a nearby one you can answer. A table that
  is superficially similar is not a substitute: `customers` does not answer a question
  about employees or headcount, and `orders` does not answer one about shipments.
  If nothing in the schema genuinely represents the thing being asked about, set
  "sql" to null and say what is missing in "explanation". Declining is a correct
  answer; a confident answer to a different question is the worst outcome.
- State any assumption you had to make in "explanation" -- especially filters you
  chose to apply, such as excluding refunded or cancelled rows from a revenue total.
- One statement. SELECT or WITH only. No INSERT/UPDATE/DELETE/CREATE/COPY/ATTACH.
- Alias every aggregate to a readable snake_case name (revenue, avg_order_value).
- Join across tables using the detected join keys when the question spans files.
- Round money to 2 decimals. Sort results sensibly and LIMIT long lists.
- Dates are real DATE/TIMESTAMP columns: use date_trunc('month', col) for trends,
  and return that truncated date as a column so it can be plotted.
- "chart" describes how to plot the result: x is the category/date column, y the
  numeric one. Use null when a single number or a plain table is the honest answer.
"""


class UnsafeQuery(Exception):
    """The generated SQL tried to do something other than read."""


@dataclass
class Answer:
    question: str
    sql: str | None = None
    explanation: str = ""
    df: pd.DataFrame | None = None
    chart: dict | None = None
    error: str | None = None
    repaired: bool = False
    notes: list[str] = field(default_factory=list)
    metrics_used: list[str] = field(default_factory=list)
    tokens: int = 0          # prompt + completion, across the call and any repair
    latency_ms: int = 0
    served_by: list[str] = field(default_factory=list)  # what actually answered


# --------------------------------------------------------------------------
# business definitions -- the customer's meaning of "revenue", not the model's
# --------------------------------------------------------------------------

def load_metrics(path, tables) -> list[dict]:
    """Read metric definitions, keeping only those this upload can satisfy.

    A definition naming a table or column that is not loaded is dropped rather
    than shown to the model: offering `SUM(amount)` for a file with no `amount`
    column invites exactly the invented-column failure the guard exists to stop.
    """
    path = Path(path)
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    have = {t.name: set(t.column_names()) for t in tables}
    usable = []
    for name, spec in raw.items():
        table, cols = spec.get("table"), set(spec.get("columns", []))
        if table in have and cols <= have[table] and spec.get("expression"):
            usable.append({"name": name, "table": table,
                           "expression": spec["expression"], "meaning": spec.get("meaning", "")})
    return usable


def read_definitions(path) -> list[dict]:
    """Every definition in the file, applicable to this upload or not (for editing)."""
    path = Path(path)
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return [{"name": k, "table": v.get("table", ""), "columns": list(v.get("columns", [])),
             "expression": v.get("expression", ""), "meaning": v.get("meaning", "")}
            for k, v in raw.items()]


_DEFS_HEADER = """# Business definitions: the organisation's meaning of a term, not the model's.
#
# When a question uses one of these terms, the model is told to use this exact
# expression, and the dashboard uses it too. A definition is only offered when its
# `table` and every one of its `columns` exist in the current upload.
# Editable in the app (Data view) or by hand.
"""


def save_definitions(path, definitions: list[dict]) -> None:
    """Write definitions as TOML. json.dumps gives valid TOML basic strings."""
    out = [_DEFS_HEADER]
    for d in definitions:
        out.append(f"\n[{d['name']}]\n"
                   f"table = {json.dumps(d['table'])}\n"
                   f"columns = {json.dumps(list(d['columns']))}\n"
                   f"expression = {json.dumps(d['expression'])}\n"
                   f"meaning = {json.dumps(d['meaning'])}\n")
    Path(path).write_text("".join(out))


def check_definition(con, tables, d: dict) -> tuple[dict, str | None]:
    """Validate one edited definition against the loaded data.

    Returns the definition with its `columns` filled in from the expression, and
    an error message, or None. A definition for a table that isn't loaded can't
    be tested; it is kept as written.
    """
    name = (d.get("name") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return d, f"`{name or '(blank)'}`: names are lowercase letters, digits and _ (e.g. net_revenue)"
    if not (d.get("expression") or "").strip():
        return d, f"`{name}`: the expression is empty"
    table = next((t for t in tables if t.name == d.get("table")), None)
    if table is None:
        return d, None
    expr = d["expression"].strip()
    cols = [c for c in table.column_names() if re.search(rf"\b{re.escape(c)}\b", expr)]
    try:
        run_sql(con, f'SELECT {expr} AS v FROM "{table.name}"')
    except Exception as exc:  # the guard's error or DuckDB's: both are the user's to fix
        return d, f"`{name}`: {str(exc).splitlines()[0]}"
    return {**d, "name": name, "expression": expr, "columns": cols}, None


def metrics_text(metrics: list[dict]) -> str:
    if not metrics:
        return ""
    lines = ["BUSINESS DEFINITIONS (use exactly when the question uses the term):"]
    for m in metrics:
        lines.append(f'  {m["name"]}  (table "{m["table"]}"):  {m["expression"]}')
        if m["meaning"]:
            lines.append(f"      -- {m['meaning']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# trace log -- so "yesterday's total was wrong" can be answered with the SQL
# --------------------------------------------------------------------------

def log_answer(answer: "Answer", path) -> None:
    """Append one JSONL line per question. Never the result rows: the log holds
    what was asked and what ran, not the customer's data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": answer.question,
        "sql": answer.sql,
        "status": ("error" if answer.error else "declined" if not answer.sql
                   else "repaired" if answer.repaired else "ok"),
        "error": answer.error,
        "rows": None if answer.df is None else len(answer.df),
        "metrics_used": answer.metrics_used,
        "tokens": answer.tokens,
        "latency_ms": answer.latency_ms,
        "model": MODEL,
        "served_by": answer.served_by,
    }
    with open(path, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def read_log(path, last: int = 50) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text().splitlines()[-last:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a half-written line from a crash must not break the panel
    return out


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|install|load|"
    r"pragma|export|vacuum|checkpoint|read_csv|read_parquet|read_json|read_text|"
    r"read_blob|glob|getenv)\b",
    re.I,
)


def guard(sql: str) -> str:
    """Reject anything that isn't a single read-only statement; bound the result.

    Belt and braces: the DuckDB connection itself is opened with external file
    access disabled (see `connect`), so this regex is the friendly error message
    rather than the only thing standing between a prompt injection and the disk.
    """
    if not sql or not sql.strip():
        raise UnsafeQuery("empty query")
    bare = _COMMENTS.sub(" ", sql)
    statements = [s for s in bare.split(";") if s.strip()]
    if len(statements) > 1:
        raise UnsafeQuery("multiple statements are not allowed")
    body = statements[0].strip()
    if not re.match(r"^(select|with)\b", body, re.I):
        raise UnsafeQuery("only SELECT/WITH queries can be run")
    hit = _BLOCKED.search(body)
    if hit:
        raise UnsafeQuery(f"`{hit.group(0)}` is not allowed in a read-only query")
    return f"SELECT * FROM (\n{body}\n) LIMIT {MAX_ROWS}"


def connect() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with the filesystem and network switched off.

    Text-to-SQL means a model's output reaches a SQL engine, so treat it as
    untrusted: without this, `SELECT * FROM read_csv('/etc/passwd')` is a valid
    query. Uploads are registered from pandas, never read from disk by DuckDB.
    """
    con = duckdb.connect()
    for setting in ("SET enable_external_access=false",):
        try:
            con.execute(setting)
        except duckdb.Error:  # older builds: the regex guard still applies
            pass
    return con


def run_sql(con, sql: str) -> pd.DataFrame:
    return con.execute(guard(sql)).fetchdf()


# --------------------------------------------------------------------------
# charts -- chosen from the result's shape, not from the model's opinion
# --------------------------------------------------------------------------

MAX_BARS = 25       # beyond this a bar chart is a smear, so show the top N
MAX_PIE = 6         # a pie with more slices than this is a worse table
_ID_LIKE = re.compile(r"(^|_)(id|key|code|sku|no|num|ref)s?$", re.I)


def pick_chart(df: pd.DataFrame | None, suggestion: dict | None = None) -> dict | None:
    """Choose a chart from the result's shape.

    The model may suggest one, but its suggestion is put through the same sanity
    checks as the fallback rules -- it has seen the schema, not the result, so it
    cannot know that its query returned 108 rows and a bar chart of 108 named
    customers is unreadable.
    """
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    nums = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    dates = [c for c in cols if pd.api.types.is_datetime64_any_dtype(df[c])]
    cats = [c for c in cols if c not in nums and c not in dates]
    # An id is a number but never a quantity: nothing is learned from plotting it.
    measures = [c for c in nums if not _ID_LIKE.search(str(c))]

    if len(df) == 1 and len(measures) == 1 and len(cols) <= 2:
        return {"type": "metric", "y": measures[0]}

    return _from_suggestion(df, suggestion, cols, measures, cats) or _from_shape(
        df, measures, dates, cats
    )


def _from_suggestion(df, suggestion, cols, measures, cats) -> dict | None:
    if not isinstance(suggestion, dict):
        return None
    kind, x, y = suggestion.get("type"), suggestion.get("x"), suggestion.get("y")
    if kind not in {"bar", "line", "scatter", "pie", "area"}:
        return None
    if x not in cols or y not in cols or y not in measures:
        return None
    if kind == "pie" and len(df) > MAX_PIE:
        kind = "bar"  # the intent was "compare parts"; a bar still says that
    spec = {"type": kind, "x": x, "y": y, "series": _series_col(df, cats, x)}
    if kind in {"bar", "pie"} and len(df) > MAX_BARS:
        spec["limit"] = MAX_BARS
    return spec


def _from_shape(df, measures, dates, cats) -> dict | None:
    if dates and measures:
        return {"type": "line", "x": dates[0], "y": measures[0],
                "series": _series_col(df, cats, None)}
    if cats and measures:
        spec = {"type": "bar", "x": cats[0], "y": measures[0], "series": None}
        if len(df) > MAX_BARS:
            spec["limit"] = MAX_BARS
        return spec
    if len(measures) >= 2 and len(df) > 20:
        return {"type": "scatter", "x": measures[0], "y": measures[1], "series": None}
    return None


def _series_col(df, cats, used) -> str | None:
    """A second categorical column worth splitting the series by, if small."""
    for c in cats:
        if c != used and 1 < df[c].nunique() <= 6:
            return c
    return None


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------

def _client(max_retries: int = 2, base_url: str | None = None, api_key: str | None = None):
    from openai import OpenAI

    key = api_key or os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set -- copy .env.example to .env and add your key")
    # The SDK's retries honour the server's retry-after on a 429; the eval
    # harness raises this because the free tier allows 8k tokens a minute.
    return OpenAI(api_key=key, base_url=base_url or BASE_URL, max_retries=max_retries)


def _user_prompt(schema: str, joins: str, question: str,
                 history: list[tuple[str, str]], metrics: str = "") -> str:
    parts = ["SCHEMA:", schema, joins]
    if metrics:
        parts.append("\n" + metrics)
    if history:
        parts.append("\nEARLIER IN THIS CONVERSATION (for follow-up context):")
        for q, sql in history[-3:]:
            parts.append(f"Q: {q}\nSQL: {sql}")
    parts.append(f"\nQUESTION: {question}")
    return "\n".join(parts)


def _complete(client, messages, meter: dict | None = None) -> dict:
    kwargs = dict(
        model=MODEL,
        messages=messages,
        temperature=0,  # SQL generation is not a place for creativity
        response_format={"type": "json_object"},
    )
    served = None
    raw_api = getattr(client.chat.completions, "with_raw_response", None)
    if raw_api is not None:
        raw = raw_api.create(**kwargs)
        resp = raw.parse()
        # A router may answer with a different model than the one asked for;
        # record who actually served it, or an eval cannot tell what it measured.
        served = raw.headers.get("x-routed-via") or getattr(resp, "model", None)
    else:  # test doubles
        resp = client.chat.completions.create(**kwargs)
    usage = getattr(resp, "usage", None)
    if meter is not None:
        if usage is not None:
            meter["tokens"] = meter.get("tokens", 0) + int(getattr(usage, "total_tokens", 0) or 0)
        if served:
            meter.setdefault("served", []).append(str(served))
    return parse_json(resp.choices[0].message.content)


def parse_json(text: str | None) -> dict:
    """JSON mode is a request, not a guarantee, once several providers are in play.

    Some wrap the object in a ```json fence or a sentence; take the outermost
    {...} rather than failing a question on formatting.
    """
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def ask(
    question: str,
    con,
    schema: str,
    joins: str,
    history: list[tuple[str, str]] | None = None,
    client=None,
    metrics: list[dict] | None = None,
) -> Answer:
    """Ask a question. Returns an Answer carrying the SQL, the data and the chart spec."""
    started = time.perf_counter()
    answer = _ask(question, con, schema, joins, history, client, metrics or [])
    answer.latency_ms = int((time.perf_counter() - started) * 1000)
    return answer


def _ask(question, con, schema, joins, history, client, metrics) -> Answer:
    try:
        client = client or _client()
    except RuntimeError as exc:
        return Answer(question, error=str(exc))
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _user_prompt(
            schema, joins, question, history or [], metrics_text(metrics))},
    ]

    meter: dict = {}
    try:
        reply = _complete(client, messages, meter)
    except Exception as exc:
        return Answer(question, error=f"Model call failed: {exc}")

    sql = (reply.get("sql") or "").strip() or None
    known = {m["name"] for m in metrics}
    answer = Answer(
        question, sql=sql, explanation=reply.get("explanation", ""),
        # only names that were actually offered -- the model may not invent one
        metrics_used=[m for m in (reply.get("metrics_used") or []) if m in known],
        tokens=meter.get("tokens", 0),
        served_by=meter.get("served", []),
    )
    if not sql:
        # The model declined -- that is a correct outcome, not a failure.
        return answer

    try:
        answer.df = run_sql(con, sql)
    except UnsafeQuery as exc:
        answer.error = f"Blocked: {exc}"
        return answer
    except Exception as exc:
        repaired = _repair(client, messages, sql, str(exc), con, meter)
        answer.tokens = meter.get("tokens", 0)
        answer.served_by = meter.get("served", [])
        if repaired is None:
            answer.error = f"Query failed: {exc}"
            return answer
        answer.sql, answer.df, answer.repaired = repaired[0], repaired[1], True
        answer.explanation = repaired[2] or answer.explanation

    answer.chart = pick_chart(answer.df, reply.get("chart"))
    return answer


def _repair(client, messages, bad_sql: str, error: str, con, meter: dict | None = None):
    """One retry, with the engine's own error message fed back to the model."""
    messages = messages + [
        {"role": "assistant", "content": json.dumps({"sql": bad_sql})},
        {
            "role": "user",
            "content": (
                f"That query failed with this DuckDB error:\n{error}\n\n"
                "Fix it using only columns that exist in the schema above. "
                "Return the same JSON shape."
            ),
        },
    ]
    try:
        reply = _complete(client, messages, meter)
        sql = (reply.get("sql") or "").strip()
        if not sql:
            return None
        return sql, run_sql(con, sql), reply.get("explanation", "")
    except Exception:
        return None


def suggest_questions(schema: str, joins: str, client=None) -> list[str]:
    """Four starter questions grounded in the actual schema.

    A blank chat box is where these demos stall: the user does not know what
    the app can answer, so they ask something vague and get a bad first result.
    Generated from the schema so it works for any upload, not just the samples.
    """
    client = client or _client()
    try:
        reply = _complete(
            client,
            [
                {
                    "role": "system",
                    "content": (
                        "Given a schema, propose 4 short analytical questions a business user "
                        "would actually ask. Vary them: one total, one breakdown or comparison, "
                        "one trend over time, one that joins two tables. Return JSON "
                        '{"questions": ["...", "...", "...", "..."]}'
                    ),
                },
                {"role": "user", "content": f"SCHEMA:\n{schema}\n{joins}"},
            ],
        )
        return [q for q in reply.get("questions", []) if isinstance(q, str)][:4]
    except Exception:
        return []
