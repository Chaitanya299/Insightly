# 3. Score join-key candidates by containment, not Jaccard

Date: 2026-09-18

## Status

Accepted

## Context

Cross-file questions need to know which column joins to which. Asking the model to guess
fails badly: it invents plausible key names, the join matches nothing, and the query
returns zero rows. An empty result looks exactly like a real answer, which makes this the
worst available failure mode.

So the join keys are detected before any question is asked and passed to the model as
hints. The question is how to score a candidate pair.

Jaccard similarity — intersection over union — is the instinctive choice for set overlap.
It is wrong here. A foreign key is many-to-one: 900 orders reference 120 customers, so
the union is dominated by the large side and the score collapses toward zero. Jaccard
would reject both real joins in the sample data.

## Decision

Score by **containment**: intersection over the size of the *smaller* side. A foreign key
scores near 1.0 regardless of the cardinality imbalance.

Candidate columns are narrowed first — near-unique, or named like a key — so the
comparison is not run over every column pair. The final confidence weights containment by
a name-similarity signal, so `sales.customer_id = customers.id` outranks a coincidental
value overlap between unrelated columns.

## Consequences

Both sample joins are detected at 100% containment. The model is told the keys instead of
guessing them.

Containment is asymmetric and that asymmetry is deliberate, but it does mean a column
whose values happen to be a subset of another unrelated column will score high on the
value signal alone — hence the name weighting and the reported score in the UI, so a
human can see and discount a bad hint rather than having it silently applied.

## Update, 2026-09-18: measured

The Context above asserts that asking the model to guess the keys fails badly. On the
sample data that is not what happened: with join hints removed the model still answered
20/20 (`docs/evals.md`), because the key names are self-explanatory. The failure described
here needs keys whose names don't match, which the sample files don't have. Until the eval
set includes such a file, this decision rests on reasoning rather than measurement.

## Update, 2026-09-18: hard suite

The hard suite (`docs/evals-hard.md`) has the mismatched keys: `orders.customer` holds
`A-7342`, which matches `clients.legacy_ref`, and `clients.client_id` is a decoy. With
sample values in the prompt, the model finds the join without hints (17/17 either way),
because it can see `A-7342` on both sides. With privacy mode on, removing the hints costs
three more questions (13/17 → 10/17): tier revenue, top company and one company's revenue.
Join hints matter exactly when the model can't see the values, which is the configuration a
privacy-sensitive customer runs.
