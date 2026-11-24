"""腾库计划核对与签发服务。"""

import hashlib
import json
from datetime import date

from app.clearance.model import Sample, build_case
from app.clearance.store import ClearanceStore
from app.clearance.verifier import PlanInput, verify_plans
from app.config import ROOT
from app.errors import DomainIssue

CLEARANCE_SCENARIOS = ROOT / "sample_data" / "clearance"


def _parse_date(value, code="BAD_DATE"):
    if value in (None, "", "-", "NA", "null", "未提供"):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DomainIssue(code)


def load_scenario(name: str):
    path = CLEARANCE_SCENARIOS / f"{name}.json"
    if not path.is_file():
        raise DomainIssue("SCENARIO_UNKNOWN", 404)
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    return data, hashlib.sha256(raw).hexdigest()


def build_from_scenario(data: dict):
    month_end = _parse_date(data["month_end"])
    samples = [
        Sample(
            sample_id=item["sample_id"],
            box_id=item["box_id"],
            disposable_from=_parse_date(item.get("disposable_from")),
            blocked=bool(item.get("blocked", False)),
        )
        for item in data["samples"]
    ]
    return build_case(month_end, int(data["monthly_limit"]), samples)


def _month_key(month_end: date) -> str:
    return month_end.strftime("%Y-%m")


def check_plans(name: str, plans_payload: list[dict]):
    data, digest = load_scenario(name)
    case = build_from_scenario(data)
    plans = [PlanInput(plan_key=item["plan_key"], box_ids=list(item["box_ids"]))
             for item in plans_payload]
    result = verify_plans(case, plans)
    result_dict = result.as_dict()
    result_dict["scenario"] = name
    result_dict["month_end"] = case.month_end.isoformat()
    result_dict["monthly_limit"] = case.monthly_limit
    result_dict["source_sha256"] = digest
    return result_dict


def issue_plans(store: ClearanceStore, name: str, request_key: str,
                plans_payload: list[dict], comment: str = ""):
    result = check_plans(name, plans_payload)
    if not result["ok"]:
        raise DomainIssue("CLEARANCE_GATE_BLOCKED", 409,
                          detail=json.dumps(result["violations"], ensure_ascii=False))
    data, _ = load_scenario(name)
    month_end = _parse_date(data["month_end"])
    payload = {
        "scenario": name,
        "month_key": _month_key(month_end),
        "month_end": result["month_end"],
        "monthly_limit": result["monthly_limit"],
        "plans": plans_payload,
        "used_items": result["used_items"],
        "cleared_boxes": result["cleared_boxes"],
        "optimal": result["optimal_joint"],
        "source_sha256": result["source_sha256"],
        "comment": comment.strip(),
    }
    batch, retried = store.create_batch(request_key, payload)
    batch["retried"] = retried
    return batch
