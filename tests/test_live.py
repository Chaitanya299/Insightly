"""Live model checks: python tests/test_live.py

Skips without GROQ_API_KEY. These exist because the stubbed suite cannot catch
prompt regressions -- `test_unanswerable_question_is_declined_not_invented`
passed with a stub while the real model happily answered "headcount" with
COUNT(*) FROM customers. A stub tests the plumbing; only the real model tests
the prompt.
"""

import os
import sys
from pathlib import Path

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


def context():
    con = engine.connect()
    tables = profiling.load_files(
        [SAMPLES / "sales.csv", SAMPLES / "customers.xlsx", SAMPLES / "products.csv"], con
    )
    joins = profiling.discover_joins(con, tables)
    return con, profiling.schema_text(tables), profiling.joins_text(joins)


def test_declines_questions_the_data_cannot_answer():
    con, schema, joins = context()
    for q in [
        "What's our headcount?",
        "How many employees do we have?",
        "What's our marketing spend?",
        "Which warehouse shipped the most?",
    ]:
        answer = engine.ask(q, con, schema, joins)
        assert answer.sql is None, f"should have declined {q!r}, got: {answer.sql}"
        assert answer.explanation, "a decline must say what is missing"


def test_still_answers_what_it_can():
    con, schema, joins = context()
    answer = engine.ask("How many customers do we have?", con, schema, joins)
    assert answer.sql and answer.df is not None
    assert int(answer.df.iloc[0, 0]) == 120


def test_totals_match_ground_truth():
    con, schema, joins = context()
    answer = engine.ask("What's the total revenue?", con, schema, joins)
    assert answer.error is None and answer.df is not None
    total = float(answer.df.iloc[0, 0])
    # gross, or net of the 62 refunded orders -- both are defensible readings,
    # and the SQL on screen says which one was used
    assert round(total, 2) in (2109620.72, 1962664.00), total


def test_cross_file_join_produces_four_regions():
    con, schema, joins = context()
    answer = engine.ask("Average order value by region", con, schema, joins)
    assert answer.error is None and len(answer.df) == 4
    assert answer.chart and answer.chart["type"] == "bar"


def test_trend_question_returns_a_date_column_and_line_chart():
    con, schema, joins = context()
    answer = engine.ask("Revenue by month in 2024", con, schema, joins)
    assert answer.error is None and len(answer.df) == 12
    assert answer.chart["type"] == "line"


if __name__ == "__main__":
    if not os.getenv("GROQ_API_KEY"):
        print("skipped: GROQ_API_KEY not set")
        sys.exit(0)
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
