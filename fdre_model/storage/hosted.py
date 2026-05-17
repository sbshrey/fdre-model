"""Hosted persistence adapter for S3 and DynamoDB.

The calculation engine still uses a local working directory as a staging area,
but immutable inputs, model versions, and decision cycles can be mirrored to
hosted object/index storage through this adapter.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fdre_model.market.models import DecisionCycle, InputVersion, ModelVersion
from fdre_model.storage.scope import WorkspaceScope


class HostedStorageNotConfigured(RuntimeError):
    """Raised when hosted storage is requested before credentials/tables exist."""


@dataclass(frozen=True)
class HostedStorageConfig:
    bucket: str
    dynamodb_table: str | None = None
    prefix: str = "fdre-market"
    region_name: str | None = None
    dynamodb_key_mode: str = "pk_sk"

    @classmethod
    def from_env(cls) -> "HostedStorageConfig":
        bucket = os.environ.get("FDRE_HOSTED_BUCKET")
        if not bucket:
            raise HostedStorageNotConfigured("FDRE_HOSTED_BUCKET is required when FDRE_STORAGE_BACKEND=hosted.")
        return cls(
            bucket=bucket,
            dynamodb_table=os.environ.get("FDRE_HOSTED_DYNAMODB_TABLE"),
            prefix=os.environ.get("FDRE_HOSTED_PREFIX", "fdre-market"),
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
            dynamodb_key_mode=os.environ.get("FDRE_HOSTED_DYNAMODB_KEY_MODE", "pk_sk"),
        )


class HostedPersistence:
    def __init__(
        self,
        config: HostedStorageConfig,
        *,
        s3_client: Any | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        self.config = config
        if s3_client is None or (dynamodb_resource is None and config.dynamodb_table):
            try:
                import boto3
            except ImportError as exc:
                raise HostedStorageNotConfigured("Install the aws extra or boto3 to use hosted persistence.") from exc
            s3_client = s3_client or boto3.client("s3", region_name=config.region_name)
            dynamodb_resource = dynamodb_resource or boto3.resource("dynamodb", region_name=config.region_name)
        self.s3 = s3_client
        self.table = dynamodb_resource.Table(config.dynamodb_table) if dynamodb_resource is not None and config.dynamodb_table else None

    @classmethod
    def from_env(cls) -> "HostedPersistence":
        return cls(HostedStorageConfig.from_env())

    def persist_input_version(self, scope: WorkspaceScope, version: InputVersion) -> None:
        prefix = f"{scope.object_prefix(self.config.prefix)}/inputs/{version.dataset_key}/{version.version_id}"
        self._put_json(f"{prefix}/metadata.json", version.to_json())
        if version.raw_path is not None:
            self._put_file(f"{prefix}/raw.csv", version.raw_path, content_type="text/csv")
        self._put_index(
            scope,
            item_type="INPUT_VERSION",
            item_id=f"{version.dataset_key}#{version.version_id}",
            created_at=version.created_at,
            s3_prefix=prefix,
            extra={"dataset_key": version.dataset_key, "source_type": version.source_type},
        )

    def persist_model_version(self, scope: WorkspaceScope, version: ModelVersion) -> None:
        prefix = f"{scope.object_prefix(self.config.prefix)}/model_versions/{version.version_type}/{version.version_id}"
        self._put_json(f"{prefix}/metadata.json", version.to_json())
        if version.payload_path is not None:
            self._put_file(f"{prefix}/{version.payload_path.name}", version.payload_path, content_type=_content_type(version.payload_path))
        self._put_index(
            scope,
            item_type="MODEL_VERSION",
            item_id=f"{version.version_type}#{version.version_id}",
            created_at=version.created_at,
            s3_prefix=prefix,
            extra={"version_type": version.version_type, "source_type": version.source_type},
        )

    def persist_decision_cycle(self, scope: WorkspaceScope, cycle: DecisionCycle) -> None:
        prefix = f"{scope.object_prefix(self.config.prefix)}/decision_cycles/{cycle.cycle_id}"
        cycle_json_path = _cycle_json_path(cycle)
        if cycle_json_path is not None:
            self._put_file(f"{prefix}/decision_cycle.json", cycle_json_path, content_type="application/json")
        for artifact_key, path in cycle.artifact_paths.items():
            self._put_file(f"{prefix}/{path.name}", path, content_type=_content_type(path))
        self._put_index(
            scope,
            item_type="DECISION_CYCLE",
            item_id=cycle.cycle_id,
            created_at=cycle.created_at,
            s3_prefix=prefix,
            extra={
                "window_start": cycle.window_start,
                "window_end": cycle.window_end,
                "artifact_keys": sorted(cycle.artifact_paths),
            },
        )

    def load_user_access(self, scope: WorkspaceScope) -> dict[str, Any] | None:
        key = f"{self.config.prefix.strip('/')}/customers/{scope.customer_id}/portfolio/users.json"
        try:
            response = self.s3.get_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return json.loads(response["Body"].read().decode("utf-8"))

    def persist_user_access(self, scope: WorkspaceScope, payload: dict[str, Any]) -> None:
        key = f"{self.config.prefix.strip('/')}/customers/{scope.customer_id}/portfolio/users.json"
        self._put_json(key, payload)
        self._put_customer_index(
            scope.customer_id,
            item_type="USER_ACCESS",
            item_id="users",
            created_at=str(payload.get("updated_at") or ""),
            s3_prefix=key.rsplit("/", 1)[0],
            extra={"user_count": len(payload.get("users") or [])},
        )

    def load_customer_portfolio(self, customer_id: str) -> dict[str, Any] | None:
        customer = WorkspaceScope.from_values(customer_id, "default").customer_id
        key = f"{self.config.prefix.strip('/')}/customers/{customer}/portfolio/projects.json"
        try:
            response = self.s3.get_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return json.loads(response["Body"].read().decode("utf-8"))

    def persist_customer_portfolio(self, customer_id: str, payload: dict[str, Any]) -> None:
        customer = WorkspaceScope.from_values(customer_id, "default").customer_id
        key = f"{self.config.prefix.strip('/')}/customers/{customer}/portfolio/projects.json"
        self._put_json(key, payload)
        for project in payload.get("projects") or []:
            project_id = str(project.get("project_id") or "").strip().lower()
            if not project_id:
                continue
            self._put_customer_index(
                customer,
                item_type="PROJECT",
                item_id=project_id,
                created_at=str(project.get("created_at") or payload.get("updated_at") or ""),
                s3_prefix=key.rsplit("/", 1)[0],
                extra={
                    "workspace_key": project_id,
                    "project_name": str(project.get("name") or ""),
                    "status": str(project.get("status") or ""),
                },
            )

    def persist_feed_catalog(self, scope: WorkspaceScope, payload: dict[str, Any]) -> None:
        prefix = f"{scope.object_prefix(self.config.prefix)}/config"
        key = f"{prefix}/feed_catalog.json"
        self._put_json(key, payload)
        self._put_index(
            scope,
            item_type="FEED_CATALOG",
            item_id="feeds",
            created_at=str(payload.get("updated_at") or ""),
            s3_prefix=prefix,
            extra={"feed_count": len(payload.get("feeds") or [])},
        )

    def _put_json(self, key: str, payload: dict[str, Any] | list[Any]) -> None:
        self.s3.put_object(
            Bucket=self.config.bucket,
            Key=key,
            Body=json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )

    def _put_file(self, key: str, path: Path, *, content_type: str) -> None:
        self.s3.put_object(
            Bucket=self.config.bucket,
            Key=key,
            Body=path.read_bytes(),
            ContentType=content_type,
        )

    def _put_index(
        self,
        scope: WorkspaceScope,
        *,
        item_type: str,
        item_id: str,
        created_at: str,
        s3_prefix: str,
        extra: dict[str, Any],
    ) -> None:
        if self.table is None:
            return
        pk = f"CUSTOMER#{scope.customer_id}#WORKSPACE#{scope.workspace_id}"
        sk = f"{item_type}#{item_id}"
        item = {
            "pk": pk,
            "sk": sk,
            "customer_id": scope.customer_id,
            "workspace_id": scope.workspace_id,
            "workspace_key": scope.workspace_id,
            "item_type": item_type,
            "item_id": item_id,
            "created_at": created_at,
            "s3_bucket": self.config.bucket,
            "s3_prefix": s3_prefix,
        }
        if self.config.dynamodb_key_mode == "customer_workspace":
            item["workspace_id"] = f"{scope.workspace_id}#{sk}"
        elif self.config.dynamodb_key_mode != "pk_sk":
            raise HostedStorageNotConfigured("FDRE_HOSTED_DYNAMODB_KEY_MODE must be pk_sk or customer_workspace.")
        item.update(extra)
        self.table.put_item(Item=item)

    def _put_customer_index(
        self,
        customer_id: str,
        *,
        item_type: str,
        item_id: str,
        created_at: str,
        s3_prefix: str,
        extra: dict[str, Any],
    ) -> None:
        if self.table is None:
            return
        customer = WorkspaceScope.from_values(customer_id, "default").customer_id
        item = {
            "pk": f"CUSTOMER#{customer}",
            "sk": f"{item_type}#{item_id}",
            "customer_id": customer,
            "workspace_id": "",
            "workspace_key": "",
            "item_type": item_type,
            "item_id": item_id,
            "created_at": created_at,
            "s3_bucket": self.config.bucket,
            "s3_prefix": s3_prefix,
        }
        if self.config.dynamodb_key_mode == "customer_workspace":
            item["workspace_id"] = f"portfolio#{item_type}#{item_id}"
        elif self.config.dynamodb_key_mode != "pk_sk":
            raise HostedStorageNotConfigured("FDRE_HOSTED_DYNAMODB_KEY_MODE must be pk_sk or customer_workspace.")
        item.update(extra)
        self.table.put_item(Item=item)


def _cycle_json_path(cycle: DecisionCycle) -> Path | None:
    if not cycle.artifact_paths:
        return None
    first_path = next(iter(cycle.artifact_paths.values()))
    path = first_path.parent / "decision_cycle.json"
    return path if path.exists() else None


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix in {".yaml", ".yml"}:
        return "application/x-yaml"
    return "application/octet-stream"
