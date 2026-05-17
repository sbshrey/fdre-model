from __future__ import annotations

from pathlib import Path

from fdre_model.market.models import InputVersion
from fdre_model.storage.hosted import HostedPersistence, HostedStorageConfig
from fdre_model.storage.scope import WorkspaceScope


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}

    def put_object(self, **kwargs: object) -> None:
        self.objects[str(kwargs["Key"])] = dict(kwargs)


class FakeTable:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put_item(self, *, Item: dict[str, object]) -> None:
        self.items.append(Item)


class FakeDynamo:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def Table(self, _name: str) -> FakeTable:
        return self.table


def test_hosted_persistence_writes_scoped_input_version_to_s3_and_dynamo(tmp_path: Path) -> None:
    raw = tmp_path / "raw.csv"
    raw.write_text("timestamp,mwh\n2026-04-01 12:00,10\n", encoding="utf-8")
    version = InputVersion(
        dataset_key="solar",
        version_id="v1",
        source_type="manual",
        original_name="manual:solar.csv",
        user_email="operator@example.com",
        created_at="2026-05-16T00:00:00Z",
        checksum="abc",
        row_count=1,
        coverage_start="2026-04-01 12:00:00",
        coverage_end="2026-04-01 12:00:00",
        validation_status="ok",
        validation_message="Validated.",
        raw_path=raw,
    )
    s3 = FakeS3()
    table = FakeTable()
    persistence = HostedPersistence(
        HostedStorageConfig(bucket="fdre-test", dynamodb_table="fdre-index", prefix="tenant-data"),
        s3_client=s3,
        dynamodb_resource=FakeDynamo(table),
    )

    persistence.persist_input_version(WorkspaceScope.from_values("Acme", "Plant A"), version)

    assert "tenant-data/customers/acme/workspaces/plant-a/inputs/solar/v1/metadata.json" in s3.objects
    assert "tenant-data/customers/acme/workspaces/plant-a/inputs/solar/v1/raw.csv" in s3.objects
    assert table.items[0]["pk"] == "CUSTOMER#acme#WORKSPACE#plant-a"
    assert table.items[0]["sk"] == "INPUT_VERSION#solar#v1"


def test_hosted_persistence_can_use_customer_workspace_dynamo_keys(tmp_path: Path) -> None:
    raw = tmp_path / "raw.csv"
    raw.write_text("timestamp,mwh\n2026-04-01 12:00,10\n", encoding="utf-8")
    version = InputVersion(
        dataset_key="solar",
        version_id="v1",
        source_type="manual",
        original_name="manual:solar.csv",
        user_email="operator@example.com",
        created_at="2026-05-16T00:00:00Z",
        checksum="abc",
        row_count=1,
        coverage_start="2026-04-01 12:00:00",
        coverage_end="2026-04-01 12:00:00",
        validation_status="ok",
        validation_message="Validated.",
        raw_path=raw,
    )
    table = FakeTable()
    persistence = HostedPersistence(
        HostedStorageConfig(
            bucket="fdre-test",
            dynamodb_table="fdre-index",
            prefix="tenant-data",
            dynamodb_key_mode="customer_workspace",
        ),
        s3_client=FakeS3(),
        dynamodb_resource=FakeDynamo(table),
    )

    persistence.persist_input_version(WorkspaceScope.from_values("Acme", "Plant A"), version)

    assert table.items[0]["customer_id"] == "acme"
    assert table.items[0]["workspace_id"] == "plant-a#INPUT_VERSION#solar#v1"
    assert table.items[0]["workspace_key"] == "plant-a"


def test_hosted_persistence_writes_customer_portfolio_metadata() -> None:
    s3 = FakeS3()
    table = FakeTable()
    persistence = HostedPersistence(
        HostedStorageConfig(bucket="fdre-test", dynamodb_table="fdre-index", prefix="tenant-data"),
        s3_client=s3,
        dynamodb_resource=FakeDynamo(table),
    )

    persistence.persist_customer_portfolio(
        "Acme Energy",
        {
            "customer_id": "acme-energy",
            "updated_at": "2026-05-17T00:00:00Z",
            "projects": [
                {
                    "project_id": "plant-a",
                    "name": "Plant A",
                    "status": "active",
                    "created_at": "2026-05-17T00:00:00Z",
                }
            ],
        },
    )

    assert "tenant-data/customers/acme-energy/portfolio/projects.json" in s3.objects
    assert table.items[0]["pk"] == "CUSTOMER#acme-energy"
    assert table.items[0]["sk"] == "PROJECT#plant-a"
    assert table.items[0]["item_type"] == "PROJECT"
