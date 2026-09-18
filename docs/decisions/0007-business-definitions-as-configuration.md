# 7. Business definitions are configuration, not the model's judgement

Date: 2026-09-18

## Status

Accepted

## Context

Asked for "total revenue", the model filters out refunded orders. That is a defensible
reading, and it says so. But it is the model's reading, re-decided on every question.
Nothing stops "revenue by region" from quietly including refunds on the next call, and
nothing tells a finance team which reading they got without opening the SQL.

On the sample data the two readings differ by $146,957 (7%). Both are "correct".
Only one is what the organisation means, and that is not something a model can know.
It is a decision someone at the customer has to make once.

## Decision

Definitions live in `config/metrics.toml`: a name, the table and columns it needs, the
exact SQL expression, and a plain-English meaning. They are injected into the prompt
with an instruction to use them exactly when the question uses the term, and the model
reports which ones it applied.

Two guards:
- A definition is offered only if its table and every column it names exist in the
  current upload. Offering `SUM(amount)` for a file with no `amount` column would invite
  exactly the invented-column failure the rest of the system works to prevent.
- `metrics_used` is filtered to names that were actually offered, so the model cannot
  cite a definition that does not exist.

The UI shows the agreed definitions before any question, and under each answer names the
definition it used.

## Consequences

The meaning of "revenue" is set by the customer, in a file they can read and edit,
without touching code. That is the shape of most real deployments: the model is the same
everywhere, and what differs per customer is their definitions.

It is still a prompt instruction, not an enforcement mechanism. The model can ignore it.
The eval harness scores against the definitions, so ignoring them shows up as a failure
rather than passing silently. See `docs/evals.md`.

A fuller semantic layer would compile definitions into SQL rather than asking the model
to copy them. That is the next step if a customer's definitions grow past a handful.
