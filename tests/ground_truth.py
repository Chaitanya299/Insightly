"""Independent answers to the demo questions, computed with pandas only.

The app answers via LLM-generated SQL over DuckDB. This script answers the same
questions through a completely different path, so the demo can be *verified*
rather than eyeballed.

Two columns, because "revenue" is not one number. The sample data has a
`Refunded` status, and the model consistently chooses to exclude it -- a
defensible reading, stated in its explanation and visible in its SQL. Which
column the app matches tells you which definition it used. Pinning that down
across a whole organisation is the semantic layer described in the write-up.
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

net = sales[sales.status == "Completed"]
joined = sales.merge(customers, left_on="customer_id", right_on="id").merge(products, on="sku")
joined_net = joined[joined.status == "Completed"]


def row(label, gross, net_):
    print(f"  {label:<12} gross ${gross:>12,.2f}    net ${net_:>12,.2f}")


print("1. Total revenue")
row("", sales.amount.sum(), net.amount.sum())

print("\n2. Average order value by region")
g = joined.groupby("region").amount.mean()
n = joined_net.groupby("region").amount.mean()
for region in g.sort_values(ascending=False).index:
    row(region, g[region], n[region])

print("\n3. Revenue by month, 2024 (first 3)")
y, yn = sales[sales.order_date.dt.year == 2024], net[net.order_date.dt.year == 2024]
g = y.groupby(y.order_date.dt.to_period("M")).amount.sum()
n = yn.groupby(yn.order_date.dt.to_period("M")).amount.sum()
for m in g.index[:3]:
    row(str(m), g[m], n[m])

print("\n4. Top 5 categories by revenue")
g = joined.groupby("category").amount.sum()
n = joined_net.groupby("category").amount.sum()
for cat in n.sort_values(ascending=False).head(5).index:
    row(cat, g[cat], n[cat])

print("\n5. North vs South revenue")
for r in ("North", "South"):
    row(r, joined[joined.region == r].amount.sum(), joined_net[joined_net.region == r].amount.sum())

print("\n6. Customers with more than 3 orders")
print(f"  {'':12} gross {(sales.groupby('customer_id').size() > 3).sum():>13}"
      f"    net {(net.groupby('customer_id').size() > 3).sum():>13}")

print(f"\n  ({len(sales)} orders, {(sales.status == 'Refunded').sum()} refunded, "
      f"{sales.order_date.min().date()} -> {sales.order_date.max().date()})")
