from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from fdre_model.market.models import RuleDefinition
from fdre_model.market.rules import DEFAULT_RULES
from fdre_model.storage.local import LocalWorkspaceStore
from fdre_model.storage.scope import WorkspaceScope


def test_input_override_creates_active_version_with_parent(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    first = store.active_version(state, "solar")
    assert first is not None

    second = store.create_version(
        state,
        "solar",
        "timestamp,mwh\n2026-04-01 12:00,10\n",
        source_type="manual_1h",
        original_name="manual:solar.csv",
        user_email="operator@example.com",
    )

    assert second.parent_version_id == first.version_id
    assert store.active_version_id(state, "solar") == second.version_id
    assert second.user_email == "operator@example.com"


def test_seed_inputs_use_packaged_seci_reference_year(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()

    solar = store.active_version(state, "solar")
    peak = store.active_version(state, "peak_schedule")

    assert solar is not None
    assert solar.source_type == "seed_seci_reference"
    assert solar.original_name == "seed:seci-reference-2026:solar_2026_hourly.csv"
    assert solar.row_count == 8760
    assert solar.coverage_start == "2026-01-01 00:00:00"
    assert solar.coverage_end == "2026-12-31 23:00:00"
    assert peak is not None
    assert peak.row_count == 8760


def test_legacy_seed_inputs_are_refreshed_but_manual_inputs_are_kept(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    solar = store.active_version(state, "solar")
    assert solar is not None
    old_solar_id = solar.version_id
    store.create_version(
        state,
        "wind",
        "timestamp,mwh\n2026-01-01 00:00,10\n2026-01-01 01:00,11\n",
        source_type="manual_1h",
        original_name="manual:wind.csv",
        user_email="operator@example.com",
    )
    manual_wind_id = store.active_version_id(state, "wind")

    store.get_version(state, "solar", old_solar_id).raw_path.write_text(
        "timestamp,mwh\n2026-01-01 00:00,0\n",
        encoding="utf-8",
    )
    metadata_path = store._version_dir(state, "solar", old_solar_id) / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["source_type"] = "seed"
    payload["original_name"] = "seed:solar.csv"
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    refreshed = store.ensure()

    assert store.active_version_id(refreshed, "solar") != old_solar_id
    assert store.active_version(refreshed, "solar").source_type == "seed_seci_reference"
    assert store.active_version_id(refreshed, "wind") == manual_wind_id


def test_decision_cycle_records_input_versions_and_artifacts(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()

    current = datetime.now().replace(minute=0, second=0, microsecond=0)
    cycle = store.create_decision_cycle(state, now=current)

    assert cycle.input_versions
    assert cycle.model_versions["assumptions"]
    assert cycle.model_versions["rules"]
    assert cycle.artifact_paths["allocation_csv"].exists()
    assert cycle.artifact_paths["workbook"].exists()
    assert cycle.artifact_paths["model_versions_json"].exists()
    assert cycle.summary["rows"] > 0
    assert "p1_forecast_22h_curtailment_mwh" in cycle.summary
    assert "p3_monthly_peak_90_deficit_mwh" in cycle.summary
    assert "annual_cuf_pct" in cycle.summary
    assert cycle.summary["assumption_version_id"] == cycle.model_versions["assumptions"]
    assert cycle.summary["rule_version_id"] == cycle.model_versions["rules"]
    assert cycle.source_health
    assert (
        cycle.summary["source_health_ok"]
        + cycle.summary["source_health_warning"]
        + cycle.summary["source_health_critical"]
    ) == len(cycle.source_health)


def test_source_health_flags_stale_active_inputs(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()

    store.create_version(
        state,
        "solar",
        "timestamp,mwh\n2026-04-01 00:00,10\n",
        source_type="manual_1h",
        original_name="manual:solar.csv",
        user_email="operator@example.com",
    )

    health = store.source_health(state, now=datetime(2026, 4, 3, 12, 0))
    solar = next(item for item in health if item.dataset_key == "solar")

    assert solar.status == "critical"
    assert "current live interval" in solar.message


def test_active_raw_input_validation_error_is_source_health_critical(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    current = datetime.now().replace(minute=0, second=0, microsecond=0)
    solar = store.active_version(state, "solar")
    assert solar is not None and solar.raw_path is not None
    solar.raw_path.write_text(
        "\n".join(
            [
                "timestamp,mwh",
                f"{current:%Y-%m-%d %H:%M},1",
                f"{current + timedelta(minutes=15):%Y-%m-%d %H:%M},2",
                f"{current + timedelta(minutes=45):%Y-%m-%d %H:%M},3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cycle = store.create_decision_cycle(state, now=current)
    solar_health = next(item for item in cycle.source_health if item.dataset_key == "solar")

    assert cycle.summary["rows"] > 0
    assert solar_health.status == "critical"
    assert "Active input fails current validation" in solar_health.message


def test_latest_cycle_refreshes_when_rolling_window_moves(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    current = datetime.now().replace(minute=0, second=0, microsecond=0)

    first = store.latest_or_create_cycle(state, now=current)
    same = store.latest_or_create_cycle(state, now=current)
    moved = store.latest_or_create_cycle(state, now=current + timedelta(hours=1))

    assert same.cycle_id == first.cycle_id
    assert moved.cycle_id != first.cycle_id


def test_assumption_save_creates_immutable_user_attributed_version(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    first = store.active_model_version(state, "assumptions")
    assert first is not None

    payload = store.load_config(state).to_dict()
    payload["capacities"]["ppa_mwh"] = 175.0
    store.save_config_from_payload(state, payload, user_email="admin@example.com")
    second = store.active_model_version(state, "assumptions")

    assert second is not None
    assert second.version_id != first.version_id
    assert second.parent_version_id == first.version_id
    assert second.user_email == "admin@example.com"
    assert second.payload_path is not None and second.payload_path.exists()
    assert store.load_config(state).capacities.ppa_mwh == 175.0


def test_rule_save_creates_immutable_user_attributed_version(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    first = store.active_model_version(state, "rules")
    assert first is not None

    rules = store.load_rules(state)
    updated = [
        RuleDefinition(rule.rule_id, rule.name, rule.priority, rule.rule_id != "merchant_sale", rule.description)
        for rule in rules
    ]
    second = store.save_rules(state, updated, user_email="admin@example.com")

    assert second.version_id != first.version_id
    assert second.parent_version_id == first.version_id
    assert second.user_email == "admin@example.com"
    assert second.payload_path is not None and second.payload_path.exists()
    assert store.active_model_version_id(state, "rules") == second.version_id
    assert any(not rule.enabled for rule in store.load_rules(state))
    assert all(isinstance(rule.condition, dict) and isinstance(rule.action, dict) for rule in store.load_rules(state))


def test_latest_cycle_refreshes_when_model_versions_change(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    current = datetime.now().replace(minute=0, second=0, microsecond=0)

    first = store.latest_or_create_cycle(state, now=current)
    payload = store.load_config(state).to_dict()
    payload["tariffs"]["ppa"] = 6.25
    store.save_config_from_payload(state, payload, user_email="admin@example.com")
    refreshed = store.latest_or_create_cycle(state, now=current)

    assert refreshed.cycle_id != first.cycle_id
    assert refreshed.model_versions["assumptions"] != first.model_versions["assumptions"]


def test_workspace_migrates_legacy_rules_to_configurable_definitions(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    legacy = [
        {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "priority": rule.priority,
            "enabled": rule.enabled,
            "description": rule.description,
        }
        for rule in list(DEFAULT_RULES)[:5]
    ]
    store.rules_path(state).write_text(json.dumps(legacy), encoding="utf-8")

    migrated = store.ensure()
    rules = {rule.rule_id: rule for rule in store.load_rules(migrated)}

    assert rules["ppa_sale"].condition
    assert rules["ppa_sale"].action == {"type": "sell_ppa"}
    assert "annual_cuf_monitor" in rules
    assert rules["annual_cuf_monitor"].enabled is False
    assert store.active_model_version(migrated, "rules").source_type == "migration"


def test_scoped_local_workspaces_are_isolated(tmp_path: Path) -> None:
    base = LocalWorkspaceStore(tmp_path / ".workspace")
    plant_a = base.for_scope(WorkspaceScope.from_values("Acme", "Plant A"))
    plant_b = base.for_scope(WorkspaceScope.from_values("Acme", "Plant B"))

    state_a = plant_a.ensure()
    state_b = plant_b.ensure()
    plant_a.create_version(
        state_a,
        "solar",
        "timestamp,mwh\n2026-04-01 12:00,10\n",
        source_type="manual_1h",
        original_name="manual:solar.csv",
        user_email="operator@example.com",
    )

    assert state_a.root != state_b.root
    assert state_a.root.parts[-4:] == ("customers", "acme", "workspaces", "plant-a")
    assert state_b.root.parts[-4:] == ("customers", "acme", "workspaces", "plant-b")
    assert plant_a.active_version_id(state_a, "solar") != plant_b.active_version_id(state_b, "solar")


def test_operator_acknowledgement_is_stored_per_cycle(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / ".workspace")
    state = store.ensure()
    current = datetime.now().replace(minute=0, second=0, microsecond=0)
    cycle = store.create_decision_cycle(state, now=current)

    acknowledgement = store.acknowledge_cycle(
        state,
        cycle.cycle_id,
        user_email="operator@example.com",
        note="Reviewed shortfall exposure.",
    )

    loaded = store.get_acknowledgement(state, cycle.cycle_id)
    assert loaded == acknowledgement
    assert loaded is not None
    assert loaded.note == "Reviewed shortfall exposure."
    assert store.acknowledgement_map(state)[cycle.cycle_id].user_email == "operator@example.com"
