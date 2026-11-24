"""已签发/待签发腾库计划的规则核对。

核对项与业务规则对应：

- ``UNKNOWN_BOX``        计划引用了不存在的箱号。
- ``BOX_NOT_CLEARABLE``  箱内有样品月底仍不能处置，整箱不排。
- ``PLAN_OVER_LIMIT``    单份计划样品件数超过月限额。
- ``DUPLICATE_BOX``      同一箱在同一份计划里重复出现。
- ``OVERLAP_BOX``        同一箱同时签入两份计划。
- ``TOTAL_OVER_LIMIT``   两份计划合计样品件数超过月限额。
- ``NOT_OPTIMAL_COUNT``  本可以腾空更多箱，当前计划少腾了箱位。
- ``NOT_OPTIMAL_DUE``    腾空箱数相同，但未优先到期更早的箱。

核对结果同时给出“最优解”用于工作台对照。重试幂等由存储层按
request_key 保证，核对层只做纯规则判断。
"""

from dataclasses import dataclass, field

from app.clearance.model import Box, ClearanceCase
from app.clearance.planner import best_joint_plans, best_single_plan


@dataclass
class PlanInput:
    plan_key: str
    box_ids: list[str]


@dataclass
class Violation:
    code: str
    plan_key: str | None = None
    box_id: str | None = None
    detail: str = ""


@dataclass
class VerifyResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    used_items: int = 0
    cleared_boxes: int = 0
    optimal_single: list[str] = field(default_factory=list)
    optimal_joint_a: list[str] = field(default_factory=list)
    optimal_joint_b: list[str] = field(default_factory=list)
    max_cleared: int = 0
    blocked_boxes: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "ok": self.ok,
            "violations": [vars(item) for item in self.violations],
            "used_items": self.used_items,
            "cleared_boxes": self.cleared_boxes,
            "optimal_single": self.optimal_single,
            "optimal_joint": {"plan_a": self.optimal_joint_a, "plan_b": self.optimal_joint_b},
            "max_cleared": self.max_cleared,
            "blocked_boxes": self.blocked_boxes,
        }


def _resolve(case: ClearanceCase, plan: PlanInput, violations: list[Violation]) -> list[Box]:
    seen: set[str] = set()
    resolved: list[Box] = []
    for box_id in plan.box_ids:
        if box_id in seen:
            violations.append(Violation("DUPLICATE_BOX", plan.plan_key, box_id))
            continue
        seen.add(box_id)
        box = case.boxes.get(box_id)
        if box is None:
            violations.append(Violation("UNKNOWN_BOX", plan.plan_key, box_id))
            continue
        if not box.all_disposable_by(case.month_end):
            violations.append(Violation("BOX_NOT_CLEARABLE", plan.plan_key, box_id))
            continue
        resolved.append(box)
    return resolved


def verify_plans(case: ClearanceCase, plans: list[PlanInput]) -> VerifyResult:
    violations: list[Violation] = []
    resolved_per_plan: dict[str, list[Box]] = {}
    for plan in plans:
        resolved_per_plan[plan.plan_key] = _resolve(case, plan, violations)

    # 单份计划限额
    for plan in plans:
        boxes = resolved_per_plan[plan.plan_key]
        used = sum(box.sample_count for box in boxes)
        if used > case.monthly_limit:
            violations.append(Violation("PLAN_OVER_LIMIT", plan.plan_key,
                                       detail=f"{used}>{case.monthly_limit}"))

    # 两计划同箱互斥；合计件数按去重后的箱集合计算，避免重叠箱重复计数
    if len(plans) == 2:
        ids_a = {box.box_id for box in resolved_per_plan[plans[0].plan_key]}
        ids_b = {box.box_id for box in resolved_per_plan[plans[1].plan_key]}
        for box_id in sorted(ids_a & ids_b):
            violations.append(Violation("OVERLAP_BOX", box_id=box_id))
        selected_id_set = ids_a | ids_b
        all_selected = [box for box in case.boxes.values()
                        if box.box_id in selected_id_set]
        total_used = sum(box.sample_count for box in all_selected)
        if total_used > case.monthly_limit:
            violations.append(Violation("TOTAL_OVER_LIMIT",
                                       detail=f"{total_used}>{case.monthly_limit}"))
    else:
        all_selected = []
        for plan in plans:
            all_selected.extend(resolved_per_plan[plan.plan_key])
        total_used = sum(box.sample_count for box in all_selected)

    # 最优性对照
    single_best = best_single_plan(case)
    joint_a, joint_b = best_joint_plans(case)
    max_cleared = len(single_best)
    if len(plans) == 2:
        max_cleared = len(joint_a) + len(joint_b)
        selected_ids = sorted(box.box_id for box in all_selected)
        optimal_ids = sorted(box.box_id for box in joint_a + joint_b)
    else:
        plan = plans[0] if plans else PlanInput("_", [])
        selected_ids = sorted(box.box_id for box in resolved_per_plan.get(plan.plan_key, []))
        optimal_ids = sorted(box.box_id for box in single_best)

    if len(selected_ids) < max_cleared:
        violations.append(Violation(
            "NOT_OPTIMAL_COUNT",
            detail=f"当前腾空 {len(selected_ids)} 箱，最多可腾空 {max_cleared} 箱"))
    elif selected_ids and selected_ids != optimal_ids:
        violations.append(Violation(
            "NOT_OPTIMAL_DUE",
            detail=f"同箱数下应优先到期更早的箱：{optimal_ids}"))

    return VerifyResult(
        ok=not violations,
        violations=violations,
        used_items=total_used,
        cleared_boxes=len({box.box_id for box in all_selected}),
        optimal_single=[box.box_id for box in single_best],
        optimal_joint_a=[box.box_id for box in joint_a],
        optimal_joint_b=[box.box_id for box in joint_b],
        max_cleared=max_cleared,
        blocked_boxes=case.blocked_box_ids(),
    )
