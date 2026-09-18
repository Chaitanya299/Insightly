"""Regenerate the hard eval dataset: python data/evals/hard/make.py

Built so each unproven component is the only thing between the model and a
wrong answer:

  * join hints    -- orders.customer holds legacy refs ("A-7342") that match
                     clients.legacy_ref; clients.client_id ("CL-0042") is the
                     obvious-looking decoy. Neither FK column is named like a key.
  * definitions   -- revenue is gross minus discount on completed orders only,
                     and the fiscal year starts 1 April. Neither is guessable.
  * sample values -- status and region are codes (CMP/RFD/CXL/PND,
                     AMER/EMEA/APAC); "cancelled" or "Europe" can't be mapped to
                     them without seeing the values.

Deterministic (seeded), so the committed files are reproducible.
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
random.seed(20260918)

names = ["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Wonka", "Tyrell",
         "Cyberdyne", "Soylent", "Oscorp", "Vandelay", "Pied Piper", "Aperture", "Massive Dynamic"]

legacy = random.sample(range(7000, 8000), 80)
clients = pd.DataFrame({
    "Client ID": [f"CL-{i:04d}" for i in range(1, 81)],
    "Legacy Ref": [f"A-{n}" for n in legacy],
    "Company": [f"{names[i % len(names)]} {['Ltd', 'Inc', 'GmbH', 'Pty', 'LLC'][i // len(names)]}"
                for i in range(80)],
    "Tier": random.choices(["T1", "T2", "T3"], weights=[20, 40, 40], k=80),
    "Region": random.choices(["AMER", "EMEA", "APAC"], weights=[40, 35, 25], k=80),
})

lines = ["HW"] * 10 + ["SW"] * 8 + ["SVC"] * 6
catalog = pd.DataFrame({
    "Ref No": [f"P-{301 + i}" for i in range(24)],
    "Name": [f"{line} item {i:02d}" for i, line in enumerate(lines)],
    "Line": lines,
    "List Price": [f"${random.choice([49, 99, 149, 299, 499, 899, 1499]):,}.00" for _ in lines],
})
price = {r["Ref No"]: float(r["List Price"].replace("$", "").replace(",", ""))
         for _, r in catalog.iterrows()}

rows = []
for i in range(300):
    ref = random.choice(catalog["Ref No"].tolist())
    units = random.randint(1, 10)
    gross = price[ref] * units
    disc = round(gross * random.choice([0, 0, 0.05, 0.10]), 2)
    rows.append({
        "Order No": f"SO-{10001 + i}",
        "Placed": (date(2023, 1, 1) + timedelta(days=random.randint(0, 910))).isoformat(),
        "Customer": random.choice(clients["Legacy Ref"].tolist()),
        "Item": ref,
        "Units": units,
        "Gross": f"${gross:,.2f}",
        "Disc": f"${disc:,.2f}",
        "Stat": random.choices(["CMP", "RFD", "CXL", "PND"], weights=[80, 8, 7, 5])[0],
    })
orders = pd.DataFrame(rows)

orders.to_csv(HERE / "orders.csv", index=False)
clients.to_excel(HERE / "clients.xlsx", index=False)
catalog.to_csv(HERE / "catalog.csv", index=False)
print(f"orders {len(orders)}, clients {len(clients)}, catalog {len(catalog)}")
print("status mix:", orders["Stat"].value_counts().to_dict())
