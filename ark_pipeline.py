#!/usr/bin/env python3
"""ARK Twitter pipeline — FETCH ONLY.

Renders/PDF generation has been moved to the private robynge/x-api repo.
This file is only used by the GitHub Actions in .github/workflows/ to
populate data/holdings/ and data/tweets/.
"""
import csv, io, json, os, re, sys, threading, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

OUT_ROOT = Path(os.environ.get("ARK_OUT_ROOT", "/tmp/ark_run"))
TWITTERAPI_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")
BASE_URL = "https://api.twitterapi.io"

# CSVs are pre-downloaded by the agent (via Google-Drive MCP) into
# /tmp/ark_run/<DATE>/csvs/<ETF>_Holdings_<DATE>.csv. We only need
# to know the ETF names to look for and to recognize them as ETF
# tickers (so we don't include them as company holdings).
ETF_TICKERS = {
    "ARKK","ARKG","ARKQ","ARKW","ARKB","ARKVX","ARKX","ARKF",
    "IZRL","PRNT","ARKD","ARKT","ARKUX","ARKSX",
}

NON_COMPANY_PATTERNS = [
    re.compile(r"\b\d+/\d+/\d+\b"),
    re.compile(r"\bHOLDCO\b", re.I),
    re.compile(r"\bMMKT\b", re.I),
    re.compile(r"\bMONEY MARKET\b", re.I),
    re.compile(r"\bTRSY OBLIG", re.I),
    re.compile(r"\bTREASURY OBLIG", re.I),
    re.compile(r"\bGOVT (CASH|MMKT)", re.I),
    re.compile(r"\bCASH MGMT", re.I),
    re.compile(r"\bSWEEP\b", re.I),
    re.compile(r"\bLIQUIDITY (FD|FUND)", re.I),
    re.compile(r"\bFEDFUND\b", re.I),
]

COMMON_WORD_NAMES = {
    "caterpillar","hp","nova","firmly","allot","lambda","bullish",
    "discord","block","toast","illumina","alphabet","3m",
    "match","snap","zoom","square",
}

SUFFIX_RE = re.compile(
    r"(?:\s+INCORPORATED|\s+CORPORATION|\s+COMPANY|\s+LIMITED|\s+HOLDINGS|"
    r"\s+GROUP|\s+TRUST|\s+ETF|\s+INC\.?|\s+CORP\.?|\s+LTD\.?|\s+CO\.?|"
    r"\s+AG|\s+PLC|\s+SA|\s+NV|\s+LLC)\.?\s*$", re.I,
)
CLASS_SUFFIX_RE = re.compile(r"[\s,]*-\s*(?:CL\.?|CLASS)\s*[A-Z]\s*$", re.I)
TRAILING_LETTER_RE = re.compile(r"\s*-\s*[A-Z]\s*$")
BLOOMBERG_RE = re.compile(r"^([A-Z]+)\s+[A-Z]{2}$")
CORP_SUFFIX_RE = re.compile(
    r"\b(inc|corp|corporation|incorporated|ltd|limited|company|holdings|"
    r"trust|co|ag|plc|sa|llc|nv)\b\.?\s*$", re.I,
)

PROMO_KEYWORDS = [
    "follow @","follow me","dm me","guaranteed","$1000/day","/day",
    "always go up","never down","make a fortune","get rich",
]
CASHTAG_RE = re.compile(r"\$[A-Z]+")
HASHTAG_RE = re.compile(r"#\w+")
TWITTER_DATE_FMT = "%a %b %d %H:%M:%S %z %Y"

def slug_priv_ticker(company):
    s = re.sub(r"[^A-Za-z0-9]+", "_", (company or "").upper()).strip("_")
    return s or "UNKNOWN_PRIVATE"

def find_weight_key(keys):
    for k in keys:
        if not k: continue
        n = k.replace("(","").replace(")","").replace("%","").replace(" ","").lower()
        if "weight" in n: return k
    return None

def parse_csv_text(text, etf):
    rows = []
    rdr = csv.DictReader(io.StringIO(text))
    keys = list(rdr.fieldnames or [])
    wk = find_weight_key(keys)
    for r in rdr:
        norm = {(k or "").strip().lower():(v or "").strip() for k,v in r.items()}
        raw = (r.get(wk) or "").strip() if wk else ""
        if not raw:
            raw = norm.get("weight (%)") or norm.get("weight") or ""
        cleaned = raw.replace("%","").strip()
        try: w = float(cleaned) if cleaned else 0.0
        except ValueError: w = 0.0
        if w == 0.0: continue
        ticker = norm.get("ticker") or norm.get("symbol") or ""
        company = norm.get("company") or norm.get("name") or norm.get("company name") or ""
        if not ticker and not company: continue
        if not ticker and company:
            ticker = slug_priv_ticker(company)
        rows.append({
            "etf": etf, "ticker": ticker, "company": company,
            "weight": w, "is_private": not (norm.get("ticker") or norm.get("symbol")),
        })
    return rows

def fetch_holdings(target_date):
    """Read pre-downloaded CSVs from /tmp/ark_run/<DATE>/csvs/.
    The agent downloads these via Google-Drive MCP before running fetch.
    Filename pattern: {ETF}_Holdings_{DATE}.csv (e.g. ARKK_Holdings_2026-04-29.csv).
    Tolerates missing files (some ETFs might not be published on a given day)."""
    csv_dir = OUT_ROOT / target_date / "csvs"
    if not csv_dir.exists():
        sys.exit(f"[err] CSV directory missing: {csv_dir}\n"
                 f"      Agent must download CSVs via Google-Drive MCP first.")
    rows = []
    found_etfs = []
    missing_etfs = []
    for etf in ETF_TICKERS:
        # accept either {ETF}_Holdings_{date}.csv (preferred) or any file
        # starting with {ETF}_Holdings_ (fallback)
        candidates = sorted(csv_dir.glob(f"{etf}_Holdings_*.csv"))
        if not candidates:
            missing_etfs.append(etf)
            continue
        # prefer one matching target_date exactly; else use the latest by name
        exact = csv_dir / f"{etf}_Holdings_{target_date}.csv"
        path = exact if exact.exists() else candidates[-1]
        try:
            text = path.read_text(encoding="utf-8")
            rows.extend(parse_csv_text(text, etf))
            found_etfs.append(etf)
        except Exception as e:
            print(f"[warn] failed to read {path.name}: {e}", file=sys.stderr)
            missing_etfs.append(etf)
    print(f"[ark] holdings: read {len(found_etfs)}/{len(ETF_TICKERS)} ETFs from {csv_dir}")
    if missing_etfs:
        print(f"[ark] missing: {','.join(sorted(missing_etfs))}")
    return rows

def is_non_company(company):
    return any(p.search(company or "") for p in NON_COMPANY_PATTERNS)

def unique_companies(rows):
    by_t = {}
    for r in rows:
        t = r["ticker"]
        if t in ETF_TICKERS: continue
        if is_non_company(r["company"]): continue
        if t not in by_t:
            by_t[t] = {"ticker":t,"company":r["company"],"is_private":r["is_private"],
                       "weight_sum":r["weight"],"etfs":[r["etf"]]}
        else:
            by_t[t]["weight_sum"] += r["weight"]
            by_t[t]["etfs"].append(r["etf"])
    return list(by_t.values())

def clean_company_name(name):
    s = (name or "").strip().rstrip(".,")
    if not s: return ""
    prev = None
    while s != prev:
        prev = s
        s = SUFFIX_RE.sub("", s).rstrip(" ,.-")
        m = re.match(r"^(.+?)[\s,]*-\s*(?:CL\.?|CLASS)?\s*[A-Z]\s*$", s, re.I)
        if m: s = m.group(1).strip(" ,.-")
    if not s: return ""
    if s.isupper():
        s = " ".join(w[:1].upper()+w[1:].lower() for w in s.split())
    return s

def disambiguating_name(company):
    s = (company or "").strip().rstrip(".")
    if not s: return ""
    prev = None
    while s != prev:
        prev = s
        s = CLASS_SUFFIX_RE.sub("", s).strip(" ,.-")
        s = TRAILING_LETTER_RE.sub("", s).strip(" ,.-")
    if s.isupper():
        s = " ".join(w[:1].upper()+w[1:].lower() for w in s.split())
    return s

def has_corp_suffix(name):
    return bool(CORP_SUFFIX_RE.search(name or ""))

def cashtag_ticker(t):
    if not t: return None
    m = BLOOMBERG_RE.match(t)
    if m: return m.group(1)
    if "_" in t or not t.isalpha(): return None
    return t

def build_query(ticker, company, is_private=False, since=None, until=None,
                since_time=None, until_time=None, min_followers=1000):
    """Build a Twitter advanced-search query string.

    Time filters: prefer `since_time` / `until_time` (Unix epoch seconds) for
    sub-day windows; fall back to `since` / `until` (YYYY-MM-DD) for daily runs.

    Filter: `min_followers:N` — only return tweets from authors with >= N
    followers. Replaces the previous `min_faves:10` (likes-based) filter,
    which was too aggressive for small-cap ARK names where retail panic
    tweets typically sit at 0-3 likes.
    """
    cleaned = clean_company_name(company)
    ambig = bool(cleaned) and cleaned.lower() in COMMON_WORD_NAMES
    keyword = None
    if cleaned and not ambig:
        keyword = cleaned
    elif ambig:
        full = disambiguating_name(company)
        if has_corp_suffix(full):
            keyword = full
    cashtag = None if is_private else cashtag_ticker(ticker)
    sub = []
    if cashtag: sub.append(f"${cashtag}")
    if keyword and (not cashtag or keyword.upper() != cashtag):
        sub.append(f'"{keyword}"')
    if not sub: return None
    base = sub[0] if len(sub)==1 else f"({' OR '.join(sub)})"
    parts = [base, "-is:retweet", f"min_followers:{min_followers}"]
    if since_time is not None:
        parts.append(f"since_time:{int(since_time)}")
    elif since:
        parts.append(f"since:{since}")
    if until_time is not None:
        parts.append(f"until_time:{int(until_time)}")
    elif until:
        parts.append(f"until:{until}")
    return " ".join(parts)

def advanced_search(query, query_type="Top", cursor="", retries=4):
    headers = {"x-api-key": TWITTERAPI_KEY}
    params = {"query": query, "queryType": query_type}
    if cursor: params["cursor"] = cursor
    delay = 2.0
    for a in range(retries):
        r = requests.get(f"{BASE_URL}/twitter/tweet/advanced_search",
                         headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            if a == retries-1: r.raise_for_status()
            time.sleep(delay); delay *= 2; continue
        r.raise_for_status()
        return r.json()
    return {}

def paginated_search(query, max_pages=3, pace=1.0):
    all_t = []; cursor = ""
    for _ in range(max_pages):
        r = advanced_search(query, "Top", cursor)
        page = r.get("tweets", [])
        all_t.extend(page)
        if not page or not r.get("has_next_page"): break
        cursor = r.get("next_cursor","")
        if not cursor: break
        time.sleep(pace)
    return all_t

def account_age_days(created_at):
    if not created_at: return None
    try: c = datetime.strptime(created_at, TWITTER_DATE_FMT)
    except ValueError: return None
    return (datetime.now(timezone.utc) - c).total_seconds()/86400

def is_spam(tweet):
    text = tweet.get("text") or ""
    tl = text.lower()
    a = tweet.get("author") or {}
    nc = len(CASHTAG_RE.findall(text))
    if nc > 3: return True, f"cashtags={nc}"
    nh = len(HASHTAG_RE.findall(text))
    if nh > 5: return True, f"hashtags={nh}"
    for kw in PROMO_KEYWORDS:
        if kw in tl: return True, f"promo='{kw}'"
    if a.get("isAutomated"): return True, "self_declared_bot"
    fol = a.get("followers")
    if fol is None or fol < 50: return True, f"followers={fol}"
    age = account_age_days(a.get("createdAt"))
    if age is not None and age < 30: return True, f"account_age={age:.0f}d"
    fol2 = fol or 0; flw = a.get("following") or 0
    if fol2 > 0 and flw/fol2 > 10: return True, f"follow_ratio={flw}/{fol2}"
    return False, ""

def cmd_fetch(target_date, window_hours=2):
    """Fetch tweets in a rolling [now - window_hours, now] window and merge into
    today's per-ticker JSONs. Designed to run every `window_hours` hours so
    today's file accumulates the full day as runs progress (dedup by tweet id).
    """
    # Per-ticker JSONs live under <ARK_OUT_ROOT>/<DATE>/tweets/<TICKER>.json.
    out_dir = OUT_ROOT / target_date / "tweets"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not TWITTERAPI_KEY:
        sys.exit("TWITTERAPI_IO_KEY env var missing")

    now = datetime.now(timezone.utc)
    until_time = int(now.timestamp())
    nominal_since = int((now - timedelta(hours=window_hours)).timestamp())

    # Gap-protection: if the previous fetch's until_time is OLDER than
    # nominal_since (because GitHub Actions delayed/skipped a scheduled run),
    # extend `since` backwards to the previous until_time minus a 5-min safety
    # overlap. This guarantees no tweets fall into a gap between consecutive
    # fetches, no matter how late the runner is.
    prev_until = None
    # Look in today's _summary.json first, then yesterday's (to handle
    # the first run of a new UTC day).
    candidate_summaries = [
        OUT_ROOT / target_date / "tweets" / "_summary.json",
        OUT_ROOT / (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
                 / "tweets" / "_summary.json",
    ]
    for s_path in candidate_summaries:
        if s_path.exists():
            try:
                s = json.loads(s_path.read_text())
                lw = s.get("last_window") or {}
                if lw.get("until_time"):
                    prev_until = int(lw["until_time"])
                    break
            except Exception:
                pass
    if prev_until is not None and prev_until < nominal_since:
        since_time = prev_until - 300  # 5-min overlap for safety
        print(f"[ark] gap detected: prev fetch ended {nominal_since - prev_until}s "
              f"before nominal window — extending since backwards")
    else:
        since_time = nominal_since

    actual_hours = (until_time - since_time) / 3600
    print(f"[ark] target={target_date} nominal_window={window_hours}h "
          f"actual_window={actual_hours:.2f}h "
          f"since_time={since_time} until_time={until_time} "
          f"({datetime.fromtimestamp(since_time, timezone.utc).isoformat()} → {now.isoformat()})")
    rows = fetch_holdings(target_date)
    uniq = unique_companies(rows)
    uniq.sort(key=lambda r: -r["weight_sum"])
    print(f"[ark] {len(uniq)} unique companies")

    counts = {"done":0,"merged":0,"err":0,"kept":0,"drop":0,"new_kept":0,"new_drop":0}
    lock = threading.Lock()

    def proc(row):
        ticker = row["ticker"]
        safe = ticker.replace("/","_").replace("\\","_")
        out = out_dir / f"{safe}.json"

        # Read prior batches from today (if any) so we can dedup + merge.
        existing = None
        existing_ids = set()
        if out.exists():
            try:
                existing = json.loads(out.read_text())
                existing_ids = {str(t.get("id")) for t in existing.get("kept", []) if t.get("id")}
                existing_ids |= {str(d.get("tweet",{}).get("id")) for d in existing.get("dropped", []) if d.get("tweet",{}).get("id")}
            except Exception:
                existing = None
                existing_ids = set()

        q = build_query(ticker, row["company"], is_private=row["is_private"],
                        since_time=since_time, until_time=until_time)
        if q is None:
            if existing is None:
                stub = {"ticker":ticker,"company":row["company"],"weight_sum":row["weight_sum"],
                        "etfs":row["etfs"],"is_private":row["is_private"],"query":None,
                        "skipped_reason":"unsearchable","n_fetched":0,"n_kept":0,"n_dropped":0,
                        "kept":[],"dropped":[]}
                out.write_text(json.dumps(stub, default=str))
            with lock: counts["done"] += 1
            return
        try:
            tweets = paginated_search(q)
        except Exception as e:
            with lock: counts["err"] += 1
            print(f"[err] {ticker}: {str(e)[:80]}")
            return
        # Drop tweets we've already saved earlier today.
        new_tweets = [t for t in tweets if str(t.get("id")) not in existing_ids]
        new_tweets.sort(key=lambda t: -(t.get("likeCount") or 0))
        new_kept, new_dropped = [], []
        for t in new_tweets:
            sp, why = is_spam(t)
            if sp: new_dropped.append({"reason":why,"tweet":t})
            else: new_kept.append(t)

        if existing:
            kept = existing.get("kept", []) + new_kept
            dropped = existing.get("dropped", []) + new_dropped
            n_fetched_total = (existing.get("n_fetched", 0) or 0) + len(tweets)
        else:
            kept, dropped = new_kept, new_dropped
            n_fetched_total = len(tweets)

        rec = {"ticker":ticker,"company":row["company"],"weight_sum":row["weight_sum"],
               "etfs":row["etfs"],"is_private":row["is_private"],"query":q,
               "n_fetched":n_fetched_total,"n_kept":len(kept),"n_dropped":len(dropped),
               "kept":kept,"dropped":dropped,
               "last_window":{"since_time":since_time,"until_time":until_time,
                              "n_new_kept":len(new_kept),"n_new_dropped":len(new_dropped)}}
        out.write_text(json.dumps(rec, default=str))
        with lock:
            counts["done"] += 1
            if existing: counts["merged"] += 1
            counts["kept"] += len(kept)
            counts["drop"] += len(dropped)
            counts["new_kept"] += len(new_kept)
            counts["new_drop"] += len(new_dropped)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(proc, r) for r in uniq]
        for i, _ in enumerate(as_completed(futs), 1):
            if i % 10 == 0 or i == len(uniq):
                el = time.time() - t0
                print(f"[{i}/{len(uniq)}] new_kept={counts['new_kept']} "
                      f"new_drop={counts['new_drop']} merged={counts['merged']} {el:.0f}s",
                      flush=True)

    files = [p for p in out_dir.glob("*.json") if p.name != "_summary.json" and p.name != "digest.json"]
    silent = sum(1 for p in files if json.loads(p.read_text()).get("n_kept",0) == 0)
    saturated = sum(1 for p in files if json.loads(p.read_text()).get("n_fetched",0) >= 60)
    summary = {"date":target_date,"total_holdings":len(uniq),
               "total_tweets_kept":counts["kept"],"total_tweets_dropped":counts["drop"],
               "silent_holdings":silent,"saturated_holdings":saturated,
               "errors":counts["err"],
               "last_window":{"since_time":since_time,"until_time":until_time,"hours":window_hours,
                              "new_kept":counts["new_kept"],"new_dropped":counts["new_drop"]}}
    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[ark] DONE {json.dumps(summary)}")


# This file is the fetch-only pipeline used by the GitHub Actions in
# .github/workflows/. The render half (digest.json -> PDF) was moved to
# the private robynge/x-api repo — do NOT add it back here.

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    td = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    if cmd == "fetch":
        # Optional 3rd arg: window in hours (default 2). Useful for backfills.
        wh = float(sys.argv[3]) if len(sys.argv) > 3 else 2
        cmd_fetch(td, window_hours=wh)
    else:
        sys.exit(f"unknown command: {cmd} (only 'fetch' is supported)")
