# Smart Fund Screener

Quantitative fund screening platform that ranks Chinese mutual funds and ETFs using return, risk, drawdown, and performance factors.

This project builds a local HTML dashboard with TTFund Skills data. It combines multi-period returns, drawdown windows, volatility, Sharpe-like scoring, fund-size filters, and a refresh workflow. It is designed for transparent, reproducible fund screening rather than investment advice.

## Features

- Multi-dimensional candidate discovery through TTFund condition selection.
- Fund-size filter, currently requiring latest fund net assets of at least CNY 50 million.
- Multi-period return columns: 1 week, 1 month, 3 months, 6 months, 1 year, 2 years, and 3 years.
- Multi-window drawdown columns: 1 month, 3 months, 6 months, 1 year, and 3 years.
- Hybrid risk calculation:
  - Uses API-provided metrics when available.
  - Falls back to NAV history and local calculations when the API does not provide precomputed fields.
  - Leaves values blank when neither source is available, instead of fabricating data.
- Local HTTP server with a refresh button for regenerating the report.
- Single HTML output that can be inspected, shared locally, or archived.

## Screenshot

Add a screenshot after generating `output/zmail_recommend.html`.

## Requirements

- Python 3.10+
- `curl` or `curl.exe`
- A TTFund Skills API key

The current runtime uses only the Python standard library.

## API Key Setup

Create a local environment variable before running the pipeline.

To get a TTFund Skills API key, open the 天天基金 app or the TTFund Skills entry point you use for Skills access, search for `skills`, and follow the API key / apikey instructions there.

Keep the key private. Do not paste a real key into source code, issues, screenshots, pull requests, or generated reports.

PowerShell:

```powershell
$env:TTFUND_APIKEY = "your-api-key-here"
```

Bash:

```bash
export TTFUND_APIKEY="your-api-key-here"
```

You can also copy `.env.example` to `.env` for your own notes, but `.env` is intentionally ignored by Git.

## Usage

The maintained entry points are:

- `pipeline/pipeline_v6.py`: generate the dashboard data and HTML.
- `pipeline/server.py`: serve the generated dashboard and expose the refresh button endpoint.
- `pipeline/ttfund_api.py`: small shared TTFund API helper module.

Generate the report:

```powershell
python .\pipeline\pipeline_v6.py
```

Start the local server:

```powershell
python .\pipeline\server.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

The page's refresh button calls `/api/refresh`, reruns the pipeline, and reloads the latest data.

## Data Notes

TTFund APIs do not always return the same fields for every fund. This project treats missing values conservatively:

- If a precomputed return or risk field is missing, it tries to compute the value from NAV history.
- If NAV history is unavailable or rate-limited, the dashboard leaves the value blank.
- NAV history requests are limited to reduce pressure on upstream APIs.

## Disclaimer

This project is for research, education, and open-source tooling. It does not provide investment advice, portfolio recommendations, or guarantees about data accuracy. Always verify fund data from official sources before making financial decisions.

## Roadmap

- Add local NAV caching so successful history responses are not lost when the upstream API is temporarily unavailable.
- Add tests for drawdown and return calculations.
- Add a static demo report with anonymized/sample data.
- Add Docker support.
- Add GitHub Pages documentation.
- Add more explicit data provenance in the UI.

## License

MIT
