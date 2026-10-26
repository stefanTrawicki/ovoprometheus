# CLAUDE.md

Guidance for working in this repository.

## What this is

Single-purpose Prometheus exporter for OVO Energy usage data. Wraps the
[`ovoenergy`](https://pypi.org/project/ovoenergy/) async client
(`OVOEnergy` class) and exposes gauges via `prometheus_client` on
`/metrics` (default `:9225`).

- Entry point: `ovo_prometheus/exporter.py` — console script `ovo-prometheus`.
- No tests, no framework: one module with `main()` (async refresh loop) and
  `run()` (sync entry point). Keep it that way unless scope genuinely grows.

## Commands

- `task install` — `uv sync`
- `task run` — run exporter locally (reads `.env`)
- `task lint` — ruff check + format (ruff is the only linter; no separate
  formatters)
- `task docker:build` / `task docker:run` — container build/run
- Before committing: `task lint`

## Environment

Credentials come from env vars or `.env` (see `.env.example`):
`OVO_USERNAME`, `OVO_PASSWORD`, optional `OVO_EXPORTER_PORT`,
`OVO_EXPORTER_INTERVAL_SECONDS`, `OVO_ACCOUNT_IDS` (comma-separated account
numbers to restrict to; unset exports every account on the profile). Never
commit `.env`.

## Operational constraints (do not break these)

- **OVO rate-limits logins via Cloudflare (429).** Default interval is 1800s;
  never lower it below ~900s. Login failures back off exponentially
  (`_login`); `authenticate()` returning `False` is indistinguishable between
  bad credentials and throttling.
- The exporter logs in **once** at startup and relies on the client's automatic
  OAuth token refresh. Only re-authenticate on `OVOEnergyAPINotAuthorized`.
- All accounts on the profile are exported per refresh; `custom_account_id` is
  set per account when fetching.
- `get_carbon_intensity().current` is a level *string* (`low`, `moderate`, …),
  not a number. Numeric intensity comes only from the `forecast` entries.
- The OVO `carbon-api` footprint endpoint 404s for some accounts — do not
  re-add it without checking it works first.
- Daily usage is fetched per month (`date=YYYY-MM`), half-hourly per day
  (`date=YYYY-MM-DD`). Half-hourly data only exists if the meter submits
  half-hour reads.

## Metric conventions

- All user-facing gauges are prefixed `ovoenergy_`.
- Per-account samples use label `account` (OVO account number as string).
- Series for dates that disappear from API responses are removed each refresh
  (`_clear()` + `_SERIES` tracking) — keep this when adding new gauges, or
  stale labels accumulate forever.
- `None` API values are skipped (`_set` ignores `None`), never exported as 0.
