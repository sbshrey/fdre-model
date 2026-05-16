"""Elastic Beanstalk WSGI entry point for the FDRE market app."""

from __future__ import annotations

import os

from fdre_model.web.app import create_app


application = create_app(
    workspace_root=os.environ.get("FDRE_WORKSPACE_ROOT") or None,
    source_config_path=os.environ.get("FDRE_SOURCE_CONFIG_PATH") or None,
)
app = application
