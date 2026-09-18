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
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
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
