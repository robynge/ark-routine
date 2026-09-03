#!/usr/bin/env python3
"""Monthly turnover of the six active ARK equity ETFs -> data/turnover/turnover.json (+ one CSV per fund).

Run by the daily refresh workflow after the holdings history and the trades CSV are current.
Requires pandas + numpy.   Usage: python3 scripts/turnover.py [--repo .] [--funds ARKK,ARKG]

Definitions (agreed 2026-09-03), all per fund, per calendar month, on month-end holdings snapshots:
  names turnover   = (names added + names removed) / names held at the start of the month
  shares turnover  = sum over names of |shares at month end - shares at month start| / total shares at month start,
                     shown including names that entered or exited (full position counts) and continuing names only
  trading turnover = total : sum over the month's trading days of |day-over-day share changes| / shares at month start
                             (every change in the holdings file, so creation/redemption activity is included)
                     active: shares listed in ARK's trade-notification emails that month / shares at month start
                     Dollar values: |share change| x that day's price (market value / shares) for total; email shares x price for active.
  Annual figures are sums of the monthly ratios (partial years scaled to 12 months in the annualized fields).
Days whose fund-wide market value spikes or dips more than 20% against both neighbouring days are dropped as bad files.
Splits: a name's share count jumping by an integer ratio while market value is unchanged restates earlier
shares to the later basis. Rows before 2021-05-06 come from Bloomberg and use today's tickers and share basis;
per-fund alias lists join SPAC / renamed tickers. Cash, money-market and currency lines are excluded.
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

FUNDS = ["ARKK", "ARKQ", "ARKW", "ARKG", "ARKF", "ARKX"]
BOUNDARY = "2021-05-06"   # first day of ARK as-reported rows; earlier rows are Bloomberg, split-adjusted to today's basis
K = np.array([2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 23, 25, 30, 33, 35, 40, 50, 60, 80, 100], float)
K = np.concatenate([K, 1 / K])
NOTES = {"ARKF": "Bloomberg rows (2019-02-07 to 2021-05-05) do not include the TCS Group GDR position ARK's own file shows at $79m on 2021-05-06, so 2021-05 counts TCS as a name added."}

ALIASES_BY_FUND = {
    "ARKG": [("VIVS", "ONVO", "Organovo (ONVO); Bloomberg rows carry the later ticker"),
             ("CMLF", "WGS", "CM Life Sciences SPAC became Sema4 (SMFR) 2021-07-26, later GeneDx (WGS)"),
             ("SMFR", "WGS", "Sema4 renamed GeneDx (WGS)"),
             ("PLUR", "PSTI", "Pluristem (PSTI) renamed Pluri (PLUR); Bloomberg rows carry the later ticker"),
             ("CMIIU", "SLGC", "CM Life Sciences II SPAC became SomaLogic (SLGC) 2021-09-03"),
             ("SRNG", "DNA", "Soaring Eagle SPAC became Ginkgo Bioworks (DNA) 2021-09-20"),
             ("DYNS", "SNTI", "Dynamics Special Purpose SPAC became Senti Bio (SNTI) 2022-06-09")],
    "ARKK": [("TWOUQ", "TWOU", "2U; Bloomberg rows carry the later ticker"),
             ("SRNG", "DNA", "Soaring Eagle SPAC became Ginkgo Bioworks (DNA) 2021-09-20"),
             ("VIVS", "ONVO", "Organovo (ONVO); Bloomberg rows carry the later ticker"),
             ("cusip:G13311132", "SLMT", "Solmate (formerly Brera Holdings) Class B shares after the 2026-05-14 reverse split; ticker blank in the files")],
    "ARKQ": [("TWOUQ", "TWOU", "2U; Bloomberg rows carry the later ticker"),
             ("VIVS", "ONVO", "Organovo (ONVO); Bloomberg rows carry the later ticker"),
             ("VELO", "VLD", "Velo3D; Bloomberg rows carry the later ticker"),
             ("SPFR", "VLD", "JAWS Spitfire SPAC became Velo3D (VLD) 2021-10-01"),
             ("EXPC", "BLDE", "Experience Investment SPAC became Blade Air Mobility (BLDE), later Strata Critical Medical (SRTA)"),
             ("AONE", "MKFG", "one (AONE) SPAC became Markforged (MKFG) 2021-07-14"),
             ("ACIC", "ACHR", "Atlas Crest SPAC became Archer Aviation (ACHR) 2021-09-17"),
             ("GLEO", "SHPWQ", "Galileo Acquisition SPAC became Shapeways (SHPW) 2021-09-30; Bloomberg rows carry the post-bankruptcy ticker"),
             ("SHPW", "SHPWQ", "Shapeways; Bloomberg rows carry the post-bankruptcy ticker"),
             ("AACT", "KDK", "Ares Acquisition II SPAC became Kodiak Robotics (KDK) 2025-09-26")],
    "ARKW": [("TWOUQ", "TWOU", "2U; Bloomberg rows carry the later ticker"),
             ("KVSB", "NXDR", "Khosla Ventures Acquisition SPAC became Nextdoor (KIND, later NXDR) 2021-11-12"),
             ("ETHQ/U", "ETHQ", "3iQ Ether Staking ETF; US-dollar units replaced the original units 2024-08-06"),
             ("cusip:G13311132", "SLMT", "Solmate (formerly Brera Holdings) Class B shares after the 2026-05-14 reverse split; ticker blank in the files")],
    "ARKF": [("ETHQ/U", "ETHQ", "3iQ Ether Staking ETF; US-dollar units replaced the original units 2024-08-06"),
             ("cusip:G13311132", "SLMT", "Solmate (formerly Brera Holdings) Class B shares after the 2026-05-14 reverse split; ticker blank in the files")],
    "ARKX": [("RTP", "JOBY", "Reinvent Technology Partners SPAC became Joby Aviation (JOBY) 2021-08-11"),
             ("VELO", "VLD", "Velo3D; Bloomberg rows carry the later ticker"),
             ("SPFR", "VLD", "JAWS Spitfire SPAC became Velo3D (VLD) 2021-10-01"),
             ("ACIC", "ACHR", "Atlas Crest SPAC became Archer Aviation (ACHR) 2021-09-17"),
             ("cusip:57064N201", "MKFG", "Markforged shares after the 2024-09-20 reverse split; ticker blank in the files")],
}


def norm_cusip(c):
    if pd.isna(c):
        return None
    c = str(c).strip().upper()
    if c == "" or "E+" in c:
        return None
    return "0" + c if re.fullmatch(r"\d{8}", c) else c


def norm_ticker(t):
    if pd.isna(t):
        return None
    t = str(t).strip().upper()
    return t.split()[0] if t else None


def load(fund, repo):
    df = pd.read_csv(Path(repo) / "data" / "consolidated" / f"{fund}.csv", dtype={"ticker": str, "cusip": str, "company": str})
    df["cusip_n"] = df.cusip.map(norm_cusip)
    df["ticker_n"] = df.ticker.map(norm_ticker)
    df["company_u"] = df.company.fillna("").str.upper().str.strip()
    df["bbg"] = df.company_u.str.contains(r"\s[A-Z]{2} EQUITY$|CURNCY$", regex=True)
    cash = ((df.ticker_n == "USD") | df.cusip_n.fillna("").str.startswith("X9") | df.cusip_n.fillna("").str.fullmatch(r"[A-Z]{3}")
            | df.company_u.str.contains(r"CURNCY|COMDTY|TRSY OBLIG|GOVT CASH|MONEY MARKET|CASH MAN", regex=True)
            | df.ticker_n.fillna("").str.fullmatch(r"[A-Z]{3}XX"))
    excluded = df[cash].groupby(["company_u", "ticker_n", "cusip_n"], dropna=False).size().reset_index(name="rows")
    df = df[~cash]
    df = df[df.shares_held.fillna(0) > 0]
    gap = df.bbg & (df.date >= BOUNDARY)          # Bloomberg fill-in days inside the as-reported era: different share basis
    gap_days = sorted(df[gap].date.unique())
    return df[~gap].copy(), excluded, gap_days


def resolve_entities(df, fund):
    aliases = [(a, b) for a, b, _ in ALIASES_BY_FUND.get(fund, [])]
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    keys = df[["ticker_n", "cusip_n", "company_u"]].drop_duplicates()
    for t, c, co in keys.itertuples(index=False):
        if t and c:
            union(("T", t), ("C", c))
    firstword = {}
    for t, c, co in keys.itertuples(index=False):
        if t and co:
            firstword.setdefault(co.split()[0], set()).add(("T", t))
    ph = keys[keys.company_u.str.contains(r"PLACE ?HOLDER|- PLACE|PLACEHOLDER|UNREGISTERED", regex=True)]
    for t, c, co in ph.itertuples(index=False):          # placeholder share lines belong to the company
        for k in firstword.get(co.split()[0], ()):
            union(("C", c) if c else ("T", t), k)
    akey = lambda x: ("C", x[6:]) if x.startswith("cusip:") else ("T", x)
    for a, b in aliases:
        union(akey(a), akey(b))

    def ent(t, c, co):
        k, v = find(("T", t)) if t else (find(("C", c)) if c else find(("N", co)))
        return f"{k}:{v}"

    df["ent"] = [ent(*r) for r in df[["ticker_n", "cusip_n", "company_u"]].itertuples(index=False)]
    lab = {}
    for e, g in df.sort_values("date").groupby("ent"):
        tk = g.ticker_n.dropna()
        lab[e] = tk.iloc[-1] if len(tk) else g.company_u.iloc[-1]
    df["name"] = df.ent.map(lab)
    return df


def _snap(k):
    """Snap a ratio to a nearby integer or reciprocal integer (within 1%); otherwise keep it."""
    for c in (float(round(k)), 1 / round(1 / k) if k < 1 and round(1 / k) else 0.0):
        if c > 0 and abs(k / c - 1) <= 0.01:
            return c
    return float(k)


def split_factor(r_s, r_mv, boundary=False):
    """Factor applied to earlier shares when a name's share count changes basis between two trading days.

    boundary=True: the pair straddles the data-source change; the basis may differ by any product of past split
    ratios, and the implied price ratio gives it (the position itself is the same on both days).
    Otherwise: a split or reverse split from the candidate list; either the share ratio or the implied price ratio must
    match the candidate almost exactly (within 3%), the other within the room left for one day's trading or price move.
    """
    if not (np.isfinite(r_s) and np.isfinite(r_mv)) or r_s <= 0 or r_mv <= 0:
        return 1.0
    r_p = r_mv / r_s
    if boundary:
        return _snap(1 / r_p) if abs(np.log(r_p)) >= np.log(1.5) and 0.7 <= r_mv <= 1.4 else 1.0
    if not 0.5 <= r_mv <= 2.0:
        return 1.0
    ks = K[np.argmin(np.abs(np.log(r_s / K)))]
    if abs(r_s / ks - 1) <= 0.03 and abs(ks * r_p - 1) <= 0.25:
        return float(ks)
    kp = K[np.argmin(np.abs(np.log(K * r_p)))]
    if abs(kp * r_p - 1) <= 0.03 and abs(r_s / kp - 1) <= 0.30:
        return float(kp)
    return 1.0


def daily_series(df):
    last_tick = lambda s: s.dropna().iloc[-1] if s.notna().any() else None
    ds = (df.groupby(["date", "ent"])
            .agg(shares=("shares_held", "sum"), mv=("market_value", "sum"), tick=("ticker_n", last_tick), company=("company_u", "last"))
            .reset_index())
    lab = df.groupby("ent").name.first()
    ds["name"] = ds.ent.map(lab)
    ds["disp"] = ds.tick.fillna(ds.company)
    ds = ds.sort_values(["ent", "date"]).reset_index(drop=True)
    g = ds.groupby("ent")
    ds["prev_date"] = g.date.shift(1)
    ds["r_s"] = ds.shares / g.shares.shift(1)
    ds["r_mv"] = ds.mv / g.mv.shift(1)
    gap = (pd.to_datetime(ds.date) - pd.to_datetime(ds.prev_date)).dt.days
    boundary = (ds.prev_date < BOUNDARY) & (ds.date >= BOUNDARY)
    ds["k"] = [split_factor(a, b, bd) if d <= 20 else 1.0 for a, b, d, bd in zip(ds.r_s, ds.r_mv, gap.fillna(999), boundary)]
    events = ds[ds.k != 1.0][["ent", "name", "prev_date", "date", "r_s", "r_mv", "k"]].copy()
    ds["adj"] = 1.0
    for ev in events.itertuples():
        ds.loc[(ds.ent == ev.ent) & (ds.date < ev.date), "adj"] *= ev.k
    ds["shares_adj"] = ds.shares * ds.adj
    return ds, events




def tick_key(t):
    """Ticker as written in emails vs holdings files: 'SOLQ.U' ~ 'SOLQ/U', 'ARCT UQ' ~ 'ARCT'."""
    if not isinstance(t, str) or not t.strip():
        return None
    return re.sub(r"[./]", "", t.strip().upper().split()[0])


def drop_bad_days(ds):
    """Drop holdings-file days whose fund-wide market value is a one-day spike or dip of more than 20% against
    BOTH neighbouring days (ARK has published files with doubled share counts, e.g. ARKK 2026-05-15). Month-end
    snapshots and day-over-day differences then bridge over the dropped day."""
    tot = ds.groupby("date").mv.sum().sort_index()
    prev, nxt = tot.shift(1), tot.shift(-1)
    spike = (tot > prev * 1.2) & (tot > nxt * 1.2)
    dip = (tot < prev * 0.8) & (tot < nxt * 0.8)
    bad = sorted(tot.index[(spike | dip).fillna(False)])
    return ds[~ds.date.isin(bad)].reset_index(drop=True), bad


def daily_trading(ds):
    """Per date: sum of |day-over-day share changes| (adjusted basis) and its dollar value, over all names."""
    sh = ds.pivot(index="date", columns="ent", values="shares_adj").sort_index().fillna(0.0)
    price = (ds.assign(p=ds.mv / ds.shares_adj).pivot(index="date", columns="ent", values="p").sort_index().ffill())
    d = sh.diff().abs()
    d.iloc[0] = 0.0                      # the first day has no prior day
    usd = (d * price.reindex_like(d)).fillna(0.0)
    return pd.DataFrame({"abs_shares": d.sum(axis=1), "abs_usd": usd.sum(axis=1)})


def active_trading(trades, ds, df):
    """Per date: shares traded per the emails and their dollar value at that day's holdings price."""
    if trades is None or trades.empty:
        return pd.DataFrame(columns=["shares", "usd", "matched"])
    key2ent = {}
    for t, e in df[["ticker_n", "ent"]].dropna().drop_duplicates().itertuples(index=False):
        key2ent.setdefault(tick_key(t), e)
    price = (ds.assign(p=ds.mv / ds.shares).pivot(index="date", columns="ent", values="p").sort_index().ffill())
    rows = []
    for r in trades.itertuples(index=False):
        e = key2ent.get(tick_key(r.Ticker))
        p = None
        if e is not None and e in price.columns:
            s = price[e][price.index <= r.Date]
            if len(s) and pd.notna(s.iloc[-1]) and r.Date >= (s.index[-1] if len(s) else ""):
                p = float(s.iloc[-1])
        rows.append((r.Date, float(r._5), float(r._5) * p if p else np.nan, p is not None))
    out = pd.DataFrame(rows, columns=["date", "shares", "usd", "matched"])
    out["matched_shares"] = out.shares.where(out.matched, 0.0)
    g = out.groupby("date")
    return pd.DataFrame({"shares": g.shares.sum(), "usd": g.usd.sum(min_count=1), "matched": g.matched_shares.sum()})


def monthly(ds, trading, active):
    ds = ds.copy(); ds["ym"] = ds.date.str[:7]
    me = ds.groupby("ym").date.max()
    snaps = {d: ds[ds.date == d].set_index("ent") for d in me}
    months = list(me.index)
    out = []
    for prev_m, m in zip(months[:-1], months[1:]):
        d0, d1 = me[prev_m], me[m]
        a, b = snaps[d0], snaps[d1]
        e0, e1 = set(a.index), set(b.index)
        cont = e0 & e1
        s0, s1 = a.shares_adj, b.shares_adj
        abs_all = sum(abs(s1.get(e, 0.0) - s0.get(e, 0.0)) for e in e0 | e1)
        abs_cont = sum(abs(s1[e] - s0[e]) for e in cont)
        base = float(s0.sum()); base_cont = float(s0[list(cont)].sum()) if cont else 0.0
        win = (trading.index > d0) & (trading.index <= d1)
        tt_sh = float(trading.abs_shares[win].sum()); tt_usd = float(trading.abs_usd[win].sum())
        awin = (active.index > d0) & (active.index <= d1) if len(active) else np.array([], bool)
        ta_sh = float(active.shares[awin].sum()) if awin.any() else None
        ta_usd = float(active.usd[awin].sum()) if awin.any() else None
        cov = float(active.matched[awin].sum() / active.shares[awin].sum()) if awin.any() and active.shares[awin].sum() > 0 else None
        out.append({"m": m, "start": d0, "end": d1, "n0": len(e0), "n1": len(e1), "add": len(e1 - e0), "rem": len(e0 - e1),
                    "nt": (len(e1 - e0) + len(e0 - e1)) / len(e0) if e0 else None,
                    "si": abs_all / base if base else None, "se": abs_cont / base_cont if base_cont else None,
                    "tt": tt_sh / base if base else None, "tt_usd": tt_usd,
                    "ta": ta_sh / base if (base and ta_sh is not None) else None, "ta_usd": ta_usd, "ta_cov": cov,
                    "sh0": base})
    last_end = pd.Timestamp(out[-1]["end"])
    if (last_end + pd.offsets.BDay(1)).month == last_end.month:
        out[-1]["partial"] = True
    return out


RATIOS = ["nt", "si", "se", "tt", "ta"]


def annual(months):
    rows = {}
    for x in months:
        if x.get("partial"):
            continue
        y = int(x["m"][:4]); r = rows.setdefault(y, {"y": y, "months": 0, "add": 0, "rem": 0, "tt_usd": 0.0, "ta_usd": 0.0, "ta_months": 0, **{k: 0.0 for k in RATIOS}})
        r["months"] += 1; r["add"] += x["add"]; r["rem"] += x["rem"]; r["tt_usd"] += x["tt_usd"] or 0.0
        for k in ("nt", "si", "se", "tt"):
            r[k] += x[k] or 0.0
        if x["ta"] is not None:
            r["ta"] += x["ta"]; r["ta_usd"] += x["ta_usd"] or 0.0; r["ta_months"] += 1
    out = []
    for y, r in sorted(rows.items()):
        for k in ("nt", "si", "se", "tt"):
            r[k + "_a"] = r[k] * 12 / r["months"]
        if r["ta_months"]:
            r["ta_a"] = r["ta"] * 12 / r["ta_months"]
        else:
            r["ta"] = None; r["ta_a"] = None; r["ta_usd"] = None
        out.append(r)
    return out


def load_trades(repo):
    p = Path(repo) / "data" / "trades" / "ark_trades.csv"
    if not p.exists():
        return None
    t = pd.read_csv(p, keep_default_na=False)
    t["Date"] = t["Date"].astype(str)
    return t


def compute_fund(fund, repo, trades):
    df, excluded, gap_days = load(fund, repo)
    df = resolve_entities(df, fund)
    ds, events = daily_series(df)
    ds, bad_days = drop_bad_days(ds)
    trading = daily_trading(ds)
    tf = trades[trades.ETF == fund] if trades is not None else None
    active = active_trading(tf, ds, df)
    months = monthly(ds, trading, active)
    return {"first_date": df.date.min(), "last_date": df.date.max(), "months": months, "annual": annual(months),
            "splits": [{"name": e["name"], "date": e["date"], "factor": e["k"], "source_change": e["prev_date"] < BOUNDARY <= e["date"]} for e in events.to_dict("records")],
            "dropped_days": bad_days,
            "note": NOTES.get(fund)}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", default="."); ap.add_argument("--funds", default=",".join(FUNDS)); a = ap.parse_args()
    repo = Path(a.repo); outdir = repo / "data" / "turnover"; outdir.mkdir(parents=True, exist_ok=True)
    trades = load_trades(repo)
    result = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "trades_through": trades.Date.max() if trades is not None else None, "funds": {}}
    for fund in a.funds.split(","):
        r = compute_fund(fund, repo, trades)
        result["funds"][fund] = r
        pd.DataFrame(r["months"]).to_csv(outdir / f"{fund}_monthly.csv", index=False)
        last = [m for m in r["months"] if not m.get("partial")][-1]
        print(f"{fund}: {len(r['months'])} months {r['months'][0]['m']}..{r['months'][-1]['m']} | {last['m']} names {last['nt']:.1%} shares {last['si']:.1%}/{last['se']:.1%} trading total {last['tt']:.1%} active {(last['ta'] or 0):.1%} (cov {last['ta_cov'] and round(last['ta_cov'], 2)})")
    (outdir / "turnover.json").write_text(json.dumps(result, separators=(",", ":"), allow_nan=False))
    print("wrote", outdir / "turnover.json", (outdir / "turnover.json").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
