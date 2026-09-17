"""Independent answers to the demo questions, computed with pandas only.

The app answers via LLM-generated SQL over DuckDB. This script answers the same
questions through a completely different path, so the demo can be *verified*
rather than eyeballed. Run it alongside the app: the numbers must match.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from profiling import clean_frame  # noqa: E402

S = ROOT / "data" / "samples"
sales, _ = clean_frame(pd.read_csv(S / "sales.csv"))
customers, _ = clean_frame(pd.read_excel(S / "customers.xlsx"))
products, _ = clean_frame(pd.read_csv(S / "products.csv"))

joined = sales.merge(customers, left_on="customer_id", right_on="id").merge(products, on="sku")

print(f"1. Total revenue                    ${sales.amount.sum():,.2f}")

print("\n2. Average order value by region")
for region, v in joined.groupby("region").amount.mean().sort_values(ascending=False).items():
    print(f"     {region:<8} ${v:,.2f}")

print("\n3. Revenue by month, 2024 (first 3)")
y24 = sales[sales.order_date.dt.year == 2024]
for m, v in y24.groupby(y24.order_date.dt.to_period("M")).amount.sum().head(3).items():
    print(f"     {m}  ${v:,.2f}")

print("\n4. Top 5 categories by revenue")
for cat, v in joined.groupby("category").amount.sum().sort_values(ascending=False).head(5).items():
    print(f"     {cat:<10} ${v:,.2f}")

n, s = joined[joined.region == "North"].amount.sum(), joined[joined.region == "South"].amount.sum()
print(f"\n5. North ${n:,.2f}  vs  South ${s:,.2f}")

repeat = sales.groupby("customer_id").size()
print(f"\n6. Customers with more than 3 orders: {(repeat > 3).sum()}")
print(f"\n   (sanity: {len(sales)} orders, {sales.order_date.min().date()} -> {sales.order_date.max().date()})")
