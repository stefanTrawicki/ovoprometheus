# ovoprometheus

Prometheus exporter for [OVO Energy](https://ovoenergy.com) usage data, built on the
[`ovoenergy`](https://pypi.org/project/ovoenergy/) Python client.

Exports daily electricity/gas consumption and cost, yesterday's half-hourly
consumption, and the grid carbon-intensity forecast on `/metrics`.

> Not officially supported by OVO Energy. Their private APIs can change at any
> time and this exporter may break. Tested with UK accounts only.

## Requirements

- [uv](https://docs.astral.sh/uv/) (local development)
- Docker (container deployment)
- An OVO Energy account: email + password

## Setup

```sh
cp .env.example .env   # then fill in OVO_USERNAME / OVO_PASSWORD
task install
```

## Run

```sh
task run            # local, serves http://localhost:9225/metrics
task docker:build
task docker:run     # container, reads .env
```

The exporter logs in once and refreshes its OAuth token automatically. It
re-authenticates (with exponential backoff) only when a request is rejected.
Keep `OVO_EXPORTER_INTERVAL_SECONDS` at 1800 (default) or higher — OVO
rate-limits logins via Cloudflare, and repeated failures just get you throttled.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OVO_USERNAME` | — | OVO account email (required) |
| `OVO_PASSWORD` | — | OVO account password (required) |
| `OVO_EXPORTER_PORT` | `9225` | Port for `/metrics` |
| `OVO_EXPORTER_INTERVAL_SECONDS` | `1800` | Refresh interval |
| `OVO_ACCOUNT_IDS` | all | Comma-separated OVO account numbers to export |

## Metrics

| Metric | Labels | Description |
|---|---|---|
| `ovoenergy_electricity_daily_kwh` | `account`, `date` | Daily electricity consumption (current + previous month) |
| `ovoenergy_electricity_daily_cost_gbp` | `account`, `date` | Daily electricity cost |
| `ovoenergy_gas_daily_kwh` | `account`, `date` | Daily gas consumption |
| `ovoenergy_gas_daily_cost_gbp` | `account`, `date` | Daily gas cost |
| `ovoenergy_electricity_halfhour_kwh` | `account`, `slot` | Half-hourly electricity (yesterday) |
| `ovoenergy_gas_halfhour_kwh` | `account`, `slot` | Half-hourly gas (yesterday) |
| `ovoenergy_carbon_intensity_forecast_gco2_per_kwh` | `time_from` | Grid carbon intensity forecast |
| `ovoenergy_carbon_intensity_current_level` | `level` | 1 = very low … 5 = very high; active level set to its index |
| `ovoenergy_exporter_up` | — | 1 if the last refresh succeeded |
| `ovoenergy_exporter_last_success_timestamp_seconds` | — | Unix time of last successful refresh |

Every account on the OVO profile is exported by default; restrict with
`OVO_ACCOUNT_IDS`. `account` is the OVO account number.

## Prometheus scrape config

```yaml
scrape_configs:
  - job_name: ovoenergy
    scrape_interval: 5m
    static_configs:
      - targets: ['localhost:9225']
```

## Notes

- Half-hourly series stay empty unless your meter submits 30-min reads
  (`hasHhData`). Missing fuel types simply produce no samples.
- The `carbon-api` footprint endpoint has been observed returning 404 for some
  accounts; it is intentionally not exported.
