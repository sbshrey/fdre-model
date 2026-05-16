#!/usr/bin/env bash
set -euo pipefail

workspace_root="${FDRE_WORKSPACE_ROOT:-/var/app/fdre-workspace}"
mkdir -p "$workspace_root"
chown webapp:webapp "$workspace_root" || true
