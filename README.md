# Piet Viljoen Scorecard

A one-page site that tracks Piet's bullish and bearish calls from the RECM weekly letter against the share price, the S&P 500 and the Cockroach Fund, all in rand.

## What's in here

| File | What it is | Who edits it |
|---|---|---|
| `data/calls/` | Your call CSVs. Every `.csv` in this folder is read, e.g. `vol1.csv`, `vol2.csv`, `2026-10-09.csv` | You: upload a new file whenever you have new calls |
| `data/cockroach.csv` | Cockroach Fund unit prices (`date,price`) | You, whenever you have new prices (optional) |
| `index.html` | The page | Nobody |
| `data.json` | Prices and scores the page reads | Built automatically |
| `scripts/build_data.py` | Reads the CSVs, fetches prices, builds `data.json` | Nobody |
| `.github/workflows/update.yml` | Runs the builder and publishes the site | Nobody |

## One-time setup (about 10 minutes)

1. Create a free account at github.com and make a new **public** repository, e.g. `piet-scorecard`.
2. On the repository page choose **uploading an existing file** (or **Add file → Upload files**). Drag in everything from this folder, including the `.github` folder, and press **Commit changes**.
   On a Mac the `.github` folder is hidden in Finder: press Cmd + Shift + . to show it.
3. Go to **Settings → Pages**. Under *Build and deployment → Source* choose **GitHub Actions**.
4. Go to the **Actions** tab, click **Update scorecard** on the left, then **Run workflow**. In two or three minutes the prices are fetched and the site is live.
5. Your site is at `https://<your-username>.github.io/piet-scorecard/`. The link also appears on the finished workflow run.

## Every week

1. Run the new letter through the extraction skill and save the rows as a CSV file, e.g. `vol3-47.csv`.
2. On GitHub open the `data/calls` folder, choose **Add file → Upload files**, drop the file in and press **Commit changes**.
3. That's it. Within a few minutes the prices are refreshed, every company is re-scored and the charts are rebuilt. The site also refreshes itself every Friday at 19:00 to pick up the week's closing prices, even when there's no new letter.

You can also paste rows into an existing file instead (open it, click the pencil, paste at the bottom, commit). If the same call appears twice (same date, ticker and stance), it's counted once.

To check a run, open the **Actions** tab. A green tick means the site is updated. Any skipped rows and any tickers with no prices are listed in the run's log under *Fetch prices and build data.json*.

## The CSV format

```
letter_date,letter_ref,letter_url,company,ticker,exchange,stance,comment,quote_link
2023-08-31,Vol 1 no 1,https://recm.co.za/letters/v1-1,FirstRand,FSR.JO,JSE,bullish,"FirstRand looks like it is in a bull market in absolute terms",https://recm.co.za/letters/v1-1#:~:text=...
```

- `quote_link` is optional. When it's there, the comment on the page links straight to that passage in the letter; otherwise it links to `letter_url`.

- `letter_date` must be YYYY-MM-DD.
- `ticker` is the Yahoo Finance symbol: JSE shares end in `.JO`, Hong Kong in `.HK`, London in `.L`, US shares have no suffix. Use the same ticker every time for the same company.
- `exchange` is one of `JSE`, `HKEX`, `NYSE`, `NASDAQ`, `LSE`, `TSX`, `TSXV`, `TSE`, `Euronext Paris`, `Euronext Amsterdam`, `XETRA`, `Nasdaq Copenhagen`, `SIX`. JSE and LSE prices are converted from cents and pence automatically. To add another, add a line to `EXCHANGES` in `scripts/build_data.py`.
- `stance` is `bullish` or `bearish`.
- Put the comment in double quotes if it contains a comma.

Rows with a mistake are skipped, and the file name, line number and reason are printed in the workflow log on the Actions tab.

## How the scoring works

Each company is scored on Piet's **latest** call on it, from that call's date to the latest Friday close (or the last trade, if the share stopped trading).

1. **Age of the call.** Under 3 months: marked *Too early* and not scored. 3 to 12 months: use the total return. 12 months or more: use the annualised return, (1 + total return)^(1 ÷ years) − 1.
2. **Trend.** Up above +5%, Down below −5%, otherwise Flat. Flat counts as no growth, so a bullish call is right only if the share is Up, and a bearish call is right if it is Down or Flat.
3. **Versus S&P 500.** The S&P 500 return in rand is worked out the same way over the same period. Relative return = share return − S&P return. A bullish call is right above +5%, a bearish call below −5%; anything in between is *Inconclusive* and not scored.
4. **Score.** One point each, so 2/2, 1/2 or 0/2, or out of 1 when the S&P result is inconclusive.

The thresholds are in `RULES` near the top of the script in `index.html`. The chart always starts at Piet's first call on the company, with the S&P 500 and the Cockroach Fund rebased to the share price on that date.

## Notes

- Prices come from Yahoo Finance through the free `yfinance` library. It is reliable enough for a weekly scorecard but not an official feed.
- To try the page with made-up prices: `python scripts/build_data.py --demo`, then open `index.html` through a local web server (`python -m http.server`).
