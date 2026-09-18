"""Ingestion, cleaning and profiling.

Files land in DuckDB as real tables. What the LLM later sees is only the
*schema card* produced here -- never the rows. Everything in this module
exists to make that card accurate enough that a model can write correct SQL
against it blind.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

# ponytail: 0.9 = "if 90% of non-null values parse, the column is that type".
# Tunable knob, not a law -- real files have a few junk rows per column.
COERCE_THRESHOLD = 0.9

_MONEY = re.compile(r"[$€£¥₹,%\s]")
_PARENS_NEG = re.compile(r"^\((.*)\)$")
_KEYISH = re.compile(r"(^|_)(id|key|code|sku|no|num|ref)s?$", re.I)


@dataclass
class Column:
    name: str
    dtype: str
    nulls_pct: float
    distinct: int
    samples: list
    spark: list[float] | None = None   # shape of the column, for a mini chart
    spark_kind: str = ""               # histogram | time | top | ""
    span: str = ""                     # "Jan 2024 - Dec 2025", "0 - 8", ""


@dataclass
class Table:
    name: str
    source: str
    rows: int
    columns: list[Column]
    notes: list[str] = field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------

def clean_column_name(raw: str, taken: set[str]) -> str:
    """'Order Date ' -> 'order_date'. Deduped against names already taken."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", str(raw).strip().lower()).strip("_")
    if not name:
        name = "column"
    if name[0].isdigit():
        name = f"c_{name}"
    base, n = name, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def coerce_numeric(s: pd.Series) -> pd.Series | None:
    """'$1,234.50' -> 1234.5, '(99)' -> -99.0. None if the column isn't numeric."""
    if not pd.api.types.is_object_dtype(s) and not pd.api.types.is_string_dtype(s):
        return None
    non_null = int(s.notna().sum())
    if non_null == 0:
        return None
    txt = s.astype("string").str.strip()
    txt = txt.str.replace(_PARENS_NEG, r"-\1", regex=True)
    txt = txt.str.replace(_MONEY, "", regex=True)
    out = pd.to_numeric(txt, errors="coerce")
    if int(out.notna().sum()) / non_null >= COERCE_THRESHOLD:
        return out
    return None


_SHORT_DATE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\s*$")


def detect_dayfirst(txt: pd.Series) -> bool | None:
    """Is a d/m/y vs m/d/y column day-first? None = genuinely ambiguous.

    Decided from the rows that *can't* be ambiguous: if any leading component
    is > 12 it must be a day, so the whole column is day-first.
    """
    parts = txt.str.extract(_SHORT_DATE)
    if parts[0].dropna().empty:
        return None
    first = pd.to_numeric(parts[0], errors="coerce")
    second = pd.to_numeric(parts[1], errors="coerce")
    if bool((first > 12).any()):
        return True
    if bool((second > 12).any()):
        return False
    return None


def coerce_datetime(s: pd.Series) -> tuple[pd.Series | None, str | None]:
    """Mixed-format date strings -> datetime64, plus a note if anything was ambiguous.

    Parsed in two passes because one global `dayfirst` flag cannot describe a
    column holding both `20/05/2024` and `2024-11-02` -- pandas would flip the
    ISO row too. Short slash/dash dates get the detected orientation; everything
    else is parsed on its own terms.
    """
    if not pd.api.types.is_object_dtype(s) and not pd.api.types.is_string_dtype(s):
        return None, None
    non_null = int(s.notna().sum())
    if non_null == 0:
        return None, None
    txt = s.astype("string").str.strip()
    # A date needs some structure. Without this, short codes like 'Q1' or '12'
    # get swallowed by the parser and a category column silently becomes a date.
    if float((txt.str.len() >= 6).mean()) < COERCE_THRESHOLD:
        return None, None

    is_short = txt.str.match(_SHORT_DATE).fillna(False)
    dayfirst = detect_dayfirst(txt)
    try:
        out = pd.to_datetime(txt.where(~is_short), errors="coerce", format="mixed")
        if bool(is_short.any()):
            short = pd.to_datetime(
                txt.where(is_short), errors="coerce", format="mixed",
                dayfirst=bool(dayfirst),
            )
            out = out.fillna(short)
    except (ValueError, TypeError):
        return None, None

    if int(out.notna().sum()) / non_null < COERCE_THRESHOLD:
        return None, None

    note = None
    if bool(is_short.any()):
        if dayfirst is None:
            note = "ambiguous D/M vs M/D dates, assumed month-first"
        elif dayfirst:
            note = "day-first dates detected (e.g. 20/05/2024)"
    return out, note


def clean_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Normalise headers and recover real types. Returns (df, human notes)."""
    notes: list[str] = []
    taken: set[str] = set()
    renames = {}
    for raw in df.columns:
        clean = clean_column_name(raw, taken)
        renames[raw] = clean
        if str(raw) != clean:
            notes.append(f"renamed `{raw}` -> `{clean}`")
    df = df.rename(columns=renames)
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")

    for col in df.columns:
        num = coerce_numeric(df[col])
        if num is not None:
            df[col] = num
            notes.append(f"`{col}` text -> number")
            continue
        dt, dt_note = coerce_datetime(df[col])
        if dt is not None:
            df[col] = dt
            notes.append(f"`{col}` text -> date" + (f" ({dt_note})" if dt_note else ""))
    return df, notes


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _frames(upload) -> list[tuple[str, pd.DataFrame]]:
    """One upload -> [(suffix, df)]. Each Excel sheet becomes its own table."""
    name = getattr(upload, "name", str(upload))
    if hasattr(upload, "seek"):
        upload.seek(0)  # Streamlit re-serves the same buffer on every rerun
    if name.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raw = upload.read() if hasattr(upload, "read") else open(upload, "rb").read()
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None)
        if len(sheets) == 1:
            return [("", next(iter(sheets.values())))]
        return [(f"_{s}", d) for s, d in sheets.items()]
    return [("", pd.read_csv(upload))]


def explain_read_error(exc: Exception) -> str:
    """Turn a pandas exception into something a non-engineer can act on."""
    name = type(exc).__name__
    if name == "EmptyDataError":
        return "the file is empty"
    if name == "UnicodeDecodeError":
        return "this doesn't look like text -- is it really a CSV?"
    if name == "ParserError":
        return "malformed CSV (unclosed quote, or rows with differing column counts)"
    if isinstance(exc, (KeyError, ValueError)) and "sheet" in str(exc).lower():
        return "the workbook has no readable sheet"
    return f"{name}: {exc}".strip()[:160]


def load_files(uploads, con) -> tuple[list[Table], list[str]]:
    """Load uploads into DuckDB `con`. Returns (tables, problems).

    Each file is isolated. One unreadable file must not take the session down
    with it -- somebody dropping their own messy CSV alongside four good ones
    should get a note about that one file, not a traceback that loses all five.
    """
    tables: list[Table] = []
    problems: list[str] = []
    taken: set[str] = set()
    for upload in uploads:
        source = getattr(upload, "name", str(upload)).split("/")[-1]
        stem = re.sub(r"\.[^.]+$", "", source)
        try:
            frames = _frames(upload)
        except Exception as exc:
            problems.append(f"**{source}** skipped -- {explain_read_error(exc)}")
            continue

        loaded = 0
        for suffix, df in frames:
            label = f"{source}{suffix}" if suffix else source
            if df.empty or not len(df.columns):
                problems.append(f"**{label}** skipped -- no rows in it")
                continue
            try:
                df, notes = clean_frame(df)
                if df.empty:
                    problems.append(f"**{label}** skipped -- no rows left after dropping empty ones")
                    continue
                table_name = clean_column_name(stem + suffix, taken)
                con.register("_staging", df)
                con.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM _staging')
                con.unregister("_staging")
            except Exception as exc:
                problems.append(f"**{label}** skipped -- {explain_read_error(exc)}")
                continue
            tables.append(Table(table_name, source, len(df), profile(df), notes))
            loaded += 1

        if not loaded and not any(source in p for p in problems):
            problems.append(f"**{source}** skipped -- nothing readable in it")
    return tables, problems


SPARK_BINS = 14
SPARK_SAMPLE = 50_000  # profiling a visual doesn't need every row of a huge file


def _spark(s: pd.Series, dtype: str, distinct: int) -> tuple[list[float] | None, str]:
    """A dozen-odd numbers describing the column's shape, for a mini chart.

    Not statistics -- a picture. It answers "what did I actually upload?" at a
    glance: where the numbers cluster, whether the dates have a gap, which
    categories dominate.
    """
    clean = s.dropna()
    if clean.empty or distinct < 2:
        return None, ""
    if len(clean) > SPARK_SAMPLE:
        clean = clean.sample(SPARK_SAMPLE, random_state=0)

    if dtype == "DATE":
        counts = clean.dt.to_period("M").value_counts().sort_index()
        return [float(v) for v in counts.tolist()], "time"

    if dtype in ("INTEGER", "DECIMAL"):
        if _KEYISH.search(str(s.name)):
            return None, ""  # an id's distribution means nothing
        try:
            counts = pd.cut(clean, bins=SPARK_BINS, duplicates="drop").value_counts().sort_index()
        except (ValueError, TypeError):
            return None, ""
        return [float(v) for v in counts.tolist()], "histogram"

    # Frequencies only mean something when values repeat. 20 product names in 20
    # rows would draw twenty bars of height one, which is noise wearing a chart.
    if distinct <= 30 and distinct <= len(clean) / 2:
        counts = clean.value_counts().head(SPARK_BINS)
        return [float(v) for v in counts.tolist()], "top"
    return None, ""


def _span(s: pd.Series, dtype: str) -> str:
    clean = s.dropna()
    if clean.empty:
        return ""
    if dtype == "DATE":
        return f"{clean.min():%b %Y} – {clean.max():%b %Y}"
    if dtype in ("INTEGER", "DECIMAL"):
        lo, hi = float(clean.min()), float(clean.max())
        whole = dtype == "INTEGER" or abs(hi) >= 100
        fmt = (lambda v: f"{v:,.0f}") if whole else (lambda v: f"{v:,.2f}")
        return f"{fmt(lo)} – {fmt(hi)}"
    return ""


def profile(df: pd.DataFrame) -> list[Column]:
    cols = []
    for name in df.columns:
        s = df[name]
        dtype = _pretty_dtype(s)
        distinct = int(s.nunique(dropna=True))
        spark, kind = _spark(s, dtype, distinct)
        cols.append(
            Column(
                name=str(name),
                dtype=dtype,
                nulls_pct=round(float(s.isna().mean()) * 100, 1),
                distinct=distinct,
                samples=[_short(v) for v in s.dropna().unique()[:3].tolist()],
                spark=spark,
                spark_kind=kind,
                span=_span(s, dtype),
            )
        )
    return cols


def _pretty_dtype(s: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(s):
        return "DATE"
    if pd.api.types.is_integer_dtype(s):
        return "INTEGER"
    if pd.api.types.is_float_dtype(s):
        return "DECIMAL"
    if pd.api.types.is_bool_dtype(s):
        return "BOOLEAN"
    return "TEXT"


def _short(v, width: int = 24) -> str:
    text = str(v)
    if isinstance(v, pd.Timestamp):
        text = v.date().isoformat()
    return text if len(text) <= width else text[: width - 1] + "…"


# --------------------------------------------------------------------------
# cross-file join discovery
# --------------------------------------------------------------------------

def _join_candidates(t: Table) -> list[Column]:
    """Columns that could plausibly be keys: near-unique, or named like a key."""
    out = []
    for c in t.columns:
        if c.dtype in ("DATE", "DECIMAL", "BOOLEAN") or c.distinct < 2:
            continue
        near_unique = t.rows and c.distinct / t.rows > 0.9
        if near_unique or _KEYISH.search(c.name):
            out.append(c)
    return out


def _name_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a.endswith(b) or b.endswith(a):
        return 0.6
    return 0.0


def discover_joins(con, tables: list[Table], min_overlap: float = 0.3) -> list[dict]:
    """Find likely join keys between tables.

    Scored by *containment* (overlap / smaller side), not Jaccard: a foreign key
    is many-to-one, so 5000 orders against 200 customers has near-zero Jaccard
    but containment ~1.0. Jaccard would miss every real FK.
    """
    joins = []
    for i, ta in enumerate(tables):
        for tb in tables[i + 1 :]:
            for ca in _join_candidates(ta):
                for cb in _join_candidates(tb):
                    score = _overlap(con, ta.name, ca.name, tb.name, cb.name)
                    if score is None:
                        continue
                    confidence = score * (0.7 + 0.3 * _name_score(ca.name, cb.name))
                    if score >= min_overlap and confidence >= min_overlap:
                        joins.append(
                            {
                                "left": f"{ta.name}.{ca.name}",
                                "right": f"{tb.name}.{cb.name}",
                                "overlap": round(score, 2),
                                "confidence": round(confidence, 2),
                            }
                        )
    joins.sort(key=lambda j: -j["confidence"])
    return joins


def _overlap(con, ta: str, ca: str, tb: str, cb: str, sample: int = 5000) -> float | None:
    sql = f'''
        WITH a AS (SELECT DISTINCT CAST("{ca}" AS VARCHAR) v FROM "{ta}"
                   WHERE "{ca}" IS NOT NULL LIMIT {sample}),
             b AS (SELECT DISTINCT CAST("{cb}" AS VARCHAR) v FROM "{tb}"
                   WHERE "{cb}" IS NOT NULL LIMIT {sample})
        SELECT (SELECT count(*) FROM a),
               (SELECT count(*) FROM b),
               (SELECT count(*) FROM a JOIN b USING (v))
    '''
    try:
        n_a, n_b, shared = con.execute(sql).fetchone()
    except Exception:
        return None
    if not n_a or not n_b:
        return None
    return shared / min(n_a, n_b)


# --------------------------------------------------------------------------
# prompt rendering
# --------------------------------------------------------------------------

def schema_text(tables: list[Table]) -> str:
    """The compact schema card handed to the model. Rows never appear here."""
    out = []
    for t in tables:
        out.append(f'TABLE "{t.name}"  -- from {t.source}, {t.rows:,} rows')
        width = max((len(c.name) for c in t.columns), default=4)
        for c in t.columns:
            bits = [f"distinct={c.distinct}"]
            if c.nulls_pct:
                bits.append(f"nulls={c.nulls_pct}%")
            samples = ", ".join(c.samples)
            out.append(f"  {c.name:<{width}}  {c.dtype:<8} {' '.join(bits):<22} e.g. {samples}")
        out.append("")
    return "\n".join(out)


def joins_text(joins: list[dict]) -> str:
    if not joins:
        return "-- no cross-file join keys detected"
    lines = ["-- likely join keys (detected by value overlap):"]
    for j in joins[:12]:
        lines.append(f"--   {j['left']} = {j['right']}  ({int(j['overlap'] * 100)}% overlap)")
    return "\n".join(lines)
