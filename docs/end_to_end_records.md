# FDRE End-to-End Record Walkthrough

This note explains the current implemented FDRE market operations system using a few concrete allocation records from the default sample data and rules.

The examples below were generated from a fresh local workspace using the committed 2026 sample inputs, default assumptions, and default rule order, with the live/current interval fixed at `2026-01-01 18:00:00`. Runtime-generated input version IDs are intentionally omitted here because they differ by workspace; in the app they are stored in each row audit trace and each `DecisionCycle`.

## What Has Been Implemented

The app is implemented as a live-ready advisory market operations system, not a one-time sizing run.

Implemented user surfaces:

- Live Market Board: recent actuals, current/live interval, and next 24 hours of forecast recommendations.
- Input Sources: upload/manual CSV input versions, activate older versions, audit metadata, and download active or historical input files.
- Rule Admin: admin users can enable/disable rules, change priority, and edit JSON conditions/actions.
- Assumptions: admin-managed capacities, tariffs, losses, peak hours, and operating-window settings.
- Decision History: each recalculation is stored as a `DecisionCycle` with artifacts and audit references.
- User Admin: admin-managed app users and Auth0 Management API integration for create/invite/reset/block/delete identity actions.
- Hosted deployment: Elastic Beanstalk app with S3/DynamoDB persistence support and workspace isolation.

Implemented outputs:

- Live allocation table in the UI.
- `market_allocation.csv`.
- `market_summary.csv`.
- `market_input_versions.json`.
- `market_model_versions.json`.
- `fdre_market_model.xlsx`.
- Decision-cycle JSON with input versions, model versions, rule order, source health, and summary.

## Input Sources

The current V1 input pipeline uses versioned CSV inputs. Every upload/manual edit creates an immutable `InputVersion`; the active version is selected per dataset.

| Dataset | Required headers | Meaning |
| --- | --- | --- |
| `solar` | `timestamp,mwh` | Solar generation available in the interval. |
| `wind` | `timestamp,mwh` | Wind generation available in the interval. |
| `bess_state` | `timestamp,soc_mwh,soh_fraction` | Battery state of charge and health. |
| `t2_pricing` | `timestamp,price` | Merchant/T2 sale price for the interval. |
| `peak_schedule` | `timestamp,is_peak` | Peak/non-peak flag by interval. |

The default sample inputs have 8,760 hourly rows each, covering `2026-01-01 00:00:00` through `2026-12-31 23:00:00`.

Source validation currently checks:

- Required headers by dataset type.
- Duplicate timestamps.
- Supported source cadence: 1 minute, 15 minutes, or 1 hour.
- Generation unit consistency: MWh or kW power readings.
- Timezone-naive timestamps.
- Rolling-window coverage and source freshness.

## Operating Window

The default operating window is:

- Interval: `1h`.
- Recent actuals: `6` hours before current/live.
- Current/live interval: the floored current hour.
- Forecast: next `24` hours.

For the example cycle with current/live time `2026-01-01 18:00:00`, the engine creates 31 rows:

- Window start: `2026-01-01 12:00:00`.
- Window end: `2026-01-02 19:00:00`.
- Rows: `6 actual + 1 live + 24 forecast = 31`.

Bucket status is derived from the current/live hour:

- `actual`: interval start is before the current/live hour.
- `live`: interval start equals the current/live hour.
- `forecast`: interval start is after the current/live hour.

## Assumption Variables

Default assumptions in `config/project.yaml`:

| Variable | Value | Used for |
| --- | ---: | --- |
| `wind_mwh` | `211.2` | Per-interval wind cap. |
| `solar_mwh` | `162.0` | Per-interval solar cap. |
| `evacuation_mwh` | `185.0` | Max total exportable generation per interval. |
| `ppa_mwh` | `150.0` | Max PPA sale per interval. |
| `merchant_mwh` | `35.0` | Max merchant sale per interval. |
| `peak_power_mwh` | `150.0` | Peak obligation target per peak interval. |
| `bess_capacity_mwh` | `100.0` | Battery energy capacity. |
| `bess_charge_limit_mwh` | `50.0` | Max raw charge per interval. |
| `bess_discharge_limit_mwh` | `50.0` | Max delivered discharge per interval. |
| `charge_loss_fraction` | `0.13` | Charging loss. |
| `discharge_loss_fraction` | `0.07` | Discharging loss. |
| `ppa` | `6.0` | PPA tariff. |
| `peak_power` | `7.0` | Peak power tariff. |
| `merchant_sell_default` | `10.0` | Fallback merchant price. |
| `penalty_multiplier` | `1.5` | Shortfall penalty multiplier against PPA tariff. |
| `default_peak_hours` | `18,19,20,21` | Default peak hours if peak CSV is missing. |

## Main Derived Variables

Each `MarketDecision` row contains these important variables:

| Variable | Meaning |
| --- | --- |
| `wind_mwh`, `solar_mwh` | Aggregated and capped generation for the interval. |
| `available_mwh` | `min(wind_mwh + solar_mwh, evacuation_mwh)`. |
| `merchant_price` | Average price in the interval, or fallback tariff if missing. |
| `bess_open_mwh` | Battery SOC at the start of rule evaluation. |
| `bess_close_mwh` | Battery SOC after rule evaluation. |
| `residual_mwh` | Energy not yet allocated by higher-priority rules. |
| `ppa_sale_mwh` | Energy allocated to PPA. |
| `merchant_sale_mwh` | Energy allocated to merchant/T2 market. |
| `peak_power_sale_mwh` | Energy allocated to peak obligation. |
| `bess_charge_mwh` | Raw MWh sent into BESS charging. |
| `bess_discharge_mwh` | Delivered MWh from BESS discharge. |
| `curtailment_mwh` | Energy not sellable/storable and therefore curtailed. |
| `shortfall_mwh` | Unserved peak obligation after generation and BESS. |
| `penalty_value` | `shortfall_mwh * ppa_tariff * penalty_multiplier`. |
| `revenue_value` | Sum of tariff/price value for sold energy in that row. |
| `recommended_market` | Largest allocation bucket in the row. |
| `applied_rule_ids` | Rules that allocated energy or created shortfall. |
| `skipped_rule_ids` | Disabled, false-condition, or conflict rules. |
| `audit_trace` | Human-readable trace of inputs and rule effects. |

## Rule Order

Rules are evaluated top-down by priority. Higher-priority rules allocate first; lower-priority rules only see remaining `residual_mwh` and remaining BESS capability.

| Priority | Rule ID | Enabled | Condition | Action | Behavior |
| ---: | --- | --- | --- | --- | --- |
| `10` | `peak_power_obligation` | Yes | `is_peak = true` | `allocate_peak_power` | During peak, allocate generation to peak obligation first, then use BESS, then record shortfall and penalty. |
| `20` | `ppa_sale` | Yes | `min_residual_mwh = 0.000001` | `sell_ppa` | Sell residual energy to PPA up to `ppa_mwh`. |
| `30` | `merchant_sale` | Yes | `min_residual_mwh = 0.000001` | `sell_merchant` | Sell remaining residual to merchant up to `merchant_mwh`. |
| `35` | `forecast_peak_charge` | No | forecast non-peak | `monitor` | Placeholder for future lookahead charging logic. |
| `40` | `bess_charge` | Yes | `min_residual_mwh = 0.000001` | `charge_bess` | Store remaining residual in BESS subject to charge cap and headroom. |
| `90` | `curtail_residual` | Yes | `min_residual_mwh = 0.000001` | `curtail` | Curtail residual that could not be sold or stored. |
| `110` | `annual_cuf_monitor` | No | any row | `monitor` | Future placeholder. |
| `120` | `monthly_compliance_monitor` | No | any row | `monitor` | Future placeholder. |
| `130` | `merchant_buy_shortfall` | No | shortfall exists | `monitor` | Future placeholder. |
| `140` | `penalty_procurement_monitor` | No | shortfall exists | `monitor` | Future placeholder. |

Important V1 behavior:

- Peak rows prioritize peak obligation before PPA or merchant.
- Non-peak rows skip peak obligation and then allocate to PPA before merchant.
- If a higher-priority rule consumes all residual energy, lower residual-allocation rules are marked as conflicts, for example `merchant_sale:conflict:residual_allocated_by=ppa_sale`.
- Disabled future rules are still present in the trace as `disabled` so the operator can see that they were not considered.
- Under the current default caps, `ppa_mwh + merchant_mwh = evacuation_mwh` (`150 + 35 = 185`). Because of that, BESS charge and curtailment usually do not trigger unless rule order, capacities, or enabled rules are changed.

## Allocation Formulas

Generation:

```text
wind_mwh = min(max(aggregated_wind, 0), wind_mwh_cap)
solar_mwh = min(max(aggregated_solar, 0), solar_mwh_cap)
available_mwh = min(wind_mwh + solar_mwh, evacuation_mwh)
residual_mwh starts as available_mwh
```

Peak obligation:

```text
target = peak_power_mwh
from_generation = min(residual_mwh, target)
residual_mwh -= from_generation
remaining = target - from_generation

delivered_from_bess = min(
    remaining,
    bess_discharge_limit_mwh,
    bess_open_mwh * (1 - discharge_loss_fraction)
)

soc_draw = delivered_from_bess / (1 - discharge_loss_fraction)
bess_close_mwh = bess_open_mwh - soc_draw
shortfall_mwh = remaining - delivered_from_bess
penalty_value = shortfall_mwh * ppa_tariff * penalty_multiplier
peak_revenue = peak_power_sale_mwh * peak_power_tariff
```

PPA sale:

```text
ppa_sale_mwh = min(residual_mwh, ppa_mwh)
residual_mwh -= ppa_sale_mwh
ppa_revenue = ppa_sale_mwh * ppa_tariff
```

Merchant sale:

```text
merchant_sale_mwh = min(residual_mwh, merchant_mwh)
residual_mwh -= merchant_sale_mwh
merchant_revenue = merchant_sale_mwh * merchant_price
```

BESS charge:

```text
headroom = bess_capacity_mwh - bess_open_mwh
raw_charge_limit_by_headroom = headroom / (1 - charge_loss_fraction)
raw_charge = min(residual_mwh, bess_charge_limit_mwh, raw_charge_limit_by_headroom)
net_soc_add = raw_charge * (1 - charge_loss_fraction)
```

## Example Cycle Summary

The example cycle uses current/live time `2026-01-01 18:00:00`.

| Metric | Value |
| --- | ---: |
| Rows | `31` |
| Window start | `2026-01-01 12:00:00` |
| Window end | `2026-01-02 19:00:00` |
| PPA sale | `1796.728499 MWh` |
| Merchant sale | `47.393113 MWh` |
| Peak power sale | `188.256631 MWh` |
| BESS charge | `0.000000 MWh` |
| BESS discharge | `32.682857 MWh` |
| Curtailment | `0.000000 MWh` |
| Shortfall | `561.743369 MWh` |
| Penalty value | `5055.690321` |
| Revenue value | `12407.644231` |

## Record 1: Actual Non-Peak, PPA Plus Merchant

Record:

```text
interval_start: 2026-01-01 13:00:00
interval_end:   2026-01-01 14:00:00
status:         actual
is_peak:        0
recommended:    PPA
```

Inputs:

| Variable | Value |
| --- | ---: |
| `wind_mwh` | `43.848728` |
| `solar_mwh` | `111.473010` |
| `merchant_price` | `6.499` |
| `bess_open_mwh` | `75.000000` |

Derived:

```text
available_mwh = min(43.848728 + 111.473010, 185.000000)
available_mwh = 155.321738
```

Rule trace:

| Step | Result |
| --- | --- |
| `peak_power_obligation` | Skipped because `is_peak = false`. |
| `ppa_sale` | Allocates `150.000000 MWh`; residual becomes `5.321738 MWh`. |
| `merchant_sale` | Allocates `5.321738 MWh`; residual becomes `0`. |
| `bess_charge` | Conflict/skipped because residual was already allocated by PPA and merchant. |
| `curtail_residual` | Conflict/skipped because residual is `0`. |

Outputs:

| Output | Value |
| --- | ---: |
| `ppa_sale_mwh` | `150.000000` |
| `merchant_sale_mwh` | `5.321738` |
| `peak_power_sale_mwh` | `0.000000` |
| `bess_close_mwh` | `75.000000` |
| `shortfall_mwh` | `0.000000` |
| `residual_mwh` | `0.000000` |
| `revenue_value` | `934.585975` |

Revenue calculation:

```text
PPA revenue      = 150.000000 * 6.000 = 900.000000
Merchant revenue =   5.321738 * 6.499 =  34.585975
Total revenue    = 934.585975
```

Audit fields:

```text
applied_rule_ids:
ppa_sale,merchant_sale

skipped_rule_ids:
peak_power_obligation:condition_false
forecast_peak_charge:disabled
bess_charge:conflict:residual_allocated_by=ppa_sale,merchant_sale
curtail_residual:conflict:residual_allocated_by=ppa_sale,merchant_sale
annual_cuf_monitor:disabled
monthly_compliance_monitor:disabled
merchant_buy_shortfall:disabled
penalty_procurement_monitor:disabled
```

## Record 2: Actual Non-Peak, PPA Only

Record:

```text
interval_start: 2026-01-01 17:00:00
interval_end:   2026-01-01 18:00:00
status:         actual
is_peak:        0
recommended:    PPA
```

Inputs:

| Variable | Value |
| --- | ---: |
| `wind_mwh` | `27.674912` |
| `solar_mwh` | `8.653500` |
| `merchant_price` | `8.000` |
| `bess_open_mwh` | `60.714286` |

Derived:

```text
available_mwh = min(27.674912 + 8.653500, 185.000000)
available_mwh = 36.328412
```

Rule trace:

| Step | Result |
| --- | --- |
| `peak_power_obligation` | Skipped because `is_peak = false`. |
| `ppa_sale` | Allocates all `36.328412 MWh`; residual becomes `0`. |
| `merchant_sale` | Conflict/skipped because PPA consumed all residual. |
| `bess_charge` | Conflict/skipped because PPA consumed all residual. |
| `curtail_residual` | Conflict/skipped because PPA consumed all residual. |

Outputs:

| Output | Value |
| --- | ---: |
| `ppa_sale_mwh` | `36.328412` |
| `merchant_sale_mwh` | `0.000000` |
| `bess_close_mwh` | `60.714286` |
| `shortfall_mwh` | `0.000000` |
| `revenue_value` | `217.970472` |

Revenue calculation:

```text
PPA revenue = 36.328412 * 6.000 = 217.970472
```

## Record 3: Live Peak, Generation Plus BESS Still Leaves Shortfall

Record:

```text
interval_start: 2026-01-01 18:00:00
interval_end:   2026-01-01 19:00:00
status:         live
is_peak:        1
recommended:    Peak Power
```

Inputs:

| Variable | Value |
| --- | ---: |
| `wind_mwh` | `36.479288` |
| `solar_mwh` | `0.000000` |
| `merchant_price` | `11.250` |
| `bess_open_mwh` | `35.142857` |

Derived:

```text
available_mwh = min(36.479288 + 0.000000, 185.000000)
available_mwh = 36.479288

peak target = 150.000000
from generation = 36.479288
remaining peak need = 113.520712
```

BESS discharge:

```text
loss factor = 1 - 0.07 = 0.93
deliverable from SOC = 35.142857 * 0.93 = 32.682857
delivered_from_bess = min(113.520712, 50.000000, 32.682857)
delivered_from_bess = 32.682857
soc_draw = 32.682857 / 0.93 = 35.142857
bess_close_mwh = 0.000000
```

Rule trace:

| Step | Result |
| --- | --- |
| `peak_power_obligation` | Allocates `36.479288 MWh` from generation plus `32.682857 MWh` from BESS. |
| `ppa_sale` | Conflict/skipped because peak rule consumed residual first. |
| `merchant_sale` | Conflict/skipped because peak rule consumed residual first. |
| `bess_charge` | Conflict/skipped because peak rule consumed residual first. |
| `curtail_residual` | Conflict/skipped because residual is `0`. |

Outputs:

| Output | Value |
| --- | ---: |
| `peak_power_sale_mwh` | `69.162145` |
| `bess_discharge_mwh` | `32.682857` |
| `bess_close_mwh` | `0.000000` |
| `shortfall_mwh` | `80.837855` |
| `penalty_value` | `727.540695` |
| `revenue_value` | `484.135015` |

Shortfall and value:

```text
shortfall_mwh = 150.000000 - 69.162145 = 80.837855
penalty_value = 80.837855 * 6.000 * 1.5 = 727.540695
revenue_value = 69.162145 * 7.000 = 484.135015
```

## Record 4: Forecast Peak, Battery Already Depleted By Live Interval

Record:

```text
interval_start: 2026-01-01 19:00:00
interval_end:   2026-01-01 20:00:00
status:         forecast
is_peak:        1
recommended:    Peak Power
```

Inputs:

| Variable | Value |
| --- | ---: |
| `wind_mwh` | `36.969377` |
| `solar_mwh` | `0.000000` |
| `merchant_price` | `11.155` |
| `bess_open_mwh` | `0.000000` |

Why `bess_open_mwh` is `0`:

- For actual and live rows, the engine resets BESS SOC from the active `bess_state` source.
- For forecast rows, the engine rolls the previous row's calculated BESS state forward.
- The live `18:00` peak row fully discharged BESS, so the `19:00` forecast starts at `0`.

Rule trace:

| Step | Result |
| --- | --- |
| `peak_power_obligation` | Allocates only `36.969377 MWh` from generation. |
| BESS discharge | `0.000000 MWh` because forecast rolling SOC is `0`. |
| Shortfall | `113.030623 MWh`. |
| Lower rules | Conflict/skipped because peak rule has priority. |

Outputs:

| Output | Value |
| --- | ---: |
| `peak_power_sale_mwh` | `36.969377` |
| `bess_discharge_mwh` | `0.000000` |
| `shortfall_mwh` | `113.030623` |
| `penalty_value` | `1017.275607` |
| `revenue_value` | `258.785639` |

Shortfall and value:

```text
shortfall_mwh = 150.000000 - 36.969377 = 113.030623
penalty_value = 113.030623 * 6.000 * 1.5 = 1017.275607
revenue_value = 36.969377 * 7.000 = 258.785639
```

This row demonstrates the live-ready rolling-state design: the forecast recommendation depends on the latest/current state, not only on static source rows.

## Record 5: Forecast Non-Peak, PPA Plus Merchant

Record:

```text
interval_start: 2026-01-02 11:00:00
interval_end:   2026-01-02 12:00:00
status:         forecast
is_peak:        0
recommended:    PPA
```

Inputs:

| Variable | Value |
| --- | ---: |
| `wind_mwh` | `22.173529` |
| `solar_mwh` | `143.793630` |
| `merchant_price` | `6.591` |
| `bess_open_mwh` | `0.000000` |

Derived:

```text
available_mwh = min(22.173529 + 143.793630, 185.000000)
available_mwh = 165.967159
```

Rule trace:

| Step | Result |
| --- | --- |
| `peak_power_obligation` | Skipped because `is_peak = false`. |
| `ppa_sale` | Allocates `150.000000 MWh`; residual becomes `15.967159 MWh`. |
| `merchant_sale` | Allocates `15.967159 MWh`; residual becomes `0`. |
| `bess_charge` | Conflict/skipped because residual has already been allocated. |
| `curtail_residual` | Conflict/skipped because residual has already been allocated. |

Outputs:

| Output | Value |
| --- | ---: |
| `ppa_sale_mwh` | `150.000000` |
| `merchant_sale_mwh` | `15.967159` |
| `shortfall_mwh` | `0.000000` |
| `revenue_value` | `1005.239545` |

Revenue calculation:

```text
PPA revenue      = 150.000000 * 6.000 = 900.000000
Merchant revenue =  15.967159 * 6.591 = 105.239545
Total revenue    = 1005.239545
```

## Audit and Versioning

Every allocation row carries audit information:

- `input_versions`: active version ID for each input dataset.
- `model_versions`: active assumptions version and rules version.
- `applied_rule_ids`: rules that changed the allocation.
- `skipped_rule_ids`: disabled rules, false conditions, and conflict traces.
- `audit_trace`: readable sequence of rule outcomes.
- `calculation timestamp`: stored at `DecisionCycle.created_at`.

Every decision cycle stores:

- `cycle_id`.
- `created_at`.
- `window_start` and `window_end`.
- `workspace_scope`.
- `input_versions`.
- `model_versions`.
- `rule_order`.
- `source_health`.
- Export artifact paths.
- Summary totals.

## Current Implementation Limits To Remember

V1 is advisory only. It recommends market allocation but does not execute dispatch instructions.

Live/API ingestion is architected through versioned input adapters, but V1 currently uses manual/CSV uploads and sample seed files.

Annual CUF, monthly compliance, merchant buy, and penalty-procurement rules exist as disabled monitor placeholders. They are available in Rule Admin but do not allocate energy until implemented/enabled with concrete actions.

BESS charge and curtailment actions are implemented, but the default rule order and default capacities make them uncommon in the current sample because PPA and merchant capacity together cover the full evacuation limit.

Forecast rows roll BESS state forward from the live/current interval, which is useful for live operations but means forecast recommendations depend strongly on the latest actual/live SOC and the prior forecast decisions in the same cycle.
