"""Build data.json for the Piet Viljoen Scorecard.

Reads every CSV in data/calls/ (one row per call), plus three optional files:

    data/delisted.csv       companies that were taken private, acquired or delisted
    data/manual_prices.csv  prices you add by hand where Yahoo has none
    data/cockroach.csv      Cockroach Fund unit prices

fetches daily prices from Yahoo Finance, cleans them, converts everything to rand,
and writes weekly (Friday) closes to data.json next to index.html.

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
DATA = ROOT / "data"
CALLS_DIR = DATA / "calls"
CALLS = DATA / "calls.csv"  # optional single file, still read if present
DELISTED = DATA / "delisted.csv"
MANUAL = DATA / "manual_prices.csv"
FUND = DATA / "cockroach.csv"
PRICES_DIR = DATA / "prices"          # downloaded price histories, one file per ticker, e.g. WBA.csv
CACHE = DATA / "price_cache.csv"      # every price ever fetched, so nothing is lost when Yahoo drops a ticker
OUT = ROOT / "data.json"
SPX = "^GSPC"
COLUMNS = ["letter_date", "letter_ref", "letter_url", "company", "ticker", "exchange", "stance", "comment", "quote_link"]

# ---------------------------------------------------------------- currencies
# How Yahoo quotes a share. Most markets quote in whole units; a few quote in
# cents/pence (Yahoo codes ZAc, GBp, ILA). The multiplier turns the quote into whole units.
SUB_UNITS = {"ZAc": ("ZAR", 0.01), "ZAC": ("ZAR", 0.01), "GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ILA": ("ILS", 0.01)}

# Ticker suffix -> Yahoo quote currency. Used when Yahoo doesn't say (and in demo mode).
# To add a market: add its Yahoo suffix and currency here. That's all.
SUFFIX = {
    ".JO": "ZAc",                      # Johannesburg (cents)
    ".L": "GBp", ".IL": "USD",          # London (pence); London international
    ".HK": "HKD",                       # Hong Kong
    ".SS": "CNY", ".SZ": "CNY",         # Shanghai, Shenzhen
    ".TO": "CAD", ".V": "CAD", ".NE": "CAD",  # Toronto, TSX Venture, Cboe Canada
    ".T": "JPY",                        # Tokyo
    ".AX": "AUD", ".NZ": "NZD",         # Australia, New Zealand
    ".SI": "SGD", ".KS": "KRW", ".KQ": "KRW", ".TW": "TWD", ".TWO": "TWD",
    ".NS": "INR", ".BO": "INR", ".JK": "IDR", ".BK": "THB", ".KL": "MYR",
    ".PA": "EUR", ".AS": "EUR", ".BR": "EUR", ".DE": "EUR", ".F": "EUR", ".MI": "EUR",
    ".MC": "EUR", ".HE": "EUR", ".IR": "EUR", ".LS": "EUR", ".VI": "EUR",
    ".CO": "DKK", ".ST": "SEK", ".OL": "NOK", ".SW": "CHF", ".WA": "PLN",
    ".SA": "BRL", ".MX": "MXN", ".TA": "ILA",
}
# Exchange names, used only when the ticker has no suffix and Yahoo doesn't say.
EXCHANGE_CCY = {
    "JSE": "ZAc", "LSE": "GBp", "HKEX": "HKD", "SEHK": "HKD", "SSE": "CNY", "SZSE": "CNY",
    "NYSE": "USD", "NASDAQ": "USD", "NYSE AMERICAN": "USD", "NYSE ARCA": "USD", "OTC": "USD",
    "TSX": "CAD", "TSXV": "CAD", "TSE": "JPY", "ASX": "AUD", "NZX": "NZD", "SGX": "SGD",
    "XETRA": "EUR", "EURONEXT PARIS": "EUR", "EURONEXT AMSTERDAM": "EUR", "EURONEXT BRUSSELS": "EUR",
    "EURONEXT DUBLIN": "EUR", "EURONEXT LISBON": "EUR", "BORSA ITALIANA": "EUR", "BME": "EUR",
    "NASDAQ COPENHAGEN": "DKK", "NASDAQ STOCKHOLM": "SEK", "OSLO BORS": "NOK", "SIX": "CHF",
    "B3": "BRL", "BMV": "MXN", "NSE": "INR", "BSE": "INR", "KRX": "KRW", "TWSE": "TWD",
}
FX_DEMO = {"USD": 18.8, "HKD": 2.4, "GBP": 23.5, "CAD": 13.8, "JPY": 0.125, "EUR": 20.5, "DKK": 2.75,
           "CHF": 21.5, "CNY": 2.6, "AUD": 12.3, "SEK": 1.75, "NOK": 1.7, "SGD": 14.0}


def quote_unit(code):
    """Yahoo currency code -> (ISO currency, multiplier to whole units)."""
    return SUB_UNITS.get(code, (code.upper(), 1.0))


def guess_quote_code(ticker, exchange):
    """Quote currency from the ticker suffix, else the exchange name, else USD for plain US tickers."""
    t = ticker.upper()
    for suf in sorted(SUFFIX, key=len, reverse=True):
        if t.endswith(suf.upper()):
            return SUFFIX[suf]
    found = EXCHANGE_CCY.get(exchange.strip().upper())
    if found:
        return found
    return "USD" if "." not in t else None


# ---------------------------------------------------------------- input files
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
                if not r["ticker"] or not r["company"]:
                    problems.append(f"{where}: company and ticker are required")
                    continue
                r["ticker"] = r["ticker"].upper()
                if guess_quote_code(r["ticker"], r["exchange"]) is None:
                    problems.append(f"{where}: can't tell the currency of '{r['ticker']}' on '{r['exchange']}'"
                                    " (add its suffix to SUFFIX in scripts/build_data.py)")
                    continue
                key = (r["letter_date"], r["ticker"], r["stance"])
                if key in seen:  # the same call pasted twice
                    continue
                seen.add(key)
                calls.append(r)
    return calls, problems


def read_simple_csv(path, required):
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if not r.get("ticker") or r["ticker"].startswith("#"):
                continue
            try:
                dt.date.fromisoformat(r.get("date", ""))
            except ValueError:
                print(f"SKIPPED {path.name} line {n}: date '{r.get('date')}' is not YYYY-MM-DD")
                continue
            if "price" in required and r.get("price", "") == "":
                print(f"SKIPPED {path.name} line {n}: price is required")
                continue
            r["ticker"] = r["ticker"].upper()
            rows.append(r)
    return rows


# ---------------------------------------------------------------- cleaning
def fix_units(s):
    """Undo Yahoo switching between cents and rand (or pence and pounds) part-way
    through a history: a ~100x jump that sticks. The latest stretch is taken as correct."""
    s = s.dropna()
    if len(s) < 2:
        return s
    vals = s.values.copy()
    factor = 1.0
    for i in range(len(vals) - 2, -1, -1):
        ratio = s.values[i + 1] / s.values[i] if s.values[i] > 0 else 0
        if 80 <= ratio <= 125:
            factor *= 100
        elif 0.008 <= ratio <= 0.0125:
            factor /= 100
        vals[i] = s.values[i] * factor
    return pd.Series(vals, index=s.index)


def despike(s, tol):
    """Drop bad prints: zero/negative prices and short-lived spikes.

    A day is a spike when it sits more than `tol` away from the median of the
    surrounding week (3 trading days either side). A genuine jump that sticks
    moves the median with it, so only prints that bounce straight back are removed.
    """
    s = s[s > 0]
    if len(s) < 5:
        return s
    med = s.rolling(7, center=True, min_periods=3).median()
    bad = (s / med - 1).abs() > tol
    return s[~bad]


def to_daily(series):
    s = series.dropna() if series is not None else pd.Series(dtype=float)
    if s.empty:
        return s
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.sort_index()


def weekly(s, index, hold_until=None):
    """Clean daily series -> Friday closes on `index`.

    Prices carry forward over holidays only, and never past the last trade,
    except when `hold_until` is given (a suspended share held at its last price
    until the delisting date)."""
    if s.empty:
        return pd.Series(index=index, dtype=float)
    w = s.resample("W-FRI").last().reindex(index)
    last = s.index.max()
    weeks_with_data = s.groupby(s.index.to_period("W-FRI")).size().size
    sparse = weeks_with_data < 0.5 * ((last - s.index.min()).days / 7 + 1)
    w = w.interpolate(limit_area="inside") if sparse else w.ffill(limit=2)   # join up hand-entered points
    if hold_until is not None:
        w = w.ffill()   # a suspended share stays at its last price until it is delisted
        w[w.index > pd.Timestamp(hold_until) + pd.Timedelta(days=6)] = float("nan")
    else:
        w[w.index > last + pd.Timedelta(days=7)] = float("nan")
    return w


# ---------------------------------------------------------------- fetching
def fetch_real(symbols, start, end):
    import yfinance as yf

    raw = yf.download(sorted(symbols), start=start, end=end + dt.timedelta(days=1),
                      interval="1d", auto_adjust=False, progress=False, group_by="column")
    if raw is None or raw.empty:
        return {s: pd.Series(dtype=float) for s in symbols}
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": next(iter(symbols))})
    return {s: (close[s] if s in close else pd.Series(dtype=float)) for s in symbols}


def yahoo_currency(ticker):
    try:
        import yfinance as yf
        return yf.Ticker(ticker).fast_info.get("currency")
    except Exception:
        return None


def fetch_demo(symbols, start, end):
    days = pd.bdate_range(start, end)
    out = {}
    for s in symbols:
        rnd = random.Random(s)
        cur = s[:3] if s.endswith("ZAR=X") else None
        if cur:
            level, drift = FX_DEMO.get(cur, 5.0), 0.0
        elif s == SPX:
            level, drift = 4500, 0.0006
        elif s == "3333.HK":
            level, drift = 0.40, 0.0
        else:
            level = rnd.uniform(20, 300) * (100 if s.endswith((".JO", ".L")) else 1)
            drift = rnd.uniform(-0.0007, 0.0009)
        vol = 0.004 if cur else 0.011 if s == SPX else 0.018
        vals = []
        for _ in days:
            level *= math.exp(drift + rnd.gauss(0, vol))
            vals.append(level)
        ser = pd.Series(vals, index=days)
        if s == "3333.HK":  # mimic a suspended share
            ser[ser.index > "2024-01-29"] = float("nan")
        out[s] = ser
    return out


def clean(values):
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 4) for v in values]


# ---------------------------------------------------------------- saved prices
def load_cache():
    """ticker -> daily Series of Yahoo quotes saved on earlier runs."""
    if not CACHE.exists():
        return {}
    df = pd.read_csv(CACHE, dtype={"ticker": str})
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"])
    return {t: g.set_index("date")["close"].astype(float).sort_index() for t, g in df.groupby("ticker")}


def save_cache(series_by_ticker):
    rows = []
    for t, ser in sorted(series_by_ticker.items()):
        ser = ser.dropna()
        rows += [(t, d.date().isoformat(), round(float(v), 6)) for d, v in ser.items()]
    with open(CACHE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "date", "close"])
        w.writerows(rows)


def _number(x):
    if pd.isna(x):
        return float("nan")
    x = str(x).strip().replace("$", "").replace(",", "").replace("R", "").replace("HK", "").replace(" ", "")
    try:
        return float(x)
    except ValueError:
        return float("nan")


def read_price_files():
    """Price histories downloaded from any website (Investing.com, Nasdaq, Yahoo, etc.).

    One CSV per ticker in data/prices/, named after the ticker (WBA.csv, 3333.HK.csv).
    Needs a date column and a price column (Close, Close/Last, Price or Adj Close).
    Prices are in whole units of the share's currency (rand, not cents)."""
    out = {}
    if not PRICES_DIR.exists():
        return out
    for path in sorted(PRICES_DIR.glob("*.csv")):
        t = path.stem.upper()
        try:
            df = pd.read_csv(path)
        except Exception as e:
            print(f"SKIPPED {path.name}: could not read it ({e})")
            continue
        cols = {c.strip().lower(): c for c in df.columns}
        dcol = next((cols[c] for c in cols if c in ("date", "datetime", "time")), None)
        pcol = next((cols[c] for c in ("close", "close/last", "price", "adj close", "close price", "last") if c in cols), None)
        if dcol is None or pcol is None:
            print(f"SKIPPED {path.name}: needs a Date column and a Close or Price column (found {list(df.columns)})")
            continue
        dates = pd.to_datetime(df[dcol], errors="coerce", format="mixed")
        vals = df[pcol].map(_number)
        ser = pd.Series(vals.values, index=dates).dropna()
        ser = ser[ser.index.notna()].sort_index()
        ser = ser[~ser.index.duplicated(keep="last")]
        if ser.empty:
            print(f"SKIPPED {path.name}: no usable rows")
            continue
        out[t] = ser
        print(f"Loaded {len(ser)} prices for {t} from data/prices/{path.name}")
    return out


# ---------------------------------------------------------------- main
def main():
    demo = "--demo" in sys.argv
    calls, problems = read_calls()
    for p in problems:
        print("SKIPPED", p)
    if not calls:
        sys.exit("No valid calls found in data/calls/")
    events = {r["ticker"]: r for r in read_simple_csv(DELISTED, [])}
    manual = read_simple_csv(MANUAL, ["price"])
    files = read_price_files()
    cache = {} if demo else load_cache()

    start = min(dt.date.fromisoformat(c["letter_date"]) for c in calls) - dt.timedelta(days=10)
    end = last_friday(dt.date.today())
    idx = pd.date_range(start + dt.timedelta(days=(4 - start.weekday()) % 7), end, freq="W-FRI")

    # 1. which currency each share is quoted in
    tickers = {}
    for c in calls:
        tickers.setdefault(c["ticker"], c["exchange"])
    units = {}
    for t, ex in tickers.items():
        code = None if demo else yahoo_currency(t)
        units[t] = quote_unit(code or guess_quote_code(t, ex))

    # 2. fetch shares, the S&P 500 and every exchange rate we need
    currencies = {cur for cur, _ in units.values()} | {"USD"}
    currencies = {c for c in currencies if c and c != "ZAR"}
    fx_sym = {c: f"{c}ZAR=X" for c in currencies}
    raw = (fetch_demo if demo else fetch_real)(set(tickers) | {SPX} | set(fx_sym.values()), start, end)

    # exchange rates and the index move far less than single shares, so they get a tighter spike filter
    fx = {c: weekly(despike(to_daily(raw.get(sym)), 0.08), idx) for c, sym in fx_sym.items()}
    fx["ZAR"] = pd.Series(1.0, index=idx)
    spx_zar = weekly(despike(to_daily(raw.get(SPX)), 0.10), idx) * fx["USD"]

    fund = None
    if FUND.exists():
        f = pd.read_csv(FUND)
        if len(f):
            fs = pd.Series(f["price"].astype(float).values, index=pd.to_datetime(f["date"]))
            fund = weekly(despike(fs.sort_index(), 0.25), idx)

    # 3. companies
    companies = {}
    for c in calls:
        co = companies.setdefault(c["ticker"], {"name": c["company"], "ticker": c["ticker"],
                                                "exchange": c["exchange"], "calls": []})
        co["calls"].append({"date": c["letter_date"], "stance": c["stance"], "comment": c["comment"],
                            "letter_ref": c["letter_ref"], "letter_url": c["letter_url"],
                            "quote_link": c.get("quote_link", "")})

    stopped = []
    for t, co in companies.items():
        cur, mult = units[t]
        fresh = to_daily(raw.get(t))
        saved = cache.get(t, pd.Series(dtype=float))
        s = fresh.combine_first(saved) if not saved.empty else fresh   # today's Yahoo data wins, saved prices fill gaps
        cache[t] = s
        s = despike(fix_units(s * mult), 0.25)          # whole units, bad prints removed
        if t in files:                                    # downloaded histories win over Yahoo
            f = files[t]
            s = f.combine_first(s) if not s.empty else f
        for m in (r for r in manual if r["ticker"] == t):  # single hand-entered prices win over everything
            s.loc[pd.Timestamp(m["date"])] = float(m["price"])
        s = s.sort_index()
        ev = events.get(t)
        hold = None
        if ev:
            when = pd.Timestamp(ev["date"])
            s = s[s.index <= when]
            if ev.get("price", "") != "":
                s.loc[when] = float(ev["price"])          # final price, e.g. the take-private offer
            hold = ev["date"]
        px = weekly(s, idx, hold_until=hold) * fx[cur]
        co["px"] = clean(px.values)
        co["currency"] = cur
        co["calls"].sort(key=lambda k: k["date"])
        if s.empty:
            print("NO PRICES for", t, f"- check the ticker on finance.yahoo.com, or add a price history as data/prices/{t}.csv")
            continue
        if ev:
            co["event"] = {"date": ev["date"], "type": ev.get("event") or "Delisted", "note": ev.get("note", ""),
                           "price": float(ev["price"]) if ev.get("price", "") != "" else None, "currency": cur}
        elif s.index.max().date() < end - dt.timedelta(days=21):
            last = s.index.max().date().isoformat()
            co["event"] = {"date": last, "type": "Stopped trading", "note": "", "price": None,
                           "currency": cur, "auto": True}
            stopped.append(f"{t} ({co['name']}) last traded {last}")

    for msg in stopped:
        print("STOPPED TRADING:", msg, "- if it was taken private or delisted, add it to data/delisted.csv")

    data = {
        "updated": end.isoformat(),
        "demo": demo,
        "dates": [d.date().isoformat() for d in idx],
        "spx": clean(spx_zar.values),
        "fund": clean(fund.values) if fund is not None else None,
        "companies": sorted(companies.values(), key=lambda c: c["name"]),
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    if not demo:
        save_cache(cache)
    print(f"Wrote {OUT.name}: {len(companies)} companies, {len(calls)} calls, prices to {end}")


def last_friday(today):
    return today - dt.timedelta(days=(today.weekday() - 4) % 7)


if __name__ == "__main__":
    main()
