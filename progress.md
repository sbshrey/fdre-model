# FDRE Market Operations Progress

This file tracks the known gaps between the current MVP and the expected live-ready FDRE operations system. Work proceeds top-down. When the user says `continue`, pick the next unchecked item.

## Backlog

- [x] 1. Add auth/RBAC boundary and protect admin surfaces.
  - Admin-only: rule order, rule enable/disable, assumptions/config changes.
  - Operator-accessible: live board, recalculation, input upload/version activation, decision history, artifacts.
  - Current implementation uses trusted headers/env vars; hosted Auth0 wiring remains a later item.
- [x] 2. Add source freshness, validation, and automatic decision-cycle refresh.
  - Detect stale/missing actuals and stale forecasts.
  - Add freshness indicators to the live board.
  - Add scheduled/polling recalculation for rolling windows.
- [x] 3. Make capacities, tariffs, commitments, peak schedule, and rules immutable/audited inputs.
  - Every assumption/rule change should create a versioned record with user/time/checksum.
  - Decision cycles should reference assumption and rule versions, not only generation/source-data versions.
- [x] 4. Expand the rule engine beyond hardcoded v1 actions.
  - Add configurable condition/action rules.
  - Add explicit conflict traces.
  - Add annual CUF, monthly compliance, merchant buy, penalty procurement, and forecast lookahead rules.
- [x] 5. Implement hosted persistence and multi-user isolation.
  - S3/DynamoDB-backed input versions and decision cycles.
  - Auth0 identity mapping.
  - Per-user/customer/workspace boundaries.
- [x] 6. Improve operations UX.
  - Auto-refresh state, source health, alerts, filters, “why this market” drilldown, operator acknowledgement.
- [x] 7. Harden ingestion validation.
  - Duplicate timestamps, missing intervals, irregular kW intervals, unit consistency, timezone handling, required rolling-window coverage.
- [x] 8. Add easy in-app active input updates.
  - Operators can edit a rolling time window or paste replacement CSV rows.
  - Saves create new active input versions with user/source metadata instead of mutating prior versions.
- [x] 9. Add preferred Live Board preview windows.
  - Default remains 6 actual + 1 live + 24 forecast = 31 rows.
  - Operators can load a custom start/live/end interval range without changing saved assumptions.
- [x] 10. Restrict portal user management to activation/deactivation.
  - Auth0 remains the source for creating identities, password reset, and identity deletion.
  - FDRE admins can only activate or deactivate workspace access from the portal.
- [x] 11. Auto-fetch Auth0 users and default them to active FDRE access.
  - Users added in Auth0 are synced as active FDRE operators by default.
  - Explicit FDRE deactivation remains a workspace-level deny until an admin reactivates access.

## Verification Log

- 2026-05-16: MVP created with live board, versioned local CSV inputs, ordered rule admin, decision history, assumptions page, exports, and tests.
- 2026-05-16: Item 1 completed with trusted-header/env RBAC for admin pages.
- 2026-05-16: Item 2 completed with source-health snapshots, stale cycle refresh on live-board load, 60-second live-board polling, and duplicate timestamp validation.
- 2026-05-16: Item 3 completed with immutable assumption/rule model versions, admin user attribution, decision-cycle model-version references, and model-version export artifacts.
- 2026-05-16: Item 4 completed with JSON condition/action rule definitions, action-type dispatch, explicit residual conflict traces, future rule-pack placeholders, and legacy rule migration.
- 2026-05-16: Item 5 completed with trusted-header customer/workspace scope, isolated local workspace roots, hosted S3/DynamoDB persistence adapter, and deployment env wiring.
- 2026-05-16: Item 6 completed with live operations alerts, row filters, visible auto-refresh state, why-this-market drilldowns, and operator acknowledgement tracking.
- 2026-05-16: Item 7 completed with stricter CSV upload checks for cadence, units, timezone-naive timestamps, active raw input revalidation, and critical source-health flags for invalid rolling-window inputs.
- 2026-05-16: Replaced synthetic bootstrap inputs with SECI-derived 2026 sample data for solar, wind, BESS state, T2 pricing, and peak schedule. Existing manual uploads are preserved; legacy seed inputs refresh automatically.
- 2026-05-16: Added in-app FDRE user administration. Admin emails can come from FDRE_ADMIN_EMAILS or FDRE_MODEL_ADMIN_EMAILS; admins can add/deactivate workspace users and set operator/admin roles.
- 2026-05-16: Added Auth0 Management API integration for admin-created users, invite/reset emails, Auth0 blocking on deactivation, and guarded Auth0 identity deletion.
- 2026-05-16: Refreshed the portal theme using Digitised Energy visual cues: light grey shell, green energy accent, compact rectangular controls, and responsive branded header.
- 2026-05-16: Added per-version input CSV downloads from the Inputs page for active and historical input versions.
- 2026-05-16: Added Syncfusion EJ2 Grid progressive enhancement for app tables using CDN assets and an optional environment-provided license key.
- 2026-05-16: Gated Syncfusion assets behind FDRE_SYNCFUSION_LICENSE_KEY so unlicensed hosted environments use native tables without the Syncfusion validation banner.
- 2026-05-16: Added in-app active input editing for source rows and pasted CSV replacements, preserving immutable version history and active-version audit metadata.
- 2026-05-16: Added Live Board preferred range controls with custom start/live/end preview cycles and default 31-row operating window display.
- 2026-05-16: Removed portal-side Auth0 creation, password reset, identity deletion, and role-edit UI; Users now only activates or deactivates workspace access.
- 2026-05-16: Added Auth0 user directory fetch, automatic FDRE activation for new Auth0 users, and first-login auto-provisioning while preserving explicit deactivation.
