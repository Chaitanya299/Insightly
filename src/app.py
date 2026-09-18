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
# Tokens from DESIGN.md.
INK, INK_2, MUTED = "#0E1B2C", "#5B6472", "#8A93A0"
GRID, BASELINE, SURFACE = "#E3E6E8", "#C9CED3", "#FFFFFF"
VERIFIED, VERIFIED_TINT = "#0F7B5F", "#E6F2EE"
BODY_FONT = "Satoshi, system-ui, sans-serif"
MONO_FONT = "JetBrains Mono, ui-monospace, monospace"
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
            st.metric(_pretty(col), "—", border=True, width="content")
            return
        prefix = "$" if _money(col) else ""
        text = f"{prefix}{value:,.2f}" if isinstance(value, float) else f"{prefix}{value:,}"
        st.metric(_pretty(col), text, border=True, width="content")
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
        font=dict(family=BODY_FONT, color=INK_2, size=12),
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
                         tickfont=dict(color=MUTED, family=MONO_FONT, size=11))
        fig.update_xaxes(gridcolor=GRID, showgrid=False, linecolor=BASELINE,
                         tickfont=dict(color=MUTED, family=MONO_FONT, size=11))
        if kind == "bar":
            fig.update_traces(textfont=dict(family=MONO_FONT, size=11, color=INK_2))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    if note:
        st.caption(note)


# What each mini chart shows, in words a first-time user can read.
_SPARK_LABEL = {
    "time": "rows per month",
    "histogram": "spread, low → high",
    "top": "most common first",
}


def _describe(c) -> str:
    """The words next to a mini chart: its range, or its actual top values."""
    if c.spark_kind == "top" and c.top:
        text = " · ".join(c.top)
    elif c.span:
        text = c.span
    else:
        text = f"{c.distinct:,} different values"
    if c.nulls_pct:
        text += f"  ·  {c.nulls_pct:g}% empty"
    return text


def render_overview(tables: list) -> None:
    """What did I actually upload? One row per column, with a picture of its shape.

    The picture answers "was this read right?" before anything is asked: where the
    numbers cluster, whether the dates have a gap, which values dominate.
    """
    st.caption(
        "Each column gets a small picture of its data. **Rows per month** shows how the "
        "dates are spread, and a gap or spike means missing or duplicated data. **Spread, "
        "low → high** is a histogram, low values on the left and high on the right. **Most "
        "common first** shows how often each value appears. The exact values and shares are listed "
        "beside it. Ids and free-text columns get no picture, because their shape means nothing."
    )
    for table in tables:
        dates = [c for c in table.columns if c.dtype == "DATE" and c.span]
        head = f"**{table.name}** · {table.rows:,} rows · {len(table.columns)} columns"
        if dates:
            head += f" · {dates[0].name} spans {dates[0].span}"
        st.markdown(head)

        rows = [{
            "column": c.name,
            "type": {"TEXT": "text", "INTEGER": "number", "DECIMAL": "number",
                     "DATE": "date"}.get(c.dtype, c.dtype.lower()),
            "picture": c.spark or [],
            "values": (f"{_SPARK_LABEL[c.spark_kind]}: " if c.spark_kind in _SPARK_LABEL else "")
                      + _describe(c),
        } for c in table.columns]
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "column": st.column_config.TextColumn(width="medium"),
                "type": st.column_config.TextColumn(width="small"),
                "picture": st.column_config.BarChartColumn(
                    y_min=0, width="medium",
                    help="Bar heights are row counts. Read it with the next column."),
                "values": st.column_config.TextColumn(
                    "what the picture shows", width="large",
                    help="Numbers and dates: lowest – highest. Categories: the most common values and their share of rows."),
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


WHY_CSS = """
<style>
  .why { display:flex; flex-wrap:wrap; align-items:stretch; gap:1rem; margin:.4rem 0 .6rem; }
  .why-card { flex:1 1 260px; border:1px solid #E3E6E8; border-radius:14px; padding:1.25rem 1.4rem;
              background:#FFFFFF; display:flex; flex-direction:column; gap:.8rem; }
  .why-card.win { border:1.5px solid #2A78D6; box-shadow:0 6px 24px rgba(42,120,214,.10); }
  .why-name { font-weight:700; font-size:1.05rem; color:#0E1B2C; }
  .why-flow { font-family:'JetBrains Mono',ui-monospace,monospace; font-size:.85rem; color:#5B6472;
              background:#F6F7F5; border-radius:10px; padding:.8rem; line-height:1.8; text-align:center; }
  .why-score { font-family:'JetBrains Mono',ui-monospace,monospace; font-variant-numeric:tabular-nums;
               font-size:2.3rem; font-weight:600; letter-spacing:-.03em; line-height:1; color:#0E1B2C; }
  .why-score small { font-family:Satoshi,sans-serif; font-size:.95rem; font-weight:500; color:#5B6472; margin-left:.4rem; }
  .why-bar { height:6px; border-radius:3px; background:#EEF0EE; overflow:hidden; }
  .why-bar span { display:block; height:100%; border-radius:3px; animation:why-fill .7s ease-out both; }
  @keyframes why-fill { from { width:0 } }
  @media (prefers-reduced-motion: reduce) { .why-bar span { animation:none } }
  .why-list { margin:0; padding-left:1.1rem; color:#5B6472; font-size:.9rem; line-height:1.65; }
  .why-vs { align-self:center; font-family:'JetBrains Mono',monospace; font-size:.8rem;
            letter-spacing:.08em; color:#8A93A0; padding:0 .2rem; }
</style>
"""


def naive_vs_full() -> dict | None:
    """The measured head-to-head from docs/evals.json: same questions, same rows."""
    path = ROOT / "docs" / "evals.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    runs = {(r["name"], r["data"]): [x for x in r["results"] if x["status"] != "not_run"]
            for r in raw.get("runs", [])}
    subset = next((d for (n, d) in runs if n == "naive" and d != "full"), None)
    naive, full = runs.get(("naive", subset)), runs.get(("full", subset))
    if not naive or not full:
        return None

    def tokens(rs):
        t = [x["tokens"] for x in rs if x.get("tokens")]
        return round(sum(t) / max(len(t), 1))

    missed = [x for x in naive if not x["passed"]]
    return {
        "subset": subset, "of": len(naive),
        "naive": sum(x["passed"] for x in naive), "full": sum(x["passed"] for x in full),
        "naive_tokens": tokens(naive), "full_tokens": tokens(full),
        "wrong": sum(x["status"] == "answered" for x in missed),
        "malformed": sum(x["status"] == "error" for x in missed),
        "declined": sum(x["status"] == "declined" for x in missed),
        "too_large": "413" in (raw.get("probe") or ""),
        "model": raw.get("model", ""),
    }


def render_why() -> None:
    """The delta, measured, side by side. Numbers come from the eval results, not from here."""
    m = naive_vs_full()
    if m is None:
        return
    st.markdown("### Why Insightly?")

    def n(k, one, many):
        return f"{m[k]} {one if m[k] == 1 else many}" if m[k] else ""

    naive_points = [n("wrong", "confidently wrong number", "confidently wrong numbers"),
                    n("malformed", "reply that wasn't valid output", "replies that weren't valid output"),
                    n("declined", "question refused that the data could answer",
                      "questions refused that the data could answer"),
                    f"{m['naive_tokens']:,} tokens per question",
                    "Full 900-row files: rejected as too large (413)" if m["too_large"] else ""]
    full_points = ["Every number computed by DuckDB, not the model",
                   "The SQL is shown under every answer, editable",
                   f"{m['full_tokens']:,} tokens per question "
                   f"({m['naive_tokens'] / max(m['full_tokens'], 1):.1f}× fewer)",
                   "Prompt stays 564 characters at 1,000,000 rows"]

    def card(name, flow, score, points, win):
        color = "#2A78D6" if win else "#C23B3B"
        items = "".join(f"<li>{p}</li>" for p in points if p)
        return (f'<div class="why-card{" win" if win else ""}">'
                f'<div class="why-name">{name}</div><div class="why-flow">{flow}</div>'
                f'<div class="why-score">{score} / {m["of"]}<small>correct</small></div>'
                f'<div class="why-bar"><span style="width:{score / m["of"]:.0%};background:{color}"></span></div>'
                f'<ul class="why-list">{items}</ul></div>')

    st.markdown(
        WHY_CSS + '<div class="why">'
        + card("✕ Naive LLM approach", "Rows → LLM → Answer", m["naive"], naive_points, False)
        + '<div class="why-vs">VS</div>'
        + card("✓ Insightly", "Schema → LLM → SQL<br>↓<br>DuckDB<br>↓<br>Verified result",
               m["full"], full_points, True)
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Measured: the same {m['of']} questions on the same {m['subset']}, each answer checked "
               f"against one computed independently in pandas · `{m['model']}` · "
               "`python tests/evals.py --subset-only`")


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
        stamp(f"computed by DuckDB · {len(answer.df):,} row{'s' if len(answer.df) != 1 else ''}")
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

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.6rem; max-width: 1200px; }
      h1 { letter-spacing: -.02em; }
      [data-testid="stMetricValue"] { font-family: 'JetBrains Mono', ui-monospace, monospace;
                                      font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
      [data-testid="stMetric"], [class*="st-key-card"] { background: #FFFFFF; }
      .page-lede { color: #5B6472; font-size: 1.02rem; margin: -.6rem 0 1.4rem; max-width: 46rem; }
      .stamp { display: inline-block; font-family: 'JetBrains Mono', ui-monospace, monospace;
               font-size: .7rem; letter-spacing: .06em; text-transform: uppercase; color: #0F7B5F;
               background: #E6F2EE; border-radius: 999px; padding: .22rem .7rem; margin: .1rem 0 .5rem; }
      .brand { font-weight: 700; font-size: 1.35rem; letter-spacing: -.02em; color: #FFFFFF;
               margin: .2rem 0 .1rem; }
      .brand b { color: #6DA7EC; font-weight: 700; }
      .brand-sub { color: #8D99AB; font-size: .82rem; line-height: 1.45; margin-bottom: 1.1rem; }
      .side-label { font-family: 'JetBrains Mono', monospace; font-size: .66rem; letter-spacing: .1em;
                    text-transform: uppercase; color: #8D99AB; margin: 1.2rem 0 .35rem; }
      .receipt { font-family: 'JetBrains Mono', monospace; font-size: .78rem; color: #C9D2DD;
                 line-height: 1.75; }
      .receipt span { color: #8D99AB; }
      [data-testid="stSidebar"] [data-testid="stPageLink"] a { border-radius: 10px; padding: .15rem .5rem; }
      [data-testid="stSidebar"] [data-testid="stPageLink"] p { font-size: .95rem; }
      .file-name { font-family: 'JetBrains Mono', monospace; font-weight: 600; font-size: .95rem; }
      .file-meta { font-family: 'JetBrains Mono', monospace; font-size: .8rem; color: #5B6472; }
    </style>
    """,
    unsafe_allow_html=True,
)


def stamp(text: str) -> None:
    """The mark that a number came from DuckDB, not from the model (DESIGN.md: green means verified)."""
    st.markdown(f'<span class="stamp">✓ verified · {text}</span>', unsafe_allow_html=True)


def page_header(title: str, lede: str) -> None:
    st.title(title)
    st.markdown(f'<p class="page-lede">{lede}</p>', unsafe_allow_html=True)


def needs_data() -> bool:
    """Empty state for every page but Upload: say what to do next, and link to it."""
    if ss.tables:
        return False
    with st.container(border=True, key="card_empty"):
        st.markdown("**No data loaded yet.** Upload CSV or Excel files, or load the samples, "
                    "and this page fills in.")
        st.page_link(PAGES["upload"], label="Go to 01 · Upload", icon=":material/arrow_forward:")
    return True


def page_upload() -> None:
    page_header("Upload your files",
                "Add several related CSV or Excel files. Each sheet becomes a table, messy money "
                "and dates are cleaned, and the links between files are found automatically.")
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        uploads = st.file_uploader(
            "CSV or Excel files",
            type=["csv", "xlsx", "xls", "xlsm"],
            accept_multiple_files=True,
            help="Upload several related files: one question can span all of them.",
        )
        if uploads:
            signature = tuple(sorted((u.name, u.size) for u in uploads))
            if signature != ss.signature:
                with st.spinner("Reading, cleaning and profiling…"):
                    rebuild(ss, uploads)
                ss.signature = signature
        # ponytail: the uploader forgets its files when you leave this page, so an empty
        # uploader is not a "clear" signal -- clearing is an explicit button instead.
        samples = sorted((ROOT / "data" / "samples").glob("*.*"))
        b1, b2 = st.columns(2)
        if samples and b1.button("Load sample files", width="stretch",
                                 help=" · ".join(f.name for f in samples)):
            with st.spinner("Reading, cleaning and profiling…"):
                rebuild(ss, samples)
            ss.signature = SAMPLES_SIG
            st.rerun()
        if ss.tables and b2.button("Clear loaded data", width="stretch"):
            ss.signature, ss.tables, ss.joins, ss.answers, ss.problems, ss.metrics = None, [], [], [], [], []
            ss.con = engine.connect()
            st.rerun()
        for problem in ss.problems:
            st.warning(problem, icon=":material/warning:")
    with right:
        st.markdown("##### What happens to your files")
        st.markdown(
            "1. **Cleaned.** Headers are normalised, `$1,234.50` becomes a number, and mixed "
            "date formats become real dates.\n"
            "2. **Connected.** Matching columns across files are found, so one question can "
            "span several of them.\n"
            "3. **Kept here.** The model sees column names, types and a few sample values, "
            "never the rows."
            + (" Privacy mode is on, so no values at all." if not SEND_SAMPLES else "")
        )

    if ss.tables:
        st.markdown("### What was loaded")
        stamp(f"{len(ss.tables)} tables · {sum(t.rows for t in ss.tables):,} rows · "
              f"{len(ss.joins)} links between files")
        cols = st.columns(min(3, len(ss.tables)))
        for i, t in enumerate(ss.tables):
            with cols[i % len(cols)].container(border=True, key=f"card_file_{i}"):
                st.markdown(f'<div class="file-name">{t.name}</div>'
                            f'<div class="file-meta">{t.source} · {t.rows:,} rows · '
                            f'{len(t.columns)} columns</div>', unsafe_allow_html=True)
                cleaned = [n for n in t.notes if "renamed" not in n]
                st.caption("Cleaned: " + "; ".join(cleaned) if cleaned else "Nothing needed cleaning.")
        if ss.joins:
            st.caption("Linked: " + " · ".join(f"`{j['left']}` = `{j['right']}`" for j in ss.joins[:6]))
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.page_link(PAGES["ask"], label="Ask a question", icon=":material/arrow_forward:")
        c2.page_link(PAGES["dashboard"], label="See the dashboard", icon=":material/arrow_forward:")
    st.divider()
    render_why()


def view_dashboard() -> None:
    page_header("Dashboard", "Built from rules, not the model: the headline numbers, the monthly "
                "trend and a breakdown that can reach across files. Every query is shown below.")
    if needs_data():
        return
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
    stamp("computed by DuckDB · queries below")
    if measure.meaning:
        st.caption(f"📐 **{_pretty(measure.label)}** uses the agreed definition: {measure.meaning}")

    left, right = st.columns([1.35, 1])
    with left.container(border=True, key="card_trend"):
        st.markdown(f"**{_pretty(measure.label)} by month**" if trend is not None else "**Trend**")
        if trend is not None and not trend.empty:
            render_chart(trend, {"type": "line", "x": "month", "y": measure.label}, height=320)
        else:
            st.caption("No date column in this table.")
    with right.container(border=True, key="card_split"):
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


def render_definitions() -> None:
    """What an agreed definition is, the ones in force, and an editor for them."""
    st.markdown("#### Agreed definitions")
    st.markdown(
        "A business word like **revenue** can mean several things: gross sales, sales net "
        "of refunds, or net of discounts. An **agreed definition** fixes the one your "
        "organisation uses, as a SQL formula plus a plain-English meaning. When a question "
        "uses the word, the model must use this exact formula instead of guessing, and the "
        "dashboard uses it too. Every answer that relied on one says so under the answer."
    )
    st.caption("Measured: without them the model chose a different meaning from the "
               "organisation's in 5 of 17 hard-suite questions. Stored in `config/metrics.toml`.")

    applied = {m["name"] for m in ss.metrics}
    for d in engine.read_definitions(METRICS_PATH):
        mark = "✓ in use" if d["name"] in applied else "○ not used: its table or columns aren't loaded"
        st.markdown(f"- **{d['name'].replace('_', ' ')}** ({mark}): {d['meaning']}  \n"
                    f"  `{d['expression']}` on `{d['table']}`")

    with st.expander("✏️ Edit definitions"):
        st.caption("Edit a cell, add a row at the bottom, or select a row and delete it. The "
                   "formula is an aggregate over one table, like `SUM(amount) FILTER (WHERE "
                   "status = 'Completed')`. Each one is test-run against the loaded data "
                   "before it's saved.")
        current = engine.read_definitions(METRICS_PATH)
        by_name = {d["name"]: d for d in current}
        edited = st.data_editor(
            pd.DataFrame(current or [{"name": "", "table": "", "expression": "", "meaning": ""}],
                         columns=["name", "table", "expression", "meaning"]),
            num_rows="dynamic", hide_index=True, width="stretch", key="defs_editor",
            column_config={
                "name": st.column_config.TextColumn("term", help="e.g. revenue, net_revenue",
                                                    required=True, width="small"),
                "table": st.column_config.SelectboxColumn(
                    "table", options=sorted({t.name for t in ss.tables} | {d["table"] for d in current}),
                    required=True, width="small"),
                "expression": st.column_config.TextColumn("SQL formula", required=True, width="large"),
                "meaning": st.column_config.TextColumn("meaning in plain English", width="large"),
            },
        )
        if st.button("Save definitions", type="primary"):
            checked, errors = [], []
            for row in edited.fillna("").to_dict("records"):
                if not any(str(v).strip() for v in row.values()):
                    continue  # an empty row left by the editor
                row["columns"] = by_name.get(row["name"], {}).get("columns", [])
                d, err = engine.check_definition(ss.con, ss.tables, row)
                (errors.append(err) if err else checked.append(d))
            if len({d["name"] for d in checked}) < len(checked):
                errors.append("Two definitions share a term; each term needs one meaning.")
            if errors:
                st.error("Not saved. Fix these first:\n\n" + "\n".join(f"- {e}" for e in errors))
            else:
                engine.save_definitions(METRICS_PATH, checked)
                ss.metrics = engine.load_metrics(METRICS_PATH, ss.tables)
                ss.cache = {}  # cached answers were written under the old definitions
                st.toast(f"Saved {len(checked)} definitions. New questions use them.", icon="✅")
                st.rerun()


def view_data() -> None:
    page_header("Your data", "What was loaded, how it was cleaned, how the files connect, and "
                "the definitions every answer must use.")
    if needs_data():
        return
    render_overview(ss.tables)
    st.markdown("#### How these files connect")
    st.caption("Found by matching values across files, so one question can span several of them.")
    if ss.joins:
        st.markdown("\n".join(f"- `{j['left']}` = `{j['right']}` · {int(j['overlap'] * 100)}% of values match"
                               for j in ss.joins[:8]))
    else:
        st.caption("No joins detected.")
    st.divider()
    render_definitions()


def view_ask() -> None:
    page_header("Ask a question", "Plain English in; a chart, a table and the SQL behind them "
                "out. The model writes the query and DuckDB computes every number.")
    if needs_data():
        return
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


def view_quality() -> None:
    page_header("Quality", "Measured, not claimed: what each component is worth, how the "
                "naive approach compares, and how the app is being used.")
    render_why()
    st.divider()
    render_evidence()
    st.divider()
    render_usage()


PAGES = {
    "upload": st.Page(page_upload, title="Upload", url_path="upload", default=True),
    "ask": st.Page(view_ask, title="Ask", url_path="ask"),
    "dashboard": st.Page(view_dashboard, title="Dashboard", url_path="dashboard"),
    "data": st.Page(view_data, title="Data", url_path="data"),
    "quality": st.Page(view_quality, title="Quality", url_path="quality"),
}
current = st.navigation(list(PAGES.values()), position="hidden")

with st.sidebar:
    st.markdown('<div class="brand"><b>◆</b> Insightly</div>'
                '<div class="brand-sub">Ask your data a question. Every number comes from SQL '
                'you can read.</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-label">Your journey</div>', unsafe_allow_html=True)
    for i, page in enumerate(PAGES.values(), 1):
        st.page_link(page, label=f"{i:02d}   {page.title}")
    st.markdown('<div class="side-label">Loaded</div>', unsafe_allow_html=True)
    if ss.tables:
        st.markdown('<div class="receipt">' + "<br>".join(
            f"{t.name} <span>· {t.rows:,} rows</span>" for t in ss.tables)
            + f"<br><span>{len(ss.joins)} links · {len(ss.metrics)} definitions</span></div>",
            unsafe_allow_html=True)
    else:
        st.markdown('<div class="receipt"><span>Nothing yet</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="side-label">Engine</div><div class="receipt">'
                f'{engine.MODEL.split("/")[-1]} <span>writes SQL</span><br>DuckDB <span>computes</span>'
                + ("<br>🔒 privacy mode <span>· no values sent</span>" if not SEND_SAMPLES else "")
                + "</div>", unsafe_allow_html=True)

current.run()
