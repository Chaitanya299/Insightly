# Evaluation results

Rendered 2026-09-18 07:06 UTC by `python tests/evals.py` from `docs/evals.json` · model `openai/gpt-oss-120b` · 20 questions.

Correct means correct by `config/metrics.toml` (revenue is net of refunds). Expected answers are computed in pandas, not typed in. **Not run** means the API refused for quota reasons; those questions are excluded, never scored as wrong.

## Score by configuration

| Configuration | Data | Correct | Tokens / question | Run at |
|---|---|---|---|---|
| `full` | full | **20 / 20** | 1,486 | 2026-09-18 06:46 UTC |
| `no_type_recovery` | full | **16 / 20** | 1,990 | 2026-09-18 06:46 UTC |
| `no_date_detection` | full | **16 / 20** | 1,490 | 2026-09-18 06:46 UTC |
| `no_join_hints` | full | **20 / 20** | 1,462 | 2026-09-18 06:46 UTC |
| `no_definitions` | full | **20 / 20** | 1,395 | 2026-09-18 06:46 UTC |
| `privacy_mode` | full | **20 / 20** | 1,233 | 2026-09-18 06:46 UTC |
| `full` | 100-row subset | **12 / 12** (8 not run) | 1,491 | 2026-09-18 06:46 UTC |
| `naive` | 100-row subset | not run (20 skipped) | — | 2026-09-18 06:46 UTC |

Naive approach on the **full** files: `not run: API quota exhausted`

## What each component is worth

Change in correct answers when one component is switched off, and which questions it cost:

**`no_type_recovery`**: -4 (loses 4, gains 0)
- ✗ Revenue by month in 2024
- ✗ What was the revenue in March 2024?
- ✗ What was the revenue in May 2024?
- ✗ Which month in 2024 had the highest revenue?

**`no_date_detection`**: -4 (loses 4, gains 0)
- ✗ Revenue by month in 2024
- ✗ What was the revenue in March 2024?
- ✗ What was the revenue in May 2024?
- ✗ Which month in 2024 had the highest revenue?

**`no_join_hints`**: +0 (loses 0, gains 0)

**`no_definitions`**: +0 (loses 0, gains 0)

**`privacy_mode`**: +0 (loses 0, gains 0)

## Every question

| Question | `full` | `no_type_recovery` | `no_date_detection` | `no_join_hints` | `no_definitions` | `privacy_mode` | `full` (sub) | `naive` (sub) |
|---|---|---|---|---|---|---|---|---|
| What is the total revenue? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| How many orders are in the data, including refunded ones? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| How many orders were refunded? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| What is the refund rate? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| What is the average order value? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Average order value by region | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Revenue by customer segment | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| What are the top 5 product categories by revenue? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Compare revenue between the North and South regions | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| What was the revenue in March 2024? | ✓ | ✗ answered | ✗ answered | ✓ | ✓ | ✓ | ✓ | — |
| What was the revenue in May 2024? | ✓ | ✗ error | ✗ answered | ✓ | ✓ | ✓ | ✓ | — |
| Which month in 2024 had the highest revenue? | ✓ | ✗ error | ✗ answered | ✓ | ✓ | ✓ | ✓ | — |
| Revenue by month in 2024 | ✓ | ✗ error | ✗ answered | ✓ | ✓ | ✓ | — | — |
| How many units of Laptops were sold, excluding refunded orders? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| What is the average discount percentage on completed orders? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| Which region has the most customers? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| How much revenue came from Enterprise customers in the North region? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| How many customers placed more than 3 orders, counting refunded orders too? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| What is our employee headcount? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| What was our marketing spend last quarter? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |

## Caveats

- One run per question unless stated. The model is not deterministic even at temperature 0, so a one-question gap between configurations is within noise.
- The naive baseline runs on a 100-row subset because the full files exceed the free tier's per-request token limit. The subset is the naive approach's best case.
- Twenty questions on one synthetic dataset. This shows what each component does *here*. A component scoring the same with and without it has not been shown to be useless, only not exercised by these questions.
- The 06:46 run printed each result but did not yet save them; `docs/evals.json` was rebuilt from that run's log. Per-question token counts were not logged, so each answer carries its run's measured average. The three `no_type_recovery` errors were query failures whose messages were not captured.
- That run exhausted the free tier's daily token quota during the subset comparison. The first version of this harness scored the resulting API errors as wrong answers, reporting the naive approach at 0/20 when it had not run. It now records them as not run and stops.
- In a separate 3-question smoke run earlier the same day, the naive approach on the full files was rejected outright (`413 Request too large`, 8,000 tokens/minute limit), and on the subset it answered 1 of 3 correctly at ~5,900 tokens per question.
