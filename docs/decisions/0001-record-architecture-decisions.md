# 1. Record architecture decisions

Date: 2026-09-18

## Status

Accepted

## Context

This project makes several choices that look arbitrary from the outside and are not
recoverable from the code. Why containment and not Jaccard. Why the model never sees a
row. Why exactly one repair retry. A reader who cannot reconstruct the reasoning has to
either trust the choice or relitigate it.

## Decision

Record each architecturally significant decision as a numbered file in `docs/decisions/`,
using Michael Nygard's format: Context, Decision, Consequences. An ADR is immutable once
accepted; a reversal is a new ADR that supersedes the old one rather than an edit.

The bar is deliberately high: a decision earns an ADR only when a competent engineer
could reasonably have chosen otherwise. Standard practice does not need a file.

## Consequences

The reasoning survives the author. Decisions are challengeable on their recorded
grounds rather than by guesswork.

The cost is discipline: ADRs written for decisions that were never really decisions turn
the directory into ceremony, and a stale one is worse than none. Five records for a
project this size is already near the ceiling.
