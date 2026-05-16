"""Ordered advisory rules for FDRE market allocation."""

from __future__ import annotations

from fdre_model.market.models import MarketDecision, OperatingState, RuleDefinition


DEFAULT_RULES: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        rule_id="peak_power_obligation",
        name="Peak Power Obligation",
        priority=10,
        enabled=True,
        description="During peak buckets, meet peak commitment first using RE and then BESS discharge.",
        condition={"is_peak": True},
        action={"type": "allocate_peak_power"},
    ),
    RuleDefinition(
        rule_id="non_peak_workbook_dispatch",
        name="Non-Peak Workbook Dispatch",
        priority=15,
        enabled=True,
        description="Apply workbook non-peak cases 2/3/4/5/7 using forecast curtailment, BESS headroom, and T1-vs-T2 tariff order.",
        condition={"is_peak": False, "min_residual_mwh": 0.000001, "no_prior_allocation": True},
        action={"type": "allocate_non_peak_workbook"},
    ),
    RuleDefinition(
        rule_id="ppa_sale",
        name="PPA Sale",
        priority=20,
        enabled=True,
        description="Allocate remaining generation to PPA up to configured PPA capacity.",
        condition={"min_residual_mwh": 0.000001},
        action={"type": "sell_ppa"},
    ),
    RuleDefinition(
        rule_id="merchant_sale",
        name="Merchant Sale",
        priority=30,
        enabled=True,
        description="Allocate remaining generation to merchant market up to merchant capacity.",
        condition={"min_residual_mwh": 0.000001},
        action={"type": "sell_merchant"},
    ),
    RuleDefinition(
        rule_id="bess_charge",
        name="BESS Charge",
        priority=40,
        enabled=True,
        description="Store remaining generation in BESS subject to charge cap and headroom.",
        condition={"min_residual_mwh": 0.000001},
        action={"type": "charge_bess"},
    ),
    RuleDefinition(
        rule_id="curtail_residual",
        name="Curtail Residual",
        priority=90,
        enabled=True,
        description="Curtail any energy that cannot be sold or stored.",
        condition={"min_residual_mwh": 0.000001},
        action={"type": "curtail"},
    ),
    RuleDefinition(
        rule_id="forecast_peak_charge",
        name="Forecast Peak Charge",
        priority=35,
        enabled=False,
        description="Future rule pack placeholder: inspect upcoming peak requirements before allocating residual energy.",
        condition={"status_in": ["forecast"], "is_peak": False},
        action={"type": "monitor", "metric": "forecast_lookahead_peak_charge"},
        rule_pack="future_forecast",
    ),
    RuleDefinition(
        rule_id="annual_cuf_monitor",
        name="Annual CUF Monitor",
        priority=110,
        enabled=False,
        description="Future rule pack placeholder: track annual CUF compliance before recommending merchant-heavy outcomes.",
        condition={"status_in": ["actual", "live", "forecast"]},
        action={"type": "monitor", "metric": "annual_cuf"},
        rule_pack="future_compliance",
    ),
    RuleDefinition(
        rule_id="monthly_compliance_monitor",
        name="Monthly Compliance Monitor",
        priority=120,
        enabled=False,
        description="Future rule pack placeholder: track monthly obligation progress and remaining compliance gap.",
        condition={"status_in": ["actual", "live", "forecast"]},
        action={"type": "monitor", "metric": "monthly_compliance"},
        rule_pack="future_compliance",
    ),
    RuleDefinition(
        rule_id="merchant_buy_shortfall",
        name="Merchant Buy Shortfall",
        priority=130,
        enabled=False,
        description="Future rule pack placeholder: evaluate merchant procurement when shortfall remains after priority dispatch.",
        condition={"min_shortfall_mwh": 0.000001},
        action={"type": "monitor", "metric": "merchant_buy_shortfall"},
        rule_pack="future_procurement",
    ),
    RuleDefinition(
        rule_id="penalty_procurement_monitor",
        name="Penalty Procurement Monitor",
        priority=140,
        enabled=False,
        description="Future rule pack placeholder: compare penalty exposure against procurement alternatives.",
        condition={"min_shortfall_mwh": 0.000001},
        action={"type": "monitor", "metric": "penalty_procurement"},
        rule_pack="future_procurement",
    ),
)


class RuleContext:
    def __init__(
        self,
        *,
        ppa_cap_mwh: float,
        merchant_cap_mwh: float,
        peak_cap_mwh: float,
        bess_capacity_mwh: float,
        bess_charge_limit_mwh: float,
        bess_discharge_limit_mwh: float,
        charge_loss_fraction: float,
        discharge_loss_fraction: float,
        ppa_tariff: float,
        peak_tariff: float,
        penalty_multiplier: float,
        forecast_curtailment_mwh_by_start: dict[object, float] | None = None,
    ) -> None:
        self.ppa_cap_mwh = float(ppa_cap_mwh)
        self.merchant_cap_mwh = float(merchant_cap_mwh)
        self.peak_cap_mwh = float(peak_cap_mwh)
        self.bess_capacity_mwh = float(bess_capacity_mwh)
        self.bess_charge_limit_mwh = float(bess_charge_limit_mwh)
        self.bess_discharge_limit_mwh = float(bess_discharge_limit_mwh)
        self.charge_loss_fraction = float(charge_loss_fraction)
        self.discharge_loss_fraction = float(discharge_loss_fraction)
        self.ppa_tariff = float(ppa_tariff)
        self.peak_tariff = float(peak_tariff)
        self.penalty_multiplier = float(penalty_multiplier)
        self.forecast_curtailment_mwh_by_start = forecast_curtailment_mwh_by_start or {}


def evaluate_rules(
    decision: MarketDecision,
    state: OperatingState,
    rules: list[RuleDefinition],
    context: RuleContext,
) -> MarketDecision:
    decision.residual_mwh = max(decision.available_mwh, 0.0)
    ordered = sorted(rules, key=lambda item: (item.priority, item.rule_id))
    residual_allocators: list[str] = []
    for rule in ordered:
        condition = _rule_condition(rule)
        if not rule.enabled:
            decision.skipped_rule_ids.append(f"{rule.rule_id}:disabled")
            continue
        action_type = _action_type(rule)
        if _requires_residual(action_type) and decision.residual_mwh <= 0.0 and _condition_matches(
            decision,
            state,
            context,
            _without_residual_conditions(condition),
        ):
            blockers = ",".join(residual_allocators) if residual_allocators else "none"
            decision.skipped_rule_ids.append(f"{rule.rule_id}:conflict:residual_allocated_by={blockers}")
            decision.audit_trace.append(f"{rule.rule_id}: conflict residual already allocated by {blockers}")
            continue
        if not _condition_matches(decision, state, context, condition):
            decision.skipped_rule_ids.append(f"{rule.rule_id}:condition_false")
            decision.audit_trace.append(f"{rule.rule_id}: condition_false")
            continue
        before = decision.residual_mwh

        if action_type == "allocate_peak_power":
            _apply_peak_power(decision, state, context, rule.rule_id)
        elif action_type == "allocate_non_peak_workbook":
            _apply_non_peak_workbook_dispatch(decision, state, context, rule.rule_id)
        elif action_type == "sell_ppa":
            _apply_ppa_sale(decision, context, rule.rule_id)
        elif action_type == "sell_merchant":
            _apply_merchant_sale(decision, context, rule.rule_id)
        elif action_type == "charge_bess":
            _apply_bess_charge(decision, state, context, rule.rule_id)
        elif action_type == "curtail":
            _apply_curtailment(decision, rule.rule_id)
        elif action_type == "monitor":
            metric = str(rule.action.get("metric") or rule.rule_id)
            decision.audit_trace.append(f"{rule.rule_id}: monitor={metric}, no allocation")
            decision.skipped_rule_ids.append(f"{rule.rule_id}:monitor_only")
            continue
        else:
            decision.skipped_rule_ids.append(f"{rule.rule_id}:unknown_action:{action_type}")
            decision.audit_trace.append(f"{rule.rule_id}: unknown_action={action_type}")
            continue
        if decision.residual_mwh < before:
            residual_allocators.append(rule.rule_id)
        if decision.residual_mwh == before and rule.rule_id not in decision.applied_rule_ids:
            decision.skipped_rule_ids.append(f"{rule.rule_id}:not_applicable")

    decision.bess_close_mwh = state.bess_soc_mwh
    decision.recommended_market = _recommended_market(decision)
    return decision


def _action_type(rule: RuleDefinition) -> str:
    action_type = str(rule.action.get("type") or "")
    if action_type:
        return action_type
    return {
        "peak_power_obligation": "allocate_peak_power",
        "non_peak_workbook_dispatch": "allocate_non_peak_workbook",
        "ppa_sale": "sell_ppa",
        "merchant_sale": "sell_merchant",
        "bess_charge": "charge_bess",
        "curtail_residual": "curtail",
    }.get(rule.rule_id, "unknown")


def _rule_condition(rule: RuleDefinition) -> dict[str, object]:
    if rule.condition:
        return rule.condition
    defaults = {item.rule_id: item for item in DEFAULT_RULES}
    default = defaults.get(rule.rule_id)
    return default.condition if default is not None else {}


def _requires_residual(action_type: str) -> bool:
    return action_type in {"allocate_non_peak_workbook", "sell_ppa", "sell_merchant", "charge_bess", "curtail"}


def _without_residual_conditions(condition: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in condition.items()
        if key not in {"min_residual_mwh", "max_residual_mwh"}
    }


def _condition_matches(
    decision: MarketDecision,
    state: OperatingState,
    context: RuleContext,
    condition: dict[str, object],
) -> bool:
    if not condition:
        return True
    for key, expected in condition.items():
        if key == "is_peak":
            matched = decision.is_peak is _bool_value(expected)
        elif key == "status_in":
            matched = decision.status in {str(item) for item in _as_list(expected)}
        elif key == "hour_in":
            matched = decision.interval_start.hour in {int(item) for item in _as_list(expected)}
        elif key == "min_residual_mwh":
            matched = decision.residual_mwh >= _float_value(expected, context)
        elif key == "max_residual_mwh":
            matched = decision.residual_mwh <= _float_value(expected, context)
        elif key == "min_merchant_price":
            matched = decision.merchant_price >= _float_value(expected, context)
        elif key == "max_merchant_price":
            matched = decision.merchant_price <= _float_value(expected, context)
        elif key == "min_shortfall_mwh":
            matched = decision.shortfall_mwh >= _float_value(expected, context)
        elif key == "soc_below_mwh":
            matched = state.bess_soc_mwh < _float_value(expected, context)
        elif key == "soc_above_mwh":
            matched = state.bess_soc_mwh > _float_value(expected, context)
        elif key == "soc_below_fraction":
            matched = state.bess_soc_mwh < context.bess_capacity_mwh * _float_value(expected, context)
        elif key == "soc_above_fraction":
            matched = state.bess_soc_mwh > context.bess_capacity_mwh * _float_value(expected, context)
        elif key == "no_prior_allocation":
            prior_mwh = (
                decision.peak_power_sale_mwh
                + decision.ppa_sale_mwh
                + decision.merchant_sale_mwh
                + decision.bess_charge_mwh
                + decision.bess_discharge_mwh
                + decision.curtailment_mwh
            )
            matched = (prior_mwh <= 0.000001) is _bool_value(expected)
        else:
            matched = False
        if not matched:
            return False
    return True


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _float_value(value: object, context: RuleContext) -> float:
    if isinstance(value, str) and hasattr(context, value):
        return float(getattr(context, value))
    return float(value)


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "peak"}


def _apply_peak_power(decision: MarketDecision, state: OperatingState, context: RuleContext, rule_id: str) -> None:
    if not decision.is_peak:
        return
    target = context.peak_cap_mwh
    from_generation = min(decision.residual_mwh, target)
    decision.peak_power_sale_mwh += from_generation
    decision.residual_mwh -= from_generation
    remaining = target - from_generation
    delivered_from_bess = 0.0
    if remaining > 0.0 and state.bess_soc_mwh > 0.0:
        loss_factor = max(1.0 - context.discharge_loss_fraction, 0.0)
        deliverable_from_soc = state.bess_soc_mwh * loss_factor
        delivered_from_bess = min(remaining, context.bess_discharge_limit_mwh, deliverable_from_soc)
        soc_draw = delivered_from_bess / loss_factor if loss_factor > 0 else delivered_from_bess
        state.bess_soc_mwh = max(state.bess_soc_mwh - soc_draw, 0.0)
        decision.bess_discharge_mwh += delivered_from_bess
        decision.peak_power_sale_mwh += delivered_from_bess
        remaining -= delivered_from_bess
    if remaining > 0.0:
        decision.shortfall_mwh += remaining
        decision.penalty_value += remaining * context.ppa_tariff * context.penalty_multiplier
    if from_generation > 0.0 or delivered_from_bess > 0.0 or remaining > 0.0:
        decision.applied_rule_ids.append(rule_id)
        decision.audit_trace.append(
            f"{rule_id}: peak_sale={decision.peak_power_sale_mwh:.3f}, bess_discharge={delivered_from_bess:.3f}, shortfall={remaining:.3f}"
        )
        decision.revenue_value += decision.peak_power_sale_mwh * context.peak_tariff


def _apply_non_peak_workbook_dispatch(
    decision: MarketDecision,
    state: OperatingState,
    context: RuleContext,
    rule_id: str,
) -> None:
    if decision.is_peak or decision.residual_mwh <= 0.0:
        return

    p1_curtailment = float(context.forecast_curtailment_mwh_by_start.get(decision.interval_start, 0.0))
    raw_headroom = _bess_raw_headroom_mwh(state, context)
    if raw_headroom <= 0.000001:
        case_id = "case7_sufficient_bess"
        charge_first = False
    elif p1_curtailment >= raw_headroom:
        case_id = "case2_forecast_curtailment_covers_bess"
        charge_first = False
    else:
        case_id = "case3_charge_bess_first"
        charge_first = True

    sale_order = ["ppa", "merchant"] if context.ppa_tariff >= decision.merchant_price else ["merchant", "ppa"]
    allocations = {"ppa": 0.0, "merchant": 0.0, "bess_charge": 0.0, "curtailment": 0.0}

    if charge_first:
        allocations["bess_charge"] += _allocate_bess_charge(decision, state, context)
    for market in sale_order:
        if market == "ppa":
            allocations["ppa"] += _allocate_ppa(decision, context)
        else:
            allocations["merchant"] += _allocate_merchant(decision, context)
    if not charge_first:
        allocations["bess_charge"] += _allocate_bess_charge(decision, state, context)
    allocations["curtailment"] += _allocate_curtailment(decision)

    if sum(allocations.values()) <= 0.000001:
        return

    decision.applied_rule_ids.append(rule_id)
    decision.audit_trace.append(
        (
            f"{rule_id}: {case_id}, "
            f"p1_forecast_curtailment={p1_curtailment:.3f}, "
            f"bess_raw_headroom={raw_headroom:.3f}, "
            f"tariff_order={'>'.join(sale_order)}, "
            f"ppa={allocations['ppa']:.3f}, "
            f"merchant={allocations['merchant']:.3f}, "
            f"bess_charge={allocations['bess_charge']:.3f}, "
            f"curtailment={allocations['curtailment']:.3f}"
        )
    )


def _apply_ppa_sale(decision: MarketDecision, context: RuleContext, rule_id: str) -> None:
    allocated = _allocate_ppa(decision, context)
    if allocated <= 0.0:
        return
    decision.applied_rule_ids.append(rule_id)
    decision.audit_trace.append(f"{rule_id}: ppa_sale={allocated:.3f}")


def _apply_merchant_sale(decision: MarketDecision, context: RuleContext, rule_id: str) -> None:
    allocated = _allocate_merchant(decision, context)
    if allocated <= 0.0:
        return
    decision.applied_rule_ids.append(rule_id)
    decision.audit_trace.append(f"{rule_id}: merchant_sale={allocated:.3f}")


def _apply_bess_charge(decision: MarketDecision, state: OperatingState, context: RuleContext, rule_id: str) -> None:
    raw_charge = _allocate_bess_charge(decision, state, context)
    if raw_charge <= 0.0:
        return
    decision.applied_rule_ids.append(rule_id)
    net_charge = raw_charge * max(1.0 - context.charge_loss_fraction, 0.0)
    decision.audit_trace.append(f"{rule_id}: raw_charge={raw_charge:.3f}, net_soc_add={net_charge:.3f}")


def _apply_curtailment(decision: MarketDecision, rule_id: str) -> None:
    curtailed = _allocate_curtailment(decision)
    if curtailed <= 0.0:
        return
    decision.audit_trace.append(f"{rule_id}: curtailed={curtailed:.3f}")
    decision.applied_rule_ids.append(rule_id)


def _allocate_ppa(decision: MarketDecision, context: RuleContext) -> float:
    if decision.residual_mwh <= 0.0:
        return 0.0
    available_cap = max(context.ppa_cap_mwh - decision.ppa_sale_mwh, 0.0)
    allocated = min(decision.residual_mwh, available_cap)
    if allocated <= 0.0:
        return 0.0
    decision.ppa_sale_mwh += allocated
    decision.residual_mwh -= allocated
    decision.revenue_value += allocated * context.ppa_tariff
    return allocated


def _allocate_merchant(decision: MarketDecision, context: RuleContext) -> float:
    if decision.residual_mwh <= 0.0:
        return 0.0
    available_cap = max(context.merchant_cap_mwh - decision.merchant_sale_mwh, 0.0)
    allocated = min(decision.residual_mwh, available_cap)
    if allocated <= 0.0:
        return 0.0
    decision.merchant_sale_mwh += allocated
    decision.residual_mwh -= allocated
    decision.revenue_value += allocated * decision.merchant_price
    return allocated


def _allocate_bess_charge(decision: MarketDecision, state: OperatingState, context: RuleContext) -> float:
    if decision.residual_mwh <= 0.0:
        return 0.0
    raw_headroom = _bess_raw_headroom_mwh(state, context)
    if raw_headroom <= 0.0:
        return 0.0
    remaining_charge_limit = max(context.bess_charge_limit_mwh - decision.bess_charge_mwh, 0.0)
    raw_charge = min(decision.residual_mwh, remaining_charge_limit, raw_headroom)
    if raw_charge <= 0.0:
        return 0.0
    net_charge = raw_charge * max(1.0 - context.charge_loss_fraction, 0.0)
    decision.bess_charge_mwh += raw_charge
    state.bess_soc_mwh = min(state.bess_soc_mwh + net_charge, context.bess_capacity_mwh)
    decision.residual_mwh -= raw_charge
    return raw_charge


def _allocate_curtailment(decision: MarketDecision) -> float:
    if decision.residual_mwh <= 0.0:
        return 0.0
    curtailed = decision.residual_mwh
    decision.curtailment_mwh += curtailed
    decision.residual_mwh = 0.0
    return curtailed


def _bess_raw_headroom_mwh(state: OperatingState, context: RuleContext) -> float:
    headroom = max(context.bess_capacity_mwh - state.bess_soc_mwh, 0.0)
    loss_factor = max(1.0 - context.charge_loss_fraction, 0.0)
    return headroom / loss_factor if loss_factor > 0.0 else headroom


def _recommended_market(decision: MarketDecision) -> str:
    amounts = {
        "Peak Power": decision.peak_power_sale_mwh,
        "PPA": decision.ppa_sale_mwh,
        "Merchant": decision.merchant_sale_mwh,
        "BESS Charge": decision.bess_charge_mwh,
        "Curtailment": decision.curtailment_mwh,
    }
    market, amount = max(amounts.items(), key=lambda item: item[1])
    if amount <= 0.0 and decision.shortfall_mwh > 0.0:
        return "Shortfall"
    return market if amount > 0.0 else "None"
