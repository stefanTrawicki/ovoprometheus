"""Prometheus exporter for OVO Energy usage data.

Exposes daily electricity/gas consumption and cost, yesterday's half-hourly
consumption, and the grid carbon-intensity forecast on /metrics.

Usage:
    pip install prometheus-client
    python examples/prometheus_exporter.py

Environment (or .env in the working directory):
    OVO_USERNAME, OVO_PASSWORD          required
    OVO_EXPORTER_PORT                   default 9225
    OVO_EXPORTER_INTERVAL_SECONDS       default 1800 (keep >= 900; OVO
                                        rate-limits logins via Cloudflare)
    OVO_ACCOUNT_IDS                     optional comma-separated OVO account
                                        numbers to export; unset = all accounts
                                        on the profile

The client logs in once and refreshes its OAuth token automatically; it only
re-authenticates when a request is rejected, with backoff.
"""

import asyncio
import contextlib
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from ovoenergy import OVOEnergy
from ovoenergy.exceptions import OVOEnergyException
from prometheus_client import Gauge, start_http_server

_LOGGER = logging.getLogger("ovoenergy_exporter")

GAS_DAILY_KWH = Gauge(
    "ovoenergy_gas_daily_kwh",
    "Daily gas consumption in kWh",
    ["account", "date"],
)
ELECTRICITY_DAILY_KWH = Gauge(
    "ovoenergy_electricity_daily_kwh",
    "Daily electricity consumption in kWh",
    ["account", "date"],
)
GAS_DAILY_COST = Gauge(
    "ovoenergy_gas_daily_cost_gbp",
    "Daily gas cost in GBP",
    ["account", "date"],
)
ELECTRICITY_DAILY_COST = Gauge(
    "ovoenergy_electricity_daily_cost_gbp",
    "Daily electricity cost in GBP",
    ["account", "date"],
)
GAS_HALFHOUR_KWH = Gauge(
    "ovoenergy_gas_halfhour_kwh",
    "Half-hourly gas consumption in kWh",
    ["account", "slot"],
)
ELECTRICITY_HALFHOUR_KWH = Gauge(
    "ovoenergy_electricity_halfhour_kwh",
    "Half-hourly electricity consumption in kWh",
    ["account", "slot"],
)
CARBON_INTENSITY_FORECAST = Gauge(
    "ovoenergy_carbon_intensity_forecast_gco2_per_kwh",
    "Grid carbon intensity forecast in gCO2/kWh",
    ["time_from"],
)
CARBON_INTENSITY_LEVEL = Gauge(
    "ovoenergy_carbon_intensity_current_level",
    "Current carbon intensity level (1=very low, 4=very high)",
    ["level"],
)
LAST_SUCCESS = Gauge(
    "ovoenergy_exporter_last_success_timestamp_seconds",
    "Unix timestamp of the last successful data refresh",
)
UP = Gauge(
    "ovoenergy_exporter_up",
    "1 if the last refresh succeeded, 0 otherwise",
)

_LEVELS = {"very low": 1, "low": 2, "moderate": 3, "high": 4, "very high": 5}
# label tuple -> gauge, so samples from dates no longer returned can be removed
_SERIES: dict[Gauge, set[tuple]] = {}


def _set(gauge: Gauge, labels: tuple, value: float | None) -> None:
    if value is None:
        return
    gauge.labels(*labels).set(value)
    _SERIES.setdefault(gauge, set()).add(labels)


def _clear() -> None:
    for gauge, keys in _SERIES.items():
        for labels in keys:
            gauge.remove(*labels)
    _SERIES.clear()


def _load_env_file(env_path: str = ".env") -> None:
    env_file = Path(env_path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


async def _login(username: str, password: str) -> OVOEnergy | None:
    """Authenticate with backoff; Cloudflare 429s brief login bursts."""
    session = None
    for attempt in range(4):
        if session is not None:
            await session.close()
        session = aiohttp.ClientSession()
        client = OVOEnergy(client_session=session)
        if await client.authenticate(username, password):
            return client
        delay = 30 * 2**attempt
        _LOGGER.warning("Login attempt %d failed; retrying in %ds", attempt + 1, delay)
        await asyncio.sleep(delay)
    await session.close()
    return None


def _select_accounts(client: OVOEnergy) -> list[int]:
    """All profile accounts, or the subset named in OVO_ACCOUNT_IDS."""
    raw = os.getenv("OVO_ACCOUNT_IDS")
    if not raw:
        return client.account_ids or []
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    available = {str(aid) for aid in client.account_ids or []}
    unknown = wanted - available
    if unknown:
        _LOGGER.warning(
            "Unknown account id(s) in OVO_ACCOUNT_IDS: %s", ", ".join(sorted(unknown))
        )
    return [aid for aid in client.account_ids or [] if str(aid) in wanted]


async def _refresh(client: OVOEnergy, accounts: list[int]) -> None:
    """Fetch one full cycle of data for the selected accounts."""
    _clear()
    today = datetime.now()
    months = [today.strftime("%Y-%m"), (today - timedelta(days=32)).strftime("%Y-%m")]
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    for account_id in accounts:
        client.custom_account_id = account_id
        label = str(account_id)
        for month in months:
            usage = await client.get_daily_usage(month)
            for reading in usage.electricity or []:
                date = (
                    reading.interval.start.strftime("%Y-%m-%d")
                    if reading.interval
                    else month
                )
                _set(ELECTRICITY_DAILY_KWH, (label, date), reading.consumption)
                _set(
                    ELECTRICITY_DAILY_COST,
                    (label, date),
                    float(reading.cost.amount) if reading.cost else None,
                )
            for reading in usage.gas or []:
                date = (
                    reading.interval.start.strftime("%Y-%m-%d")
                    if reading.interval
                    else month
                )
                _set(GAS_DAILY_KWH, (label, date), reading.consumption)
                _set(
                    GAS_DAILY_COST,
                    (label, date),
                    float(reading.cost.amount) if reading.cost else None,
                )

        halfhourly = await client.get_half_hourly_usage(yesterday)
        for reading in halfhourly.electricity or []:
            _set(
                ELECTRICITY_HALFHOUR_KWH,
                (label, reading.interval.start.isoformat()),
                reading.consumption,
            )
        for reading in halfhourly.gas or []:
            _set(
                GAS_HALFHOUR_KWH,
                (label, reading.interval.start.isoformat()),
                reading.consumption,
            )

    intensity = await client.get_carbon_intensity()
    for forecast in intensity.forecast:
        _set(CARBON_INTENSITY_FORECAST, (forecast.time_from,), forecast.intensity)
    if isinstance(intensity.current, str) and intensity.current.lower() in _LEVELS:
        level = intensity.current.lower()
        for name, value in _LEVELS.items():
            CARBON_INTENSITY_LEVEL.labels(name).set(value if name == level else 0)

    LAST_SUCCESS.set(datetime.now().timestamp())
    UP.set(1)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _load_env_file()
    username = os.getenv("OVO_USERNAME")
    password = os.getenv("OVO_PASSWORD")
    if not username or not password:
        raise SystemExit("OVO_USERNAME and OVO_PASSWORD must be set")

    port = int(os.getenv("OVO_EXPORTER_PORT", "9225"))
    interval = int(os.getenv("OVO_EXPORTER_INTERVAL_SECONDS", "1800"))
    start_http_server(port)
    _LOGGER.info("Listening on :%d, refreshing every %ds", port, interval)

    client = await _login(username, password)
    if client is None:
        raise SystemExit("Authentication failed")
    await client.bootstrap_accounts()
    accounts = _select_accounts(client)
    _LOGGER.info("Accounts: %s", accounts)

    while True:
        try:
            await _refresh(client, accounts)
            _LOGGER.info("Refresh ok")
        except OVOEnergyException as err:
            _LOGGER.warning("Refresh failed: %s", err)
            UP.set(0)
        await asyncio.sleep(interval)


def run() -> None:
    """Console-script entry point."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())


if __name__ == "__main__":
    run()
