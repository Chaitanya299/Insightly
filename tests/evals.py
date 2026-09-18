"""Measure the delta: python tests/evals.py [config ...] [--no-naive] [--repeat N]

Twenty questions with answers computed independently in pandas, run against:

  * the full system, and the full system with one component switched off at a
    time -- so each piece of engineering is scored by what breaks without it;
  * the naive approach (CSV text pasted into the prompt), on a 100-row subset,
    because the full files do not fit in a single request on the free tier.

"Correct" means correct by the organisation's definition in config/metrics.toml
(revenue is net of refunds). A gross total is a wrong answer here, exactly as it
would be to the finance team that wrote the definition.

Writes docs/evals.md. Runs at the speed of the API's rate limit: on Groq's free
tier (8k tokens/minute) the full run takes roughly half an hour.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import engine  # noqa: E402
import profiling  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
METRICS = ROOT / "config" / "metrics.toml"
SUBSET_ROWS = 100


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
    text = str(v).strip()
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


# --------------------------------------------------------------------------
# the questions, with answers derived from the data rather than typed in
# --------------------------------------------------------------------------

@dataclass
class Case:
    question: str
    tags: set[str]
    expect: object  # (sales, customers, products) -> checker
    decline: bool = False


def _frames(sales_raw, customers_raw, products_raw):
    sales, _ = profiling.clean_frame(sales_raw)
    customers, _ = profiling.clean_frame(customers_raw)
    products, _ = profiling.clean_frame(products_raw)
    joined = sales.merge(customers, left_on="customer_id", right_on="id").merge(products, on="sku")
    net = sales[sales.status == "Completed"]
    jnet = joined[joined.status == "Completed"]
    return sales, customers, net, jnet


def _month(net, y, m):
    d = net[(net.order_date.dt.year == y) & (net.order_date.dt.month == m)]
    return float(d.amount.sum())


def _best_month(net, y) -> str:
    return f"{y}-{max(range(1, 13), key=lambda m: _month(net, y, m)):02d}"


CASES = [
    Case("What is the total revenue?", {"definition"},
         lambda s, c, n, j: scalar(n.amount.sum())),
    Case("How many orders are in the data, including refunded ones?", set(),
         lambda s, c, n, j: count(len(s))),
    Case("How many orders were refunded?", set(),
         lambda s, c, n, j: count(int((s.status == "Refunded").sum()))),
    Case("What is the refund rate?", {"definition"},
         lambda s, c, n, j: scalar(float((s.status == "Refunded").mean()), pct=True)),
    Case("What is the average order value?", {"definition"},
         lambda s, c, n, j: scalar(n.amount.mean())),
    Case("Average order value by region", {"definition", "join"},
         lambda s, c, n, j: keyed(j.groupby("region").amount.mean().to_dict())),
    Case("Revenue by customer segment", {"definition", "join"},
         lambda s, c, n, j: keyed(j.groupby("segment").amount.sum().to_dict())),
    Case("What are the top 5 product categories by revenue?", {"definition", "join"},
         lambda s, c, n, j: ranked(list(j.groupby("category").amount.sum().nlargest(5).index))),
    Case("Compare revenue between the North and South regions", {"definition", "join"},
         lambda s, c, n, j: keyed({r: j[j.region == r].amount.sum() for r in ("North", "South")})),
    Case("What was the revenue in March 2024?", {"definition", "dates"},
         lambda s, c, n, j: scalar(_month(n, 2024, 3))),
    Case("What was the revenue in May 2024?", {"definition", "dates"},
         lambda s, c, n, j: scalar(_month(n, 2024, 5))),
    Case("Which month in 2024 had the highest revenue?", {"definition", "dates"},
         lambda s, c, n, j: ranked([_best_month(n, 2024)])),
    Case("Revenue by month in 2024", {"definition", "dates"},
         lambda s, c, n, j: keyed({f"2024-{m:02d}": _month(n, 2024, m)
                                   for m in range(1, 13) if _month(n, 2024, m)})),
    Case("How many units of Laptops were sold, excluding refunded orders?", {"join"},
         lambda s, c, n, j: scalar(float(j[j.category == "Laptops"].quantity.sum()))),
    Case("What is the average discount percentage on completed orders?", {"types"},
         lambda s, c, n, j: scalar(float(n.discount.mean()))),
    Case("Which region has the most customers?", set(),
         lambda s, c, n, j: ranked([c.region.value_counts().idxmax()])),
    Case("How much revenue came from Enterprise customers in the North region?",
         {"definition", "join"},
         lambda s, c, n, j: scalar(j[(j.segment == "Enterprise") & (j.region == "North")].amount.sum())),
    Case("How many customers placed more than 3 orders, counting refunded orders too?", set(),
         lambda s, c, n, j: count(int((s.groupby("customer_id").size() > 3).sum()))),
    Case("What is our employee headcount?", {"decline"}, None, decline=True),
    Case("What was our marketing spend last quarter?", {"decline"}, None, decline=True),
]


# --------------------------------------------------------------------------
# configurations
# --------------------------------------------------------------------------

CONFIGS = {
    "full":             dict(),
    "no_type_recovery": dict(recover_types=False),
    "no_date_detection": dict(detect_orientation=False),
    "no_join_hints":    dict(joins=False),
    "no_definitions":   dict(metrics=False),
    "privacy_mode":     dict(samples=False),
}


def _upload(name: str, df: pd.DataFrame):
    buf = io.BytesIO(df.to_csv(index=False).encode())
    buf.name = name
    return buf


def system_context(raw: dict[str, pd.DataFrame], recover_types=True, detect_orientation=True,
                   joins=True, metrics=True, samples=True):
    con = engine.connect()
    uploads = [_upload(f"{name}.csv", df) for name, df in raw.items()]
    tables, problems = profiling.load_files(uploads, con, recover_types=recover_types,
                                            detect_orientation=detect_orientation)
    assert not problems, problems
    return {
        "con": con,
        "schema": profiling.schema_text(tables, samples=samples),
        "joins": profiling.joins_text(profiling.discover_joins(con, tables)) if joins else "",
        "metrics": engine.load_metrics(METRICS, tables) if metrics else [],
    }


def ask_system(ctx, question, client):
    a = engine.ask(question, ctx["con"], ctx["schema"], ctx["joins"],
                   client=client, metrics=ctx["metrics"])
    status = "error" if a.error else "declined" if not a.sql else "answered"
    return status, (None if status != "answered" else a.df), a.tokens, a.error


NAIVE_SYSTEM = """You are a data analyst. The user's files are below as CSV text.
Answer the question by reading and computing from the data.
Return ONLY JSON: {"columns": [...], "rows": [[...], ...], "explanation": "..."}
holding the result table. Use "rows": null if the files cannot answer the question."""


def ask_naive(raw: dict[str, pd.DataFrame], question, client):
    files = "\n\n".join(f"--- {name}.csv ---\n{df.to_csv(index=False)}" for name, df in raw.items())
    meter: dict = {}
    try:
        reply = engine._complete(client, [
            {"role": "system", "content": NAIVE_SYSTEM},
            {"role": "user", "content": f"{files}\n\nQUESTION: {question}"},
        ], meter)
    except Exception as exc:
        return "error", None, meter.get("tokens", 0), str(exc)[:200]
    rows = reply.get("rows")
    if rows is None:
        return "declined", None, meter.get("tokens", 0), None
    try:
        cols = reply.get("columns") or [f"c{i}" for i in range(len(rows[0]) if rows else 0)]
        df = pd.DataFrame(rows, columns=cols)
    except Exception as exc:
        return "error", None, meter.get("tokens", 0), f"unparseable table: {exc}"[:200]
    return "answered", df, meter.get("tokens", 0), None


# --------------------------------------------------------------------------
# running and scoring
# --------------------------------------------------------------------------

@dataclass
class Run:
    name: str
    data: str
    results: list[dict] = field(default_factory=list)
    at: str = ""

    @property
    def score(self) -> int:
        return sum(1 for r in self.results if r["passed"])

    @property
    def ran(self) -> int:
        return sum(1 for r in self.results if r["status"] != "not_run")


def grade(case: Case, checker, status: str, df) -> bool:
    if case.decline:
        return status == "declined"
    return status == "answered" and checker(df)


# Once the API refuses for quota reasons, every further call is wasted and every
# further "error" is about the account, not the system under test. Scoring those
# as wrong answers is how the first full run reported the naive approach at 0/20
# when it had not been run at all.
_quota_exhausted = False


def _is_quota(err: str | None) -> bool:
    text = (err or "").lower()
    return "429" in text or "rate limit" in text or "tokens per day" in text


def run_config(name, data_label, raw, frames, asker, client, repeat) -> Run:
    global _quota_exhausted
    run = Run(name, data_label, at=f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    for case in CASES:
        checker = None if case.decline else case.expect(*frames)
        for _ in range(repeat):
            if _quota_exhausted:
                status, df, tokens, err = "not_run", None, 0, "skipped: API quota exhausted"
            else:
                status, df, tokens, err = asker(case.question, client)
                if status == "error" and _is_quota(err):
                    _quota_exhausted = True
                    status = "not_run"
            passed = None if status == "not_run" else grade(case, checker, status, df)
            run.results.append({"question": case.question, "tags": sorted(case.tags),
                                "status": status, "passed": passed, "tokens": tokens,
                                "error": _redact(err) if err else None})
            mark = {True: "✓", False: "✗", None: "·"}[passed]
            print(f"  {mark} [{name}] {case.question[:62]:<62} {status}", flush=True)
    return run


def _redact(text: str) -> str:
    """API errors carry the account's organisation id; it must not land in a pushed report."""
    return re.sub(r"\borg_[A-Za-z0-9]+", "org_[redacted]", text or "")


def naive_full_probe(raw, client) -> str:
    """Try the naive approach once on the full files, and report what happens.

    A 413 is the finding (the request is too large to send at all); a 429 only
    means the account is out of quota, which says nothing about the approach.
    """
    status, _, _, err = ask_naive(raw, CASES[0].question, client)
    if status == "error" and _is_quota(err):
        return "not run: API quota exhausted"
    return status if status != "error" else f"error: {_redact(err)}"


def save(path: Path, runs: list[Run], probe, notes: list[str] | None = None) -> None:
    path.write_text(json.dumps({
        "model": engine.MODEL,
        "probe": probe,
        "notes": notes or [],
        "runs": [{"name": r.name, "data": r.data, "at": r.at, "results": r.results} for r in runs],
    }, indent=1))


def load(path: Path) -> tuple[list[Run], str | None]:
    if not path.exists():
        return [], None
    raw = json.loads(path.read_text())
    return [Run(r["name"], r["data"], r["results"], r.get("at", "")) for r in raw["runs"]], raw.get("probe")


def notes(path: Path) -> list[str]:
    """Free-text provenance notes kept alongside the results, e.g. how a run was recovered."""
    return json.loads(path.read_text()).get("notes", []) if path.exists() else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("configs", nargs="*", default=list(CONFIGS), help=f"subset of {list(CONFIGS)}")
    ap.add_argument("--no-naive", action="store_true", help="skip the naive comparison")
    ap.add_argument("--subset-only", action="store_true",
                    help="run only the naive-vs-full comparison on the subset")
    ap.add_argument("--merge", action="store_true",
                    help="keep earlier results from docs/evals.json for runs not repeated now")
    ap.add_argument("--render-only", action="store_true",
                    help="make no API calls; re-render the report from docs/evals.json")
    ap.add_argument("--repeat", type=int, default=1, help="runs per question (the model is not deterministic)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "evals.md"))
    ap.add_argument("--json", default=str(ROOT / "docs" / "evals.json"))
    args = ap.parse_args()
    json_path = Path(args.json)

    if args.render_only:
        runs, probe = load(json_path)
        Path(args.out).write_text(render(runs, probe, notes(json_path)))
        print(Path(args.out).read_text())
        return

    client = engine._client(max_retries=12)  # sit out per-minute 429s rather than fail them

    full_raw = {
        "sales": pd.read_csv(SAMPLES / "sales.csv"),
        "customers": pd.read_excel(SAMPLES / "customers.xlsx"),
        "products": pd.read_csv(SAMPLES / "products.csv"),
    }
    sub_sales = full_raw["sales"].head(SUBSET_ROWS)
    sub_raw = {
        "sales": sub_sales,
        "customers": full_raw["customers"][full_raw["customers"]["id"].isin(sub_sales["customer_id"])],
        "products": full_raw["products"],
    }
    full_frames = _frames(*full_raw.values())
    sub_frames = _frames(*sub_raw.values())

    runs: list[Run] = []
    for name in ([] if args.subset_only else args.configs):
        print(f"\n== {name} (full data) ==", flush=True)
        ctx = system_context(full_raw, **CONFIGS[name])
        runs.append(run_config(name, "full", full_raw, full_frames,
                               lambda q, cl, ctx=ctx: ask_system(ctx, q, cl), client, args.repeat))

    probe = None
    if not args.no_naive:
        print("\n== naive on full data (single probe) ==", flush=True)
        probe = naive_full_probe(full_raw, client)
        print(f"  {probe[:160]}", flush=True)
        print(f"\n== full system, {SUBSET_ROWS}-row subset ==", flush=True)
        ctx = system_context(sub_raw)
        runs.append(run_config("full", f"{SUBSET_ROWS}-row subset", sub_raw, sub_frames,
                               lambda q, cl: ask_system(ctx, q, cl), client, args.repeat))
        print(f"\n== naive, {SUBSET_ROWS}-row subset ==", flush=True)
        runs.append(run_config("naive", f"{SUBSET_ROWS}-row subset", sub_raw, sub_frames,
                               lambda q, cl: ask_naive(sub_raw, q, cl), client, args.repeat))

    if args.merge:
        earlier, earlier_probe = load(json_path)
        # A fresh run replaces an earlier one only if it actually got answers:
        # a run the quota refused outright must not erase real results.
        replacing = {(r.name, r.data) for r in runs if r.ran}
        have = {(r.name, r.data) for r in earlier}
        runs = ([r for r in earlier if (r.name, r.data) not in replacing]
                + [r for r in runs if r.ran or (r.name, r.data) not in have])
        # a probe that did not run must not overwrite one that did
        if probe is None or probe.startswith("not run"):
            probe = earlier_probe or probe
    kept = notes(json_path) if args.merge else []
    save(json_path, runs, probe, kept)
    report = render(runs, probe, kept)
    Path(args.out).write_text(report)
    print("\n" + report)


def _order(runs: list[Run]) -> list[Run]:
    rank = {name: i for i, name in enumerate(list(CONFIGS) + ["naive"])}
    return sorted(runs, key=lambda r: (r.data != "full", rank.get(r.name, 99)))


def render(runs: list[Run], probe, notes: list[str] | None = None) -> str:
    runs = _order(runs)
    full = next((r for r in runs if r.name == "full" and r.data == "full"), None)
    model = engine.MODEL
    lines = [
        "# Evaluation results",
        "",
        f"Rendered {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python tests/evals.py` "
        f"from `docs/evals.json` · model `{model}` · {len(CASES)} questions.",
        "",
        "Correct means correct by `config/metrics.toml` (revenue is net of refunds). "
        "Expected answers are computed in pandas, not typed in. "
        "**Not run** means the API refused for quota reasons; those questions are excluded, "
        "never scored as wrong.",
        "",
        "## Score by configuration",
        "",
        "| Configuration | Data | Correct | Tokens / question | Run at |",
        "|---|---|---|---|---|",
    ]
    for r in runs:
        toks = [x["tokens"] for x in r.results if x.get("tokens")]
        avg = f"{sum(toks) // len(toks):,}" if toks else "—"
        missing = len(r.results) - r.ran
        score = f"**{r.score} / {r.ran}**" + (f" ({missing} not run)" if missing else "")
        if not r.ran:
            score = f"not run ({missing} skipped)"
        lines.append(f"| `{r.name}` | {r.data} | {score} | {avg} | {r.at} |")
    if probe is not None:
        lines += ["", f"Naive approach on the **full** files: `{probe[:220]}`"]

    if full:
        lines += ["", "## What each component is worth", "",
                  "Change in correct answers when one component is switched off, "
                  "and which questions it cost:", ""]
        full_pass = {x["question"] for x in full.results if x["passed"]}
        for r in runs:
            if r is full or r.data != "full":
                continue
            passed = {x["question"] for x in r.results if x["passed"]}
            lost = sorted(full_pass - passed)
            gained = sorted(passed - full_pass)
            lines.append(f"**`{r.name}`**: {r.score - full.score:+d} "
                         f"(loses {len(lost)}, gains {len(gained)})")
            lines += [f"- ✗ {q}" for q in lost]
            lines += [f"- ✓ {q} *(passes here, fails in full)*" for q in gained]
            lines.append("")

    lines += ["## Every question", "",
              "| Question | " + " | ".join(f"`{r.name}`{' (sub)' if r.data != 'full' else ''}"
                                          for r in runs) + " |",
              "|---|" + "---|" * len(runs)]
    for case in CASES:
        cells = []
        for r in runs:
            hits = [x for x in r.results if x["question"] == case.question]
            ran = [x for x in hits if x["status"] != "not_run"]
            ok = sum(1 for x in ran if x["passed"])
            if not ran:
                cells.append("—")
            elif ok == len(ran):
                cells.append("✓")
            elif ok == 0:
                cells.append(f"✗ {ran[0]['status']}")
            else:
                cells.append(f"{ok}/{len(ran)}")
        lines.append(f"| {case.question} | " + " | ".join(cells) + " |")

    lines += ["", "## Caveats", "",
              "- One run per question unless stated. The model is not deterministic even at "
              "temperature 0, so a one-question gap between configurations is within noise.",
              "- The naive baseline runs on a 100-row subset because the full files exceed the "
              "free tier's per-request token limit. The subset is the naive approach's best case.",
              "- Twenty questions on one synthetic dataset. This shows what each component does "
              "*here*. A component scoring the same with and without it has not been shown to "
              "be useless, only not exercised by these questions."]
    lines += [f"- {n}" for n in (notes or [])]
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
