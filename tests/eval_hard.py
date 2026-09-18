"""The hard eval suite: data built so the components the sample data never
exercised -- join hints, agreed definitions, sample values -- each stand alone
between the model and a wrong answer. See data/evals/hard/make.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import profiling
from eval_checks import Case, count, keyed, ranked, scalar

HARD = Path(__file__).resolve().parent.parent / "data" / "evals" / "hard"
FILES = {"orders": HARD / "orders.csv", "clients": HARD / "clients.xlsx", "catalog": HARD / "catalog.csv"}
METRICS = HARD / "metrics.toml"


def load() -> dict[str, pd.DataFrame]:
    return {name: (pd.read_excel(path) if path.suffix == ".xlsx" else pd.read_csv(path))
            for name, path in FILES.items()}


def fiscal_year(dates: pd.Series) -> pd.Series:
    """FY starts 1 April: FY2024 runs 1 Apr 2024 to 31 Mar 2025."""
    return dates.dt.year.where(dates.dt.month >= 4, dates.dt.year - 1)


def frames(raw):
    orders, _ = profiling.clean_frame(raw["orders"])
    clients, _ = profiling.clean_frame(raw["clients"])
    catalog, _ = profiling.clean_frame(raw["catalog"])
    orders = orders.assign(rev=orders.gross - orders.disc)
    joined = (orders.merge(clients, left_on="customer", right_on="legacy_ref")
                    .merge(catalog, left_on="item", right_on="ref_no"))
    assert len(joined) == len(orders), "every order must join to one client and one product"
    net = orders[orders.stat == "CMP"]
    jnet = joined[joined.stat == "CMP"]
    return orders, clients, net, jnet, joined


def _share(s: pd.Series, code: str) -> float:
    return float((s == code).mean())


CASES = [
    Case("How many orders are in the data?", set(),
         lambda o, c, n, j, ja: count(len(o))),
    Case("What is the total revenue?", {"definition"},
         lambda o, c, n, j, ja: scalar(n.rev.sum())),
    Case("What was revenue in calendar year 2024?", {"definition"},
         lambda o, c, n, j, ja: scalar(n[n.placed.dt.year == 2024].rev.sum())),
    Case("What was revenue in FY2024?", {"definition", "fiscal"},
         lambda o, c, n, j, ja: scalar(n[fiscal_year(n.placed) == 2024].rev.sum())),
    Case("Revenue by fiscal year", {"definition", "fiscal"},
         lambda o, c, n, j, ja: keyed(n.groupby(fiscal_year(n.placed)).rev.sum().to_dict())),
    Case("How many orders were cancelled?", {"codes"},
         lambda o, c, n, j, ja: count(int((o.stat == "CXL").sum()))),
    Case("How many orders are still pending?", {"codes"},
         lambda o, c, n, j, ja: count(int((o.stat == "PND").sum()))),
    Case("What share of orders were refunded?", {"codes"},
         lambda o, c, n, j, ja: scalar(_share(o.stat, "RFD"), pct=True)),
    Case("Revenue by client tier", {"join", "definition"},
         lambda o, c, n, j, ja: keyed(j.groupby("tier").rev.sum().to_dict())),
    # The top company is the same under net or gross revenue, so this one tests
    # only the join; the next question is the one that separates the definitions.
    Case("Which company generated the most revenue?", {"join"},
         lambda o, c, n, j, ja: ranked([j.groupby("company").rev.sum().idxmax()])),
    Case("How much revenue did Aperture GmbH generate?", {"join", "definition"},
         lambda o, c, n, j, ja: scalar(j[j.company == "Aperture GmbH"].rev.sum())),
    Case("What was the revenue from clients in Europe?", {"join", "codes", "definition"},
         lambda o, c, n, j, ja: scalar(j[j.region == "EMEA"].rev.sum())),
    Case("Which region has the most clients?", set(),
         lambda o, c, n, j, ja: ranked([c.region.value_counts().idxmax()])),
    Case("How many units were ordered per product line, across all orders?", {"join"},
         lambda o, c, n, j, ja: keyed(ja.groupby("line").units.sum().to_dict())),
    Case("Revenue by product line", {"join", "definition"},
         lambda o, c, n, j, ja: keyed(j.groupby("line").rev.sum().to_dict())),
    Case("What is our average delivery time?", {"decline"}, None, decline=True),
    Case("How many support tickets did T1 clients open?", {"decline"}, None, decline=True),
]
