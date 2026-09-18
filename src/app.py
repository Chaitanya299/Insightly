"""Streamlit UI: upload files, ask questions, see the SQL behind every answer."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

# Before importing engine: it reads the model and endpoint settings at import time.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import dashboard  # noqa: E402
import engine  # noqa: E402
import profiling  # noqa: E402

st.set_page_config(page_title="Insightly", page_icon="📊", layout="wide")

# The dataviz reference palette: categorical order is fixed and validated for
# colour-vision deficiency, so series 1 is always blue and hues are never cycled.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
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


def render_chart(df: pd.DataFrame, chart: dict, height: int | None = None):
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
        height=height or (380 if kind != "bar" else max(300, min(520, 60 + 26 * len(data)))),
        xaxis_title=None,
        yaxis_title=None,
        legend_title_text="",
        legend=dict(orientation="h", y=1.08, x=0, font=dict(color=INK_2)),
        separators=".,",
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK_2, size=12),
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(color=INK)),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        barcornerradius=4,
        bargap=0.25,
    )
    if kind in {"line", "area"}:
        fig.update_traces(line=dict(width=2), marker=dict(size=8, line=dict(width=2, color=SURFACE)))
    if kind not in {"pie"}:
        fig.update_yaxes(gridcolor=GRID, zerolinecolor=BASELINE, linecolor=BASELINE,
                         tickfont=dict(color=MUTED))
        fig.update_xaxes(gridcolor=GRID, showgrid=False, linecolor=BASELINE,
                         tickfont=dict(color=MUTED))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
            width="stretch",
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


STATUS_ICON = {"ok": "✓ answered", "repaired": "↻ repaired", "declined": "⊘ declined", "error": "✕ error"}
EVAL_FILES = [
    ("Sample data · gpt-oss-120b", ROOT / "docs" / "evals.json"),
    ("Sample data · current system · gpt-oss-20b", ROOT / "docs" / "evals-current.json"),
    ("Hard data (built to break each component) · gpt-oss-20b", ROOT / "docs" / "evals-hard.json"),
]
CONFIG_LABEL = {
    "full": "Full system", "no_type_recovery": "No type recovery",
    "no_date_detection": "No date detection", "no_join_hints": "No join hints",
    "no_definitions": "No definitions", "privacy_mode": "Privacy mode",
    "privacy_no_join_hints": "Privacy, no join hints", "naive": "Naive (rows in prompt)",
}


def render_usage() -> None:
    """How the app is being used: what was asked, what it cost, what failed."""
    records = engine.read_log(TRACE_PATH, last=500)
    st.markdown("#### Usage")
    if not records:
        st.caption("No questions logged yet. Every question asked lands here, with its SQL.")
        return
    answered = [r for r in records if r["status"] in ("ok", "repaired")]
    latency = sorted(r["latency_ms"] for r in records if r.get("latency_ms"))
    tokens = [r["tokens"] for r in records if r.get("tokens")]
    a, b, c, d = st.columns(4)
    a.metric("Questions", f"{len(records):,}", border=True)
    b.metric("Answered", f"{len(answered) / len(records):.0%}", border=True,
             help="Declines count against this, even when declining was right.")
    c.metric("Median latency", f"{latency[len(latency) // 2] / 1000:.1f} s" if latency else "—",
             border=True)
    d.metric("Tokens / question", f"{sum(tokens) // max(len(tokens), 1):,}", border=True)

    left, right = st.columns(2)
    with left:
        counts = pd.Series([STATUS_ICON.get(r["status"], r["status"]) for r in records])
        by_status = counts.value_counts().rename_axis("outcome").reset_index(name="questions")
        st.caption("Outcomes")
        render_chart(by_status, {"type": "bar", "x": "outcome", "y": "questions"}, height=260)
    with right:
        timeline = pd.DataFrame({"question_no": range(1, len(records) + 1),
                                 "latency_seconds": [(r.get("latency_ms") or 0) / 1000 for r in records]})
        st.caption("Latency per question, oldest first")
        render_chart(timeline, {"type": "line", "x": "question_no", "y": "latency_seconds"}, height=260)

    st.dataframe(
        pd.DataFrame([{
            "when": r["at"][:19].replace("T", " "),
            "question": r["question"],
            "outcome": STATUS_ICON.get(r["status"], r["status"]),
            "rows": r["rows"],
            "tokens": r["tokens"],
            "ms": r["latency_ms"],
            "sql": r.get("sql") or "",
        } for r in reversed(records)]),
        hide_index=True, width="stretch", height=260,
    )
    where = Path(TRACE_PATH)
    st.caption(f"Full trace: `{where.relative_to(ROOT) if where.is_relative_to(ROOT) else where}` "
               "· holds questions and SQL, never result rows.")


def load_eval(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    rows = []
    for r in raw.get("runs", []):
        ran = [x for x in r["results"] if x["status"] != "not_run"]
        if not ran:
            continue
        rows.append({
            "configuration": CONFIG_LABEL.get(r["name"], r["name"])
                             + ("" if r["data"] == "full" else f" · {r['data']}"),
            "correct": sum(1 for x in ran if x["passed"]),
            "of": len(ran),
            # as the report does: a failed call records 0 tokens, which is unmeasured, not free
            "tokens_per_question": round(sum(t := [x["tokens"] for x in ran if x.get("tokens")])
                                         / max(len(t), 1)),
            "full_data": r["data"] == "full",
        })
    return pd.DataFrame(rows) if rows else None


def render_evidence() -> None:
    """The measured delta: each component switched off, and what it cost."""
    st.markdown("#### Evidence")
    st.caption("Expected answers are computed independently in pandas. Each configuration "
               "switches one component off. Quota refusals are excluded, never scored as wrong.")
    available = [(label, df) for label, path in EVAL_FILES if (df := load_eval(path)) is not None]
    if not available:
        st.caption("No eval results found. Run `python tests/evals.py`.")
        return
    label = st.selectbox("Eval suite", [l for l, _ in available], label_visibility="collapsed")
    df = dict(available)[label]
    full = df[df["configuration"] == "Full system"]
    naive = df[df["configuration"].str.startswith("Naive")]
    subset_full = df[df["configuration"].str.startswith("Full system ·")]
    cols = st.columns(3)
    if not full.empty:
        f = full.iloc[0]
        cols[0].metric("Full system", f"{f['correct']} / {f['of']}", border=True)
        # ablations only: the naive baseline runs on a different (smaller) dataset
        worst = df[df["full_data"] & (df["configuration"] != "Full system")].nsmallest(1, "correct")
        if not worst.empty and worst.iloc[0]["correct"] < f["correct"]:
            w = worst.iloc[0]
            cols[1].metric("Biggest drop from one component", f"{int(w['correct'] - f['correct'])} answers",
                           delta=w["configuration"], delta_color="off", delta_arrow="off", border=True)
    if not naive.empty and not subset_full.empty:
        n, sf = naive.iloc[0], subset_full.iloc[0]
        cols[2].metric("Naive vs this system (same rows)", f"{n['correct']} vs {sf['correct']} / {n['of']}",
                       delta=f"{n['tokens_per_question'] / max(sf['tokens_per_question'], 1):.1f}× the tokens",
                       delta_color="off", delta_arrow="off", border=True)
    render_chart(df[["configuration", "correct"]], {"type": "bar", "x": "configuration", "y": "correct"})
    st.dataframe(df.drop(columns="full_data"), hide_index=True, width="stretch",
                 column_config={"of": "questions", "tokens_per_question": "tokens / question"})


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
            st.dataframe(shown, width="stretch", hide_index=True,
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
        if st.button("Load sample files", width="stretch"):
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


st.markdown(
    """
    <style>
      .block-container { padding-top: 3.4rem; max-width: 1180px; }
      .ins-head { display:flex; flex-wrap:wrap; align-items:baseline; column-gap:.75rem; row-gap:.1rem; margin-bottom:.3rem; }
      .ins-logo { font-size:1.55rem; font-weight:700; letter-spacing:-.02em; color:#0b0b0b; }
      .ins-mark { color:#2a78d6; }
      .ins-tag { color:#52514e; font-size:.95rem; }
      .ins-pill { display:inline-block; font-size:.78rem; color:#52514e; border:1px solid #e1e0d9;
                  border-radius:999px; padding:.1rem .6rem; margin-right:.35rem; background:#fff; }
      div[data-testid="stMetric"] { background:#fff; }
    </style>
    <div class="ins-head">
      <span class="ins-logo"><span class="ins-mark">◆</span> Insightly</span>
      <span class="ins-tag">Ask your data a question. Every number comes from SQL you can read.</span>
    </div>
    """,
    unsafe_allow_html=True,
)

if not ss.tables:
    st.markdown("")
    a, b, c = st.columns(3)
    for col, title, body in [
        (a, "1 · Upload", "Drop several CSV or Excel files in the sidebar, or load the samples. "
                          "Each sheet becomes a table; messy money and dates are cleaned."),
        (b, "2 · Ask", "Ask in plain English. The model writes SQL; DuckDB computes the answer. "
                       "The model never sees your rows."),
        (c, "3 · Check", "Every answer shows its chart, table and SQL. Edit the SQL and re-run it "
                         "to check the machine rather than trust it."),
    ]:
        with col.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(body)
    st.stop()

pills = [f"{len(ss.tables)} tables", f"{sum(t.rows for t in ss.tables):,} rows",
         f"{len(ss.joins)} joins found", f"{len(ss.metrics)} agreed definitions",
         "🔒 privacy mode" if not SEND_SAMPLES else "model: " + engine.MODEL.split("/")[-1]]
st.markdown("".join(f'<span class="ins-pill">{p}</span>' for p in pills), unsafe_allow_html=True)
st.markdown("")

VIEWS = ["💬 Ask", "📊 Dashboard", "🗂 Data", "✅ Quality"]
view = st.segmented_control("View", VIEWS, default=VIEWS[0], key="view", required=True,
                            label_visibility="collapsed") or VIEWS[0]


def view_dashboard() -> None:
    facts = dashboard.fact_tables(ss.tables, ss.metrics)
    if not facts:
        st.info("No numeric or date columns to chart. Ask a question instead.")
        return
    c1, c2, c3, c4 = st.columns([1, 1.2, 1.2, 1])
    by_name = {t.name: t for t in facts}
    table = by_name[c1.selectbox("Table", list(by_name), disabled=len(facts) == 1)]
    ms = {m.label: m for m in dashboard.measures(table, ss.metrics)}
    measure = ms[c2.selectbox("Measure", list(ms), format_func=_pretty)]
    dims = {d.label: d for d in dashboard.dimensions(table, ss.tables, ss.joins)}
    # A breakdown by a column the measure already filters on is one bar: skip it by default.
    useful = next((i for i, d in enumerate(dims.values()) if d.column not in measure.expression), 0)
    dim = dims[c3.selectbox("Break down by", list(dims), index=useful, format_func=_pretty)] if dims else None
    date_cols = dashboard.dates(table)
    date_col = c4.selectbox("Date", date_cols, format_func=_pretty) if date_cols else None

    queries = {}
    try:
        queries["Headline"] = dashboard.kpi_sql(table, measure)
        kpi = engine.run_sql(ss.con, queries["Headline"])
        trend = None
        if date_col:
            queries["Trend"] = dashboard.trend_sql(table, measure, date_col)
            trend = engine.run_sql(ss.con, queries["Trend"]).dropna()
        split = None
        if dim:
            queries["Breakdown"] = dashboard.breakdown_sql(table, measure, dim)
            split = engine.run_sql(ss.con, queries["Breakdown"]).dropna()
    except Exception as exc:
        st.error(f"Could not build this view: {exc}")
        return

    value = kpi[measure.label].iloc[0]

    def fmt(v) -> str:
        return dashboard.compact(v, money=_money(measure.label))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(_pretty(measure.label), fmt(value), border=True,
              help=measure.meaning or f"`{measure.expression}`",
              chart_data=trend[measure.label].tolist() if trend is not None and len(trend) > 2 else None,
              chart_type="area")
    if trend is not None and len(trend) >= 2:
        last, prev = trend.iloc[-1], trend.iloc[-2]
        change = (last[measure.label] - prev[measure.label]) / prev[measure.label] if prev[measure.label] else None
        k2.metric(f"{last['month']:%b %Y} (latest)", fmt(last[measure.label]),
                  delta=f"{change:+.1%} vs {prev['month']:%b}" if change is not None else None,
                  border=True, help="The latest month may be incomplete.")
        best = trend.loc[trend[measure.label].idxmax()]
        k3.metric("Best month", f"{best['month']:%b %Y}", fmt(best[measure.label]),
                  delta_color="off", delta_arrow="off", border=True)
    else:
        k2.metric("Rows", f"{int(kpi['row_count'].iloc[0]):,}", border=True)
        k3.metric("Columns", f"{len(table.columns)}", border=True)
    if split is not None and not split.empty:
        top = split.iloc[0]
        share = top[measure.label] / split[measure.label].sum() if split[measure.label].sum() else 0
        k4.metric(f"Top {_pretty(dim.label).lower()}", str(top[dim.label]),
                  f"{share:.0%} of total", delta_color="off", delta_arrow="off", border=True)
    else:
        k4.metric("Rows", f"{int(kpi['row_count'].iloc[0]):,}", border=True)
    if measure.meaning:
        st.caption(f"📐 **{_pretty(measure.label)}** uses the agreed definition: {measure.meaning}")

    left, right = st.columns([1.35, 1])
    with left.container(border=True):
        st.markdown(f"**{_pretty(measure.label)} by month**" if trend is not None else "**Trend**")
        if trend is not None and not trend.empty:
            render_chart(trend, {"type": "line", "x": "month", "y": measure.label}, height=320)
        else:
            st.caption("No date column in this table.")
    with right.container(border=True):
        st.markdown(f"**By {_pretty(dim.label).lower()}**" if dim else "**Breakdown**")
        if split is not None and not split.empty:
            chart = engine.pick_chart(split, {"type": "bar", "x": dim.label, "y": measure.label})
            if chart:
                render_chart(ordered_for_display(split, chart), chart, height=320)
            if dim.on:
                st.caption(f"Joined from `{dim.table}` on `{table.name}.{dim.on[0]}` = "
                           f"`{dim.table}.{dim.on[1]}`")
        else:
            st.caption("No category column to break this down by.")

    with st.expander("SQL behind this dashboard"):
        for name, sql in queries.items():
            st.caption(name)
            st.code(sql, language="sql")


def view_data() -> None:
    render_overview(ss.tables)
    left, right = st.columns(2)
    with left:
        st.markdown("**Agreed definitions**")
        if ss.metrics:
            st.caption("From `config/metrics.toml`. Answers use these instead of the model's own reading.")
            for m in ss.metrics:
                st.markdown(f"- **{m['name'].replace('_', ' ')}**: {m['meaning']}  \n  `{m['expression']}`")
        else:
            st.caption("None apply to these files.")
    with right:
        st.markdown("**How these files connect**")
        if ss.joins:
            for j in ss.joins[:8]:
                st.markdown(f"- `{j['left']}` = `{j['right']}` · {int(j['overlap'] * 100)}% of values overlap")
        else:
            st.caption("No joins detected.")


def view_ask() -> None:
    if not ss.suggestions and not ss.answers:
        try:
            ss.suggestions = engine.suggest_questions(ss.schema, ss.joins_text)
        except Exception:
            ss.suggestions = []

    if ss.suggestions and not ss.answers:
        st.caption("Try one of these")
        # two across, not four -- four columns truncate the question to "What is the tot…"
        for row_start in range(0, len(ss.suggestions), 2):
            for col, q in zip(st.columns(2), ss.suggestions[row_start : row_start + 2]):
                if col.button(q, key=f"sug_{q[:40]}", width="stretch"):
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


if view == VIEWS[1]:
    view_dashboard()
elif view == VIEWS[2]:
    view_data()
elif view == VIEWS[3]:
    render_evidence()
    st.divider()
    render_usage()
else:
    view_ask()
