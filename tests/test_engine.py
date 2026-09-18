"""Self-check: python tests/test_engine.py

Plain asserts, no framework. Covers the parts that are wrong silently if they
break -- the safety guard, type coercion, join detection, and the model->SQL
round trip with a stubbed LLM so it runs without an API key.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine  # noqa: E402
import profiling  # noqa: E402

SAMPLES = ROOT / "data" / "samples"


class StubLLM:
    """Replays canned JSON replies in order, recording what it was asked."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs["messages"])
        body = json.dumps(self.replies.pop(0))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))],
            usage=SimpleNamespace(total_tokens=1000),
        )


def test_guard_blocks_writes():
    for bad in [
        "DROP TABLE sales",
        "UPDATE sales SET amount = 0",
        "SELECT 1; DROP TABLE sales",
        "SELECT * FROM read_csv('/etc/passwd')",
        "  ",
    ]:
        try:
            engine.guard(bad)
            raise AssertionError(f"guard allowed: {bad!r}")
        except engine.UnsafeQuery:
            pass

    # comments are stripped before the statement split, so this is just SELECT 1
    assert "DROP" not in engine.guard("SELECT 1 -- ; DROP TABLE sales").upper()
    wrapped = engine.guard("WITH t AS (SELECT 1 x) SELECT * FROM t")
    assert wrapped.upper().startswith("SELECT") and "LIMIT" in wrapped.upper()


def test_external_file_access_is_off():
    con = engine.connect()
    try:
        con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()
        raise AssertionError("DuckDB still has filesystem access")
    except Exception as exc:
        assert "disabled" in str(exc).lower() or "permission" in str(exc).lower()


def test_numeric_coercion():
    out = profiling.coerce_numeric(pd.Series(["$1,234.50", "(99)", "15%", None]))
    assert out is not None
    assert list(out.dropna()) == [1234.5, -99.0, 15.0]
    # a genuine text column must be left alone
    assert profiling.coerce_numeric(pd.Series(["North", "South", "East"])) is None


def test_date_orientation_is_detected_not_guessed():
    dayfirst = pd.Series(["20/05/2024", "03/05/2024", "14/02/2024"])
    out, note = profiling.coerce_datetime(dayfirst)
    assert out is not None and note is not None
    assert out.iloc[1].month == 5, "03/05/2024 in a day-first column is 3 May"

    monthfirst = pd.Series(["05/20/2024", "05/03/2024", "02/14/2024"])
    out, _ = profiling.coerce_datetime(monthfirst)
    assert out.iloc[1].day == 3, "05/03/2024 in a month-first column is 3 May"

    # short codes must not be swallowed by the date parser
    assert profiling.coerce_datetime(pd.Series(["Q1", "Q2", "Q3"]))[0] is None


def test_column_name_cleaning():
    taken = set()
    assert profiling.clean_column_name("Order Date ", taken) == "order_date"
    assert profiling.clean_column_name("Discount %", taken) == "discount"
    assert profiling.clean_column_name("Order Date", taken) == "order_date_2"


def test_join_discovery_on_fixtures():
    con = engine.connect()
    tables, _ = profiling.load_files(
        [SAMPLES / "sales.csv", SAMPLES / "customers.xlsx", SAMPLES / "products.csv"], con
    )
    assert {t.name for t in tables} == {"sales", "customers", "products"}

    pairs = {frozenset([j["left"], j["right"]]) for j in profiling.discover_joins(con, tables)}
    assert frozenset(["sales.customer_id", "customers.id"]) in pairs
    assert frozenset(["sales.sku", "products.sku"]) in pairs

    # the schema card must carry types and never leak whole rows
    card = profiling.schema_text(tables)
    assert "order_date" in card and "DATE" in card and "Customer 050" not in card


def test_one_bad_file_does_not_kill_the_other_files(tmp=None):
    """The failure that ends a live demo: someone drops in a file pandas can't read."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    (d / "empty.csv").write_bytes(b"")
    (d / "binary.csv").write_bytes(bytes(range(200)) * 8)
    (d / "malformed.csv").write_text('name,value\n"unclosed,5\nx,6\n')
    (d / "headeronly.csv").write_text("col\n")

    con = engine.connect()
    tables, problems = profiling.load_files(
        [SAMPLES / "sales.csv", d / "empty.csv", d / "binary.csv",
         d / "malformed.csv", d / "headeronly.csv", SAMPLES / "customers.xlsx"],
        con,
    )
    # the good files still loaded
    assert {t.name for t in tables} == {"sales", "customers"}
    assert con.execute("SELECT count(*) FROM sales").fetchone()[0] == 900
    # and every bad one was reported in words a non-engineer can act on
    assert len(problems) == 4, problems
    joined = " ".join(problems)
    for expected in ["empty.csv", "binary.csv", "malformed.csv", "headeronly.csv"]:
        assert expected in joined
    assert "Traceback" not in joined and "the file is empty" in joined


def test_chart_picker_reads_result_shape():
    dates = pd.DataFrame({"month": pd.to_datetime(["2024-01-01", "2024-02-01"]), "revenue": [1, 2]})
    assert engine.pick_chart(dates)["type"] == "line"

    cats = pd.DataFrame({"region": list("ABCDE"), "revenue": [5, 4, 3, 2, 1]})
    assert engine.pick_chart(cats)["type"] == "bar"

    assert engine.pick_chart(pd.DataFrame({"total": [42.0]}))["type"] == "metric"
    assert engine.pick_chart(pd.DataFrame()) is None

    # a suggestion naming columns that did not come back is ignored
    picked = engine.pick_chart(cats, {"type": "bar", "x": "nonexistent", "y": "revenue"})
    assert picked["x"] == "region"


def test_chart_picker_sanity_checks_the_models_suggestion():
    """The model saw the schema, not the result, so its chart advice needs checking."""
    wide = pd.DataFrame({
        "customer_id": range(108),
        "customer_name": [f"Customer {i:03d}" for i in range(108)],
        "order_count": [15 - i // 10 for i in range(108)],
    })
    # 108 named bars is a smear -- keep the bar, cap it, and say so in the UI
    picked = engine.pick_chart(wide, {"type": "bar", "x": "customer_name", "y": "order_count"})
    assert picked["type"] == "bar" and picked["limit"] == engine.MAX_BARS

    # an id is numeric but never a quantity: it must not become an axis
    assert engine.pick_chart(pd.DataFrame({"customer_id": [1, 2, 3], "order_id": [9, 8, 7]})) is None
    assert engine.pick_chart(wide)["y"] == "order_count"

    # a pie past a handful of slices is a worse table; demote it rather than draw it
    many = pd.DataFrame({"sku": [f"S-{i}" for i in range(20)], "n": list(range(20, 0, -1))})
    assert engine.pick_chart(many, {"type": "pie", "x": "sku", "y": "n"})["type"] == "bar"
    few = pd.DataFrame({"cat": list("ABCDE"), "n": [5, 4, 3, 2, 1]})
    assert engine.pick_chart(few, {"type": "pie", "x": "cat", "y": "n"})["type"] == "pie"

    # a suggestion whose y is not a measure is rejected
    swapped = pd.DataFrame({"revenue": [5, 3, 1], "region": ["N", "S", "E"]})
    assert engine.pick_chart(swapped, {"type": "bar", "x": "revenue", "y": "region"})["y"] == "revenue"


def test_ask_end_to_end_with_stub_model():
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv", SAMPLES / "customers.xlsx"], con)
    schema, joins = profiling.schema_text(tables), profiling.joins_text(profiling.discover_joins(con, tables))

    sql = ("SELECT region, round(sum(amount), 2) AS revenue FROM sales "
           "JOIN customers ON sales.customer_id = customers.id GROUP BY 1 ORDER BY revenue DESC")
    stub = StubLLM({"sql": sql, "chart": {"type": "bar", "x": "region", "y": "revenue"},
                    "explanation": "Revenue per region."})
    answer = engine.ask("revenue by region", con, schema, joins, client=stub)

    assert answer.error is None and answer.repaired is False
    assert answer.chart["type"] == "bar"
    assert len(answer.df) == 4

    # the number must match what DuckDB computes directly -- no model in the path
    truth = con.execute(
        "SELECT round(sum(amount), 2) FROM sales JOIN customers ON sales.customer_id = customers.id"
    ).fetchone()[0]
    assert round(float(answer.df["revenue"].sum()), 2) == round(float(truth), 2)

    # and the model was shown the schema, never the rows
    prompt = stub.calls[0][1]["content"]
    assert "TABLE \"sales\"" in prompt and "Customer 050" not in prompt


def test_self_repair_retries_once_with_the_error():
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv"], con)
    schema = profiling.schema_text(tables)

    stub = StubLLM(
        {"sql": "SELECT sum(total_amount) AS revenue FROM sales", "explanation": "wrong column"},
        {"sql": "SELECT round(sum(amount), 2) AS revenue FROM sales", "explanation": "fixed"},
    )
    answer = engine.ask("total revenue", con, schema, "", client=stub)

    assert answer.repaired is True and answer.error is None
    assert answer.df["revenue"].iloc[0] > 0
    retry_prompt = stub.calls[1][-1]["content"]
    assert "failed with this DuckDB error" in retry_prompt and "total_amount" in retry_prompt


def test_unanswerable_question_is_declined_not_invented():
    stub = StubLLM({"sql": None, "explanation": "No headcount data in these files."})
    answer = engine.ask("what is our headcount?", engine.connect(), "TABLE sales", "", client=stub)
    assert answer.sql is None and answer.df is None and answer.error is None
    assert "headcount" in answer.explanation.lower()


def test_metric_definitions_only_offered_when_the_upload_can_satisfy_them():
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv"], con)
    metrics = engine.load_metrics(ROOT / "config" / "metrics.toml", tables)
    assert "revenue" in {m["name"] for m in metrics}
    # the agreed expression must produce the agreed number
    revenue = next(m for m in metrics if m["name"] == "revenue")
    value = con.execute(f'SELECT {revenue["expression"]} FROM sales').fetchone()[0]
    assert round(float(value), 2) == 1962664.00

    # no sales table -> no sales definitions, rather than an invitation to invent columns
    only_customers, _ = profiling.load_files([SAMPLES / "customers.xlsx"], engine.connect())
    assert engine.load_metrics(ROOT / "config" / "metrics.toml", only_customers) == []
    assert engine.load_metrics(ROOT / "config" / "missing.toml", tables) == []


def test_metrics_reach_the_prompt_and_invented_names_are_dropped():
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv"], con)
    metrics = engine.load_metrics(ROOT / "config" / "metrics.toml", tables)
    stub = StubLLM({
        "sql": "SELECT SUM(amount) FILTER (WHERE status = 'Completed') AS revenue FROM sales",
        "explanation": "Net revenue.",
        "metrics_used": ["revenue", "definition_the_model_made_up"],
    })
    answer = engine.ask("total revenue", con, profiling.schema_text(tables), "",
                        client=stub, metrics=metrics)
    assert "BUSINESS DEFINITIONS" in stub.calls[0][1]["content"]
    assert answer.metrics_used == ["revenue"]
    assert answer.tokens == 1000 and answer.latency_ms >= 0


def test_privacy_mode_sends_no_data_values():
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv", SAMPLES / "customers.xlsx"], con)
    private = profiling.schema_text(tables, samples=False)
    assert "order_date" in private and "DATE" in private      # structure still there
    for value in ["Completed", "Refunded", "Customer 0", "North", "2016.31"]:
        assert value not in private, value                     # but no values at all
    assert "Completed" in profiling.schema_text(tables)        # and normal mode keeps them


def test_trace_log_records_what_ran_but_not_the_data():
    import tempfile

    path = Path(tempfile.mkdtemp()) / "logs" / "queries.jsonl"
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv"], con)
    stub = StubLLM({"sql": "SELECT customer_id, amount FROM sales LIMIT 3", "explanation": ""})
    answer = engine.ask("a few orders", con, profiling.schema_text(tables), "", client=stub)
    engine.log_answer(answer, path)
    engine.log_answer(engine.Answer("headcount?", explanation="no employee data"), path)
    with open(path, "a") as fh:
        fh.write("{half a line from a crash")

    records = engine.read_log(path)
    assert [r["status"] for r in records] == ["ok", "declined"]   # corrupt line skipped
    assert records[0]["rows"] == 3 and records[0]["sql"].startswith("SELECT")
    raw = path.read_text()
    first_amount = str(answer.df["amount"].iloc[0])
    assert first_amount not in raw, "result values must never be written to the trace"


def test_ablation_switches_really_switch_the_component_off():
    """The eval harness is only honest if 'off' means off."""
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv"], con, recover_types=False)
    types = {c.name: c.dtype for c in tables[0].columns}
    assert types["amount"] == "TEXT" and types["order_date"] == "TEXT"

    # 03/05/2024 in a column that also holds 20/05/2024 is 3 May. The naive parse
    # reads it as 5 March -- the silent error the detection exists to prevent.
    col = pd.Series(["20/05/2024", "03/05/2024", "2024-11-02"])
    naive, _ = profiling.coerce_datetime(col, detect_orientation=False)
    smart, _ = profiling.coerce_datetime(col)
    assert naive.iloc[1].month == 3 and smart.iloc[1].month == 5
    assert naive.iloc[2] == smart.iloc[2]  # ISO rows agree either way


def test_eval_harness_never_scores_a_quota_refusal_as_a_wrong_answer():
    """The first full eval run reported the naive approach at 0/20 when it had not
    run at all: every 'answer' was the API refusing on quota."""
    sys.path.insert(0, str(ROOT / "tests"))
    import evals

    evals._quota_exhausted = False
    calls = []

    def refused(question, client):
        calls.append(question)
        return "error", None, 0, "Error code: 429 - Rate limit reached ... tokens per day (TPD)"

    frames = evals._frames(pd.read_csv(SAMPLES / "sales.csv"),
                           pd.read_excel(SAMPLES / "customers.xlsx"),
                           pd.read_csv(SAMPLES / "products.csv"))
    run = evals.run_config("naive", "subset", None, frames, refused, None, repeat=1)
    assert len(calls) == 1, "must stop calling once the quota is gone"
    assert run.ran == 0 and run.score == 0
    assert all(r["status"] == "not_run" and r["passed"] is None for r in run.results)
    assert "not run" in evals.render([run], None)
    evals._quota_exhausted = False


HARD = ROOT / "data" / "evals" / "hard"


def test_join_discovery_finds_keys_whose_names_do_not_match():
    """A foreign key is usually named after what it points at, not `x_id`."""
    con = engine.connect()
    tables, _ = profiling.load_files([HARD / "orders.csv", HARD / "clients.xlsx", HARD / "catalog.csv"], con)
    pairs = {frozenset([j["left"], j["right"]]) for j in profiling.discover_joins(con, tables)}
    assert frozenset(["orders.customer", "clients.legacy_ref"]) in pairs
    assert frozenset(["orders.item", "catalog.ref_no"]) in pairs
    # the obvious-looking decoy shares no values and must not be proposed
    assert frozenset(["orders.customer", "clients.client_id"]) not in pairs

    # and loosening the rule must not start joining small integers by coincidence
    con = engine.connect()
    tables, _ = profiling.load_files([SAMPLES / "sales.csv", SAMPLES / "customers.xlsx"], con)
    joined_cols = {c for j in profiling.discover_joins(con, tables) for c in (j["left"], j["right"])}
    assert "sales.quantity" not in joined_cols and "sales.discount" not in joined_cols


def test_schema_card_lists_every_category_value():
    """Three samples of a four-code column hide the fourth code from every filter."""
    con = engine.connect()
    tables, _ = profiling.load_files([HARD / "orders.csv"], con)
    card = profiling.schema_text(tables)
    status_line = next(line for line in card.splitlines() if line.strip().startswith("stat "))
    for code in ["CMP", "RFD", "CXL", "PND"]:
        assert code in status_line, code
    # high-cardinality columns still get samples, not a dump of every value
    customer_line = next(line for line in card.splitlines() if line.strip().startswith("customer "))
    assert "e.g." in customer_line and "values:" not in customer_line
    # and privacy mode still sends none of it
    assert "CMP" not in profiling.schema_text(tables, samples=False)


def test_eval_harness_waits_out_a_cooldown_but_not_a_daily_quota():
    """A router's '~8m cooldown' is worth waiting for; 'tokens per day' is not."""
    sys.path.insert(0, str(ROOT / "tests"))
    import evals

    frames = evals._frames(pd.read_csv(SAMPLES / "sales.csv"),
                           pd.read_excel(SAMPLES / "customers.xlsx"),
                           pd.read_csv(SAMPLES / "products.csv"))
    slept, real_sleep = [], evals.time.sleep
    evals.time.sleep = slept.append
    try:
        evals._quota_exhausted = False
        calls = []

        def cooling_then_fine(question, client):
            calls.append(question)
            if len(calls) == 1:
                return "error", None, 0, "Model call failed: 429 All models rate-limited. Soonest cooldown reset ~2m"
            return "declined", None, 10, None, ["router/some-model"]

        run = evals.run_config("x", "full", None, frames, cooling_then_fine, None, 1,
                               cases=[evals.CASES[-1]])  # a decline case
        assert slept and 100 < slept[0] < 200, slept          # waited ~2 min, once
        assert run.results[0]["status"] == "declined" and run.results[0]["passed"]
        assert not evals._quota_exhausted

        daily = lambda q, c: ("error", None, 0, "Model call failed: 429 rate limit on tokens per day (TPD)")
        run = evals.run_config("y", "full", None, frames, daily, None, 1, cases=evals.CASES[:2])
        assert [r["status"] for r in run.results] == ["not_run", "not_run"]
        assert len(slept) == 1, "a daily quota must not be waited on"
    finally:
        evals.time.sleep = real_sleep
        evals._quota_exhausted = False


def test_dashboard_uses_the_agreed_definition_and_joins_across_files():
    import dashboard

    con = engine.connect()
    tables, _ = profiling.load_files(sorted(SAMPLES.glob("*.*")), con)
    joins = profiling.discover_joins(con, tables)
    metrics = engine.load_metrics(ROOT / "config" / "metrics.toml", tables)
    sales = dashboard.fact_tables(tables, metrics)[0]
    assert sales.name == "sales", "the table with a defined metric leads"

    revenue = next(m for m in dashboard.measures(sales, metrics) if m.label == "revenue")
    total = engine.run_sql(con, dashboard.kpi_sql(sales, revenue))["revenue"].iloc[0]
    by_region = next(d for d in dashboard.dimensions(sales, tables, joins) if d.label == "region")
    assert by_region.on, "region lives in customers, so it must come through a join"
    split = engine.run_sql(con, dashboard.breakdown_sql(sales, revenue, by_region))
    assert len(split) == 4 and abs(split["revenue"].sum() - total) < 0.01

    raw = pd.read_csv(SAMPLES / "sales.csv")
    amount = raw["Amount"].astype(str).str.replace(r"[$,\s]", "", regex=True).astype(float)
    assert abs(total - amount[raw["Status"] == "Completed"].sum()) < 0.01, "net, not gross"
    assert dashboard.compact(1_962_345, money=True) == "$1.96M"
    assert dashboard.compact(12.5) == "12.50" and dashboard.compact(None) == "—"


def test_edited_definitions_are_checked_then_round_trip_through_toml():
    import tempfile

    con = engine.connect()
    tables, _ = profiling.load_files(sorted(SAMPLES.glob("*.*")), con)
    edit = {"name": "gross_revenue", "table": "sales", "columns": [],
            "expression": "SUM(amount)", "meaning": 'All orders, "refunds" included'}
    d, err = engine.check_definition(con, tables, edit)
    assert err is None and d["columns"] == ["amount"], "columns are read off the formula"
    assert engine.check_definition(con, tables, {**edit, "expression": "SUM(nope)"})[1]
    assert engine.check_definition(con, tables, {**edit, "name": "Gross Revenue"})[1]
    assert "multiple statements" in engine.check_definition(
        con, tables, {**edit, "expression": "1; DROP TABLE sales"})[1]

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metrics.toml"
        engine.save_definitions(path, [d])
        assert engine.read_definitions(path) == [d], "quotes in the meaning survive"
        assert [m["name"] for m in engine.load_metrics(path, tables)] == ["gross_revenue"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  pass  {t.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
