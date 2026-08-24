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

2021-05-06 → present. Two provenances, identical in format:

- **2026-04-29 onward** — ARK's official CSVs, fetched daily by
  `.github/workflows/refresh-csvs.yml`. All 14 funds.
- **before that** — backfilled from a `blog.arkinvesttrades.com` archive by
  `scripts/backfill_blog_history.py`, covering the 8 equity ETFs (ARKK ARKQ ARKW
  ARKG ARKF ARKX PRNT IZRL). Verified against ARK's own files on the overlap
  window: 320 files compared, byte-for-byte identical.

So rows before 2026-04-29 exist only for those 8 funds; ARKB, ARKD, ARKT and the
three venture funds start at 2026-04-29; ARKY starts 2026-08-20.

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
- Two gaps longer than a weekend: 2021-10-28 → 2021-11-03, and
  2022-06-23 → 2022-06-28.
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
