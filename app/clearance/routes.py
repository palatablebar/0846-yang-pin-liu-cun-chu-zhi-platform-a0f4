from flask import Blueprint, jsonify, request

from app.clearance.service import (CLEARANCE_SCENARIOS, check_plans,
                                   issue_plans, load_scenario)
from app.clearance.store import ClearanceStore
from app.errors import DomainIssue

clearance_api = Blueprint("clearance_api", __name__)


def clearance_store():
    from flask import current_app
    return current_app.extensions["clearance"]


@clearance_api.get("/api/clearance/scenarios")
def scenarios():
    names = sorted(path.stem for path in CLEARANCE_SCENARIOS.glob("*.json"))
    return jsonify({"scenarios": names})


@clearance_api.get("/api/clearance/scenarios/<name>")
def scenario_detail(name):
    data, digest = load_scenario(name)
    return jsonify({"scenario": name, "data": data, "sha256": digest})


@clearance_api.post("/api/clearance/check")
def check():
    body = request.get_json(silent=True) or {}
    if "scenario" not in body or "plans" not in body:
        raise DomainIssue("BAD_REQUEST")
    return jsonify(check_plans(body["scenario"], body["plans"]))


@clearance_api.post("/api/clearance/issue")
def issue():
    body = request.get_json(silent=True) or {}
    if "scenario" not in body or "request_key" not in body or "plans" not in body:
        raise DomainIssue("BAD_REQUEST")
    batch = issue_plans(clearance_store(), body["scenario"], body["request_key"],
                        body["plans"], body.get("comment", ""))
    return jsonify(batch)


@clearance_api.get("/api/clearance/batches")
def batches():
    return jsonify({"batches": clearance_store().list_batches()})


@clearance_api.get("/api/clearance/batches/<batch_id>")
def batch_detail(batch_id):
    row = clearance_store().get(batch_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify(ClearanceStore._serialize(row))
