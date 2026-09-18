"""Answer checkers shared by every eval suite: one per answer shape.

Kept apart from the harness so a suite module can import them without importing
the harness (which imports the suites).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


# --------------------------------------------------------------------------
# checkers -- one per answer shape, shared by every configuration
# --------------------------------------------------------------------------

def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, 0.001 * abs(b))


def _numbers(values) -> list[float]:
    out = []
    for v in values:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            if pd.notna(v):
                out.append(float(v))
            continue
        try:  # the naive baseline returns "$1,234.50"; be generous to it
            out.append(float(str(v).replace("$", "").replace(",", "").replace("%", "").strip()))
        except ValueError:
            pass
    return out


def _key(v) -> str:
    """Normalise a category or a month so '2024-03-01', Timestamp and 'March 2024' agree."""
    if isinstance(v, (pd.Timestamp, datetime)):
        return f"{v:%Y-%m}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # a year computed in SQL can come back as 2024.0
    text = re.sub(r"^fy\s*", "", str(v).strip(), flags=re.I)  # "FY2024" -> "2024"
    if len(text) >= 6 and any(ch.isdigit() for ch in text):
        try:
            return f"{pd.to_datetime(text):%Y-%m}"
        except (ValueError, TypeError):
            pass
    return text.lower()


def scalar(expected: float, pct: bool = False):
    def check(df):
        if df is None or df.empty or len(df) > 3:
            return False
        targets = [expected, expected * 100] if pct else [expected]
        return any(_close(n, t) for n in _numbers(df.to_numpy().ravel()) for t in targets)
    return check


def count(expected: int):
    def check(df):
        if df is None:
            return False
        if len(df) == expected:          # "which customers ..." -> the rows themselves
            return True
        return len(df) == 1 and any(_close(n, expected) for n in _numbers(df.iloc[0]))
    return check


def _key_column(df, keys: list[str]):
    for col in df.columns:
        vals = [_key(v) for v in df[col]]
        if set(keys) <= set(vals):
            return vals
    return None


def keyed(expected: dict):
    keys = {_key(k): v for k, v in expected.items()}

    def check(df):
        if df is None or df.empty:
            return False
        for col in df.columns:
            vals = [_key(v) for v in df[col]]
            if not set(keys) <= set(vals):
                continue
            ok = True
            for k, want in keys.items():
                row = df.iloc[vals.index(k)]
                if not any(_close(n, want) for n in _numbers(row)):
                    ok = False
                    break
            if ok:
                return True
        return False
    return check


def ranked(expected: list):
    keys = [_key(k) for k in expected]

    def check(df):
        if df is None or df.empty:
            return False
        vals = _key_column(df, keys)
        return vals is not None and vals[: len(keys)] == keys
    return check


def declines(df):
    return df is None  # caller passes None only when the system declined


@dataclass
class Case:
    question: str
    tags: set[str]
    expect: object  # (sales, customers, products) -> checker
    decline: bool = False
