# Mr Market

A one-page site that shows every company Piet has commented on in the RECM weekly letter, with its share price since his takes, against the S&P 500, all in rand.

## What's in here

| File | What it is | Who edits it |
|---|---|---|
| `data/calls/` | Your call CSVs. Every `.csv` in this folder is read, e.g. `vol1.csv`, `vol2.csv`, `2026-10-09.csv` | You: upload a new file whenever you have new calls |
| `data/delisted.csv` | Companies taken private, acquired or delisted (`ticker,date,price,event,note`) | You, when a company stops trading |
| `data/prices/` | Downloaded price histories, one CSV per ticker (e.g. `WBA.csv`), for companies Yahoo no longer has | You, when a company shows no prices |
| `data/holdings.csv` | Companies the Cockroach Fund owns (`company,ticker`), listed as links under the fund's chart | You, when the fund's holdings change |
| `data/renamed.csv` | Companies that now trade under a new ticker (`ticker,new_ticker,ratio,date,note`) | You, when a company is renamed |
| `data/manual_prices.csv` | Single prices you add by hand (`ticker,date,price`) | You, rarely |
| `data/price_cache.csv` | Every price fetched so far, so history isn't lost if Yahoo drops a ticker later | Built automatically |
| `data/cockroach.csv` | Cockroach Fund (Merchant West SCI Worldwide Flexible Fund) unit prices from inception (`date,price`). Always shown first on the page | You, when you have new prices |
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
- `exchange` is for your reference. The currency is worked out automatically: first from Yahoo, then from the ticker's suffix, then from the exchange name. JSE and London prices are converted from cents and pence.
- `stance` is `bullish` or `bearish`.
- Put the comment in double quotes if it contains a comma.

Rows with a mistake are skipped, and the file name, line number and reason are printed in the workflow log on the Actions tab.

## What the page shows

- **Companies**: every company in your CSVs, with how far the share is up or down since Piet's most recent take on it.
- **Chart**: the share price in rand from his first call, with the S&P 500 (in rand) starting at the same point. Click a take (on the chart, in the buttons above it, or in the table) to start the chart from that date instead.
- **Piet's takes**: each take's date and comment, linked to the passage in the letter.

"Since last take" runs from the date of Piet's latest take to the latest Friday close, or to the date a company stopped trading.

## The Cockroach Fund

The Cockroach Fund is always first in the list and selected when the page opens. Its chart runs from the first date in `data/cockroach.csv`, so start that file at the fund's inception, against the S&P 500 in rand from the same date.

Unit trusts aren't on Yahoo, so the prices come from you. Get the daily or monthly unit price history from Merchant West or Morningstar and save it as:

```
date,price
2014-01-31,1.0000
2014-02-28,1.0123
```

Use prices with distributions reinvested (total return) if you can; plain unit prices drop on each distribution date and understate the fund's growth. Monthly prices are fine: points in between are joined up. Add new prices whenever you like; the next run picks them up.

Under the fund's chart, **Companies in the fund** lists everything in `data/holdings.csv`, each with Piet's latest take on it and its date:

```
company,ticker
Berkshire Hathaway,BRK-B
Fairfax Financial,FFH.TO
```

Use the same ticker as in your takes CSVs. Each one links to that company's page, and the company's page links back to the fund. A holding with no takes yet is shown as plain text, and the run log lists it under **HOLDING**.

## Other markets

Most markets work without any changes: Johannesburg (`.JO`), London (`.L`), Hong Kong (`.HK`), Shanghai (`.SS`), Shenzhen (`.SZ`), Tokyo (`.T`), Toronto (`.TO`, `.V`), Australia (`.AX`), Singapore (`.SI`), Korea (`.KS`), Taiwan (`.TW`), India (`.NS`, `.BO`), Paris (`.PA`), Amsterdam (`.AS`), Frankfurt (`.DE`, `.F`), Milan (`.MI`), Madrid (`.MC`), Copenhagen (`.CO`), Stockholm (`.ST`), Oslo (`.OL`), Zurich (`.SW`), Brazil (`.SA`), Mexico (`.MX`) and US shares with no suffix. Each is converted to rand at that week's exchange rate.

For a market not listed, add its Yahoo suffix and currency to `SUFFIX` at the top of `scripts/build_data.py`, e.g. `".KL": "MYR",`. The run log tells you when a row was skipped because the currency couldn't be worked out.

## Companies that were taken private or delisted

Yahoo often drops the history of companies that no longer trade. To keep them on the scorecard, add a row to `data/delisted.csv`:

```
ticker,date,price,event,note
3333.HK,2025-08-25,,Delisted,"Trading suspended from 29 Jan 2024; listing cancelled after 18 months of suspension"
WBA,2025-08-28,11.45,Taken private,"Acquired by Sycamore Partners for $11.45 a share in cash, plus a right to up to $3.00 more"
```

- `date` is when it stopped trading, was taken private or was delisted.
- `price` is the final price per share in the share's own currency, in whole units (rand, not cents). For a take-private, use the offer price. Leave it blank to use the last traded price; a suspended share is then held at that price until `date`.
- `event` is the label shown on the page, e.g. *Taken private*, *Acquired*, *Delisted*, *Liquidated*.

The chart stops at that date with a marker, the company is labelled in the list, and both scores are measured to that date.

If Yahoo has no prices at all for a company (common once it has been taken private), download its price history from a site that keeps old listings, such as Investing.com (search the company, open *Historical Data*, choose a date range and download). Save it as `data/prices/<ticker>.csv`, e.g. `data/prices/WBA.csv`, and upload it. Any file with a Date column and a Close, Close/Last or Price column works. Prices must be in whole units (rand, not cents). Monthly prices are fine: points in between are joined up.

For one or two prices, you can instead add rows to `data/manual_prices.csv` (`ticker,date,price`). Downloaded and hand-entered prices always win over Yahoo's.

From now on every price the site fetches is also saved in `data/price_cache.csv`, so if Yahoo drops a company in future, its history stays on the site.

Every run also lists any share whose prices stopped more than three weeks ago under **STOPPED TRADING** in the log, so you know which ones to add.

## Companies that changed ticker

When a company is renamed or merged into a new listing, Yahoo usually drops the old ticker. Add a row to `data/renamed.csv` and the prices under the new ticker continue the old history:

```
ticker,new_ticker,ratio,date,note
AMS.JO,VAL.JO,1,2025-05-28,"Anglo American Platinum, renamed Valterra Platinum"
CEIX,CNR,1,2025-01-14,"CONSOL Energy merged with Arch Resources and was renamed Core Natural Resources"
```

- `ratio` is how many new shares one old share became (1 for a plain rename).
- `date` is optional and only shown on the page.
- Keep the old ticker in your takes CSV; the page shows a note saying what it trades as now.

Exchange rates Yahoo doesn't quote directly against the rand (for example the Swedish krona) are worked out through the US dollar automatically.

## Bad prices from Yahoo

The builder cleans Yahoo's data before using it:

- **Zero or negative prices** are dropped.
- **One-off spikes** that jump away and straight back are dropped: more than 25% for shares, 10% for the S&P 500 and 8% for exchange rates. Real moves that stick are kept.
- **Unit switches**, where Yahoo flips a JSE share between cents and rand (a 100x jump that sticks), are put back on one scale.

## Notes

- Prices come from Yahoo Finance through the free `yfinance` library. It is reliable enough for a weekly scorecard but not an official feed.
- To try the page with made-up prices: `python scripts/build_data.py --demo`, then open `index.html` through a local web server (`python -m http.server`).
