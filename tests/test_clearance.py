from datetime import date

import pytest

from app.clearance.model import Sample, build_case
from app.clearance.planner import best_joint_plans, best_single_plan
from app.clearance.verifier import PlanInput, verify_plans


def s(sample_id, box_id, disposable, blocked=False):
    return Sample(sample_id, box_id,
                  date.fromisoformat(disposable) if disposable else None, blocked)


def fill(prefix, box_id, count, day, blocked=False):
    return [s(f"{prefix}{k}", box_id, day, blocked) for k in range(count)]


def make_case(samples, limit=10, month_end="2026-11-30"):
    return build_case(date.fromisoformat(month_end), limit, samples)


def ids(boxes):
    return [box.box_id for box in boxes]


# ---------- 规则 2：整箱有样品月底不能处置就不排 ----------

def test_box_with_late_sample_is_not_clearable():
    c = make_case([s("a", "B1", "2026-11-10"), s("b", "B1", "2026-12-05")])
    assert c.blocked_box_ids() == ["B1"]
    assert best_single_plan(c) == []


def test_box_with_legal_hold_sample_is_not_clearable():
    c = make_case([s("a", "B1", "2026-11-10"), s("b", "B1", "2026-11-12", blocked=True)])
    assert c.blocked_box_ids() == ["B1"]
    assert best_single_plan(c) == []


def test_box_with_missing_disposal_date_is_not_clearable():
    c = make_case([s("a", "B1", "2026-11-10"), s("b", "B1", None)])
    assert c.blocked_box_ids() == ["B1"]


def test_sample_disposable_on_month_end_is_clearable():
    # 边界：可处置日恰为月末，仍算可腾空
    c = make_case([s("a", "B1", "2026-11-30")])
    assert ids(best_single_plan(c)) == ["B1"]


# ---------- 规则 3：箱到期日 = 最后一件样品可处置日 ----------

def test_box_due_date_is_last_sample_disposal_date():
    c = make_case([s("a", "B1", "2026-11-03"), s("b", "B1", "2026-11-28"),
                   s("c", "B1", "2026-11-15")])
    assert c.boxes["B1"].due_date == date(2026, 11, 28)


def test_box_due_date_none_when_any_sample_undisposable():
    c = make_case([s("a", "B1", "2026-11-03"), s("b", "B1", None)])
    assert c.boxes["B1"].due_date is None


# ---------- 规则 4：先多腾箱，同数看到期 ----------

def test_priority_favors_more_boxes_over_earlier_due():
    # B1 到期最早但要 6 件；B2、B3 各 2 件。限额 6：
    # 只排 B1 腾 1 箱，排 B2+B3 腾 2 箱 -> 必须选后者
    samples = (fill("a", "B1", 6, "2026-11-05")
               + fill("b", "B2", 2, "2026-11-20")
               + fill("c", "B3", 2, "2026-11-25"))
    c = make_case(samples, limit=6)
    assert ids(best_single_plan(c)) == ["B2", "B3"]


def test_tie_on_box_count_earlier_due_wins():
    # 限额只够腾 1 箱：B2 到期 2026-11-10，B3 到期 2026-11-20 -> B2
    samples = fill("b", "B2", 3, "2026-11-10") + fill("c", "B3", 3, "2026-11-20")
    c = make_case(samples, limit=3)
    assert ids(best_single_plan(c)) == ["B2"]


def test_tie_on_due_date_box_id_breaks_tie_deterministically():
    c = make_case([s("a", "B9", "2026-11-10"), s("b", "B2", "2026-11-10")], limit=1)
    assert ids(best_single_plan(c)) == ["B2"]


def test_monthly_limit_counts_samples_not_boxes():
    # B1=3 件、B2=3 件，限额 5：两箱合计 6 件超限，只能选到期早的 B1
    samples = fill("a", "B1", 3, "2026-11-08") + fill("b", "B2", 3, "2026-11-18")
    c = make_case(samples, limit=5)
    assert ids(best_single_plan(c)) == ["B1"]


def test_unused_quota_is_acceptable_when_no_more_box_fits():
    c = make_case(fill("a", "B1", 4, "2026-11-08"), limit=10)
    plan = best_single_plan(c)
    assert ids(plan) == ["B1"]
    assert sum(box.sample_count for box in plan) == 4


# ---------- 规则 5：两份计划联合 ----------

def test_joint_plans_split_boxes_without_overlap():
    samples = (fill("a", "B1", 2, "2026-11-05")
               + fill("b", "B2", 3, "2026-11-15")
               + fill("c", "B3", 2, "2026-11-25"))
    c = make_case(samples, limit=10)
    plan_a, plan_b = best_joint_plans(c)
    selected = ids(plan_a) + ids(plan_b)
    assert sorted(selected) == ["B1", "B2", "B3"]
    assert set(ids(plan_a)).isdisjoint(set(ids(plan_b)))
    assert sum(box.sample_count for box in plan_a + plan_b) <= 10


def test_joint_assignment_is_deterministic():
    # 同一入选集合重复求解结果必须一致（去向向量兜底，同集合优先归入 A）
    samples = fill("a", "B1", 2, "2026-11-05") + fill("b", "B2", 2, "2026-11-15")
    c = make_case(samples, limit=10)
    first = best_joint_plans(c)
    for _ in range(5):
        assert best_joint_plans(c) == first
    plan_a, plan_b = first
    assert sorted(ids(plan_a) + ids(plan_b)) == ["B1", "B2"]
    # 向量字典序下 (1,1) 优于 (1,2)，同集合整体归入计划 A
    assert ids(plan_a) == ["B1", "B2"]
    assert ids(plan_b) == []


def test_joint_total_over_limit_is_rejected_by_verifier():
    samples = fill("a", "B1", 4, "2026-11-05") + fill("b", "B2", 4, "2026-11-15")
    c = make_case(samples, limit=6)
    result = verify_plans(c, [PlanInput("A", ["B1"]), PlanInput("B", ["B2"])])
    codes = {item.code for item in result.violations}
    assert "TOTAL_OVER_LIMIT" in codes
    assert result.ok is False


def test_same_box_in_two_plans_is_overlap():
    c = make_case([s("a", "B1", "2026-11-05"), s("b", "B2", "2026-11-15")])
    result = verify_plans(c, [PlanInput("A", ["B1", "B2"]), PlanInput("B", ["B1"])])
    assert any(item.code == "OVERLAP_BOX" and item.box_id == "B1"
               for item in result.violations)


def test_overlapping_box_not_double_counted_against_limit():
    # X1=3 件、X2=2 件，限额 6：去重后 5 件不超限；
    # 重叠只报 OVERLAP_BOX，不得误报 TOTAL_OVER_LIMIT。
    from app.clearance.service import check_plans

    result = check_plans("violations", [
        {"plan_key": "A", "box_ids": ["X1", "X2"]},
        {"plan_key": "B", "box_ids": ["X2"]},
    ])
    codes = {item["code"] for item in result["violations"]}
    assert "OVERLAP_BOX" in codes
    assert "TOTAL_OVER_LIMIT" not in codes
    assert result["used_items"] == 5


def test_duplicate_box_in_one_plan_flagged():
    c = make_case([s("a", "B1", "2026-11-05")])
    result = verify_plans(c, [PlanInput("A", ["B1", "B1"]), PlanInput("B", [])])
    assert any(item.code == "DUPLICATE_BOX" for item in result.violations)


def test_unknown_box_flagged():
    c = make_case([s("a", "B1", "2026-11-05")])
    result = verify_plans(c, [PlanInput("A", ["ZZZ"]), PlanInput("B", [])])
    assert any(item.code == "UNKNOWN_BOX" for item in result.violations)


def test_non_clearable_box_in_plan_flagged():
    c = make_case([s("a", "B1", "2026-11-05"), s("b", "B1", "2026-12-20")])
    result = verify_plans(c, [PlanInput("A", ["B1"]), PlanInput("B", [])])
    assert any(item.code == "BOX_NOT_CLEARABLE" for item in result.violations)


def test_single_plan_over_limit_flagged():
    c = make_case(fill("a", "B1", 8, "2026-11-05"), limit=6)
    result = verify_plans(c, [PlanInput("A", ["B1"]), PlanInput("B", [])])
    assert any(item.code == "PLAN_OVER_LIMIT" for item in result.violations)


# ---------- 规则 4 在核对层：次优计划被指出 ----------

def test_verifier_flags_missed_box_count():
    # 限额 6：B1 占 6 件只能腾 1 箱；B2+B3 各 2 件可腾 2 箱
    samples = (fill("a", "B1", 6, "2026-11-02")
               + fill("b", "B2", 2, "2026-11-20")
               + fill("c", "B3", 2, "2026-11-25"))
    c = make_case(samples, limit=6)
    result = verify_plans(c, [PlanInput("A", ["B1"]), PlanInput("B", [])])
    assert any(item.code == "NOT_OPTIMAL_COUNT" for item in result.violations)


def test_verifier_flags_due_order_when_count_ties():
    # 都只能腾 1 箱，选了到期晚的 B3
    samples = fill("b", "B2", 3, "2026-11-10") + fill("c", "B3", 3, "2026-11-20")
    c = make_case(samples, limit=3)
    result = verify_plans(c, [PlanInput("A", ["B3"]), PlanInput("B", [])])
    assert any(item.code == "NOT_OPTIMAL_DUE" for item in result.violations)


def test_optimal_plan_passes_verification():
    samples = fill("b", "B2", 3, "2026-11-10") + fill("c", "B3", 3, "2026-11-20")
    c = make_case(samples, limit=6)
    result = verify_plans(c, [PlanInput("A", ["B2"]), PlanInput("B", ["B3"])])
    assert result.ok
    assert result.cleared_boxes == 2
    assert result.used_items == 6


# ---------- 标准场景端到端数值 ----------

def test_standard_scenario_expected_optimum():
    from app.clearance.service import build_from_scenario, check_plans, load_scenario

    data, _ = load_scenario("standard")
    c = build_from_scenario(data)
    ready = c.clearable_boxes()
    # B2=8 件到期11-15，B1=4 件到期11-20，B3=3 件到期11-25；B4/B5 不可腾空。
    # 排 B2 后只剩 2 件额度，配不上任何箱 -> 只能腾 1 箱；
    # B1+B3=7 件可腾 2 箱，多腾箱优先。
    assert [(box.box_id, box.sample_count) for box in ready] == [
        ("B2", 8), ("B1", 4), ("B3", 3)]
    plan_a, plan_b = best_joint_plans(c)
    assert sorted(ids(plan_a) + ids(plan_b)) == ["B1", "B3"]

    result = check_plans("standard", [
        {"plan_key": "A", "box_ids": ["B1"]},
        {"plan_key": "B", "box_ids": ["B3"]},
    ])
    assert result["ok"] is True
    assert result["used_items"] == 7
    assert result["cleared_boxes"] == 2


def test_standard_scenario_violating_submission_blocked():
    from app.clearance.service import check_plans

    result = check_plans("standard", [
        {"plan_key": "A", "box_ids": ["B2"]},   # 只腾 1 箱，次优
        {"plan_key": "B", "box_ids": ["B4"]},   # B4 有样品 12 月才可处置
    ])
    codes = {item["code"] for item in result["violations"]}
    assert "BOX_NOT_CLEARABLE" in codes
    assert "NOT_OPTIMAL_COUNT" in codes


def test_violations_scenario():
    from app.clearance.service import check_plans

    # X1=3 件、X2=2 件；限额 6；X3 月底不能腾空、X4 保全
    result = check_plans("violations", [
        {"plan_key": "A", "box_ids": ["X1"]},
        {"plan_key": "B", "box_ids": ["X2", "X3"]},
    ])
    codes = {item["code"] for item in result["violations"]}
    assert "BOX_NOT_CLEARABLE" in codes
    assert result["ok"] is False


def test_unknown_scenario_raises():
    from app.clearance.service import check_plans
    from app.errors import DomainIssue

    with pytest.raises(DomainIssue):
        check_plans("nope", [{"plan_key": "A", "box_ids": []}])
