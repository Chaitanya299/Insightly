---
# gstack: design-md-format=spec
name: Insightly
description: Audit-grade calm. A precise, well-made financial report that happens to answer questions.
colors:
  ink: "#0E1B2C"
  on-ink: "#E8ECF1"
  on-ink-muted: "#8D99AB"
  canvas: "#F6F7F5"
  surface: "#FFFFFF"
  surface-sunk: "#EEF0EE"
  text: "#0E1B2C"
  text-muted: "#5B6472"
  hairline: "#E3E6E8"
  primary: "#2A78D6"
  on-primary: "#FFFFFF"
  primary-hover: "#256ABF"
  verified: "#0F7B5F"
  verified-tint: "#E6F2EE"
  warning: "#B26B00"
  error: "#C23B3B"
typography:
  display:
    fontFamily: Satoshi
    fontWeight: 700
    fontSize: 2rem
    letterSpacing: -0.02em
  body:
    fontFamily: Satoshi
    fontSize: 1rem
    lineHeight: 1.55
  label:
    fontFamily: JetBrains Mono
    fontSize: 0.72rem
    letterSpacing: 0.06em
  mono:
    fontFamily: JetBrains Mono
    fontFeature: tnum
rounded:
  sm: 6px
  md: 10px
  lg: 14px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
  nav-link:
    textColor: "{colors.on-ink}"
  verified-stamp:
    backgroundColor: "{colors.verified-tint}"
    textColor: "{colors.verified}"
    rounded: "{rounded.full}"
---

# Insightly

## Overview

**Creative North Star:** audit-grade ledger. Calm, exact and quietly expensive, because the
product's one promise is that every number can be trusted.
**Product context:** an AI data Q&A web app. Analysts upload CSV/Excel files and ask
questions; the model writes SQL and DuckDB computes the answer. The audience is analysts
and the hiring panels evaluating the build. Peers: Hex, Mode, Metabase.
**Mode per surface:** Upload is Persuade then Operate; Ask, Dashboard and Data are Operate;
Quality is Read.
**Key characteristics:**
- A deep-ink sidebar that shows the whole journey as numbered steps, 01 to 05.
- A bright, quiet canvas where the data is the only colour.
- Every figure set in tabular mono, so columns of numbers line up like a ledger.
- A green "verified" stamp wherever a number came from DuckDB, and nowhere else.

## Colors

**Strategy:** restrained. Neutrals plus one data blue; green is reserved for verification.
**Light or dark:** light canvas, because the use scene is desk work in daylight over long
sessions. The ink sidebar anchors the page and carries the navigation without darkening
the working area.
`primary` marks interaction (buttons, the active nav step, chart series 1). `verified` is
a semantic colour, not decoration: it appears only on the stamp and on correct scores.
Chart colours follow the CVD-validated categorical order in `src/app.py`.

## Typography

Satoshi (Fontshare) carries headings and UI: a crisp modern grotesk with more character
than the category's Inter default, and it stays neutral enough for dense tables.
JetBrains Mono with tabular figures sets every number, SQL query and micro-label, so the
figures read as figures. The heading scale jumps clearly (2rem, 1.25rem, 1rem); labels are
small uppercase mono. Both are loaded by URL through Streamlit's theme config.

## Layout

A fixed ink sidebar holds the brand, the five numbered steps, and a receipt of what's
loaded. The content column caps at 1200px. Each page opens with a title and one plain
sentence of purpose; there are no kickers above titles. The spacing unit is 8px: 24px
between blocks, 48px between page sections.

## Elevation & Depth

Mostly flat. Surfaces separate by a 1px hairline on the canvas. The one lifted element is
the "Insightly" comparison card (offset 0 6px, 24px blur, blue at 10%) because it is the
answer to "why this?". There are no glows and no zero-offset halos.

## Shapes

Cards use 14px radius, inputs and buttons 10px, stamps and pills are fully rounded. Nested
radii subtract the gap. There are no cards inside cards.

## Components

- **Nav step:** mono number and a label. Active: primary text and a surface tint.
- **Verified stamp:** a mono uppercase label on a green tint, e.g. `✓ VERIFIED · DUCKDB · 4 ROWS`.
- **KPI tile:** surface, hairline, a mono value, a muted label.
- **Empty state:** says what to do next and links to 01 Upload.

## Do's and Don'ts

- Do set every number in JetBrains Mono with tabular figures.
- Do use green only for "verified" and correct scores.
- Do give every page a title plus one sentence that says what it's for.
- Don't use gradients, glows, blobs or emoji as decoration.
- Don't use coloured side borders on cards; signal state with a tint or a label.
- Don't centre body text.

## Motion

- **Approach:** minimal-functional
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro 80ms, short 180ms
- **The one authored moment:** the score bars in the comparison fill on load.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-18 | Initial design system created | /design-consultation: memorable thing is "trustworthy numbers"; Streamlit multipage kept for demo safety |
