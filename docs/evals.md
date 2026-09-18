# Evaluation results — `sample` suite

Rendered 2026-09-18 10:59 UTC by `python tests/evals.py --suite sample` from `docs/evals.json` · 20 questions.

Correct means correct by `config/metrics.toml`. Expected answers are computed in pandas, not typed in. **Not run** means the API refused for quota reasons; those questions are excluded, never scored as wrong.

## Score by configuration

| Configuration | Data | Correct | Tokens / question | Model | Run at |
|---|---|---|---|---|---|
| `full` | full | **20 / 20** | 1,486 | `openai/gpt-oss-120b` | 2026-09-18 06:46 UTC |
| `no_type_recovery` | full | **16 / 20** | 1,990 | `openai/gpt-oss-120b` | 2026-09-18 06:46 UTC |
| `no_date_detection` | full | **16 / 20** | 1,490 | `openai/gpt-oss-120b` | 2026-09-18 06:46 UTC |
| `no_join_hints` | full | **20 / 20** | 1,462 | `openai/gpt-oss-120b` | 2026-09-18 06:46 UTC |
| `no_definitions` | full | **20 / 20** | 1,395 | `openai/gpt-oss-120b` | 2026-09-18 06:46 UTC |
| `privacy_mode` | full | **20 / 20** | 1,233 | `openai/gpt-oss-120b` | 2026-09-18 06:46 UTC |
| `full` | 100-row subset | **20 / 20** | 1,504 | `openai/gpt-oss-120b` | 2026-09-18 10:42 UTC |
| `naive` | 100-row subset | **10 / 20** | 6,040 | `openai/gpt-oss-120b` | 2026-09-18 10:45 UTC |

Naive approach on the **full** files: `error: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_[redacted]` service tier `on_demand` on tokens per minute (TPM): Limit 8`

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
| What is the total revenue? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| How many orders are in the data, including refunded ones? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| How many orders were refunded? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| What is the refund rate? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| What is the average order value? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ answered |
| Average order value by region | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ error |
| Revenue by customer segment | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ declined |
| What are the top 5 product categories by revenue? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ error |
| Compare revenue between the North and South regions | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ error |
| What was the revenue in March 2024? | ✓ | ✗ answered | ✗ answered | ✓ | ✓ | ✓ | ✓ | ✗ answered |
| What was the revenue in May 2024? | ✓ | ✗ error | ✗ answered | ✓ | ✓ | ✓ | ✓ | ✗ answered |
| Which month in 2024 had the highest revenue? | ✓ | ✗ error | ✗ answered | ✓ | ✓ | ✓ | ✓ | ✗ error |
| Revenue by month in 2024 | ✓ | ✗ error | ✗ answered | ✓ | ✓ | ✓ | ✓ | ✗ error |
| How many units of Laptops were sold, excluding refunded orders? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| What is the average discount percentage on completed orders? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Which region has the most customers? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ error |
| How much revenue came from Enterprise customers in the North region? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| How many customers placed more than 3 orders, counting refunded orders too? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| What is our employee headcount? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| What was our marketing spend last quarter? | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## Caveats

- One run per question unless stated. The model is not deterministic even at temperature 0, so a one-question gap between configurations is within noise.
- The naive baseline runs on a 100-row subset because the full files exceed the free tier's per-request token limit. The subset is the naive approach's best case.
- 20 questions on one synthetic dataset. This shows what each component does *here*. A component scoring the same with and without it has not been shown to be useless, only not exercised by these questions.
- The 06:46 run printed each result but did not yet save them; `docs/evals.json` was rebuilt from that run's log. Per-question token counts were not logged, so each answer carries its run's measured average. The three `no_type_recovery` errors were query failures whose messages were not captured.
- That run exhausted the free tier's daily token quota during the subset comparison. The first version of this harness scored the resulting API errors as wrong answers, reporting the naive approach at 0/20 when it had not run. It now records them as not run and stops.
- In a separate 3-question smoke run earlier the same day, the naive approach on the full files was rejected outright (`413 Request too large`, 8,000 tokens/minute limit), and on the subset it answered 1 of 3 correctly at ~5,900 tokens per question.
- These results predate two changes found while building the hard suite: join discovery now accepts foreign keys not named like keys, and the schema card lists every value of small category columns, so the prompt this suite sees has changed. The numbers above measured the earlier system; rerun to measure the current one.
