"""腾箱优先级与最优计划求解。

求解目标（与业务规则一一对应）：

1. 只在“本月可腾空箱”里挑选。
2. 在样品件数月限额内，让腾空箱数最多。
3. 腾空箱数相同，优先到期日更早的箱：按箱到期日升序逐个比较
   （到期日相同按箱号兜底），第一个更优者胜出，结果确定可复算。
4. 两份计划联合求解：箱在 A、B 之间互斥，合计件数不超限额，
   最大化合计腾空箱数，再按到期日先后确定入选集合；同一入选集合内
   按到期顺序，箱号小者优先归入 A，结果唯一。

箱子数量按月度计划规模设计，使用确定性的子集枚举，避免贪心在
“先排到期早的箱会挤掉两个能腾空的箱”这类场景里给出次优解。
"""

from itertools import combinations, product

from app.clearance.model import Box, ClearanceCase


def _items(box: Box) -> int:
    return box.sample_count


def _rank_key(box: Box):
    return (box.due_date.isoformat(), box.box_id)


def _sorted(boxes):
    return sorted(boxes, key=_rank_key)


def _set_key(boxes):
    """入选集合的排序指纹：按到期日、箱号升序。"""
    return tuple(_rank_key(box) for box in _sorted(boxes))


def best_single_plan(case: ClearanceCase) -> list[Box]:
    """单份计划在限额内的最优箱集合。"""
    ready = case.clearable_boxes()
    for size in range(len(ready), 0, -1):
        feasible = []
        for combo in combinations(ready, size):
            if sum(_items(box) for box in combo) <= case.monthly_limit:
                feasible.append(list(combo))
        if feasible:
            # 箱数相同：到期日升序逐个比较，取整体最早者
            feasible.sort(key=_set_key)
            return _sorted(feasible[0])
    return []


def best_joint_plans(case: ClearanceCase) -> tuple[list[Box], list[Box]]:
    """两份计划联合最优，返回 (计划A, 计划B)。

    两阶段求解，避免把“含一个极早到期箱”的集合错误地整体判优：
    阶段一按箱数分层，找到限额内可达的最大腾空箱数；
    阶段二只在该箱数的可行解里，按入选集合到期次序、再按去向向量
    （箱号小者优先归 A）挑唯一最优解。
    """
    ready = case.clearable_boxes()
    feasible_by_count: dict[int, list[tuple]] = {}
    for assignment in product((0, 1, 2), repeat=len(ready)):
        plan_a = [box for box, choice in zip(ready, assignment) if choice == 1]
        plan_b = [box for box, choice in zip(ready, assignment) if choice == 2]
        used = sum(_items(box) for box in plan_a) + sum(_items(box) for box in plan_b)
        if used > case.monthly_limit:
            continue
        count = len(plan_a) + len(plan_b)
        vector = tuple(assignment)
        candidate = (
            _set_key(plan_a + plan_b),
            vector,
            [box.box_id for box in plan_a],
            [box.box_id for box in plan_b],
        )
        feasible_by_count.setdefault(count, []).append(candidate)
    if not feasible_by_count:
        return [], []
    top_count = max(feasible_by_count)
    winners = feasible_by_count[top_count]
    # 先看到期次序，再看去向向量，保证唯一确定
    winners.sort(key=lambda item: (item[0], item[1]))
    _, _, a_ids, b_ids = winners[0]
    by_id = {box.box_id: box for box in ready}
    return _sorted([by_id[box_id] for box_id in a_ids]), \
        _sorted([by_id[box_id] for box_id in b_ids])
