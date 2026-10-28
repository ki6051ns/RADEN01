# RADEN01

Qwen multi-agent market monitoring and paper-trading research.

## Scope
- Raw market data first, with source timestamps and minimal transformation.
- Role-specialized Qwen agents for monitoring, entry, and exit decisions.
- Two input modes: live market feed (subject to provider terms) and chronological CSV replay.
- Paper trading only by default; no live order placement.
- Evaluate inference latency, execution assumptions, fills, slippage, and P&L.

## Status
Initial repository bootstrap. Implementation and infrastructure setup pending.

## Data and security
Never commit broker credentials, account details, paid market data, or raw proprietary market feeds. Use permitted synthetic fixtures for public tests.
