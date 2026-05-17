# FDRE Market Model

Standalone live-ready FDRE market operations advisory system.

This project is intentionally separate from the SECI BESS sizing repository. It reuses the SECI app patterns: managed inputs, immutable versions, decision snapshots, local workspace fallback, and downloadable artifacts.

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
fdre-market-web --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010`.

The app creates `.workspace/customers/<customer>/workspaces/<workspace>/` with versioned input data and decision cycles. Local defaults are `local-customer/default`.

## V1 Scope

- Advisory market recommendations only.
- Manual/CSV inputs only.
- Rolling board: recent actuals, current interval, and next 24 hours.
- Configurable interval, default hourly.
- Source-health checks for missing/stale actuals and incomplete forecast coverage.
- Upload validation for duplicate timestamps, missing/irregular intervals, supported 1m/15m/1h cadences, mixed generation units, and timezone-aware timestamps.
- Live-board polling with stale decision-cycle refresh on page load.
- Immutable model versions for assumptions and rules, with user/time/checksum audit metadata.
- Decision cycles reference input versions plus active assumption/rule versions.
- Ordered rule engine with admin-managed enable/disable, priority, JSON conditions, and JSON actions.
- Explicit skipped/conflict traces when lower-priority residual rules are blocked by higher-priority allocations.
- Workbook-aligned variable and case registry for translating Notes-sheet codes into app assumptions, inputs, outputs, and rule work.
- Disabled future rule-pack placeholders for annual CUF, monthly compliance, merchant buy, penalty procurement, and forecast lookahead behavior.
- Customer/workspace isolation through trusted identity headers, with the same scope used for hosted object/index keys.
- Portfolio-ready client/project model where `customer_id` is the client and `workspace_id` is the project.
- Customer portfolio board with project selection, roll-up health, shortfall, peak compliance, annual CUF, and acknowledgement status.
- Client-level user activation/deactivation shared across all projects for the client.
- Project-level feed catalog metadata for future live-data integrations.
- Live Board run presets for running, intraday, day-ahead, and custom windows.
- Soft data-quality gate recorded on each advisory decision cycle.
- Live operations UX with alert cards, filtered interval views, why-this-market drilldowns, and operator acknowledgement state.
- Outputs: allocation CSV, summary CSV, input-version audit JSON, model-version audit JSON, and XLSX workbook.

## Local Identity And Admin Access

By default the local app treats requests as `local.operator@example.com` with the `operator` role. To access admin pages such as Rules and Assumptions, run with:

```bash
FDRE_MODEL_USER_EMAIL=admin@example.com FDRE_MODEL_USER_ROLE=admin fdre-market-web --host 127.0.0.1 --port 8010
```

For hosted deployment, pass trusted headers from the authenticating proxy/app gateway:

- `X-User-Email`
- `X-User-Role`
- `X-Auth-Subject`
- `X-Customer-Id`
- `X-Workspace-Id`

Do not expose those headers without an upstream auth layer.

## Hosted Persistence

Local storage remains the working/staging store. To mirror immutable artifacts to S3 and index them in DynamoDB:

```bash
pip install -e .[aws]
FDRE_STORAGE_BACKEND=hosted \
FDRE_HOSTED_BUCKET=my-fdre-bucket \
FDRE_HOSTED_DYNAMODB_TABLE=fdre-index \
FDRE_HOSTED_PREFIX=fdre-market \
fdre-market-web --host 127.0.0.1 --port 8010
```

S3 keys and DynamoDB partition keys include `customer_id` and `workspace_id`.

## Elastic Beanstalk

The app ships with an Elastic Beanstalk WSGI entry point (`application.py`), a Gunicorn `Procfile`, and `requirements.txt` that installs the package with the AWS persistence extra.

Required EB environment variables:

- `FDRE_STORAGE_BACKEND=hosted`
- `FDRE_AUTH0_DOMAIN`
- `FDRE_AUTH0_CLIENT_ID`
- `FDRE_AUTH0_CLIENT_SECRET`
- `FDRE_AUTH0_MGMT_CLIENT_ID` for listing, verifying, and blocking/unblocking existing Auth0 identities
- `FDRE_AUTH0_MGMT_CLIENT_SECRET` for listing, verifying, and blocking/unblocking existing Auth0 identities
- `FDRE_AUTH0_CONNECTION_NAME`, defaults to `Username-Password-Authentication`
- `FDRE_MODEL_PUBLIC_BASE_URL`
- `FDRE_HOSTED_BUCKET`
- `FDRE_HOSTED_DYNAMODB_TABLE`
- `FDRE_HOSTED_DYNAMODB_KEY_MODE=pk_sk` for `pk`/`sk` tables, or `customer_workspace` for tables keyed by `customer_id`/`workspace_id`
- `FDRE_HOSTED_PREFIX`
- `AWS_REGION`
- `AWS_DEFAULT_REGION`
- `FDRE_MODEL_SECRET_KEY`
- `FDRE_WORKSPACE_ROOT=/var/app/fdre-workspace`

For direct EB access, the built-in auth is trusted-header/env based. Put an authenticated proxy in front of EB before customer-facing production use.

When Auth0 variables are set, the app uses hosted Auth0 login directly. Configure the Auth0 application with:

- Allowed Callback URL: `<FDRE_MODEL_PUBLIC_BASE_URL>/callback`
- Allowed Logout URL: `<FDRE_MODEL_PUBLIC_BASE_URL>/login`
- Allowed Web Origin: `<FDRE_MODEL_PUBLIC_BASE_URL>`

If Management API credentials are also set, FDRE admins can fetch existing Auth0 users into the Users page. New Auth0 users are auto-activated as FDRE operators by default for the client, while explicit FDRE deactivation remains a client-level deny across all projects. Auth0 user creation, password reset, and identity deletion stay in Auth0. Grant the machine-to-machine application only the required Auth0 Management API scopes: `read:users` and `update:users`.
