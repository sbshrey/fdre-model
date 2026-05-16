from __future__ import annotations

from fdre_model.config import AppConfig
from fdre_model.market.registry import workbook_case_rows, workbook_variable_rows


def test_workbook_variable_registry_maps_core_notes_codes_to_config_values() -> None:
    rows = workbook_variable_rows(AppConfig())
    by_code = {row["code"]: row for row in rows}

    assert by_code["M1"]["app_field"] == "MarketDecision.ppa_sale_mwh"
    assert by_code["G1"]["current_value"] == "211.2"
    assert by_code["G2"]["current_value"] == "162"
    assert by_code["T1"]["current_value"] == "6"
    assert by_code["Cap10"]["current_value"] == "150"
    assert by_code["P1"]["status"] == "implemented"
    assert by_code["P2"]["status"] == "implemented"


def test_workbook_case_registry_covers_peak_non_peak_and_procurement_cases() -> None:
    rows = workbook_case_rows()
    codes = {row["code"] for row in rows}

    assert {"Case 2", "Case 3", "Cases 4/5", "Case 6/7", "Clause 1/iii"}.issubset(codes)
    assert {"Case 8", "Cases 9/10", "Cases 11/12"}.issubset(codes)
    assert any(row["scope"] == "Peak" and row["status"] == "partial" for row in rows)
    assert any(row["scope"] == "Annual CUF" and row["status"] == "planned" for row in rows)
