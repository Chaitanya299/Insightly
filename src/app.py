"""Streamlit UI: upload files, ask questions, see the SQL behind every answer."""

from __future__ import annotations

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


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def state():
    ss = st.session_state
    ss.setdefault("con", engine.connect())
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
    return ss


def rebuild(ss, uploads):
    """Reload everything when the set of uploaded files changes."""
    ss.con = engine.connect()
    ss.tables, ss.problems = profiling.load_files(uploads, ss.con)
    ss.joins = profiling.discover_joins(ss.con, ss.tables)
    ss.schema = profiling.schema_text(ss.tables)
    ss.joins_text = profiling.joins_text(ss.joins)
    ss.answers, ss.cache, ss.suggestions = [], {}, []


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_chart(df: pd.DataFrame, chart: dict):
    kind = chart["type"]
    if kind == "metric":
        value = df[chart["y"]].iloc[0]
        label = str(chart["y"]).replace("_", " ").title()
        st.metric(label, f"{value:,.2f}" if isinstance(value, float) else f"{value:,}")
        return

    x, y, series = chart.get("x"), chart.get("y"), chart.get("series")
    common = dict(color_discrete_sequence=PALETTE)
    if kind == "bar":
        fig = px.bar(df, x=x, y=y, color=series, **common)
    elif kind == "line":
        fig = px.line(df, x=x, y=y, color=series, markers=len(df) <= 40, **common)
    elif kind == "area":
        fig = px.area(df, x=x, y=y, color=series, **common)
    elif kind == "scatter":
        fig = px.scatter(df, x=x, y=y, color=series, **common)
    elif kind == "pie":
        fig = px.pie(df, names=x, values=y, **common)
    else:
        return
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=380,
        xaxis_title=None,
        yaxis_title=None,
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_answer(ss, idx: int, answer: engine.Answer):
    if answer.error:
        st.error(answer.error)
    if answer.explanation:
        st.markdown(answer.explanation)
    if answer.repaired:
        st.caption("↻ the first query errored; it was repaired automatically and re-run")

    if answer.df is not None and not answer.df.empty:
        if answer.chart:
            render_chart(answer.df, answer.chart)
        if not (answer.chart and answer.chart["type"] == "metric"):
            st.dataframe(answer.df, use_container_width=True, hide_index=True)
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

    for table in ss.tables:
        with st.expander(f"{table.name} · {table.rows:,} rows"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {"column": c.name, "type": c.dtype, "nulls": f"{c.nulls_pct}%"}
                        for c in table.columns
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            if table.notes:
                st.caption("**Cleaned on import**\n\n" + "\n".join(f"- {n}" for n in table.notes))

    if ss.joins:
        st.subheader("Detected joins")
        for j in ss.joins[:8]:
            st.caption(f"`{j['left']}` = `{j['right']}` · {int(j['overlap'] * 100)}% overlap")

st.title("Ask your data a question")

if not ss.tables:
    st.info("Upload one or more CSV/Excel files in the sidebar to start. "
            "Sample files are in `data/samples/`.")
    st.stop()

st.caption(
    f"{len(ss.tables)} table(s) loaded · answers are computed by DuckDB from generated SQL, "
    "which you can inspect and edit under every result."
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
                answer = engine.ask(question, ss.con, ss.schema, ss.joins_text, history)
                ss.cache[key] = answer
    ss.answers.append(answer)
    st.rerun()
