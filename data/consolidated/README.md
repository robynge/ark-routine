# Consolidated holdings

One CSV per fund (`ARKK.csv`, `ARKG.csv`, … — 15 files), each holding every row
we have for that fund, one row per (trading date, position), oldest day first.
Rebuilt from `data/holdings/<YYYY>/<YYYY-MM-DD>/` by `scripts/build_history.py`
on every run of the daily refresh workflow.

ARKY (Active Autocallable Income ETF, inception 2026-08-19, fetched daily since
2026-08-24) appears only for days ARK published the standard schema — its CSV
format has flip-flopped: 2026-08-20/21 (backfilled by hand from ark-funds.com
downloads) use the normal holdings columns, while other days carry a dateless
autocallable-notes format that contributes no rows.

## Columns

Identical in every file. `fund` is constant within a file but kept so files
concatenate cleanly.

| column | example | notes |
|---|---|---|
| `date` | `2026-08-05` | ISO, so it sorts as text. Taken from **inside** the CSV, not the folder name. |
| `fund` | `ARKK` | matches the filename |
| `company` | `TESLA INC` | as ARK writes it |
| `ticker` | `TSLA` | empty for private venture holdings |
| `cusip` | `88160R101` | empty for private venture holdings |
| `weight` | `9.51` | percent, no `%` |
| `shares_held` | `1763749` | empty for the 3 venture funds |
| `market_value` | `577363235.15` | USD, no `$` or thousands separators |

Numeric columns keep the source's exact digits — they are never re-formatted, so
cents survive on 11-digit market values.

## Coverage

2014-10-23 → present. Three provenances, one format:

- **2026-04-29 onward** — ARK's official CSVs, fetched daily by
  `.github/workflows/refresh-csvs.yml`.
- **2021-05-06 → 2026-04-28** — backfilled from a `blog.arkinvesttrades.com`
  archive by `scripts/backfill_blog_history.py`, covering the 8 equity ETFs
  (ARKK ARKQ ARKW ARKG ARKF ARKX PRNT IZRL). Verified against ARK's own files
  on the overlap window: 320 files compared, byte-for-byte identical.
- **before 2021-05-06, plus a handful of archive gap days** — Bloomberg
  backfill of 6 funds (ARKK ARKQ ARKW ARKG ARKF ARKX), see the next section.

Per-fund first dates: ARKW 2014-10-23; ARKK / ARKG / ARKQ 2014-10-30;
ARKF 2019-02-07; ARKX 2021-03-30; PRNT / IZRL 2021-05-06; ARKB / ARKD / ARKT
and the three venture funds 2026-04-29; ARKY 2026-08-20.

## Bloomberg backfill (2026-08-24)

311,334 rows / 7,197 (date, fund) days were merged in from the local
`Transformed Data/` folder of per-fund **Bloomberg** holdings exports
(`<FUND>_Transformed_Data.xlsx`, ARKK ARKQ ARKW ARKG ARKF ARKX; those files had
themselves been gap-filled from the blog archive up to 2026-07-09 per their own
`Transformed回填记录.xlsx`). Two kinds of days came in:

- all pre-archive history (from the per-fund first dates above up to 2021-05-05);
- archive gap days the blog never had: 2021-10-29 → 2021-11-11 (per fund,
  ARKK only through 11-02), 2022-06-24 and 06-27, ARKK 2023-03-06,
  ARKX 2022-12-12.

Mechanics: `scripts/backfill_bloomberg.py` converted the xlsx into static seed
files `data/backfill_bloomberg/<FUND>.csv` (this repo's schema); on every
rebuild `scripts/build_history.py` unions those seeds in, and **the official
archive always wins** on any (date, fund) collision — so the daily workflow can
never overwrite ARK-published data with Bloomberg data.

Caveats that apply ONLY to Bloomberg-backfilled rows:

- `company` holds the Bloomberg security name (`TSLA US Equity`), not ARK's
  company name.
- `shares_held` is **split-adjusted to the current share basis** (e.g. TSLA
  ×15, NVDA ×10, GOOG ×20 on pre-split dates) — NOT as-reported for the day.
  `market_value` is actual dollars, so implied prices are split-adjusted too.
  Rows that entered the Bloomberg files via the blog conversion can carry
  fractional share counts.
- `weight` was converted from Bloomberg's fraction to percent (4 dp); a few
  rows are legitimately negative.
- Excluded by rule: weekends, all NYSE holidays, and the 2018-12-05 /
  2025-01-09 funeral closures. The source xlsx contained 8 mis-dated holiday
  blocks (2025-09-01 … 2026-05-25) whose holdings belonged to a different era
  entirely; the holiday rule drops all of them.

The pre-backfill state of every consolidated file is kept in
`backup_2026-08-24/` (same convention as the source folder's own
`backup_2026-07-03/`).

## What the rebuild deduplicates

The daily workflow stamps each folder with the calendar day it ran, so the same
data appears under several folder names:

- **weekend and holiday folders** are verbatim copies of the previous trading day
- **venture funds report monthly**, so their file is re-copied every day for weeks

The rebuild therefore keys on the date inside each file and keeps one file per
(date, fund) — preferring the copy whose folder name matches its own date. It
deduplicates per FILE, never per row, so a fund that legitimately lists the same
company twice (ARKSX holds two Sortium and two Flexport positions) keeps both.

## Known source-side quirks, carried through as-is

These come from upstream and are deliberately not "corrected":

- A few days have an incomplete file — e.g. IZRL on 2022-04-22 lists 65 holdings
  summing to 91.9% instead of ~99%. Five (date, fund) pairs sum outside 95–105%.
- The two blog-era gaps are now mostly closed by the Bloomberg backfill. Still
  missing everywhere: 2021-10-28 and 2022-06-23 (absent from Bloomberg too).
  PRNT and IZRL, which Bloomberg does not cover, keep the original gaps in full
  (2021-10-28 → 2021-11-03 and 2022-06-23 → 2022-06-28).
- ARK left the SpaceX ticker blank in the official files for 2026-06-17 only.
- 2026-05-31 is a Sunday but carries venture-fund rows, because those funds
  report at month end regardless of weekday.

One class of bad data **was** corrected: ARK renamed ARKF and ARKX around
2026-01-02 and the workflow kept fetching the old filenames, which returned a
frozen 2026-01-02 file under an HTTP 200 for months. 64 such files
(2026-04-29 → 2026-06-15) were replaced with the blog archive's correct data.

## Rebuilding by hand

```bash
python3 scripts/build_history.py            # rebuild from data/holdings/
python3 scripts/build_history.py --selftest # assertions, no I/O on the archive
```
