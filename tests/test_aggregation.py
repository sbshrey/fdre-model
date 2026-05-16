from __future__ import annotations

from datetime import datetime

import pytest

from fdre_model.config import AppConfig
from fdre_model.market.aggregation import (
    aggregate_generation,
    build_time_buckets,
    parse_csv_text,
    validate_csv_text,
)


def test_minute_and_15m_energy_aggregate_to_hourly_mwh() -> None:
    config = AppConfig()
    buckets = build_time_buckets(config, now=datetime(2026, 4, 1, 12, 0))
    text = "\n".join(
        [
            "timestamp,mwh",
            "2026-04-01 12:00,1",
            "2026-04-01 12:15,2",
            "2026-04-01 12:30,3",
            "2026-04-01 12:45,4",
        ]
    )
    rows = parse_csv_text(text, dataset_kind="generation").rows

    aggregated = aggregate_generation(rows, buckets)

    assert aggregated[datetime(2026, 4, 1, 12, 0)] == pytest.approx(10.0)


def test_kw_source_is_converted_using_inferred_interval() -> None:
    config = AppConfig()
    buckets = build_time_buckets(config, now=datetime(2026, 4, 1, 12, 0))
    text = "\n".join(
        [
            "timestamp,Power in KW",
            "2026-04-01 12:00,1000",
            "2026-04-01 12:15,1000",
            "2026-04-01 12:30,1000",
            "2026-04-01 12:45,1000",
        ]
    )
    rows = parse_csv_text(text, dataset_kind="generation").rows

    aggregated = aggregate_generation(rows, buckets)

    assert aggregated[datetime(2026, 4, 1, 12, 0)] == pytest.approx(1.0)


def test_duplicate_timestamps_are_rejected() -> None:
    text = "\n".join(
        [
            "timestamp,mwh",
            "2026-04-01 12:00,1",
            "2026-04-01 12:00,2",
        ]
    )

    with pytest.raises(ValueError, match="duplicate timestamps"):
        parse_csv_text(text, dataset_kind="generation")


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-04-01 12:01",
        "2026-04-01 12:15",
        "2026-04-01 13:00",
    ],
)
def test_supported_source_cadences_are_valid(timestamp: str) -> None:
    text = "\n".join(
        [
            "timestamp,mwh",
            "2026-04-01 12:00,1",
            f"{timestamp},2",
        ]
    )

    parsed = validate_csv_text(text, dataset_kind="generation")

    assert parsed.row_count == 2


def test_missing_intervals_are_rejected() -> None:
    text = "\n".join(
        [
            "timestamp,mwh",
            "2026-04-01 12:00,1",
            "2026-04-01 12:15,2",
            "2026-04-01 12:45,3",
        ]
    )

    with pytest.raises(ValueError, match="missing or irregular intervals"):
        validate_csv_text(text, dataset_kind="generation")


def test_irregular_kw_intervals_are_rejected() -> None:
    text = "\n".join(
        [
            "timestamp,kw",
            "2026-04-01 12:00,1000",
            "2026-04-01 12:15,1000",
            "2026-04-01 12:50,1000",
        ]
    )

    with pytest.raises(ValueError, match="Generation kW CSV has missing or irregular intervals"):
        validate_csv_text(text, dataset_kind="generation")


def test_unsupported_source_cadence_is_rejected() -> None:
    text = "\n".join(
        [
            "timestamp,mwh",
            "2026-04-01 12:00,1",
            "2026-04-01 12:30,2",
        ]
    )

    with pytest.raises(ValueError, match="one of 1m, 15m, or 1h"):
        validate_csv_text(text, dataset_kind="generation")


def test_mixed_generation_units_are_rejected() -> None:
    text = "\n".join(
        [
            "timestamp,mwh,kw",
            "2026-04-01 12:00,1,",
            "2026-04-01 13:00,,1000",
        ]
    )

    with pytest.raises(ValueError, match="either MWh energy columns or kW power columns"):
        validate_csv_text(text, dataset_kind="generation")


def test_timezone_aware_timestamps_are_rejected() -> None:
    text = "\n".join(
        [
            "timestamp,mwh",
            "2026-04-01T12:00:00+05:30,1",
        ]
    )

    with pytest.raises(ValueError, match="project-local time without timezone offsets"):
        parse_csv_text(text, dataset_kind="generation")
