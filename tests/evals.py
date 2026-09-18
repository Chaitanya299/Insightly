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
import os
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


from eval_checks import Case, count, declines, keyed, ranked, scalar  # noqa: E402,F401


# --------------------------------------------------------------------------
# the sample suite -- questions with answers derived from the data
# --------------------------------------------------------------------------

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
    # Privacy mode removes the sample values a model might match keys by, which
    # leaves join hints (names only, no values) as the one way to find the key.
    "privacy_no_join_hints": dict(samples=False, joins=False),
}


def _upload(name: str, df: pd.DataFrame):
    buf = io.BytesIO(df.to_csv(index=False).encode())
    buf.name = name
    return buf


def system_context(raw: dict[str, pd.DataFrame], metrics_path=METRICS, recover_types=True,
                   detect_orientation=True, joins=True, metrics=True, samples=True):
    con = engine.connect()
    uploads = [_upload(f"{name}.csv", df) for name, df in raw.items()]
    tables, problems = profiling.load_files(uploads, con, recover_types=recover_types,
                                            detect_orientation=detect_orientation)
    assert not problems, problems
    return {
        "con": con,
        "schema": profiling.schema_text(tables, samples=samples),
        "joins": profiling.joins_text(profiling.discover_joins(con, tables)) if joins else "",
        "metrics": engine.load_metrics(metrics_path, tables) if metrics else [],
    }


def ask_system(ctx, question, client):
    a = engine.ask(question, ctx["con"], ctx["schema"], ctx["joins"],
                   client=client, metrics=ctx["metrics"])
    status = "error" if a.error else "declined" if not a.sql else "answered"
    return status, (None if status != "answered" else a.df), a.tokens, a.error, a.served_by


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
        return "error", None, meter.get("tokens", 0), str(exc)[:200], meter.get("served", [])
    served = meter.get("served", [])
    rows = reply.get("rows")
    if rows is None:
        return "declined", None, meter.get("tokens", 0), None, served
    try:
        cols = reply.get("columns") or [f"c{i}" for i in range(len(rows[0]) if rows else 0)]
        df = pd.DataFrame(rows, columns=cols)
    except Exception as exc:
        return "error", None, meter.get("tokens", 0), f"unparseable table: {exc}"[:200], served
    return "answered", df, meter.get("tokens", 0), None, served


# --------------------------------------------------------------------------
# running and scoring
# --------------------------------------------------------------------------

@dataclass
class Run:
    name: str
    data: str
    results: list[dict] = field(default_factory=list)
    at: str = ""
    provider: str = ""
    model: str = ""

    @property
    def served(self) -> list[str]:
        return sorted({m for r in self.results for m in r.get("served_by", [])})

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


MAX_WAIT_S = 20 * 60   # longest cooldown worth sitting out; beyond this, stop
MAX_WAITS = 4          # cooldowns sat out per question before giving up on it


def _is_daily(err: str | None) -> bool:
    """A daily quota does not come back in minutes; waiting for it is pointless."""
    text = (err or "").lower()
    return any(s in text for s in ("per day", "(tpd)", "(rpd)", "daily"))


def retry_after_s(err: str | None) -> float | None:
    """Seconds until a rate limit lifts, if the error says. None if it doesn't.

    Routers and providers each say it differently: FreeLLMAPI puts `retryAtMs`
    in the body ("cooldown reset ~8m"); Groq says "try again in 7m12.5s".
    """
    text = err or ""
    m = re.search(r"retryAtMs['\"]?\s*:\s*(\d{12,})", text)
    if m:
        return max(0.0, int(m.group(1)) / 1000 - time.time())
    m = re.search(r"(?:reset|again in)\s*~?\s*(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", text, re.I)
    if m and any(m.groups()):
        h, mins, secs = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mins * 60 + secs
    return None


def _is_quota(err: str | None) -> bool:
    text = (err or "").lower()
    if text.startswith(("query failed", "blocked")):
        return False  # the SQL engine's own errors, whatever digits they contain
    return any(s in text for s in ("429", "rate limit", "tokens per day", "quota", "exhausted"))


def run_config(name, data_label, raw, frames, asker, client, repeat, cases=None) -> Run:
    global _quota_exhausted
    run = Run(name, data_label, at=f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
              provider=_ACTIVE["provider"], model=engine.MODEL)
    for case in cases or CASES:
        checker = None if case.decline else case.expect(*frames)
        for _ in range(repeat):
            served: list = []
            if _quota_exhausted:
                status, df, tokens, err = "not_run", None, 0, "skipped: API quota exhausted"
            else:
                for waits in range(MAX_WAITS + 1):
                    status, df, tokens, err, *extra = asker(case.question, client)
                    served = extra[0] if extra else []
                    if not (status == "error" and _is_quota(err)):
                        break
                    # A cooldown with a stated reset is worth waiting out; a daily
                    # quota, or a wait longer than the cap, is not.
                    wait = retry_after_s(err)
                    # A router reports the pool: "groq: daily_quota_exhausted ... soonest
                    # cooldown reset ~10m" means another member is back in 10 minutes.
                    # Its retryAtMs is authoritative; only a bare daily-quota error ends the run.
                    daily = _is_daily(err) and "retryatms" not in (err or "").lower()
                    if daily or wait is None or wait > MAX_WAIT_S or waits == MAX_WAITS:
                        _quota_exhausted = True
                        status = "not_run"
                        break
                    print(f"  … rate-limited, waiting {wait / 60:.1f} min for the cooldown", flush=True)
                    time.sleep(wait + 5)
            passed = None if status == "not_run" else grade(case, checker, status, df)
            run.results.append({"question": case.question, "tags": sorted(case.tags),
                                "status": status, "passed": passed, "tokens": tokens,
                                "served_by": sorted(set(served)),
                                "error": _redact(err) if err else None})
            mark = {True: "✓", False: "✗", None: "·"}[passed]
            print(f"  {mark} [{name}] {case.question[:62]:<62} {status}", flush=True)
    return run


def _redact(text: str) -> str:
    """API errors carry the account's organisation id; it must not land in a pushed report."""
    return re.sub(r"\borg_[A-Za-z0-9]+", "org_[redacted]", text or "")


def naive_full_probe(raw, client, question: str | None = None) -> str:
    """Try the naive approach once on the full files, and report what happens.

    A 413 is the finding (the request is too large to send at all); a 429 only
    means the account is out of quota, which says nothing about the approach.
    """
    status, _, _, err, *_ = ask_naive(raw, question or CASES[0].question, client)
    if status == "error" and _is_quota(err):
        return "not run: API quota exhausted"
    return status if status != "error" else f"error: {_redact(err)}"


def save(path: Path, runs: list[Run], probe, notes: list[str] | None = None) -> None:
    path.write_text(json.dumps({
        "model": engine.MODEL,
        "probe": probe,
        "notes": notes or [],
        "runs": [{"name": r.name, "data": r.data, "at": r.at, "provider": r.provider,
                  "model": r.model, "results": r.results} for r in runs],
    }, indent=1))


def load(path: Path) -> tuple[list[Run], str | None]:
    if not path.exists():
        return [], None
    raw = json.loads(path.read_text())
    return [Run(r["name"], r["data"], r["results"], r.get("at", ""),
                r.get("provider", "groq"), r.get("model", raw.get("model", "")))
            for r in raw["runs"]], raw.get("probe")


def notes(path: Path) -> list[str]:
    """Free-text provenance notes kept alongside the results, e.g. how a run was recovered."""
    return json.loads(path.read_text()).get("notes", []) if path.exists() else []


@dataclass
class Suite:
    name: str
    load: object         # () -> {table: raw DataFrame, as uploaded}
    frames: object       # raw -> the tuple each case's `expect` takes
    cases: list
    metrics: Path
    out: Path
    subset: object = None  # raw -> smaller raw for the naive run; None = naive sees everything
    configs: list | None = None  # default configurations; None = all of CONFIGS


def _sample_load() -> dict[str, pd.DataFrame]:
    return {"sales": pd.read_csv(SAMPLES / "sales.csv"),
            "customers": pd.read_excel(SAMPLES / "customers.xlsx"),
            "products": pd.read_csv(SAMPLES / "products.csv")}


def _sample_subset(raw):
    sales = raw["sales"].head(SUBSET_ROWS)
    return {"sales": sales,
            "customers": raw["customers"][raw["customers"]["id"].isin(sales["customer_id"])],
            "products": raw["products"]}


def suites() -> dict[str, Suite]:
    import eval_hard  # imported here: it needs src/ on the path, set up above

    return {
        "sample": Suite("sample", _sample_load, lambda raw: _frames(*raw.values()), CASES,
                        METRICS, ROOT / "docs" / "evals.md", _sample_subset),
        # The hard data's dates are all ISO, so date detection cannot change an answer,
        # and type recovery was already measured on the sample suite: running either
        # here would spend quota on configurations that cannot tell us anything new.
        "hard": Suite("hard", eval_hard.load, eval_hard.frames, eval_hard.CASES,
                      eval_hard.METRICS, ROOT / "docs" / "evals-hard.md", None,
                      configs=["full", "no_join_hints", "no_definitions", "privacy_mode",
                               "privacy_no_join_hints"]),
    }


# The API the harness talks to. Production is Groq; FreeLLMAPI is a local router
# stacking many providers' free tiers, useful when Groq's daily quota runs out.
PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "model_env": "GROQ_MODEL"},
    "freellmapi": {"base_url": os.getenv("FREELLMAPI_URL", "http://localhost:3001/v1"),
                   "key_env": "FREELLMAPI_API_KEY", "key_aliases": ["FREE_LLM_API"],
                   "model_env": "FREELLMAPI_MODEL"},
}
_ACTIVE = {"provider": "groq"}


def _key(spec: dict) -> str | None:
    for env in [spec["key_env"], *spec.get("key_aliases", [])]:
        if os.getenv(env):
            return os.getenv(env)
    return None


def connect_provider(name: str, model: str | None, allow_routing: bool):
    spec = PROVIDERS[name]
    key = _key(spec)
    if not key:
        sys.exit(f"{spec['key_env']} is not set. Add it to .env (never pass keys on the command line).")
    model = model or os.getenv(spec["model_env"]) or (engine.MODEL if name == "groq" else None)
    if not model:
        sys.exit(f"Pick a model: --model <id> or {spec['model_env']} in .env. "
                 f"`python tests/evals.py --provider {name} --list-models` shows what's available.")
    # An ablation compares configurations. If a router picks a different model
    # per request, the comparison measures the router, not the component.
    if (model.startswith("auto") or model == "fusion") and not allow_routing:
        sys.exit(f"'{model}' lets the router choose a different model per request, which "
                 "breaks the comparison between configurations. Pin one model, or pass "
                 "--allow-routing if you accept that.")
    engine.MODEL = model
    _ACTIVE["provider"] = name
    return engine._client(max_retries=12, base_url=spec["base_url"], api_key=key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("configs", nargs="*", help=f"subset of {list(CONFIGS)} (default: the suite's own)")
    ap.add_argument("--suite", choices=["sample", "hard"], default="sample")
    ap.add_argument("--provider", choices=list(PROVIDERS), default="groq")
    ap.add_argument("--model", help="model id to pin (default: provider's *_MODEL env var)")
    ap.add_argument("--list-models", action="store_true", help="list the provider's models and exit")
    ap.add_argument("--allow-routing", action="store_true", help="permit auto-routed models")
    ap.add_argument("--no-naive", action="store_true", help="skip the naive comparison")
    ap.add_argument("--subset-only", action="store_true",
                    help="run only the naive comparison (and its full-system counterpart)")
    ap.add_argument("--merge", action="store_true",
                    help="keep earlier results for runs not repeated now")
    ap.add_argument("--render-only", action="store_true",
                    help="make no API calls; re-render the report from the saved results")
    ap.add_argument("--repeat", type=int, default=1, help="runs per question (the model is not deterministic)")
    ap.add_argument("--out")
    ap.add_argument("--json")
    args = ap.parse_args()

    suite = suites()[args.suite]
    out = Path(args.out or suite.out)
    json_path = Path(args.json or out.with_suffix(".json"))

    if args.render_only:
        runs, probe = load(json_path)
        out.write_text(render(runs, probe, notes(json_path), suite))
        print(out.read_text())
        return

    if args.list_models:
        spec = PROVIDERS[args.provider]
        key = _key(spec) or sys.exit(f"{spec['key_env']} is not set.")
        client = engine._client(max_retries=0, base_url=spec["base_url"], api_key=key)
        for m in sorted(m.id for m in client.models.list().data):
            print(m)
        return

    client = connect_provider(args.provider, args.model, args.allow_routing)
    print(f"suite {suite.name} · provider {args.provider} · model {engine.MODEL}", flush=True)

    raw = suite.load()
    frames = suite.frames(raw)
    runs: list[Run] = []
    configs = args.configs or suite.configs or list(CONFIGS)
    for name in ([] if args.subset_only else configs):
        print(f"\n== {name} (full data) ==", flush=True)
        ctx = system_context(raw, suite.metrics, **CONFIGS[name])
        runs.append(run_config(name, "full", raw, frames,
                               lambda q, cl, ctx=ctx: ask_system(ctx, q, cl), client, args.repeat,
                               suite.cases))

    probe = None
    if not args.no_naive:
        if suite.subset is not None:
            print("\n== naive on full data (single probe) ==", flush=True)
            probe = naive_full_probe(raw, client, suite.cases[0].question)
            print(f"  {probe[:160]}", flush=True)
            sub_raw = suite.subset(raw)
            sub_frames = suite.frames(sub_raw)
            label = f"{SUBSET_ROWS}-row subset"
            print(f"\n== full system, {label} ==", flush=True)
            ctx = system_context(sub_raw, suite.metrics)
            runs.append(run_config("full", label, sub_raw, sub_frames,
                                   lambda q, cl: ask_system(ctx, q, cl), client, args.repeat,
                                   suite.cases))
        else:
            sub_raw, sub_frames, label = raw, frames, "full"
        print(f"\n== naive, {label} ==", flush=True)
        runs.append(run_config("naive", label, sub_raw, sub_frames,
                               lambda q, cl: ask_naive(sub_raw, q, cl), client, args.repeat,
                               suite.cases))

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
    report = render(runs, probe, kept, suite)
    out.write_text(report)
    print("\n" + report)


def _order(runs: list[Run]) -> list[Run]:
    rank = {name: i for i, name in enumerate(list(CONFIGS) + ["naive"])}
    return sorted(runs, key=lambda r: (r.data != "full", rank.get(r.name, 99)))


def _model_cell(r: Run) -> str:
    """What the run asked for, and what actually answered if that differs."""
    served = r.served
    cell = f"`{r.model}`" if r.model else "—"
    if len(served) > 1:
        cell += " ⚠ answered by " + ", ".join(f"`{m}`" for m in served)
    elif served and served[0] != r.model:
        cell += f" via `{served[0]}`"
    return cell


def render(runs: list[Run], probe, notes: list[str] | None = None, suite: "Suite | None" = None) -> str:
    runs = _order(runs)
    cases = suite.cases if suite else CASES
    name = suite.name if suite else "sample"
    source = (suite.out.with_suffix(".json").relative_to(ROOT) if suite else "docs/evals.json")
    metrics = (suite.metrics.relative_to(ROOT) if suite else "config/metrics.toml")
    full = next((r for r in runs if r.name == "full" and r.data == "full"), None)
    lines = [
        f"# Evaluation results — `{name}` suite",
        "",
        f"Rendered {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python tests/evals.py "
        f"--suite {name}` from `{source}` · {len(cases)} questions.",
        "",
        f"Correct means correct by `{metrics}`. Expected answers are computed in pandas, not "
        "typed in. **Not run** means the API refused for quota reasons; those questions are "
        "excluded, never scored as wrong.",
        "",
        "## Score by configuration",
        "",
        "| Configuration | Data | Correct | Tokens / question | Model | Run at |",
        "|---|---|---|---|---|---|",
    ]
    for r in runs:
        toks = [x["tokens"] for x in r.results if x.get("tokens")]
        avg = f"{sum(toks) // len(toks):,}" if toks else "—"
        missing = len(r.results) - r.ran
        score = f"**{r.score} / {r.ran}**" + (f" ({missing} not run)" if missing else "")
        if not r.ran:
            score = f"not run ({missing} skipped)"
        lines.append(f"| `{r.name}` | {r.data} | {score} | {avg} | {_model_cell(r)} | {r.at} |")
    models = {r.model for r in runs if r.ran}
    if len(models) > 1:
        lines += ["", "⚠ **These runs used different models** (" + ", ".join(f"`{m}`" for m in sorted(models))
                  + "). Differences between them are not ablations; compare only rows with the same model."]
    if any(len(r.served) > 1 for r in runs):
        lines += ["", "⚠ **At least one run was answered by more than one model** (a router failed "
                  "over mid-run). Its score mixes models."]
    if probe is not None:
        lines += ["", f"Naive approach on the **full** files: `{probe[:220]}`"]

    if full:
        lines += ["", "## What each component is worth", "",
                  "Change in correct answers when one component is switched off, "
                  "and which questions it cost:", ""]
        full_pass = {x["question"] for x in full.results if x["passed"]}
        for r in runs:
            if r is full or r.data != "full" or r.name == "naive" or not r.ran:
                continue
            if r.model != full.model:
                lines += [f"**`{r.name}`**: not comparable — run on `{r.model}`, full on `{full.model}`", ""]
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
    for case in cases:
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
              f"- {len(cases)} questions on one synthetic dataset. This shows what each component "
              "does *here*. A component scoring the same with and without it has not been shown "
              "to be useless, only not exercised by these questions."]
    if suite is None or suite.subset is not None:
        lines.insert(len(lines) - 1, "- The naive baseline runs on a 100-row subset because the "
                     "full files exceed the free tier's per-request token limit. The subset is the "
                     "naive approach's best case.")
    lines += [f"- {n}" for n in (notes or [])]
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
