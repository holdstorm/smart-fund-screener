# Data Source Notes

This project currently uses TTFund Skills API responses.

## Environment Variable

The API key must be provided through:

```text
TTFUND_APIKEY
```

`TTFUND_API_KEY` is also accepted as a compatibility alias.

## Metric Strategy

The dashboard uses a conservative hybrid strategy:

- Prefer precomputed API fields for return metrics when present.
- Use NAV history to calculate drawdowns and missing short-window returns when available.
- Leave cells blank when both precomputed fields and NAV history are unavailable.
- Limit NAV history requests to reduce upstream API pressure.

## Known Limitations

- Some funds return incomplete fields.
- NAV history may temporarily return zero items after repeated refreshes.
- QDII funds can have delayed NAV dates compared with domestic funds.
- The generated dashboard is for research and tooling only, not investment advice.
