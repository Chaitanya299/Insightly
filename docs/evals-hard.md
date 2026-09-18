# Evaluation results — `hard` suite

Rendered 2026-09-18 08:39 UTC by `python tests/evals.py --suite hard` from `docs/evals-hard.json` · 17 questions.

Correct means correct by `data/evals/hard/metrics.toml`. Expected answers are computed in pandas, not typed in. **Not run** means the API refused for quota reasons; those questions are excluded, never scored as wrong.

## Score by configuration

| Configuration | Data | Correct | Tokens / question | Model | Run at |
|---|---|---|---|---|---|
| `full` | full | **17 / 17** | 1,537 | `gpt-oss-20b` ⚠ answered by `groq/openai/gpt-oss-20b`, `nvidia/openai/gpt-oss-20b` | 2026-09-18 08:32 UTC |
| `no_join_hints` | full | **17 / 17** | 1,481 | `gpt-oss-20b` ⚠ answered by `groq/openai/gpt-oss-20b`, `nvidia/openai/gpt-oss-20b` | 2026-09-18 08:34 UTC |
| `no_definitions` | full | **12 / 17** | 1,401 | `gpt-oss-20b` ⚠ answered by `groq/openai/gpt-oss-20b`, `nvidia/openai/gpt-oss-20b` | 2026-09-18 08:35 UTC |
| `privacy_mode` | full | **13 / 17** | 1,263 | `gpt-oss-20b` ⚠ answered by `groq/openai/gpt-oss-20b`, `nvidia/openai/gpt-oss-20b` | 2026-09-18 08:37 UTC |
| `privacy_no_join_hints` | full | **10 / 17** | 1,253 | `gpt-oss-20b` ⚠ answered by `groq/openai/gpt-oss-20b`, `nvidia/openai/gpt-oss-20b` | 2026-09-18 08:38 UTC |

⚠ **At least one run was answered by more than one model** (a router failed over mid-run). Its score mixes models.

## What each component is worth

Change in correct answers when one component is switched off, and which questions it cost:

**`no_join_hints`**: +0 (loses 0, gains 0)

**`no_definitions`**: -5 (loses 5, gains 0)
- ✗ Revenue by client tier
- ✗ Revenue by fiscal year
- ✗ What was revenue in FY2024?
- ✗ What was revenue in calendar year 2024?
- ✗ What was the revenue from clients in Europe?

**`privacy_mode`**: -4 (loses 4, gains 0)
- ✗ How many orders are still pending?
- ✗ How many orders were cancelled?
- ✗ What share of orders were refunded?
- ✗ What was the revenue from clients in Europe?

**`privacy_no_join_hints`**: -7 (loses 7, gains 0)
- ✗ How many orders are still pending?
- ✗ How many orders were cancelled?
- ✗ How much revenue did Aperture GmbH generate?
- ✗ Revenue by client tier
- ✗ What share of orders were refunded?
- ✗ What was the revenue from clients in Europe?
- ✗ Which company generated the most revenue?

## Every question

| Question | `full` | `no_join_hints` | `no_definitions` | `privacy_mode` | `privacy_no_join_hints` |
|---|---|---|---|---|---|
| How many orders are in the data? | ✓ | ✓ | ✓ | ✓ | ✓ |
| What is the total revenue? | ✓ | ✓ | ✓ | ✓ | ✓ |
| What was revenue in calendar year 2024? | ✓ | ✓ | ✗ answered | ✓ | ✓ |
| What was revenue in FY2024? | ✓ | ✓ | ✗ answered | ✓ | ✓ |
| Revenue by fiscal year | ✓ | ✓ | ✗ answered | ✓ | ✓ |
| How many orders were cancelled? | ✓ | ✓ | ✓ | ✗ declined | ✗ answered |
| How many orders are still pending? | ✓ | ✓ | ✓ | ✗ answered | ✗ answered |
| What share of orders were refunded? | ✓ | ✓ | ✓ | ✗ declined | ✗ answered |
| Revenue by client tier | ✓ | ✓ | ✗ answered | ✓ | ✗ answered |
| Which company generated the most revenue? | ✓ | ✓ | ✓ | ✓ | ✗ answered |
| How much revenue did Aperture GmbH generate? | ✓ | ✓ | ✓ | ✓ | ✗ answered |
| What was the revenue from clients in Europe? | ✓ | ✓ | ✗ answered | ✗ answered | ✗ answered |
| Which region has the most clients? | ✓ | ✓ | ✓ | ✓ | ✓ |
| How many units were ordered per product line, across all orders? | ✓ | ✓ | ✓ | ✓ | ✓ |
| Revenue by product line | ✓ | ✓ | ✓ | ✓ | ✓ |
| What is our average delivery time? | ✓ | ✓ | ✓ | ✓ | ✓ |
| How many support tickets did T1 clients open? | ✓ | ✓ | ✓ | ✓ | ✓ |

## Caveats

- One run per question unless stated. The model is not deterministic even at temperature 0, so a one-question gap between configurations is within noise.
- 17 questions on one synthetic dataset. This shows what each component does *here*. A component scoring the same with and without it has not been shown to be useless, only not exercised by these questions.
