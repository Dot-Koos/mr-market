"""Build data.json for the Piet Viljoen Scorecard.

Reads every CSV in data/calls/ (one row per call) and data/cockroach.csv (optional fund
unit prices), fetches weekly prices from Yahoo Finance, converts everything to
rand, and writes data.json next to index.html.

    python scripts/build_data.py          # real prices (needs internet + yfinance)
    python scripts/build_data.py --demo   # made-up prices, for testing the page
"""
import csv
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CALLS_DIR = ROOT / "data" / "calls"
CALLS = ROOT / "data" / "calls.csv"  # optional single file, still read if present
FUND = ROOT / "data" / "cockroach.csv"
OUT = ROOT / "data.json"

# exchange -> (currency, multiplier to get whole units). JSE and LSE quote in cents/pence.
EXCHANGES = {
    "JSE": ("ZAR", 0.01),
    "HKEX": ("HKD", 1.0),
    "NYSE": ("USD", 1.0),
    "NASDAQ": ("USD", 1.0),
    "LSE": ("GBP", 0.01),
    "TSX": ("CAD", 1.0),
    "TSXV": ("CAD", 1.0),
    "TSE": ("JPY", 1.0),
    "EURONEXT PARIS": ("EUR", 1.0),
    "EURONEXT AMSTERDAM": ("EUR", 1.0),
    "XETRA": ("EUR", 1.0),
    "NASDAQ COPENHAGEN": ("DKK", 1.0),
    "SIX": ("CHF", 1.0),
}
FX = {"USD": "USDZAR=X", "HKD": "HKDZAR=X", "GBP": "GBPZAR=X", "CAD": "CADZAR=X", "JPY": "JPYZAR=X",
      "EUR": "EURZAR=X", "DKK": "DKKZAR=X", "CHF": "CHFZAR=X"}
FX_DEMO = {"USDZAR=X": 18.8, "HKDZAR=X": 2.4, "GBPZAR=X": 23.5, "CADZAR=X": 13.8, "JPYZAR=X": 0.125,
           "EURZAR=X": 20.5, "DKKZAR=X": 2.75, "CHFZAR=X": 21.5}
SPX = "^GSPC"
COLUMNS = ["letter_date", "letter_ref", "letter_url", "company", "ticker", "exchange", "stance", "comment", "quote_link"]


def call_files():
    """Every CSV in data/calls/ (in name order), plus data/calls.csv if it exists."""
    files = sorted(CALLS_DIR.glob("*.csv"), key=lambda p: p.name.lower()) if CALLS_DIR.exists() else []
    if CALLS.exists():
        files.append(CALLS)
    return files


def read_calls():
    calls, problems, seen = [], [], set()
    for path in call_files():
        cols = COLUMNS
        with open(path, newline="", encoding="utf-8-sig") as f:
            for n, row in enumerate(csv.reader(f), start=1):
                where = f"{path.name} line {n}"
                if not row or not "".join(row).strip():
                    continue
                if row[0].strip().lower() == "letter_date":  # a header row: use its column order
                    cols = [c.strip().lower() for c in row]
                    continue
                row = [c.strip() for c in row]
                r = {k: "" for k in COLUMNS}
                r.update({k: v for k, v in zip(cols, row) if k})
                try:
                    dt.date.fromisoformat(r["letter_date"])
                except ValueError:
                    problems.append(f"{where}: date '{r['letter_date']}' is not YYYY-MM-DD")
                    continue
                r["stance"] = r["stance"].lower()
                if r["stance"] not in ("bullish", "bearish"):
                    problems.append(f"{where}: stance '{r['stance']}' must be bullish or bearish")
                    continue
                r["exchange"] = r["exchange"].upper()
                if r["exchange"] not in EXCHANGES:
                    problems.append(f"{where}: exchange '{r['exchange']}' not known (add it to EXCHANGES)")
                    continue
                if not r["ticker"] or not r["company"]:
                    problems.append(f"{where}: company and ticker are required")
                    continue
                key = (r["letter_date"], r["ticker"].upper(), r["stance"])
                if key in seen:  # the same call pasted twice
                    continue
                seen.add(key)
                calls.append(r)
    return calls, problems


def fridays(start, end):
    start = start + dt.timedelta(days=(4 - start.weekday()) % 7)
    return pd.date_range(start, end, freq="W-FRI")


def last_friday(today):
    return today - dt.timedelta(days=(today.weekday() - 4) % 7)


def weekly(series, index, limit=None):
    """Daily series -> Friday closes on `index`; carry forward over holidays only."""
    s = series.dropna()
    if s.empty:
        return pd.Series(index=index, dtype=float)
    s.index = pd.to_datetime(s.index).tz_localize(None)
    w = s.resample("W-FRI").last()
    w = w.reindex(index)
    w = w.ffill(limit=2)
    # never carry a price past the last real trade (delisted / suspended shares)
    w[w.index > s.index.max() + pd.Timedelta(days=7)] = float("nan")
    return w


def fetch_real(symbols, start, end):
    import yfinance as yf

    raw = yf.download(sorted(symbols), start=start, end=end + dt.timedelta(days=1),
                      interval="1d", auto_adjust=False, progress=False, group_by="column")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": next(iter(symbols))})
    return {s: (close[s] if s in close else pd.Series(dtype=float)) for s in symbols}


def fetch_demo(symbols, start, end):
    days = pd.bdate_range(start, end)
    out = {}
    for s in symbols:
        rnd = random.Random(s)
        if s in FX_DEMO:
            level, drift = FX_DEMO[s], 0.0
        elif s == SPX:
            level, drift = 4500, 0.0006
        elif s == "3333.HK":
            level, drift = 0.40, 0.0
        else:
            level = rnd.uniform(20, 300) * (100 if s.endswith((".JO", ".L")) else 1)
            drift = rnd.uniform(-0.0007, 0.0009)
        vol = 0.004 if s.endswith("=X") else 0.011 if s == SPX else 0.018
        vals = []
        for _ in days:
            level *= math.exp(drift + rnd.gauss(0, vol))
            vals.append(level)
        ser = pd.Series(vals, index=days)
        if s == "3333.HK":  # mimic a suspended share
            ser[ser.index > "2024-01-26"] = float("nan")
        out[s] = ser
    return out


def clean(values):
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 4) for v in values]


def main():
    demo = "--demo" in sys.argv
    calls, problems = read_calls()
    for p in problems:
        print("SKIPPED", p)
    if not calls:
        sys.exit("No valid calls found in data/calls/")

    start = min(dt.date.fromisoformat(c["letter_date"]) for c in calls) - dt.timedelta(days=10)
    end = last_friday(dt.date.today())
    idx = fridays(start, end)

    tickers = {c["ticker"]: c["exchange"] for c in calls}
    symbols = set(tickers) | {SPX, FX["USD"]} | {FX[EXCHANGES[e][0]] for e in tickers.values() if EXCHANGES[e][0] != "ZAR"}
    raw = (fetch_demo if demo else fetch_real)(symbols, start, end)

    fx = {cur: weekly(raw[sym], idx) for cur, sym in FX.items() if sym in raw}
    spx_zar = weekly(raw[SPX], idx) * fx["USD"]

    fund = None
    if FUND.exists():
        f = pd.read_csv(FUND)
        if len(f):
            fs = pd.Series(f["price"].astype(float).values, index=pd.to_datetime(f["date"]))
            fund = weekly(fs.sort_index(), idx)

    companies = {}
    for c in calls:
        co = companies.setdefault(c["ticker"], {"name": c["company"], "ticker": c["ticker"],
                                                "exchange": c["exchange"], "calls": []})
        co["calls"].append({"date": c["letter_date"], "stance": c["stance"], "comment": c["comment"],
                            "letter_ref": c["letter_ref"], "letter_url": c["letter_url"],
                            "quote_link": c.get("quote_link", "")})
    for co in companies.values():
        cur, mult = EXCHANGES[co["exchange"]]
        px = weekly(raw.get(co["ticker"], pd.Series(dtype=float)), idx) * mult
        if cur != "ZAR":
            px = px * fx[cur]
        co["px"] = clean(px.values)
        co["calls"].sort(key=lambda k: k["date"])
        if all(v is None for v in co["px"]):
            print("NO PRICES for", co["ticker"], "- check the ticker on finance.yahoo.com")

    data = {
        "updated": end.isoformat(),
        "demo": demo,
        "dates": [d.date().isoformat() for d in idx],
        "spx": clean(spx_zar.values),
        "fund": clean(fund.values) if fund is not None else None,
        "companies": sorted(companies.values(), key=lambda c: c["name"]),
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"Wrote {OUT.name}: {len(companies)} companies, {len(calls)} calls, prices to {end}")


if __name__ == "__main__":
    main()
