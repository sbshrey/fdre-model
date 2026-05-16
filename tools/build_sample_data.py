"""Build packaged FDRE sample CSVs from the SECI reference repository."""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path


CURRENT_SAMPLE_YEAR = 2026
SOLAR_SCALE_MW = 162.0
WIND_SCALE_MW = 211.2
BESS_CAPACITY_MWH = 100.0
SECI_BESS_DEGRADATION_PER_CYCLE = 0.0002739726027


def main() -> None:
    parser = argparse.ArgumentParser(description="Build current-year FDRE sample inputs from SECI reference data.")
    parser.add_argument("--seci-repo", required=True, type=Path)
    parser.add_argument("--out-dir", default=Path("fdre_model/sample_data"), type=Path)
    parser.add_argument("--year", default=CURRENT_SAMPLE_YEAR, type=int)
    args = parser.parse_args()

    seci_repo = args.seci_repo.expanduser().resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    solar = _hourly_generation(
        seci_repo / "data" / "Solar_2025-01-01_data_.csv",
        timestamp_column="timestamp",
        timestamp_formats=("%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"),
        scale_mw=SOLAR_SCALE_MW,
        year=args.year,
    )
    wind = _hourly_generation(
        seci_repo / "data" / "Wind_2025_01-01_data_.csv",
        timestamp_column="time stamp",
        timestamp_formats=("%Y-%m-%d %H:%M",),
        scale_mw=WIND_SCALE_MW,
        year=args.year,
    )
    output_profile = _minute_profile(
        seci_repo / "data" / "seci_fdre_v_amendment_03_output_profile.csv",
        value_column="output_profile_kw",
        year=args.year,
    )
    peak_profile = _minute_profile(
        seci_repo / "data" / "seci_fdre_v_amendment_03_output_profile_18_22.csv",
        value_column="output_profile_18_22_kw",
        year=args.year,
    )
    hours = list(_hour_range(args.year))

    _write_hourly_generation(out_dir / "solar_2026_hourly.csv", solar)
    _write_hourly_generation(out_dir / "wind_2026_hourly.csv", wind)
    _write_bess(out_dir / "bess_state_2026_hourly.csv", hours)
    _write_prices(out_dir / "t2_pricing_2026_hourly.csv", hours, output_profile, peak_profile)
    _write_peak_schedule(out_dir / "peak_schedule_2026_hourly.csv", hours, peak_profile)
    _write_metadata(out_dir / "metadata.json", seci_repo, args.year)


def _hourly_generation(
    path: Path,
    *,
    timestamp_column: str,
    timestamp_formats: tuple[str, ...],
    scale_mw: float,
    year: int,
) -> dict[datetime, float]:
    source = _read_kw_rows(path, timestamp_column=timestamp_column, timestamp_formats=timestamp_formats, year=year)
    sorted_source = sorted(source.items())
    source_index = 0
    previous_kw = 0.0
    hourly: dict[datetime, float] = {}
    current_hour: datetime | None = None
    kw_minute_sum = 0.0
    for minute in _minute_range(year):
        while source_index < len(sorted_source) and sorted_source[source_index][0] <= minute:
            previous_kw = sorted_source[source_index][1]
            source_index += 1
        hour = minute.replace(minute=0)
        if current_hour is None:
            current_hour = hour
        if hour != current_hour:
            hourly[current_hour] = round(kw_minute_sum / 60.0 / 1000.0 * scale_mw, 6)
            current_hour = hour
            kw_minute_sum = 0.0
        kw_minute_sum += previous_kw
    if current_hour is not None:
        hourly[current_hour] = round(kw_minute_sum / 60.0 / 1000.0 * scale_mw, 6)
    return hourly


def _read_kw_rows(
    path: Path,
    *,
    timestamp_column: str,
    timestamp_formats: tuple[str, ...],
    year: int,
) -> dict[datetime, float]:
    rows: dict[datetime, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            raw_ts = (row.get(timestamp_column) or "").strip()
            raw_kw = (row.get("Power in KW") or "").strip()
            if not raw_ts or not raw_kw:
                continue
            timestamp = _parse_timestamp(raw_ts, timestamp_formats).replace(year=year)
            rows[timestamp] = max(float(raw_kw), 0.0)
    return rows


def _minute_profile(path: Path, *, value_column: str, year: int) -> dict[datetime, float]:
    result: dict[datetime, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            timestamp = datetime.fromisoformat(str(row["timestamp"])).replace(year=year)
            result[timestamp] = max(float(row[value_column]), 0.0)
    return result


def _write_hourly_generation(path: Path, values: dict[datetime, float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "mwh"))
        for timestamp, mwh in sorted(values.items()):
            writer.writerow((timestamp.strftime("%Y-%m-%d %H:%M"), f"{mwh:.6f}"))


def _write_bess(path: Path, hours: list[datetime]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "soc_mwh", "soh_fraction"))
        for timestamp in hours:
            cycle_count = (timestamp.timetuple().tm_yday - 1) + (timestamp.hour / 24.0)
            soh = max(1.0 - cycle_count * SECI_BESS_DEGRADATION_PER_CYCLE, 0.0)
            daylight_charge = max(0.0, 1.0 - abs(timestamp.hour - 13) / 7.0)
            peak_draw = 0.22 if timestamp.hour in {18, 19, 20, 21} else 0.0
            soc_fraction = min(max(0.5 + 0.25 * daylight_charge - peak_draw, 0.15), 0.9)
            writer.writerow((timestamp.strftime("%Y-%m-%d %H:%M"), f"{BESS_CAPACITY_MWH * soc_fraction:.6f}", f"{soh:.6f}"))


def _write_prices(
    path: Path,
    hours: list[datetime],
    output_profile: dict[datetime, float],
    peak_profile: dict[datetime, float],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "price"))
        for hour in hours:
            avg_output_kw = _hour_average(output_profile, hour)
            peak_kw = _hour_average(peak_profile, hour)
            seasonal_adder = 0.35 if hour.month in {4, 5, 6, 7, 8, 9} else 0.0
            price = 5.75 + 2.25 * (avg_output_kw / 1000.0) + (3.25 if peak_kw > 0.0 else 0.0) + seasonal_adder
            writer.writerow((hour.strftime("%Y-%m-%d %H:%M"), f"{price:.3f}"))


def _write_peak_schedule(path: Path, hours: list[datetime], peak_profile: dict[datetime, float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "is_peak"))
        for hour in hours:
            writer.writerow((hour.strftime("%Y-%m-%d %H:%M"), "1" if _hour_average(peak_profile, hour) > 0.0 else "0"))


def _write_metadata(path: Path, seci_repo: Path, year: int) -> None:
    payload = {
        "sample_year": year,
        "source_repo": str(seci_repo),
        "sources": [
            "data/Solar_2025-01-01_data_.csv",
            "data/Wind_2025_01-01_data_.csv",
            "data/seci_fdre_v_amendment_03_output_profile.csv",
            "data/seci_fdre_v_amendment_03_output_profile_18_22.csv",
            "config/project.yaml simulation.battery.degradation_per_cycle",
        ],
        "gap_fill": "Source generation was forward-filled minute by minute; minutes before first observation use 0.",
        "aggregation": "Minute kW readings are converted to hourly MWh and scaled to FDRE sample capacities.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _hour_average(profile: dict[datetime, float], hour: datetime) -> float:
    values = [profile.get(hour + timedelta(minutes=index), 0.0) for index in range(60)]
    return sum(values) / len(values)


def _hour_range(year: int) -> Iterable[datetime]:
    current = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23)
    while current <= end:
        yield current
        current += timedelta(hours=1)


def _minute_range(year: int) -> Iterable[datetime]:
    current = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59)
    while current <= end:
        yield current
        current += timedelta(minutes=1)


def _parse_timestamp(value: str, formats: tuple[str, ...]) -> datetime:
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp: {value}")


if __name__ == "__main__":
    main()
