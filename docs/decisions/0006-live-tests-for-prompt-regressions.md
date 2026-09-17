# 6. Test against the real model, not only a stub

Date: 2026-09-18

## Status

Accepted

## Context

The first test suite stubbed the model: canned JSON replies, no API key needed, fast and
deterministic. One test asserted that an unanswerable question is declined rather than
invented. It passed.

Then the same question was put to the real model. Asked "what's our headcount?", it
answered `SELECT COUNT(*) FROM customers` — 120 — silently substituting customers for
employees. The behaviour the test claimed to protect had never worked.

The stub passed because a stub replays the reply the test author already decided on. It
exercises the plumbing around the model. It cannot exercise the prompt, which is the part
that actually produces the behaviour.

## Decision

Keep both suites, with different jobs.

`tests/test_engine.py` stubs the model and owns the plumbing: the guard, type coercion,
join discovery, chart selection, the repair path, and the end-to-end wiring. No API key,
always runnable.

`tests/test_live.py` calls the real model and owns the prompt: that unanswerable
questions are declined, that answerable ones still answer, that totals match ground
truth. It skips cleanly when no key is present.

## Consequences

Prompt regressions are caught. The decline rule was tightened and is now pinned by four
unanswerable questions, with two answerable controls to catch over-correction.

The live suite is slower, costs tokens, and is not perfectly deterministic — so it is not
a gate on every edit, and its assertions are deliberately loose where the model has
latitude (the revenue assertion accepts either the gross or net reading, because both are
defensible).

The general lesson, and the reason this ADR exists: a test double tests the code you
wrote around the dependency, never the dependency's behaviour. When the dependency *is*
the behaviour, the double proves nothing.
