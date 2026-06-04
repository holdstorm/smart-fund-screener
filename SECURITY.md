# Security Policy

## Supported Versions

This project is in early development. Security fixes are applied to the `main` branch.

## Reporting a Vulnerability

Please open a private security advisory on GitHub if available, or contact the maintainer directly.

Do not publish API keys, account identifiers, or private data in public issues.

## Secrets

Never commit `.env` files or real API keys. The application expects:

```text
TTFUND_APIKEY
```

If a key was committed before the repository was made public, rotate it before publishing.
