"""Streamlit UI: upload files, ask questions, see the SQL behind every answer."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import engine  # noqa: E402
import profiling  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

st.set_page_config(page_title="Data Q&A", page_icon="📊", layout="wide")

PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2", "#B279A2"]
SAMPLES_SIG = ("samples",)
ROOT = Path(__file__).parent.parent
METRICS_PATH = os.getenv("METRICS_PATH", str(ROOT / "config" / "metrics.toml"))
TRACE_PATH = os.getenv("TRACE_PATH", str(ROOT / "logs" / "queries.jsonl"))
# Privacy mode: send column names and types only, no sample values, to the model.
SEND_SAMPLES = os.getenv("SEND_SAMPLES", "true").strip().lower() not in {"0", "false", "no", "off"}


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def state():
    ss = st.session_state
    if "con" not in ss:  # not setdefault: its default is built on every rerun
        ss.con = engine.connect()
    ss.setdefault("tables", [])
    ss.setdefault("problems", [])
    ss.setdefault("joins", [])
    ss.setdefault("schema", "")
    ss.setdefault("joins_text", "")
    ss.setdefault("answers", [])
    ss.setdefault("signature", None)
    ss.setdefault("suggestions", [])
    ss.setdefault("cache", {})
    ss.setdefault("pending", None)
    ss.setdefault("metrics", [])
    return ss


def rebuild(ss, uploads):
    """Reload everything when the set of uploaded files changes."""
    ss.con = engine.connect()
    ss.tables, ss.problems = profiling.load_files(uploads, ss.con)
    ss.joins = profiling.discover_joins(ss.con, ss.tables)
    ss.schema = profiling.schema_text(ss.tables, samples=SEND_SAMPLES)
    ss.joins_text = profiling.joins_text(ss.joins)
    ss.metrics = engine.load_metrics(METRICS_PATH, ss.tables)
    ss.answers, ss.cache, ss.suggestions = [], {}, []


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

_MONEY_COL = re.compile(
    r"(amount|revenue|sales|cost|price|spend|profit|margin|total|value|aov|balance|fee)",
    re.I,
)


def _money(col: str) -> bool:
    return bool(_MONEY_COL.search(str(col)))


def _num_format(col: str, series: pd.Series) -> str:
    """A d3 format string. Money gets a currency prefix; small numbers keep decimals."""
    if _money(col):
        return "$,.0f" if series.abs().max() >= 100 else "$,.2f"
    if pd.api.types.is_integer_dtype(series):
        return ",.0f"
    return ",.0f" if series.abs().max() >= 100 else ",.2f"


def _pretty(col: str) -> str:
    return str(col).replace("_", " ").strip().title()


def ordered_for_display(df: pd.DataFrame, chart: dict | None) -> pd.DataFrame:
    """One ordering for the chart and the table beneath it.

    They show the same numbers, so showing them in two different orders on the
    same screen (chart ranked by value, table alphabetical) reads as a bug.
    """
    if chart is None or df is None or df.empty:
        return df
    x, y = chart.get("x"), chart.get("y")
    if chart["type"] not in {"bar", "pie"} or x not in df.columns or y not in df.columns:
        return df
    if pd.api.types.is_datetime64_any_dtype(df[x]):
        return df  # time is already the right order; never re-rank it by value
    return df.sort_values(y, ascending=False)


def money_column_config(df: pd.DataFrame) -> dict:
    """Format currency-looking columns in the table the way the chart formats them."""
    config = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) and _money(col):
            # "dollar" is locale-aware and gives "$1,234.57"; a printf "$%.2f"
            # drops the thousands separator and disagrees with the chart labels.
            config[col] = st.column_config.NumberColumn(col, format="dollar")
    return config


def render_chart(df: pd.DataFrame, chart: dict):
    kind = chart["type"]
    if kind == "metric":
        col = chart["y"]
        value = df[col].iloc[0]
        if pd.isna(value):
            st.metric(_pretty(col), "—")
            return
        prefix = "$" if _money(col) else ""
        text = f"{prefix}{value:,.2f}" if isinstance(value, float) else f"{prefix}{value:,}"
        st.metric(_pretty(col), text)
        return

    x, y, series = chart.get("x"), chart.get("y"), chart.get("series")
    if x not in df.columns or y not in df.columns:
        return
    data, note = df, None

    # Too many categories to read: show the biggest ones and say so, rather than
    # rendering a smear of 108 bars or silently dropping the chart.
    limit = chart.get("limit")
    if limit and len(data) > limit:
        note = f"Showing the top {limit} of {len(data):,} by {_pretty(y).lower()}."
        data = data.nlargest(limit, y)

    x_is_date = pd.api.types.is_datetime64_any_dtype(data[x])

    fmt = _num_format(y, data[y])
    common = dict(color_discrete_sequence=PALETTE)

    if kind == "pie":
        fig = px.pie(data, names=x, values=y, hole=0.45, **common)
        fig.update_traces(textposition="outside", texttemplate="%{label}<br>%{percent}")
    elif kind == "bar":
        # Long labels are unreadable rotated; horizontal bars read left to right.
        longest = data[x].astype(str).str.len().max() if not x_is_date else 0
        horizontal = not x_is_date and (longest > 14 or len(data) > 10)
        if horizontal:
            data = data.iloc[::-1]  # plotly draws the first row at the bottom
            fig = px.bar(data, x=y, y=x, orientation="h", color=series, **common)
            fig.update_xaxes(tickformat=fmt)
        else:
            fig = px.bar(data, x=x, y=y, color=series, **common)
            fig.update_yaxes(tickformat=fmt)
        # Values on the bars: a zero baseline is honest but flattens a narrow
        # range (2,196 vs 2,481 look identical), so print the numbers.
        if len(data) <= 14 and not series:
            axis = "x" if horizontal else "y"
            fig.update_traces(texttemplate="%{" + axis + ":" + fmt + "}",
                              textposition="outside", cliponaxis=False)
    elif kind in {"line", "area"}:
        draw = px.area if kind == "area" else px.line
        fig = draw(data, x=x, y=y, color=series,
                   **({"markers": len(data) <= 40} if kind == "line" else {}), **common)
        fig.update_yaxes(tickformat=fmt)
        if x_is_date:
            span = (data[x].max() - data[x].min()).days
            fig.update_xaxes(tickformat="%b %Y" if span > 90 else "%d %b")
    elif kind == "scatter":
        fig = px.scatter(data, x=x, y=y, color=series, opacity=0.65, **common)
        fig.update_yaxes(tickformat=fmt)
        fig.update_xaxes(tickformat=_num_format(x, data[x]))
    else:
        return

    fig.update_layout(
        margin=dict(l=0, r=10, t=10, b=0),
        height=380 if kind != "bar" else max(300, min(520, 60 + 26 * len(data))),
        xaxis_title=None,
        yaxis_title=None,
        legend_title_text="",
        separators=".,",
        hoverlabel=dict(bgcolor="white"),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    if kind not in {"pie"}:
        fig.update_yaxes(gridcolor="rgba(0,0,0,0.07)", zerolinecolor="rgba(0,0,0,0.15)")
        fig.update_xaxes(gridcolor="rgba(0,0,0,0.07)")
    st.plotly_chart(fig, use_container_width=True)
    if note:
        st.caption(note)


_SPARK_LABEL = {"time": "over time", "histogram": "spread", "top": "top values"}


def render_overview(tables: list) -> None:
    """What did I actually upload? One row per column, with its shape.

    The point is the shape, not the statistic: where the numbers cluster, whether
    the dates have a hole in them, which categories dominate. It is also the
    fastest way to catch a file that was read wrong before asking it anything.
    """
    for table in tables:
        dates = [c for c in table.columns if c.dtype == "DATE" and c.span]
        head = f"**{table.name}** · {table.rows:,} rows · {len(table.columns)} columns"
        if dates:
            head += f" · {dates[0].name} spans {dates[0].span}"
        st.markdown(head)

        rows = []
        for c in table.columns:
            summary = c.span or f"{c.distinct:,} distinct"
            if c.nulls_pct:
                summary += f"  ·  {c.nulls_pct:g}% empty"
            rows.append({
                "column": c.name,
                "type": c.dtype,
                "shape": c.spark or [],
                "": _SPARK_LABEL.get(c.spark_kind, ""),
                "range": summary,
            })
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "column": st.column_config.TextColumn(width="small"),
                "type": st.column_config.TextColumn(width="small"),
                "shape": st.column_config.BarChartColumn(y_min=0, width="medium"),
                "": st.column_config.TextColumn(width="small"),
                "range": st.column_config.TextColumn("range / values", width="medium"),
            },
        )
        if table.notes:
            cleaned = [n for n in table.notes if "renamed" not in n]
            if cleaned:
                st.caption("Cleaned on import: " + "; ".join(cleaned))


def render_trace_panel() -> None:
    """Recent questions, what ran, and what each cost. For the engineer, not the analyst."""
    records = engine.read_log(TRACE_PATH, last=50)
    if not records:
        return
    with st.expander(f"Recent queries ({len(records)})"):
        answered = [r for r in records if r["status"] in ("ok", "repaired")]
        tokens = [r["tokens"] for r in records if r.get("tokens")]
        latency = sorted(r["latency_ms"] for r in records if r.get("latency_ms"))
        if latency:
            st.caption(
                f"{len(answered)}/{len(records)} answered · "
                f"median {latency[len(latency) // 2]:,} ms · "
                f"{sum(tokens) // max(len(tokens), 1):,} tokens/question"
            )
        st.dataframe(
            pd.DataFrame([{
                "when": r["at"][11:19],
                "question": r["question"],
                "status": r["status"],
                "rows": r["rows"],
                "tokens": r["tokens"],
                "ms": r["latency_ms"],
            } for r in reversed(records)]),
            hide_index=True, use_container_width=True,
        )
        st.caption(f"Full trace, including SQL: `{Path(TRACE_PATH).relative_to(ROOT)}`"
                   if Path(TRACE_PATH).is_relative_to(ROOT) else f"Full trace: `{TRACE_PATH}`")


def render_answer(ss, idx: int, answer: engine.Answer):
    if answer.error:
        st.error(answer.error)
    if answer.explanation:
        st.markdown(answer.explanation)
    if answer.repaired:
        st.caption("↻ the first query errored; it was repaired automatically and re-run")
    if answer.metrics_used:
        meanings = {m["name"]: m["meaning"] for m in ss.metrics}
        st.caption("📐 Uses the agreed definition of " + "; ".join(
            f"**{name.replace('_', ' ')}** ({meanings.get(name, '')})" for name in answer.metrics_used))

    if answer.df is not None and not answer.df.empty:
        shown = ordered_for_display(answer.df, answer.chart)
        if answer.chart:
            render_chart(shown, answer.chart)
        if not (answer.chart and answer.chart["type"] == "metric"):
            st.dataframe(shown, use_container_width=True, hide_index=True,
                         column_config=money_column_config(shown))
            if len(answer.df) >= engine.MAX_ROWS:
                st.caption(f"showing the first {engine.MAX_ROWS:,} rows")
    elif answer.df is not None:
        st.info("That query ran fine but returned no rows.")

    if answer.sql:
        with st.expander("SQL"):
            edited = st.text_area(
                "The query that produced this answer — edit it and re-run:",
                value=answer.sql,
                height=170,
                key=f"sql_{idx}",
                label_visibility="collapsed",
            )
            if st.button("Re-run", key=f"rerun_{idx}"):
                try:
                    answer.df = engine.run_sql(ss.con, edited)
                    answer.sql, answer.error = edited, None
                    answer.chart = engine.pick_chart(answer.df)
                    answer.explanation = "_Result of your edited query._"
                    answer.repaired = False
                except Exception as exc:
                    answer.error = str(exc)
                st.rerun()
            if answer.df is not None and not answer.df.empty:
                st.download_button(
                    "Download CSV",
                    answer.df.to_csv(index=False).encode(),
                    file_name="answer.csv",
                    key=f"dl_{idx}",
                )


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

ss = state()

with st.sidebar:
    st.subheader("Data")
    uploads = st.file_uploader(
        "CSV or Excel files",
        type=["csv", "xlsx", "xls", "xlsm"],
        accept_multiple_files=True,
        help="Upload several related files — questions can span all of them.",
    )
    if uploads:
        signature = tuple(sorted((u.name, u.size) for u in uploads))
        if signature != ss.signature:
            with st.spinner("Reading and profiling…"):
                rebuild(ss, uploads)
            ss.signature = signature
    elif ss.signature not in (None, SAMPLES_SIG):
        # the uploader was cleared -- drop everything except a sample session
        ss.signature, ss.tables, ss.joins, ss.answers, ss.problems = None, [], [], [], []

    samples = sorted((Path(__file__).parent.parent / "data" / "samples").glob("*.*"))
    if samples and not uploads:
        if st.button("Load sample files", use_container_width=True):
            with st.spinner("Reading and profiling…"):
                rebuild(ss, samples)
            ss.signature = SAMPLES_SIG
            st.rerun()
        st.caption(" · ".join(f.name for f in samples))

    for problem in ss.problems:
        st.warning(problem, icon="⚠️")

    # The detail lives in the main overview now -- two places showing the same
    # schema is worse than one. This is just a receipt for what loaded.
    if ss.tables:
        st.caption(
            f"**{len(ss.tables)} table(s)**, "
            f"{sum(t.rows for t in ss.tables):,} rows total\n\n"
            + "\n".join(f"- `{t.name}` · {t.rows:,} rows" for t in ss.tables)
        )
    if not SEND_SAMPLES:
        st.caption("🔒 **Privacy mode** — only column names and types are sent to the "
                   "model. No data values leave this machine.")

    render_trace_panel()

st.title("Ask your data a question")

if not ss.tables:
    st.info("Upload one or more CSV/Excel files in the sidebar to start. "
            "Sample files are in `data/samples/`.")
    st.stop()

st.caption(
    f"{len(ss.tables)} table(s) loaded · answers are computed by DuckDB from generated SQL, "
    "which you can inspect and edit under every result."
)

with st.expander("What's in these files", expanded=not ss.answers):
    render_overview(ss.tables)
    if ss.metrics:
        st.markdown("**Agreed definitions** — from `config/metrics.toml`; answers use these "
                    "instead of the model's own reading:")
        for m in ss.metrics:
            st.markdown(f"- **{m['name'].replace('_', ' ')}** — {m['meaning']}  \n"
                        f"  `{m['expression']}`")
    if ss.joins:
        st.markdown("**Detected joins** — how these files connect:")
        for j in ss.joins[:8]:
            st.markdown(
                f"- `{j['left']}` = `{j['right']}` · {int(j['overlap'] * 100)}% of values overlap"
            )

if not ss.suggestions and not ss.answers:
    try:
        ss.suggestions = engine.suggest_questions(ss.schema, ss.joins_text)
    except Exception:
        ss.suggestions = []

if ss.suggestions and not ss.answers:
    st.write("**Try one of these:**")
    # two across, not four -- four columns truncate the question to "What is the tot…"
    for row_start in range(0, len(ss.suggestions), 2):
        for col, q in zip(st.columns(2), ss.suggestions[row_start : row_start + 2]):
            if col.button(q, key=f"sug_{q[:40]}", use_container_width=True):
                ss.pending = q

for idx, answer in enumerate(ss.answers):
    with st.chat_message("user"):
        st.write(answer.question)
    with st.chat_message("assistant"):
        render_answer(ss, idx, answer)

typed = st.chat_input("e.g. average order value by region")
question = typed or ss.pending
ss.pending = None

if question:
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Writing SQL…"):
            history = [(a.question, a.sql) for a in ss.answers if a.sql]
            key = (question, ss.signature)
            if key in ss.cache:
                answer = ss.cache[key]
            else:
                answer = engine.ask(question, ss.con, ss.schema, ss.joins_text, history,
                                    metrics=ss.metrics)
                ss.cache[key] = answer
                try:
                    engine.log_answer(answer, TRACE_PATH)
                except OSError:
                    pass  # a full disk must not cost the user their answer
    ss.answers.append(answer)
    st.rerun()
