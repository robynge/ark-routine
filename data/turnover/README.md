# ARK turnover series

Computed by `scripts/turnover.py` in the daily workflow from `data/consolidated/` (holdings) and
`data/trades/ark_trades.csv` (ARK's emailed trades), for the six active equity ETFs (ARKK, ARKQ,
ARKW, ARKG, ARKF, ARKX). Files: `turnover.json` (monthly + annual, all funds), `<FUND>_monthly.csv`,
`daily/<FUND>.json` and `daily/<FUND>_daily.csv` (one row per holdings day).

## Monthly (month-end holdings snapshots)

- `nt` names turnover = (names added + names removed) / names held at the start of the month
- `si` shares turnover including names that entered or exited = sum |shares end − start| / shares at start
  (`si_abs` = that numerator in shares, `sh0` = shares at start)
- `se` shares turnover, continuing names only (`se_abs` / `sh0c`)
- `ntd`, `sid`, `sed` = the same three measured day by day (see Daily) and summed over the month
- `tt` total trades and `ta` active trades = sums of the daily percentages (see Daily); `tt_usd` / `ta_usd`
  their dollar values; `ta_cov` = share of emailed shares matched to a holdings name; `days` = trading days
- `partial` marks the current month to date

## Daily (`daily/<FUND>.json`, parallel arrays)

Per holdings day, everything divided by what was held at the start of the day (the previous holdings day):
`n0` names, `sh0` shares, `add` / `rem` names in and out, `nt`, `si`, `se` as above but day over day,
and the trades split per name: `a` = shares actively traded per ARK's email (buy +, sell −), `d` = change
in the holdings file, `f` = `d − a` = creation/redemption basket and other changes.
`ta` active trades = Σ |a| / `sh0`; `tt` total trades = Σ (|a| + |f|) / `sh0`, so total always contains
active. Emails not matched to a holdings name count in both. `ta` is null before the emails start
(2025-05). `tt_usd`, `ta_usd`, `si_usd` price each share at that day's holdings price.

## Annual

Sums of the monthly figures per calendar year; the current year is the sum through the latest complete
month and is flagged `partial` (never scaled). `splits` lists share-basis changes applied (stock splits and
the 2021-05-06 change of data source — rows before it are Bloomberg, on today's share basis);
`dropped_days` are holdings files skipped because the fund's total market value spiked or dipped more than
20% against both neighbouring days. Cash, money-market and currency lines are excluded; SPAC and renamed
tickers are joined per the alias lists in the script.
