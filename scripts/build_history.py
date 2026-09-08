#!/usr/bin/env python3
"""Rebuild data/consolidated/<FUND>.csv -- one file per fund -- from every
per-day holdings CSV under data/holdings/<YYYY>/<YYYY-MM-DD>/.

One row per (trading date, holding), oldest day first, each new day appended
below the last. Columns (identical in every file; `fund` is constant within a
file but kept so files concatenate cleanly):

    date, fund, company, ticker, cusip, weight, shares_held, market_value

Values are cleaned for analysis, not for display: ISO dates that sort correctly,
and bare numbers with no $ , or % glyphs so a spreadsheet or pandas reads them
as numbers. The three venture funds (ARKSX/ARKUX/ARKVX) publish only a weight,
so shares_held and market_value are empty for their rows. ARKY publishes its own
dateless autocallable-notes schema, parsed separately by _read_notes.

Two facts about the source archive drive the dedupe:
  * The daily Action stamps folders with the calendar day it ran, so weekend and
    holiday folders are verbatim copies of the previous trading day's files.
  * Venture funds report monthly, so their file is re-copied every day for weeks.
Both mean the same (date, fund) shows up under many folders. We therefore key on
the date INSIDE each file and keep one file per (date, fund) -- preferring the
one whose folder name matches its own date, which is the day it was really
published. Dedupe is per FILE, never per row, so a fund legitimately holding two
rows with the same identity (buffer-ETF option legs) keeps both. ARKY's notes
files carry no date to key on, so those dedupe on content and are filed under the
earliest folder each distinct file appears under.

After scanning the archive, static seed files in data/backfill_bloomberg/
(Bloomberg-sourced pre-archive history, see scripts/backfill_bloomberg.py) are
unioned in; the archive always wins on any (date, fund) collision.

Full rebuild every run: deterministic, so re-running is a no-op the committer
sees as an empty diff, and there is no incremental-append state to drift.

Usage: build_history.py [--repo .] [--outdir data/consolidated]
"""
import argparse
import csv
import glob
import os
import re
import sys

HEADER = ["date", "fund", "company", "ticker", "cusip",
          "weight", "shares_held", "market_value"]

FOLDER_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")

# "OKLO Autocall ELN LONG TRS 38.39 PA 12/02/2026" -> OKLO
NOTES_TICKER_RE = re.compile(r"^([A-Z]{1,5}) Autocall\b")


def iso(mdy):
    """'08/05/2026' -> '2026-08-05'; passes through anything already ISO."""
    mdy = mdy.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", mdy):
        return mdy
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", mdy)
    if not m:
        return None
    mm, dd, yy = m.groups()
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


def num(s):
    """'$577,363,235.15' / '1,763,749' / '9.51%' -> bare number string.

    Strips glyphs and validates, but returns the ORIGINAL digits rather than a
    reformatted float: market values run to 11+ significant digits and any
    round-trip through a format spec silently drops cents.
    """
    s = (s or "").strip().replace(",", "").replace("$", "").replace("%", "")
    if s in ("", "-", "--", "N/A", "NA"):
        return ""
    neg = s.startswith("(") and s.endswith(")")   # accounting negatives
    if neg:
        s = s[1:-1]
    try:
        float(s)
    except ValueError:
        return ""
    return f"-{s}" if neg else s


def _read_notes(rows, header, fund, date):
    """-> [row] for ARKY's autocallable-notes schema:

        position, cusip, $ notional per note, market value ($), market weight (%)

    None of date/fund/company/ticker exist here, so the date comes from the
    archive folder and the fund from the filename. Cash and treasury rows have no
    ticker; an equity-linked note names its underlying as the leading symbol of
    the position string. `$ notional per note` is a face amount, not a share
    count, so shares_held stays empty rather than carrying a number that would
    not sum like shares -- the per-day archive keeps it if it is ever wanted.
    """
    if not date:
        return []
    i_pos = header.index("position")
    i_cusip = header.index("cusip") if "cusip" in header else None
    i_mv = next((i for i, h in enumerate(header) if h.startswith("market value")), None)
    i_wt = next((i for i, h in enumerate(header) if "weight" in h), None)

    out = []
    for r in rows[1:]:
        if len([c for c in r if c.strip()]) < 3 or len(r) <= i_pos:
            continue
        position = r[i_pos].strip()
        underlying = NOTES_TICKER_RE.match(position)
        out.append([
            date,
            fund,
            position,
            underlying.group(1) if underlying else "",
            r[i_cusip].strip() if i_cusip is not None and i_cusip < len(r) else "",
            num(r[i_wt]) if i_wt is not None and i_wt < len(r) else "",
            "",
            num(r[i_mv]) if i_mv is not None and i_mv < len(r) else "",
        ])
    return out


def read_holdings(path, fund_hint="", date_hint=""):
    """-> ([row], dateless) for one per-fund CSV.

    Standard-schema rows carry their own date, and a handful of blog-sourced
    files cover two trading days at once, so callers must group by each row's
    date rather than assume one date per file. ARKY's notes schema has no date
    column at all: those rows are dated from `date_hint` (the archive folder) and
    come back with `dateless` True, which tells the caller to dedupe them on
    content instead of on the date they were filed under.
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return [], False
    header = [h.strip().lower() for h in rows[0]]
    if "date" not in header and "position" in header:
        return _read_notes(rows, header, fund_hint, date_hint), True
    try:
        ix = {name: header.index(name) for name in ("date", "fund", "company", "ticker", "cusip")}
    except ValueError:
        return [], False
    # 11 ETFs carry shares + market value; the 3 venture funds carry neither
    i_shares = header.index("shares") if "shares" in header else None
    i_mv = next((i for i, h in enumerate(header) if h.startswith("market value")), None)
    i_wt = next((i for i, h in enumerate(header) if h.startswith("weight")), None)

    out = []
    for r in rows[1:]:
        # ARK appends a one-cell legal disclaimer to every file
        if len([c for c in r if c.strip()]) < 3 or len(r) <= max(ix.values()):
            continue
        d = iso(r[ix["date"]])
        if not d:
            continue
        out.append([
            d,
            r[ix["fund"]].strip() or fund_hint,
            r[ix["company"]].strip(),
            r[ix["ticker"]].strip(),
            r[ix["cusip"]].strip(),
            num(r[i_wt]) if i_wt is not None and i_wt < len(r) else "",
            num(r[i_shares]) if i_shares is not None and i_shares < len(r) else "",
            num(r[i_mv]) if i_mv is not None and i_mv < len(r) else "",
        ])
    return out, False


def selftest():
    import tempfile
    assert iso("08/05/2026") == "2026-08-05"
    assert iso("1/2/2026") == "2026-01-02"
    assert iso("2026-08-05") == "2026-08-05"
    assert iso("garbage") is None
    assert num("$577,363,235.15") == "577363235.15"
    assert num("1,763,749") == "1763749"
    assert num("9.51%") == "9.51"
    assert num("(1,234.5)") == "-1234.5"
    assert num("") == "" and num("N/A") == "" and num("-") == ""

    d = tempfile.mkdtemp()
    etf = ("date,fund,company,ticker,cusip,shares,market value ($),weight (%)\n"
           '{d},ARKK,TESLA INC,TSLA,88160R101,"1,000","$2,000.00",10.00%\n'
           '"Holdings are subject to change."\n')
    # a real trading day
    os.makedirs(os.path.join(d, "data", "holdings", "2026", "2026-01-05"))
    open(os.path.join(d, "data", "holdings", "2026", "2026-01-05", "ARKK_Holdings_2026-01-05.csv"),
         "w").write(etf.format(d="01/05/2026"))
    # weekend folder: verbatim copy of Friday, must collapse
    os.makedirs(os.path.join(d, "data", "holdings", "2026", "2026-01-06"))
    open(os.path.join(d, "data", "holdings", "2026", "2026-01-06", "ARKK_Holdings_2026-01-06.csv"),
         "w").write(etf.format(d="01/05/2026"))
    # venture fund: weight only, no shares/market value
    open(os.path.join(d, "data", "holdings", "2026", "2026-01-05", "ARKVX_Holdings_2026-01-05.csv"),
         "w").write("date,fund,company,ticker,cusip,weight (%)\n"
                    "01/05/2026,ARKVX,OpenAI,,,6.18%\n")
    # ARKY: dateless notes schema, re-copied verbatim into the weekend folder
    notes = ("position,cusip,$ notional per note,market value ($),market weight (%)\n"
             'GOLDMAN FS TRSY OBLIG INST 468,X9USDGSFT,"20,291,640","$20,291,639.79",82.16%\n'
             'TSLA Autocall ELN LONG TRS 20.47 PA 07/30/2027,1718170,"675,000","$41,372.00",0.13%\n'
             'OKLO Autocall ELN LONG TRS 38.39 PA 12/02/2026,1718116,"675,000","-$59,304.00",-0.19%\n'
             '"Holdings are subject to change."\n')
    for day in ("2026-01-05", "2026-01-06"):
        open(os.path.join(d, "data", "holdings", "2026", day, f"ARKY_Holdings_{day}.csv"),
             "w").write(notes)

    # Bloomberg seed: fills 2026-01-02; loses the 2026-01-05 collision to the archive
    os.makedirs(os.path.join(d, "data", "backfill_bloomberg"))
    open(os.path.join(d, "data", "backfill_bloomberg", "ARKK.csv"), "w").write(
        "date,fund,company,ticker,cusip,weight,shares_held,market_value\n"
        "2026-01-02,ARKK,TSLA US Equity,TSLA,88160R101,9.00,900,1800.00\n"
        "2026-01-05,ARKK,SHOULD LOSE,TSLA,88160R101,1.00,1,1.00\n")

    rows, dateless = read_holdings(os.path.join(d, "data", "holdings", "2026", "2026-01-05",
                                                "ARKK_Holdings_2026-01-05.csv"), "ARKK")
    assert not dateless
    assert rows == [["2026-01-05", "ARKK", "TESLA INC", "TSLA", "88160R101",
                     "10.00", "1000", "2000.00"]], rows     # disclaimer dropped

    outdir = os.path.join("data", "consolidated")
    sys.argv = ["build_history.py", "--repo", d, "--outdir", outdir]
    main()
    arkk = list(csv.reader(open(os.path.join(d, outdir, "ARKK.csv"), newline="")))
    arkvx = list(csv.reader(open(os.path.join(d, outdir, "ARKVX.csv"), newline="")))
    assert arkk[0] == HEADER and arkvx[0] == HEADER
    assert len(arkk) == 3, arkk        # seed 01-02 + archive 01-05; weekend copy gone
    assert arkk[1][0] == "2026-01-02" and arkk[1][2] == "TSLA US Equity", arkk[1]
    assert arkk[2][0] == "2026-01-05" and arkk[2][2] == "TESLA INC", arkk[2]  # archive beat seed
    assert len(arkvx) == 2, arkvx
    assert arkvx[1][1] == "ARKVX"
    assert arkvx[1][6] == "" and arkvx[1][7] == "", arkvx[1]  # venture: blank shares + mv

    arky = list(csv.reader(open(os.path.join(d, outdir, "ARKY.csv"), newline="")))
    assert arky[0] == HEADER
    assert len(arky) == 4, arky                          # weekend copy collapsed
    assert {r[0] for r in arky[1:]} == {"2026-01-05"}    # dated from the folder
    assert arky[1][2].startswith("GOLDMAN") and arky[1][3] == "", arky[1]   # cash: no ticker
    assert arky[2][3] == "TSLA" and arky[3][3] == "OKLO", arky   # ELN underlyings
    assert arky[2][6] == "", arky[2]                     # notional is not a share count
    assert arky[2][7] == "41372.00" and arky[3][7] == "-59304.00", arky
    assert arky[3][5] == "-0.19", arky[3]                # negative weight survives
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(__file__), ".."))
    ap.add_argument("--outdir", default=os.path.join("data", "consolidated"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    holdings = os.path.join(args.repo, "data", "holdings")
    chosen = {}   # (date, fund) -> (folder_matches_that_date, rows)
    seen_notes = set()   # content of every dateless file kept so far
    silent = []   # files that parsed to nothing -- what a schema change looks like
    scanned = groups = 0
    for path in sorted(glob.glob(os.path.join(holdings, "*", "*", "*.csv"))):
        folder = os.path.basename(os.path.dirname(path))
        if not FOLDER_RE.fullmatch(folder):
            continue          # skips the LATEST symlink
        scanned += 1
        rows, dateless = read_holdings(path, date_hint=folder,
                                       fund_hint=os.path.basename(path).split("_")[0])
        if not rows:
            silent.append(os.path.basename(path))
            continue
        if dateless:
            # The folder is the only date these have, so a weekend or holiday
            # copy of the last published file would otherwise land as a trading
            # day of its own. Paths are walked oldest first, so the folder a
            # given file first appears under is the day it was really published.
            sig = (rows[0][1], tuple(tuple(r[2:]) for r in rows))
            if sig in seen_notes:
                groups += 1        # counted so the dropped-copies tally stays honest
                continue
            seen_notes.add(sig)
        # a file may cover more than one trading day, so split before choosing
        per_key = {}
        for r in rows:
            per_key.setdefault((r[0], r[1]), []).append(r)
        for key, rws in per_key.items():
            groups += 1
            canonical = (folder == key[0])
            prev = chosen.get(key)
            # keep the copy filed on its own date; otherwise first one wins
            if prev is None or (canonical and not prev[0]):
                chosen[key] = (canonical, rws)

    # Bloomberg seed: pre-archive history and gap days the per-day archive never
    # had (see scripts/backfill_bloomberg.py). Archive wins on any collision.
    seed_files = sorted(glob.glob(os.path.join(args.repo, "data", "backfill_bloomberg", "*.csv")))
    seed_groups = 0
    for path in seed_files:
        per_key = {}
        with open(path, newline="", encoding="utf-8") as fh:
            r = csv.reader(fh)
            next(r, None)
            for row in r:
                if len(row) == len(HEADER):
                    per_key.setdefault((row[0], row[1]), []).append(row)
        for key, rws in per_key.items():
            if key not in chosen:
                chosen[key] = (True, rws)
                seed_groups += 1

    by_fund = {}
    for _, rws in chosen.values():
        for r in rws:
            by_fund.setdefault(r[1], []).append(r)

    out_dir = os.path.join(args.repo, args.outdir)
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for fund in sorted(by_fund):
        rows = by_fund[fund]
        # oldest day first; within a day, largest position first
        rows.sort(key=lambda r: (r[0], -(float(r[5]) if r[5] else 0.0)))
        with open(os.path.join(out_dir, f"{fund}.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(rows)
        total += len(rows)

    dates = sorted({r[0] for rws in by_fund.values() for r in rws})
    print(f"scanned {scanned} files -> {groups} (date,fund) groups -> kept {len(chosen) - seed_groups} "
          f"({groups - (len(chosen) - seed_groups)} duplicate copies dropped)")
    if seed_files:
        print(f"+ {seed_groups} (date,fund) groups from {len(seed_files)} Bloomberg seed files")
    print(f"wrote {len(by_fund)} per-fund files to {out_dir}")
    print(f"  {total:,} rows | {len(dates)} dates {dates[0]}..{dates[-1]} | {len(by_fund)} funds")
    print(f"  {', '.join(sorted(by_fund))}")
    # ARK changed ARKY's schema on 2026-08-24 and every file was dropped in
    # silence for three weeks, because a file this parser cannot read looks
    # exactly like a file that is not there. Name them instead.
    if silent:
        print(f"  WARNING: {len(silent)} archived file(s) parsed to zero rows -- "
              f"unrecognised schema? {', '.join(sorted(silent)[:5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
