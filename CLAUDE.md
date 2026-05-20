# Lil Tony Trading Brain — Project Rules

> Inherits all rules from Big Tony's House Rules (~/.CLAUDE.md).
> The rules below are additive and specific to this trading bot project.

## Project Overview
Lil Tony is an automated trading bot. All code in this project touches real money,
real market data, and real accounts. Treat every action accordingly.

## T1 — Never trade with real money without explicit approval
Paper trade by default. Any switch from paper to live trading requires Big Tony's
explicit go-ahead. Confirm the environment (paper vs. live) before every run.

## T2 — No order without a safety check
All buy/sell/cancel orders must pass a pre-flight check: position size limits,
account balance verification, and duplicate-order guard. No exceptions.

## T3 — API keys and broker credentials are sacred
Broker tokens, API keys, and account IDs go in environment variables only.
Never hardcode. Never log. Never print. Covered by R6 — emphasized here because
the stakes are financial.

## T4 — Log everything, lose nothing
Every order attempt, fill, rejection, and error must be logged with a timestamp.
Logs are the audit trail. Keep them complete and never truncate silently.

## T5 — Fail safe, not open
If the bot loses connectivity, hits an unexpected error, or can't confirm an order
status — halt and alert. Do not retry blindly. Do not assume success.

## T6 — Strategy changes require review
Any modification to trading logic, signal thresholds, position sizing, or risk
parameters must be reviewed by Big Tony before going live. Flag the diff clearly.

## T7 — Market hours awareness
Know the market schedule. Do not place orders outside valid trading hours unless
the strategy explicitly supports extended hours and Big Tony has approved it.

## T8 — No external calls outside approved endpoints
Only connect to pre-approved broker APIs and data feeds. No scraping, no
unapproved third-party services, no tunneling outside the house (see R9).

## T9 — Strategies are under active review
All trading strategies (Momentum Breakout, Mean Reversion, Unusual Options Activity)
are currently in review phase. Do not treat any signal as approved for live execution.
Big Tony retains full authority to review, modify, or reject any strategy at any time.

## T10 — Full trade history is always accessible to Big Tony
The complete alert and signal log lives at logs/alerts.jsonl. Every signal fired must
be logged there with full detail (ticker, contract, entry, target, stop, strategy,
score, timestamp). Big Tony can review, audit, or export this log at any time.
Never delete, truncate, or overwrite this file.
