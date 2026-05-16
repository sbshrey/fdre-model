"""Tenant and workspace scope helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_CUSTOMER_ID = "local-customer"
DEFAULT_WORKSPACE_ID = "default"


@dataclass(frozen=True)
class WorkspaceScope:
    customer_id: str = DEFAULT_CUSTOMER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID

    def to_json(self) -> dict[str, str]:
        return {
            "customer_id": self.customer_id,
            "workspace_id": self.workspace_id,
        }

    @classmethod
    def from_values(cls, customer_id: str | None, workspace_id: str | None) -> "WorkspaceScope":
        return cls(
            customer_id=sanitize_scope_component(customer_id or DEFAULT_CUSTOMER_ID),
            workspace_id=sanitize_scope_component(workspace_id or DEFAULT_WORKSPACE_ID),
        )

    def path_parts(self) -> tuple[str, str, str, str]:
        return ("customers", self.customer_id, "workspaces", self.workspace_id)

    def object_prefix(self, prefix: str = "") -> str:
        parts = [part.strip("/") for part in (prefix, *self.path_parts()) if part.strip("/")]
        return "/".join(parts)


def sanitize_scope_component(value: str) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    text = text.strip(".-")
    return text[:80] or "default"
