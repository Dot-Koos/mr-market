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
RENAMED = DATA / "renamed.csv"        # companies that now trade under a new ticker
MANUAL = DATA / "manual_prices.csv"
FUND = DATA / "cockroach.csv"   # Cockroach Fund unit prices (date,price) from inception
HOLDINGS = DATA / "cockroach_holdings.csv"   # what the Cockroach Fund holds, from the letters
HOLDINGS_OLD = DATA / "holdings.csv"         # older name, still read if the new file is missing
FUND_NOTES = DATA / "cockroach_notes.csv"    # notes on the holdings (letter_ref,letter_url,item,note)
FUND_NAME = "Cockroach Fund"
FUND_FULL_NAME = "Merchant West SCI Worldwide Flexible Fund"
PRICES_DIR = DATA / "prices"          # downloaded price histories, one file per ticker, e.g. WBA.csv
CACHE = DATA / "price_cache.csv"
CURRENCIES = DATA / "currencies.csv"   # each ticker's quote currency, looked up once and remembered      # every price ever fetched, so nothing is lost when Yahoo drops a ticker
DIV_CACHE = DATA / "dividend_cache.csv"   # every dividend seen so far, as a yield on the ex-date
OUT = ROOT / "data.json"
SPX = "^GSPC"          # S&P 500 price index, used only if the total return index is missing
SPX_TR = "^SP500TR"    # S&P 500 Total Return index (dividends reinvested)
ADJ = {}               # Yahoo's dividend-adjusted closes, filled in by the fetchers
DIVS = {}              # Yahoo's dividends per share, in the same units as its closes
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
    ".SA": "BRL", ".MX": "MXN", ".TA": "ILA", ".ME": "RUB", ".IS": "TRY", ".SR": "SAR", ".QA": "QAR", ".CA": "EGP", ".NR": "NGN",
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


def read_simple_csv(path, required, need_date=True):
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if not r.get("ticker") or r["ticker"].startswith("#"):
                continue
            if need_date or r.get("date"):
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
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
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
                      interval="1d", auto_adjust=False, actions=True, progress=False, group_by="column")
    if raw is None or raw.empty:
        return {s: pd.Series(dtype=float) for s in symbols}
    multi = isinstance(raw.columns, pd.MultiIndex)
    one = next(iter(symbols))
    close = raw["Close"] if multi else raw[["Close"]].rename(columns={"Close": one})
    if "Adj Close" in (raw.columns.get_level_values(0) if multi else raw.columns):
        adj = raw["Adj Close"] if multi else raw[["Adj Close"]].rename(columns={"Adj Close": one})
        for s in symbols:
            if s in adj:
                ADJ[s] = adj[s]
    if "Dividends" in (raw.columns.get_level_values(0) if multi else raw.columns):
        dv = raw["Dividends"] if multi else raw[["Dividends"]].rename(columns={"Dividends": one})
        for s in symbols:
            if s in dv:
                DIVS[s] = dv[s]
    if not ADJ and not DIVS:
        print("WARNING: Yahoo sent no dividends, so dividends can't be added this run")
    return {s: (close[s] if s in close else pd.Series(dtype=float)) for s in symbols}


def div_yields(close, adj, divs=None):
    """Dividends as a yield on each ex-date: dividend / previous close.

    Two sources from Yahoo, both in the share's own quote units:
    - its list of dividends per share (right for JSE shares, where both are in cents);
    - the step in Adj Close / Close on each ex-date (Yahoo's adjusted close scales
      earlier prices down by the dividend). For JSE shares Yahoo gets this 100x too
      small, and for some London shares the dividend list is 100x too small.
    On each ex-date the larger of the two is used. A yield doesn't change when later
    dividends rescale the history, so it can be saved."""
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    c = to_daily(close)
    c = c[c > 0]
    if len(c) < 2:
        return empty
    # from the adjusted close
    a = to_daily(adj)
    both = pd.concat([c, a[a > 0]], axis=1, keys=["c", "a"]).dropna()
    if len(both) >= 2:
        ratio = both["a"] / both["c"]
        y_adj = (ratio / ratio.shift(1) - 1).dropna()
        y_adj = y_adj[y_adj > 0.0005]          # rounding noise is ignored
    else:
        y_adj = empty
    # from the list of dividends
    d = to_daily(divs)
    d = d[d > 0]
    prev = c.shift(1)
    y_div = pd.Series({t: v / prev.asof(t - pd.Timedelta(seconds=1)) for t, v in d.items()
                       if pd.notna(prev.asof(t - pd.Timedelta(seconds=1)))}, dtype=float)
    # merge: the same ex-date seen by both sources (within 3 days) counts once, the larger yield
    y = y_div.copy()
    for t, v in y_adj.items():
        near = y.index[abs(y.index - t) <= pd.Timedelta(days=3)] if len(y) else []
        if len(near):
            k = near[0]
            y[k] = max(y[k], v)
        else:
            y.loc[t] = v
    y = y.sort_index()
    return y[(y > 0) & (y < 0.5)]           # above 50% is bad data rather than a dividend


def load_div_cache():
    if not DIV_CACHE.exists():
        return {}
    df = pd.read_csv(DIV_CACHE, dtype={"ticker": str})
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"])
    return {t: g.set_index("date")["yield"].astype(float).sort_index() for t, g in df.groupby("ticker")}


def save_div_cache(series_by_ticker):
    with open(DIV_CACHE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "date", "yield"])
        for t, ser in sorted(series_by_ticker.items()):
            w.writerows((t, d.date().isoformat(), round(float(v), 8)) for d, v in ser.dropna().items())


def total_return(s, y):
    """Daily prices -> total return: each dividend is reinvested in the share on its ex-date.
    The result equals the price until the first dividend, then grows ahead of it."""
    if s.empty or y.empty:
        return s
    cum = (1 + y.sort_index()).cumprod()
    f = cum.reindex(cum.index.union(s.index)).ffill().reindex(s.index).fillna(1.0)
    return s * f


def yahoo_currency(ticker):
    try:
        import yfinance as yf
        return yf.Ticker(ticker).fast_info.get("currency")
    except Exception:
        return None


def lookup_currencies(tickers):
    """Ask Yahoo for several tickers' quote currencies at once (in parallel)."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = list(tickers)
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        return dict(zip(tickers, pool.map(yahoo_currency, tickers)))


def load_currencies():
    if not CURRENCIES.exists():
        return {}
    with open(CURRENCIES, newline="") as f:
        return {r["ticker"]: r["currency"] for r in csv.DictReader(f) if r.get("ticker") and r.get("currency")}


def save_currencies(found):
    with open(CURRENCIES, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "currency"])
        w.writerows(sorted(found.items()))


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
        elif s == SPX_TR:
            level, drift = 9000, 0.00065
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
        if not cur and s not in (SPX, SPX_TR):   # made-up dividends: twice a year, 1-2.5% each
            y = rnd.uniform(0.01, 0.025)
            ex = [d for d in days if d.month in (4, 10) and d.day <= 7 and d.weekday() == 0]
            factor = pd.Series(1.0, index=days)
            for d in ex:
                factor[factor.index < d] /= (1 + y)
            ADJ[s] = ser * factor
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

    # Cockroach Fund: its own date axis, from the first price in data/cockroach.csv (inception)
    fund_daily = pd.Series(dtype=float)
    if FUND.exists():
        f = pd.read_csv(FUND)
        if len(f):
            f.columns = [c.strip().lower() for c in f.columns]
            fund_daily = pd.Series(f["price"].map(_number).values, index=pd.to_datetime(f["date"], errors="coerce", format="mixed"))
            fund_daily = despike(fund_daily[fund_daily.index.notna()].dropna().sort_index(), 0.25)
    if fund_daily.empty and demo:   # made-up history so the preview has something to show
        days = pd.bdate_range("2014-01-31", end)
        rnd = random.Random("cockroach"); lvl = 100.0; vals = []
        for _ in days:
            lvl *= math.exp(0.00035 + rnd.gauss(0, 0.006)); vals.append(lvl)
        fund_daily = pd.Series(vals, index=days)
    fund_start = fund_daily.index.min().date() if not fund_daily.empty else None
    fetch_start = min(start, fund_start - dt.timedelta(days=10)) if fund_start else start
    fund_idx = (pd.date_range(fund_start + dt.timedelta(days=(4 - fund_start.weekday()) % 7), end, freq="W-FRI")
                if fund_start else None)

    # 1. which currency each share is quoted in
    tickers = {}
    for c in calls:
        tickers.setdefault(c["ticker"], c["exchange"])
    renamed = {}
    for r in read_simple_csv(RENAMED, [], need_date=False):
        if r.get("new_ticker"):
            renamed[r["ticker"]] = {"new": r["new_ticker"].upper(), "ratio": float(r.get("ratio") or 1),
                                    "date": r["date"], "note": r.get("note", ""), "name": r.get("new_name", "")}
    for old, a in renamed.items():          # takes only under the new ticker still need the old history
        if a["new"] in tickers and old not in tickers:
            tickers[old] = tickers[a["new"]]
    # 2. fetch the shares, the S&P 500 and the dollar in one batch. Companies that stopped trading
    #    more than a month ago are not asked for again: their prices come from the saved history.
    gone = {t for t, ev in events.items()
            if pd.Timestamp(ev["date"]).date() < end - dt.timedelta(days=30) and t in cache}
    look = {t: (renamed[t]["new"] if t in renamed else t) for t in tickers}   # renamed shares trade under the new ticker
    shares = {v for t, v in look.items() if t not in gone}
    fetch = fetch_demo if demo else fetch_real
    raw = fetch(shares | {SPX, SPX_TR, "USDZAR=X", "ZAR=X"}, fetch_start, end)
    divcache = {} if demo else load_div_cache()

    # 3. which currency each share is quoted in: remembered from earlier runs, else asked once
    known = {} if demo else load_currencies()
    ask = [v for v in set(look.values()) if v not in known and not to_daily(raw.get(v)).empty]
    if ask and not demo:
        found = {k: v for k, v in lookup_currencies(ask).items() if v}
        known.update(found)
        print(f"Looked up the currency of {len(ask)} new ticker(s)")
    units = {}
    for t, ex in tickers.items():
        v = look[t]
        units[t] = quote_unit(known.get(v) or guess_quote_code(v, ex) or guess_quote_code(t, ex) or "USD")

    # 4. the exchange rates those currencies need
    currencies = {cur for cur, _ in units.values()} | {"USD"}
    currencies = {c for c in currencies if c and c != "ZAR"}
    fx_sym = {c: f"{c}ZAR=X" for c in currencies}
    # fallback for rates Yahoo doesn't quote directly (e.g. SEK/ZAR): go via the US dollar, ZAR=X / SEK=X
    cross = {c: f"{c}=X" for c in currencies if c != "USD"}
    more = (set(fx_sym.values()) | set(cross.values())) - set(raw)
    if more:
        raw.update(fetch(more, fetch_start, end))

    # exchange rates and the index move far less than single shares, so they get a tighter spike filter
    fx = {c: weekly(despike(to_daily(raw.get(sym)), 0.08), idx) for c, sym in fx_sym.items()}
    usd_zar = fx.get("USD")
    if usd_zar is None or usd_zar.isna().all():
        usd_zar = weekly(despike(to_daily(raw.get("ZAR=X")), 0.08), idx)
        fx["USD"] = usd_zar
    for c, sym in cross.items():
        if fx[c].isna().all():
            usd_c = weekly(despike(to_daily(raw.get(sym)), 0.08), idx)
            fx[c] = usd_zar / usd_c
            if fx[c].isna().all():
                print(f"NO EXCHANGE RATE for {c}: shares priced in {c} can't be converted to rand")
            else:
                print(f"Using {c}/ZAR via the US dollar")
    fx["ZAR"] = pd.Series(1.0, index=idx)
    # the S&P 500 Total Return index; the price index only if Yahoo has no total return history
    spx_sym = SPX_TR if not to_daily(raw.get(SPX_TR)).empty else SPX
    if spx_sym == SPX:
        print(f"WARNING: no prices for {SPX_TR}, so the S&P 500 line is the price index without dividends")
    spx_zar = weekly(despike(to_daily(raw.get(spx_sym)), 0.10), idx) * fx["USD"]

    fund = weekly(fund_daily, idx) if not fund_daily.empty else None
    cockroach = {"name": FUND_NAME, "full_name": FUND_FULL_NAME, "dates": [], "px": [], "spx": []}
    if fund_idx is not None and len(fund_idx):
        # the S&P 500 in rand on the fund's own (longer) date axis
        usd_long = weekly(despike(to_daily(raw.get("USDZAR=X")), 0.08), fund_idx)
        if usd_long.isna().all():
            usd_long = weekly(despike(to_daily(raw.get("ZAR=X")), 0.08), fund_idx)
        cockroach["inception"] = fund_daily.index.min().date().isoformat()
        cockroach["last"] = fund_daily.index.max().date().isoformat()
        cockroach["dates"] = [d.date().isoformat() for d in fund_idx]
        cockroach["px"] = clean(weekly(fund_daily, fund_idx).values)
        cockroach["spx"] = clean((weekly(despike(to_daily(raw.get(spx_sym)), 0.10), fund_idx) * usd_long).values)
        print(f"Cockroach Fund: {len(fund_idx)} weeks from {fund_start}")
    else:
        print("Cockroach Fund: no prices yet - add them to data/cockroach.csv (date,price) from inception")

    # 3. companies
    # takes under a company's old ticker and its new ticker (renamed.csv) belong to one company
    new_to_old = {a["new"]: old for old, a in renamed.items()}
    companies = {}
    for c in calls:
        t = c["ticker"]
        src = new_to_old.get(t, t)          # the old ticker carries the full price history
        co = companies.setdefault(src, {"name": c["company"], "ticker": t,
                                        "exchange": c["exchange"], "calls": []})
        co["calls"].append({"date": c["letter_date"], "stance": c["stance"], "comment": c["comment"],
                            "letter_ref": c["letter_ref"], "letter_url": c["letter_url"],
                            "quote_link": c.get("quote_link", "")})

    stopped = []
    for t, co in companies.items():
        if t not in units:                  # takes only under the new ticker of a rename
            units[t] = quote_unit(known.get(look.get(t, t)) or guess_quote_code(renamed.get(t, {}).get("new", t), co["exchange"]) or "USD")
        cur, mult = units[t]
        fresh = to_daily(raw.get(t))
        if t in renamed:                                  # prices under the new ticker continue the old history
            a = renamed[t]
            newer = to_daily(raw.get(a["new"])) * a["ratio"]
            fresh = newer.combine_first(fresh) if not fresh.empty else newer
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
        # dividends, as yields on their ex-dates: today's Yahoo data wins, saved ones fill gaps
        y = div_yields(raw.get(t), ADJ.get(t), DIVS.get(t))
        if t in renamed:
            newer_y = div_yields(raw.get(renamed[t]["new"]), ADJ.get(renamed[t]["new"]), DIVS.get(renamed[t]["new"]))
            y = newer_y.combine_first(y) if not y.empty else newer_y
        saved_y = divcache.get(t, pd.Series(dtype=float))
        y = y.combine_first(saved_y) if not saved_y.empty else y
        if not y.empty:
            divcache[t] = y
        ev = events.get(t)
        hold = None
        if ev:
            when = pd.Timestamp(ev["date"])
            s = s[s.index <= when]
            y = y[y.index <= when]
            if ev.get("price", "") != "":
                s.loc[when] = float(ev["price"])          # final price, e.g. the take-private offer
            hold = ev["date"]
        if not s.empty:
            y = y[(y.index > s.index.min()) & (y.index <= s.index.max())]
        px = weekly(s, idx, hold_until=hold) * fx[cur]
        tr = weekly(total_return(s, y), idx, hold_until=hold) * fx[cur]
        co["px"] = clean(px.values)
        co["tr"] = clean(tr.values)
        # ex-dividend dates, as the Friday of their week, for the markers on the chart
        co["divs"] = sorted({(d + pd.Timedelta(days=(4 - d.weekday()) % 7)).date().isoformat()
                             for d in y.index if idx[0] <= d <= idx[-1] + pd.Timedelta(days=6)})
        co["currency"] = cur
        if t in renamed:
            a = renamed[t]
            # show the company under its current name and ticker; keep the old ones for reference
            co["renamed"] = {"ticker": a["new"], "date": a["date"], "note": a["note"],
                             "old_ticker": t, "old_name": co["name"]}
            co["name"] = a["name"] or co["name"]
            co["ticker"] = a["new"]
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

    # 4. Cockroach Fund holdings, linked to companies on the page by ticker
    holdings = []
    hfile = HOLDINGS if HOLDINGS.exists() else HOLDINGS_OLD
    on_site = set(companies) | {r["new"] for r in renamed.values()}
    if hfile.exists():
        with open(hfile, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                name = r.get("asset") or r.get("holding") or r.get("company") or r.get("name") or ""
                t = r.get("ticker", "").upper()
                if not name and not t:
                    continue
                size = r.get("latest_size") or r.get("size") or r.get("weight") or ""
                if size and size.replace(".", "", 1).isdigit():
                    size += "%"
                holdings.append({"company": name or t, "ticker": t, "size": size,
                                 "class": r.get("asset_class", ""), "status": r.get("status", ""),
                                 "quote": r.get("quote", ""), "letter_ref": r.get("latest_letter_ref", ""),
                                 "letter_date": r.get("latest_letter_date", "") or r.get("as_of", ""),
                                 "letter_url": r.get("latest_letter_url", "")})
                if t and t not in on_site:
                    print(f"HOLDING {name or t} ({t}) has no takes, so it is listed without a link")
    if not holdings and demo:
        holdings = [{"company": companies[t]["name"], "ticker": t, "size": "", "class": ""} for t in list(companies)[:8]]

    fund_notes = []
    if FUND_NOTES.exists():
        with open(FUND_NOTES, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                if not r.get("note") or r.get("item", "").lower() == "no holdings":
                    continue
                fund_notes.append({"ref": r.get("letter_ref", ""), "url": r.get("letter_url", ""),
                                   "item": r.get("item", ""), "note": r["note"]})

    for msg in stopped:
        print("STOPPED TRADING:", msg, "- if it was taken private or delisted, add it to data/delisted.csv")

    data = {
        "updated": end.isoformat(),
        "demo": demo,
        "dates": [d.date().isoformat() for d in idx],
        "spx": clean(spx_zar.values),
        "spx_total_return": spx_sym == SPX_TR,
        "fund": clean(fund.values) if fund is not None else None,
        "cockroach": cockroach,
        "holdings": holdings,
        "fund_notes": fund_notes,
        "companies": sorted(companies.values(), key=lambda c: c["name"]),
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    if not demo:
        save_cache(cache)
        save_currencies(known)
        save_div_cache(divcache)
    print(f"Wrote {OUT.name}: {len(companies)} companies, {len(calls)} calls, prices to {end}")


def last_friday(today):
    return today - dt.timedelta(days=(today.weekday() - 4) % 7)


if __name__ == "__main__":
    main()
