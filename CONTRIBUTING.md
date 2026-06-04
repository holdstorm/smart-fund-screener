# Contributing

Thanks for helping improve Fund ETF Dashboard.

## Development

1. Fork the repository.
2. Create a branch for your change.
3. Run the local checks.
4. Open a pull request with a concise description.

## Local Checks

```powershell
python -m py_compile .\pipeline\ttfund_api.py .\pipeline\pipeline_v6.py .\pipeline\server.py
```

## Good First Issues

- Add unit tests for drawdown calculations.
- Add NAV response caching.
- Improve generated report accessibility.
- Add data provenance indicators to each metric.
- Add a Dockerfile.

## Financial Disclaimer

Please avoid language that implies investment advice. This project is a research and tooling project.
