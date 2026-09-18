# Evaluation results — `sample` suite

Rendered 2026-09-18 09:02 UTC by `python tests/evals.py --suite sample` from `docs/evals.json` · 20 questions.

Correct means correct by `config/metrics.toml`. Expected answers are computed in pandas, not typed in. **Not run** means the API refused for quota reasons; those questions are excluded, never scored as wrong.

## Score by configuration

| Configuration | Data | Correct | Tokens / question | Model | Run at |
|---|---|---|---|---|---|
| `full` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `no_type_recovery` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `no_date_detection` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `no_join_hints` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `no_definitions` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `privacy_mode` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `privacy_no_join_hints` | full | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `full` | 100-row subset | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |
| `naive` | 100-row subset | not run (20 skipped) | — | `openai/gpt-oss-120b` | 2026-09-18 09:02 UTC |

Naive approach on the **full** files: `not run: API quota exhausted`

## What each component is worth

Change in correct answers when one component is switched off, and which questions it cost:

## Every question

| Question | `full` | `no_type_recovery` | `no_date_detection` | `no_join_hints` | `no_definitions` | `privacy_mode` | `privacy_no_join_hints` | `full` (sub) | `naive` (sub) |
|---|---|---|---|---|---|---|---|---|---|
| What is the total revenue? | — | — | — | — | — | — | — | — | — |
| How many orders are in the data, including refunded ones? | — | — | — | — | — | — | — | — | — |
| How many orders were refunded? | — | — | — | — | — | — | — | — | — |
| What is the refund rate? | — | — | — | — | — | — | — | — | — |
| What is the average order value? | — | — | — | — | — | — | — | — | — |
| Average order value by region | — | — | — | — | — | — | — | — | — |
| Revenue by customer segment | — | — | — | — | — | — | — | — | — |
| What are the top 5 product categories by revenue? | — | — | — | — | — | — | — | — | — |
| Compare revenue between the North and South regions | — | — | — | — | — | — | — | — | — |
| What was the revenue in March 2024? | — | — | — | — | — | — | — | — | — |
| What was the revenue in May 2024? | — | — | — | — | — | — | — | — | — |
| Which month in 2024 had the highest revenue? | — | — | — | — | — | — | — | — | — |
| Revenue by month in 2024 | — | — | — | — | — | — | — | — | — |
| How many units of Laptops were sold, excluding refunded orders? | — | — | — | — | — | — | — | — | — |
| What is the average discount percentage on completed orders? | — | — | — | — | — | — | — | — | — |
| Which region has the most customers? | — | — | — | — | — | — | — | — | — |
| How much revenue came from Enterprise customers in the North region? | — | — | — | — | — | — | — | — | — |
| How many customers placed more than 3 orders, counting refunded orders too? | — | — | — | — | — | — | — | — | — |
| What is our employee headcount? | — | — | — | — | — | — | — | — | — |
| What was our marketing spend last quarter? | — | — | — | — | — | — | — | — | — |

## Caveats

- One run per question unless stated. The model is not deterministic even at temperature 0, so a one-question gap between configurations is within noise.
- The naive baseline runs on a 100-row subset because the full files exceed the free tier's per-request token limit. The subset is the naive approach's best case.
- 20 questions on one synthetic dataset. This shows what each component does *here*. A component scoring the same with and without it has not been shown to be useless, only not exercised by these questions.
