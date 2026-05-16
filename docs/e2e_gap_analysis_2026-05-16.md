# FDRE End-to-End Test And Workbook Gap Analysis

Date: 2026-05-16  
Workbook reviewed: `FDRE Model.xlsx`  
Sheets reviewed: `Notes`, `MODEL`, `MODEL v2`  
Code checked: `881fc8d Auto-activate Auth0 users`  
Deployed EB version checked: `fdre-auth0-auto-users-20260516061135`

## End-to-End Test Result

| Area | Result | Notes |
| --- | --- | --- |
| Automated regression suite | Pass | `53 passed in 19.38s`. |
| Hosted health | Pass | Elastic Beanstalk environment is `Ready / Green`; `/api/health` returned `{"status":"ok"}`. |
| Live Board UI | Pass | Rendered 31-row operating window with source health, KPIs, filters, why column, CSV/XLSX links. |
| Inputs UI | Pass | Inputs page rendered with source versions, edit active flow, manual upload areas, and download links. |
| Rules UI | Pass | Rule admin rendered enabled v1 rules and disabled future monitor placeholders. |
| Assumptions UI | Pass | Market config rendered interval, capacities, tariffs, losses, peak hours, and BESS starting state. |
| Users UI | Pass | Access-control page rendered; Auth0 directory panel is shown only when Management API is configured. |

## Workbook Requirement Summary

The `Notes` sheet defines the target model around these groups:

| Workbook area | Examples from Notes / MODEL v2 |
| --- | --- |
| Markets | `M1 PPA Sale`, `M2 Merchant Sale`, `M3 Peak Power Sale`, `M4 BESS`, `M5 GDAM`. |
| Generation | `G1 Wind`, `G2 Solar`, `G3 BESS Discharge`, `G4 Merchant Buy`. |
| Consumption | `C1 PPA`, `C2 Merchant`, `C3 Peak Power`, `C4 BESS Charge`, charge/discharge losses. |
| Tariffs | `T1 PPA`, `T2 Merchant Sell`, `T3 Peak Power`, `T4 Penalty`, `T5 Merchant Buy`, `T6 GDAM`, `T7 Others`. |
| Capacity | Wind, solar, BESS, PPA, merchant, live BESS SOC/SOH, evacuation, live peak power, peak power. |
| Forecast | Wind, solar, BESS degradation profile, T2 pricing, plus 22-hour, 30-day, and 365-day derived forecasts. |
| Compliance | `90% Peak Power`, cumulative CUF, annual CUF floor, merchant/procurement constraints. |
| Rule cases | Non-peak cases 2/3/4/5, peak cases 6/7, merchant-for-peak, residual BESS discharge, merchant buy/sell, procurement, merchant beyond capacity. |

## Current Coverage

Implemented well:

- Rolling operating window: recent actuals, live interval, next 24 forecast rows.
- Versioned inputs for solar, wind, BESS SOC/SOH state, T2 pricing, and peak schedule.
- Assumption UI for core capacities, tariffs, BESS losses, peak hours, and operating window.
- Rule priority ordering, enable/disable, conflict trace, and row-level why explanation.
- Workbook-aligned variable registry and decision-cycle forecast/compliance metrics for `P1-P5`, monthly peak compliance, annual generation, annual CUF, and 30-day peak forecast.
- Core v1 allocation: workbook non-peak dispatch, peak obligation using live peak target, residual PPA/merchant/BESS handling, curtailment, and shortfall.
- Decision-cycle artifacts and audit metadata.
- Auth0 login and default-active Auth0 user sync.

## Gaps Versus Workbook

### 1. Workbook Variable Dictionary Is Not First-Class

The workbook uses compact model codes: `M1-M5`, `G1-G4`, `C1-C6`, `T1-T7`, `Cap1-Cap12`, `P1-P5`, and cases. The app stores friendly config fields but does not maintain a workbook-aligned variable registry.

Impact: rules cannot yet be configured in the same language as the `Notes` sheet. This makes workbook parity hard to verify and harder for business users to audit.

Status update: implemented after this review. The app now includes a workbook variable and case registry surfaced on Assumptions and Rules. Follow-on items still need to implement the planned calculations and rule actions behind that registry.

### 2. Consumption / Commitment Model Is Too Thin

The app has capacity values for PPA, merchant, and peak power, but it does not model consumption/commitment targets as their own inputs:

- `C1 PPA`
- `C2 Merchant`
- `C3 Peak Power`
- `C4 BESS Charge`
- `C6 Charge Loss`
- `C6 Discharge Loss`

Impact: current decisions are capacity-capped allocation rows, not full workbook-style market obligation rows.

### 3. Forecast-Derived Parameters Are Missing

The workbook uses derived forecast parameters:

- `P1`: curtailed energy before peak hours, based on forecast generation over grid connectivity.
- `P2`: curtailed energy now.
- `P3`: expected deficit in 90% monthly peak compliance.
- `P4`: 5% of annual PPA generation.
- `P5`: live generation plus 365-day generation forecast.
- `Forecast30D(Cap9)` and `365D(Generation)`.

Status update: implemented after this review. Each decision cycle now stores workbook-derived `P1-P5`, 30-day peak forecast, and 365-day generation metrics, and the Live Board shows them in a Workbook Metrics panel.

Remaining gap: these metrics are visible/exported and now feed non-peak and peak cases. Procurement and penalty-minimization rules still need to consume them for workbook-parity decisions.

### 4. Compliance Calculations Are Placeholders

`MODEL v2` adds cumulative compliance columns:

- `90% Peak Power`
- `CUF of Plant`

Status update: implemented after this review. Monthly 90% peak compliance, monthly peak deficit, annual generation, and annual CUF are now calculated on every decision cycle.

Remaining gap: compliance values do not yet drive merchant sale beyond capacity, procurement, or penalty minimization decisions.

### 5. Non-Peak Rule Cases Are Implemented For V1 Dispatch

Status update: implemented after this review. The app now includes an enabled `non_peak_workbook_dispatch` rule that covers:

- Case 2: forecast curtailment can cover BESS headroom, so current residual is sold before charging.
- Case 3: forecast curtailment cannot cover BESS headroom, so BESS charging is prioritized.
- Cases 4/5: PPA versus merchant sale order is chosen from `T1` PPA tariff versus live `T2` merchant price.
- Case 7: when BESS has no material headroom, residual can flow to sale markets instead of charging.

Remaining gap: merchant sale beyond configured capacity is still deferred until compliance-driven merchant/procurement rules are implemented.

### 6. Peak Rule Cases Are Implemented For V1 Dispatch

Status update: implemented after this review. The app now uses:

- Optional `live_peak_power_mwh` on the Peak Schedule input for `Cap9`, falling back to configured `Cap10`.
- Case 6/7 handling: solar/wind meet peak first; if generation exceeds live peak power, residual flows to lower sale/storage rules; if generation is short, BESS discharges against the live peak gap.
- Cycle-level monthly 90% peak compliance state.
- Clause 1/iii merchant-for-peak support when monthly peak compliance has a gap and live `T2` is below `0.8 x T3`.

Remaining gap: separate merchant-buy/procurement market outputs and penalty-minimization rule packs are still deferred.

### 7. BESS Modeling Is Not Workbook-Complete

The app uses BESS SOC and charge/discharge losses, but the workbook also needs:

- BESS degradation profile as a forecast input.
- SOH/degradation affecting usable capacity or dispatch.
- C-rate based charge/discharge constraints.
- Residual BESS discharge if enough power is expected next day.
- Excess BESS merchant buy/sell arbitrage.

Impact: BESS behavior is advisory and simplified, especially outside peak shortfall handling.

### 8. Merchant Buy / Procurement Is Not Implemented

The workbook defines:

- Merchant buy (`G4`, `T5`).
- RE procurement for penalty mitigation.
- Procurement up to 5% of annual CUF.
- Buy only when tariff is below penalty or PPA thresholds.

The app currently has monitor placeholders only.

Impact: the system cannot recommend buying power to reduce penalty or meet CUF/compliance.

### 9. Market Set Is Incomplete

The workbook includes `GDAM` and `Others` tariff/market placeholders. The app currently supports PPA, merchant sale, peak power, BESS charge/discharge, curtailment, and shortfall.

Impact: market recommendations do not yet cover all workbook market categories.

### 10. Workbook Parity Tests Are Missing

There are strong engine tests, but no tests that assert selected `MODEL v2` row scenarios produce the same allocations as the app.

Impact: changes can be correct against code behavior while still drifting from the workbook logic.

## Recommended Work Order

1. Add a workbook-aligned variable registry and expose it in assumptions/rules.
2. Add workbook-complete BESS behavior: degradation, SOH capacity adjustment, C-rate, residual discharge, and arbitrage.
3. Implement merchant buy/procurement rules for penalty minimization and annual CUF.
4. Add workbook parity tests using representative rows from `MODEL v2`.
