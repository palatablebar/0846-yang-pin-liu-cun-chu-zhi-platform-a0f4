"""月度腾库计划领域模型。

数据以箱子（box）和样品（sample）为单位。每个样品有一个最早可处置日
``disposable_from``；样品也可能被保全（``blocked=True``），本月始终不可处置。

规则（见 docs/BUSINESS-RULES.md 腾库计划章节）：

1. 库房每月按样品件数限额（monthly_limit）。
2. 箱内只要有一件样品到月底仍不能处置，整箱就不排——本月只允许排“可腾空箱”。
3. 箱子的到期日 = 箱内最后一件样品何时可处置（max(disposable_from)）。
4. 优先级：本月能多腾箱位时优先多腾；腾空箱数相同，再看哪个箱先到期
   （到期日相同按箱号兜底，保证结果确定）。
5. 两份计划同时提交签发：同一箱只签入一份；两份合计样品件数不得超过月限额。
6. 同一计划按 request_key 幂等重试，不重复占用额度。
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Sample:
    sample_id: str
    box_id: str
    disposable_from: date | None
    blocked: bool = False


@dataclass
class Box:
    box_id: str
    samples: list[Sample] = field(default_factory=list)

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def all_disposable_by(self, month_end: date) -> bool:
        """整箱在月底前可否腾空：无保全件，且每件都有不晚于月底的可处置日。"""
        if not self.samples:
            return False
        for sample in self.samples:
            if sample.blocked:
                return False
            if sample.disposable_from is None or sample.disposable_from > month_end:
                return False
        return True

    @property
    def due_date(self) -> date | None:
        """箱到期日 = 最后一件样品可处置日；存在不可处置件时为 None。"""
        dates = [sample.disposable_from for sample in self.samples
                 if not sample.blocked and sample.disposable_from is not None]
        if len(dates) != len(self.samples):
            return None
        return max(dates)


@dataclass
class ClearanceCase:
    """一次月度腾库核对的输入场景。"""

    month_end: date
    monthly_limit: int
    boxes: dict[str, Box]

    def clearable_boxes(self) -> list[Box]:
        """本月可腾空箱，按规则 4 排序：到期日升序、箱号兜底。"""
        ready = [box for box in self.boxes.values() if box.all_disposable_by(self.month_end)]
        return sorted(ready, key=lambda box: (box.due_date, box.box_id))

    def blocked_box_ids(self) -> list[str]:
        return sorted(box_id for box_id, box in self.boxes.items()
                      if not box.all_disposable_by(self.month_end))


def build_case(month_end: date, monthly_limit: int, samples: list[Sample]) -> ClearanceCase:
    if monthly_limit < 0:
        raise ValueError("BAD_LIMIT")
    boxes: dict[str, Box] = {}
    for sample in samples:
        box = boxes.setdefault(sample.box_id, Box(sample.box_id))
        box.samples.append(sample)
    for box in boxes.values():
        box.samples.sort(key=lambda item: item.sample_id)
    return ClearanceCase(month_end=month_end, monthly_limit=monthly_limit, boxes=boxes)
