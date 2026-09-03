# ARK turnover series

`turnover.json` — monthly turnover of the six active ARK equity ETFs (ARKK, ARKQ, ARKW, ARKG, ARKF, ARKX),
computed by `scripts/turnover.py` from `data/consolidated/` (holdings) and `data/trades/ark_trades.csv`
(ARK's emailed trades). `<FUND>_monthly.csv` holds the same monthly rows per fund.

Per fund and calendar month, on month-end holdings snapshots:

- `nt` names turnover = (names added + names removed) / names at the start of the month
- `si` shares turnover including names that entered or exited = sum |shares end − start| / shares at start
- `se` shares turnover, continuing names only (both numerator and denominator on the names held at both ends)
- `tt` trading turnover, total = sum over the month's trading days of |day-over-day share changes| / shares at start
  (every change in the holdings file, so creation/redemption activity is included; a redemption that offsets a
  purchase in the same name on the same day nets out, which is why `ta` can exceed `tt`)
- `ta` trading turnover, active = shares in ARK's trade-notification emails that month / shares at start
  (`ta_cov` = share of those email shares matched to a holdings price; `null` before the emails start, 2025-05)
- `tt_usd` / `ta_usd` = the same in dollars at that day's holdings price; `sh0` = shares at month start
- `partial` marks the current month to date

`dropped_days` lists holdings-file days skipped as inconsistent: the fund's total market value spikes or dips more than
20% against both neighbouring days (ARK has published files with doubled share counts, e.g. ARKK 2026-05-15); month-end
snapshots and day-over-day differences bridge over them. Multi-day swings in the file are kept as published, so a month
in which ARK's files show the whole fund's share count moving by 15–20% for a few days reports a high `tt`.

`annual` sums the monthly ratios per calendar year (`*_a` = scaled to 12 months for partial years). `splits`
lists share-basis changes applied (stock splits, and the 2021-05-06 change of data source — rows before it are
Bloomberg, on today's share basis). Cash, money-market and currency lines are excluded; SPAC and renamed
tickers are joined per the alias lists in the script.

Consumed by the ARK Movers dashboard (`/turnover`), which mirrors this file into KV once a day.
