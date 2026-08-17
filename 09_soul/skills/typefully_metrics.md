# Typefully Metrics CLI

Private CLI for querying Typefully `accounts-v2` and growth metrics APIs.

## Setup

Add these keys to your workspace `.env` (do not commit real values):

```bash
TYPEFULLY_AUTHORIZATION=<jwt_token>
TYPEFULLY_ACCOUNT=<account_id>
TYPEFULLY_SESSION=<session_uuid>
TYPEFULLY_COOKIE=<cookie_header_optional>

# Optional
TYPEFULLY_BASE_URL=https://typefully.com
TYPEFULLY_TIMEOUT=30
TYPEFULLY_USER_AGENT=Mozilla/5.0
TYPEFULLY_REFERER=https://typefully.com/grow
```

`TYPEFULLY_COOKIE` is optional. Keep it if the endpoint requires it for your account.

## Commands

Run from repo root:

```bash
python 09_soul/tools/typefully_metrics.py accounts
```

```bash
python 09_soul/tools/typefully_metrics.py --env-file /absolute/path/to/.env accounts
```

```bash
python 09_soul/tools/typefully_metrics.py metric impressions --start-date 2026-02-20 --end-date 2026-03-05
```

```bash
python 09_soul/tools/typefully_metrics.py snapshot --start-date 2026-02-20 --end-date 2026-03-05 --output /tmp/typefully_snapshot.json
```

If `--start-date/--end-date` are omitted, defaults to the last 14 days.

## Metric Names

- `published_tweets`
- `impressions`
- `profile_clicks`
- `followers`
- `engagements`
- `engagement_rate`
- `engagement_heatmap`

## Notes

- Credentials are read from `.env` by walking upward from the current working directory.
- You can override env path with `--env-file` or `TYPEFULLY_ENV_FILE`.
- Errors include HTTP status and response body for quick debugging.
- `snapshot` fetches all metrics and prints or saves one JSON payload.

## Analysis Constraint

When using these metrics for engagement analysis, explicitly treat thread replies as a separate category. `published_tweets-count` may include replies inside threads, so do not interpret it as "top-level tweet count" unless you apply a thread-reply exclusion step in downstream analysis.
