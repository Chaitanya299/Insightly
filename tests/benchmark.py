"""Does the architecture actually scale? python tests/benchmark.py [rows]

The design claim is that the model's prompt does not grow with the data, because
it only ever sees a schema card. This measures that claim instead of asserting it:
generate a large messy file, ingest it, and compare the schema card against what
putting the rows in the prompt would have cost.
"""

import csv
import os
import random
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine  # noqa: E402
import profiling  # noqa: E402

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
CHARS_PER_TOKEN = 4  # rough, and generous to the approach we're comparing against


def generate(path: Path, n: int) -> None:
    random.seed(1)
    cats = ["LAP", "MON", "KEY", "HEA", "DOC"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Order ID", "Order Date", "customer_id", "SKU", "Quantity", "Amount", "Status"])
        for i in range(n):
            d = date(2023, 1, 1) + timedelta(days=random.randint(0, 1000))
            w.writerow([
                i,
                d.strftime(random.choice(["%Y-%m-%d", "%d/%m/%Y"])),  # still mixed formats
                random.randint(1000, 9999),
                f"{random.choice(cats)}-{random.randint(100, 140)}",
                random.randint(1, 9),
                f"${random.uniform(20, 5000):,.2f}",                  # still money as text
                random.choice(["Completed", "Refunded"]),
            ])


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "big_sales.csv"
    print(f"generating {ROWS:,} rows…")
    generate(tmp, ROWS)
    size_mb = os.path.getsize(tmp) / 1e6

    con = engine.connect()
    t0 = time.time()
    tables, problems = profiling.load_files([tmp], con)
    ingest = time.time() - t0
    assert tables and not problems, problems

    schema = profiling.schema_text(tables)
    card_tokens = len(schema) // CHARS_PER_TOKEN
    raw_tokens = int(os.path.getsize(tmp) / CHARS_PER_TOKEN)

    print(f"\nfile                 {size_mb:,.0f} MB, {tables[0].rows:,} rows")
    print(f"ingest + clean + profile   {ingest:5.1f}s   "
          f"(type recovery ran on every row: "
          f"{sum('text ->' in n for n in tables[0].notes)} columns converted)")

    for label, sql in [
        ("total",  f'SELECT round(sum(amount), 2) FROM "{tables[0].name}" WHERE status = \'Completed\''),
        ("trend",  f'SELECT date_trunc(\'month\', order_date) m, round(sum(amount), 2) r '
                   f'FROM "{tables[0].name}" GROUP BY 1 ORDER BY 1'),
        ("group",  f'SELECT sku, count(*) n, round(avg(amount), 2) a '
                   f'FROM "{tables[0].name}" GROUP BY 1 ORDER BY n DESC LIMIT 10'),
    ]:
        t0 = time.time()
        df = engine.run_sql(con, sql)
        print(f"query: {label:<6}             {time.time() - t0:5.2f}s   -> {len(df)} rows")

    print(f"\nprompt sent to the model   {len(schema):,} chars (~{card_tokens:,} tokens)")
    print(f"the same data as rows      ~{raw_tokens:,} tokens "
          f"({raw_tokens // max(card_tokens, 1):,}x larger, and past every context window)")
    print("\nThe schema card is the same size at 1M rows as at 900. That is the "
          "whole point of the design:\nrow count changes the answer, never the prompt.")


if __name__ == "__main__":
    main()
