"""Question -> SQL -> answer.

The model writes SQL. DuckDB computes the numbers. The model never sees a
single data value, so it cannot invent one -- every figure on screen is the
output of a query the user can read, edit and re-run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import duckdb
import pandas as pd

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_ROWS = 5000

SYSTEM = """You are a careful data analyst who answers questions by writing DuckDB SQL.

You are given the SCHEMA of tables loaded from the user's uploaded files, and
detected join keys between them. You never see the data itself.

Return ONLY a JSON object:
{
  "sql": "<a single DuckDB SELECT statement, or null if unanswerable>",
  "chart": {"type": "bar|line|scatter|pie|metric", "x": "<column>", "y": "<column>"} or null,
  "explanation": "<one or two plain sentences about what the query does>"
}

Rules:
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

def _client():
    from groq import Groq

    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set -- copy .env.example to .env and add your key")
    return Groq(api_key=key)


def _user_prompt(schema: str, joins: str, question: str, history: list[tuple[str, str]]) -> str:
    parts = ["SCHEMA:", schema, joins]
    if history:
        parts.append("\nEARLIER IN THIS CONVERSATION (for follow-up context):")
        for q, sql in history[-3:]:
            parts.append(f"Q: {q}\nSQL: {sql}")
    parts.append(f"\nQUESTION: {question}")
    return "\n".join(parts)


def _complete(client, messages) -> dict:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0,  # SQL generation is not a place for creativity
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def ask(
    question: str,
    con,
    schema: str,
    joins: str,
    history: list[tuple[str, str]] | None = None,
    client=None,
) -> Answer:
    """Ask a question. Returns an Answer carrying the SQL, the data and the chart spec."""
    try:
        client = client or _client()
    except RuntimeError as exc:
        return Answer(question, error=str(exc))
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _user_prompt(schema, joins, question, history or [])},
    ]

    try:
        reply = _complete(client, messages)
    except Exception as exc:
        return Answer(question, error=f"Model call failed: {exc}")

    sql = (reply.get("sql") or "").strip() or None
    answer = Answer(question, sql=sql, explanation=reply.get("explanation", ""))
    if not sql:
        # The model declined -- that is a correct outcome, not a failure.
        return answer

    try:
        answer.df = run_sql(con, sql)
    except UnsafeQuery as exc:
        answer.error = f"Blocked: {exc}"
        return answer
    except Exception as exc:
        repaired = _repair(client, messages, sql, str(exc), con)
        if repaired is None:
            answer.error = f"Query failed: {exc}"
            return answer
        answer.sql, answer.df, answer.repaired = repaired[0], repaired[1], True
        answer.explanation = repaired[2] or answer.explanation

    answer.chart = pick_chart(answer.df, reply.get("chart"))
    return answer


def _repair(client, messages, bad_sql: str, error: str, con):
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
        reply = _complete(client, messages)
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
