"""Decision-cycle artifact writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from fdre_model.market.models import InputVersion, MarketDecision, ModelVersion


ALLOCATION_COLUMNS = [
    "interval_start",
    "interval_end",
    "status",
    "is_peak",
    "wind_mwh",
    "solar_mwh",
    "available_mwh",
    "merchant_price",
    "bess_open_mwh",
    "bess_close_mwh",
    "ppa_sale_mwh",
    "merchant_sale_mwh",
    "peak_power_sale_mwh",
    "bess_charge_mwh",
    "bess_discharge_mwh",
    "curtailment_mwh",
    "shortfall_mwh",
    "penalty_value",
    "revenue_value",
    "recommended_market",
    "applied_rule_ids",
    "skipped_rule_ids",
    "residual_mwh",
    "audit_trace",
]


def write_decision_artifacts(
    target_dir: str | Path,
    *,
    decisions: list[MarketDecision],
    summary: dict[str, Any],
    input_versions: list[InputVersion],
    model_versions: list[ModelVersion],
) -> dict[str, Path]:
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    allocation_path = target / "market_allocation.csv"
    summary_path = target / "market_summary.csv"
    versions_path = target / "market_input_versions.json"
    model_versions_path = target / "market_model_versions.json"
    workbook_path = target / "fdre_market_model.xlsx"

    rows = [decision.to_row() for decision in decisions]
    _write_csv(allocation_path, ALLOCATION_COLUMNS, rows)
    _write_csv(summary_path, list(summary.keys()), [summary])
    versions_path.write_text(
        json.dumps([version.to_json() for version in input_versions], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    model_versions_path.write_text(
        json.dumps([version.to_json() for version in model_versions], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_workbook(workbook_path, rows, summary, input_versions, model_versions)
    return {
        "allocation_csv": allocation_path,
        "summary_csv": summary_path,
        "input_versions_json": versions_path,
        "model_versions_json": model_versions_path,
        "workbook": workbook_path,
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_workbook(
    path: Path,
    allocation_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    input_versions: list[InputVersion],
    model_versions: list[ModelVersion],
) -> None:
    import xlsxwriter

    workbook = xlsxwriter.Workbook(str(path))
    try:
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#E8EEF7", "border": 1})
        number_fmt = workbook.add_format({"num_format": "#,##0.00"})
        _write_sheet(workbook, "Allocation", ALLOCATION_COLUMNS, allocation_rows, header_fmt, number_fmt)
        _write_sheet(
            workbook,
            "Summary",
            ["metric", "value"],
            [{"metric": key, "value": value} for key, value in summary.items()],
            header_fmt,
            number_fmt,
        )
        version_rows = [version.to_json() for version in input_versions]
        version_columns = [
            "dataset_key",
            "version_id",
            "source_type",
            "original_name",
            "user_email",
            "created_at",
            "checksum",
            "row_count",
            "coverage_start",
            "coverage_end",
            "validation_status",
            "validation_message",
            "parent_version_id",
        ]
        _write_sheet(workbook, "Input Versions", version_columns, version_rows, header_fmt, number_fmt)
        model_version_rows = [version.to_json() for version in model_versions]
        model_version_columns = [
            "version_type",
            "version_id",
            "source_type",
            "user_email",
            "created_at",
            "checksum",
            "summary",
            "parent_version_id",
        ]
        _write_sheet(workbook, "Model Versions", model_version_columns, model_version_rows, header_fmt, number_fmt)
    finally:
        workbook.close()


def _write_sheet(
    workbook: Any,
    sheet_name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    header_fmt: Any,
    number_fmt: Any,
) -> None:
    worksheet = workbook.add_worksheet(sheet_name)
    for col, column in enumerate(columns):
        worksheet.write(0, col, column, header_fmt)
    for row_index, row in enumerate(rows, start=1):
        for col, column in enumerate(columns):
            value = row.get(column, "")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                worksheet.write_number(row_index, col, float(value), number_fmt)
            else:
                worksheet.write(row_index, col, "" if value is None else str(value))
    worksheet.freeze_panes(1, 0)
    worksheet.autofilter(0, 0, max(len(rows), 1), max(len(columns) - 1, 0))
    for col, column in enumerate(columns):
        width = min(max(len(column) + 2, 12), 38)
        worksheet.set_column(col, col, width)
