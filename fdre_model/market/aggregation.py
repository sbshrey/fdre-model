"""Input parsing, interval aggregation, and rolling window construction."""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Iterable
from zoneinfo import ZoneInfo

from fdre_model.config import AppConfig
from fdre_model.market.models import TimeBucket


ENERGY_COLUMNS = ("mwh", "value_mwh", "energy_mwh", "value", "generation_mwh")
POWER_COLUMNS = ("kw", "power_kw", "Power in KW", "power in kw")
PRICE_COLUMNS = ("price", "merchant_price", "t2_price", "tariff", "value")
SOC_COLUMNS = ("soc_mwh", "bess_soc_mwh", "value_mwh", "value")
SOH_COLUMNS = ("soh_fraction", "bess_soh_fraction", "soh", "state_of_health")
PEAK_COLUMNS = ("is_peak", "peak", "flag_peak", "peak_flag")
TIMESTAMP_COLUMNS = ("timestamp", "date_time", "date & time stamp", "time stamp", "datetime")
SUPPORTED_SOURCE_INTERVAL_SECONDS = (60.0, 15.0 * 60.0, 60.0 * 60.0)


@dataclass(frozen=True)
class ParsedRows:
    rows: list[dict[str, object]]
    row_count: int
    coverage_start: datetime | None
    coverage_end: datetime | None


def parse_interval(value: str) -> timedelta:
    value = str(value).strip().lower()
    if value == "1h":
        return timedelta(hours=1)
    if value == "15m":
        return timedelta(minutes=15)
    if value == "1m":
        return timedelta(minutes=1)
    raise ValueError("Supported intervals are 1m, 15m, and 1h.")


def floor_datetime(value: datetime, interval: timedelta) -> datetime:
    seconds = int(interval.total_seconds())
    epoch = datetime(value.year, value.month, value.day)
    offset = int((value - epoch).total_seconds())
    return epoch + timedelta(seconds=(offset // seconds) * seconds)


def build_time_buckets(
    config: AppConfig,
    *,
    now: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[TimeBucket]:
    current = floor_datetime(now or _configured_now(config), parse_interval(config.market_model.interval))
    interval = parse_interval(config.market_model.interval)
    if window_start is None and window_end is None:
        start = current - timedelta(hours=config.market_model.recent_hours)
        count = int((timedelta(hours=config.market_model.recent_hours + config.market_model.forecast_hours) / interval)) + 1
        end = start + count * interval
    else:
        start = floor_datetime(window_start or (current - timedelta(hours=config.market_model.recent_hours)), interval)
        end = ceil_datetime(window_end or (current + timedelta(hours=config.market_model.forecast_hours + 1)), interval)
        if end <= start:
            raise ValueError("Live preview end must be after start.")
    buckets: list[TimeBucket] = []
    index = 0
    while True:
        bucket_start = start + index * interval
        if bucket_start >= end:
            break
        bucket_end = bucket_start + interval
        if bucket_start < current:
            status = "actual"
        elif bucket_start == current:
            status = "live"
        else:
            status = "forecast"
        buckets.append(
            TimeBucket(
                start=bucket_start,
                end=bucket_end,
                status=status,
                is_peak=bucket_start.hour in config.market_model.default_peak_hours,
            )
        )
        index += 1
    return buckets


def ceil_datetime(value: datetime, interval: timedelta) -> datetime:
    floored = floor_datetime(value, interval)
    if floored == value:
        return floored
    return floored + interval


def _configured_now(config: AppConfig) -> datetime:
    try:
        return datetime.now(ZoneInfo(config.project.timezone)).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def parse_csv_text(text: str, *, dataset_kind: str) -> ParsedRows:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row.")
    rows = [dict(row) for row in reader if any((value or "").strip() for value in row.values())]
    normalized: list[dict[str, object]] = []
    for raw in rows:
        timestamp = _parse_timestamp(_first(raw, TIMESTAMP_COLUMNS))
        normalized.append({"timestamp": timestamp, **_normalize_payload(raw, dataset_kind)})
    timestamps = [row["timestamp"] for row in normalized]
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("CSV contains duplicate timestamps.")
    return ParsedRows(
        rows=normalized,
        row_count=len(normalized),
        coverage_start=min(timestamps) if timestamps else None,
        coverage_end=max(timestamps) if timestamps else None,
    )


def aggregate_generation(rows: list[dict[str, object]], buckets: list[TimeBucket]) -> dict[datetime, float]:
    interval_hours = _infer_source_hours(rows)
    bucket_values = {bucket.start: 0.0 for bucket in buckets}
    for row in rows:
        timestamp = row["timestamp"]
        assert isinstance(timestamp, datetime)
        target = _find_bucket(timestamp, buckets)
        if target is None:
            continue
        if "mwh" in row:
            mwh = float(row["mwh"])
        else:
            mwh = float(row.get("kw") or 0.0) * interval_hours / 1000.0
        bucket_values[target.start] += max(mwh, 0.0)
    return bucket_values


def aggregate_prices(rows: list[dict[str, object]], buckets: list[TimeBucket], *, default: float) -> dict[datetime, float]:
    grouped: dict[datetime, list[float]] = {bucket.start: [] for bucket in buckets}
    for row in rows:
        timestamp = row["timestamp"]
        assert isinstance(timestamp, datetime)
        target = _find_bucket(timestamp, buckets)
        if target is None:
            continue
        grouped[target.start].append(float(row.get("price") or default))
    return {bucket_start: (sum(values) / len(values) if values else float(default)) for bucket_start, values in grouped.items()}


def aggregate_peak_schedule(rows: list[dict[str, object]], buckets: list[TimeBucket]) -> dict[datetime, bool]:
    result = {bucket.start: bucket.is_peak for bucket in buckets}
    for row in rows:
        timestamp = row["timestamp"]
        assert isinstance(timestamp, datetime)
        target = _find_bucket(timestamp, buckets)
        if target is None:
            continue
        result[target.start] = bool(row.get("is_peak"))
    return result


def latest_bess_state(
    rows: list[dict[str, object]],
    buckets: list[TimeBucket],
    *,
    default_soc_mwh: float,
    default_soh_fraction: float,
) -> dict[datetime, tuple[float, float]]:
    sorted_rows = sorted(rows, key=lambda row: row["timestamp"])
    result: dict[datetime, tuple[float, float]] = {}
    index = 0
    current_soc = float(default_soc_mwh)
    current_soh = float(default_soh_fraction)
    for bucket in buckets:
        while index < len(sorted_rows):
            timestamp = sorted_rows[index]["timestamp"]
            assert isinstance(timestamp, datetime)
            if timestamp > bucket.start:
                break
            current_soc = float(sorted_rows[index].get("soc_mwh") or current_soc)
            current_soh = float(sorted_rows[index].get("soh_fraction") or current_soh)
            index += 1
        result[bucket.start] = (current_soc, current_soh)
    return result


def validate_csv_text(text: str, *, dataset_kind: str) -> ParsedRows:
    parsed = parse_csv_text(text, dataset_kind=dataset_kind)
    if not parsed.rows:
        raise ValueError("CSV contains no data rows.")
    if dataset_kind == "generation":
        _validate_generation_units(parsed.rows)
    _validate_source_cadence(parsed.rows, dataset_kind=dataset_kind)
    return parsed


def _normalize_payload(row: dict[str, str], dataset_kind: str) -> dict[str, object]:
    if dataset_kind == "generation":
        mwh = _first(row, ENERGY_COLUMNS)
        if mwh not in (None, ""):
            return {"mwh": float(mwh), "_unit": "mwh"}
        kw = _first(row, POWER_COLUMNS)
        if kw in (None, ""):
            raise ValueError("Generation CSV needs an MWh column or a kW power column.")
        return {"kw": float(kw), "_unit": "kw"}
    if dataset_kind == "price":
        price = _first(row, PRICE_COLUMNS)
        if price in (None, ""):
            raise ValueError("Price CSV needs a price/tariff/value column.")
        return {"price": float(price)}
    if dataset_kind == "bess_state":
        soc = _first(row, SOC_COLUMNS)
        if soc in (None, ""):
            raise ValueError("BESS state CSV needs soc_mwh.")
        soh = _first(row, SOH_COLUMNS)
        return {"soc_mwh": float(soc), "soh_fraction": float(soh) if soh not in (None, "") else 1.0}
    if dataset_kind == "peak_schedule":
        raw = _first(row, PEAK_COLUMNS)
        if raw in (None, ""):
            raise ValueError("Peak schedule CSV needs is_peak.")
        return {"is_peak": _parse_bool(raw)}
    raise ValueError(f"Unsupported dataset kind: {dataset_kind}")


def _find_bucket(timestamp: datetime, buckets: list[TimeBucket]) -> TimeBucket | None:
    for bucket in buckets:
        if bucket.start <= timestamp < bucket.end:
            return bucket
    return None


def _infer_source_hours(rows: list[dict[str, object]]) -> float:
    timestamps = sorted(row["timestamp"] for row in rows if isinstance(row["timestamp"], datetime))
    if len(timestamps) < 2:
        return 1.0
    deltas = [
        (timestamps[index + 1] - timestamps[index]).total_seconds() / 3600.0
        for index in range(len(timestamps) - 1)
        if (timestamps[index + 1] - timestamps[index]).total_seconds() > 0
    ]
    if not deltas:
        return 1.0
    return max(float(median(deltas)), 1.0 / 60.0)


def _parse_timestamp(value: str | None) -> datetime:
    if value in (None, ""):
        raise ValueError("Missing timestamp.")
    text = str(value).strip()
    iso_text = text.replace("T", " ")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return _require_project_local_timestamp(parsed)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp format: {value}")


def _validate_generation_units(rows: list[dict[str, object]]) -> None:
    units = {str(row.get("_unit")) for row in rows if row.get("_unit")}
    if len(units) > 1:
        raise ValueError("Generation CSV must use either MWh energy columns or kW power columns, not both.")


def _validate_source_cadence(rows: list[dict[str, object]], *, dataset_kind: str) -> None:
    timestamps = sorted(row["timestamp"] for row in rows if isinstance(row.get("timestamp"), datetime))
    if len(timestamps) < 2:
        return
    deltas = [
        (timestamps[index + 1] - timestamps[index]).total_seconds()
        for index in range(len(timestamps) - 1)
    ]
    unique_deltas = sorted(set(deltas))
    if len(unique_deltas) > 1:
        if dataset_kind == "generation" and any(row.get("_unit") == "kw" for row in rows):
            raise ValueError(
                "Generation kW CSV has missing or irregular intervals; use a consistent 1m, 15m, or 1h cadence."
            )
        raise ValueError("CSV has missing or irregular intervals; use a consistent 1m, 15m, or 1h cadence.")
    if unique_deltas[0] not in SUPPORTED_SOURCE_INTERVAL_SECONDS:
        raise ValueError("CSV interval must be one of 1m, 15m, or 1h.")


def _require_project_local_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError("CSV timestamps must use project-local time without timezone offsets.")
    return value.replace(tzinfo=None)


def _first(row: dict[str, str], names: Iterable[str]) -> str | None:
    lower = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        if name in row:
            return row[name]
        value = lower.get(str(name).lower())
        if value is not None:
            return value
    return None


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return not math.isclose(float(value), 0.0)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "peak"}:
        return True
    if text in {"0", "false", "no", "n", "non-peak", "nonpeak"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value}")
