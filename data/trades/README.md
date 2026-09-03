# ARK daily trades

`ark_trades.csv` — every trade line from ARK's daily "Actively Managed ETFs - Daily Trade
Information" emails (tradingdesk@arkfunds.com), one row per fund, ticker and direction,
from 2025-05-19 onward. Columns: Date, ETF, Direction, Ticker, Company Name, Shares
Traded, % of Total ETF (the email's own figures; Ticker is blank for private placements).

ARK's notices list portfolio adjustments only and exclude ETF creation/redemption basket
activity, so day-over-day changes in `data/holdings/` will not match these rows.

Refreshed by the daily workflow, which mirrors
https://ark-movers-dashboard.robynge.workers.dev/api/trades.csv (the dashboard ingests the
emails every evening; a CORRECTION re-send from ARK replaces that day's rows).
