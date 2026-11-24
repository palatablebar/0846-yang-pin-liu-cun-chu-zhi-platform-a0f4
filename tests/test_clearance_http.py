from app.clearance.service import issue_plans
from app.clearance.store import ClearanceStore


def good_payload():
    return [
        {"plan_key": "A", "box_ids": ["B1"]},
        {"plan_key": "B", "box_ids": ["B3"]},
    ]


def test_issue_then_retry_is_idempotent(tmp_path):
    store = ClearanceStore(str(tmp_path / "c.sqlite3"))
    first = issue_plans(store, "standard", "req-1", good_payload(), "首签")
    assert first["status"] == "ISSUED"
    assert first["retried"] is False

    second = issue_plans(store, "standard", "req-1", good_payload(), "重试")
    assert second["retried"] is True
    assert second["batch_id"] == first["batch_id"]
    # 存储中只有一条记录，额度只占一次
    rows = store.list_batches()
    assert len(rows) == 1
    assert store.quota_used("2026-11") == 7


def test_distinct_request_keys_each_consume_quota(tmp_path):
    store = ClearanceStore(str(tmp_path / "c.sqlite3"))
    issue_plans(store, "standard", "req-1", good_payload())
    issue_plans(store, "standard", "req-2", good_payload())
    assert len(store.list_batches()) == 2
    assert store.quota_used("2026-11") == 14


def test_issue_blocked_when_verification_fails(tmp_path):
    import pytest

    from app.errors import DomainIssue
    store = ClearanceStore(str(tmp_path / "c.sqlite3"))
    bad = [
        {"plan_key": "A", "box_ids": ["B2"]},
        {"plan_key": "B", "box_ids": ["B4"]},
    ]
    with pytest.raises(DomainIssue) as exc:
        issue_plans(store, "standard", "req-bad", bad)
    assert exc.value.code == "CLEARANCE_GATE_BLOCKED"
    assert store.list_batches() == []


def test_check_endpoint(client):
    response = client.post("/api/clearance/check", json={
        "scenario": "standard", "plans": good_payload()})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    # 联合最优的入选集合固定为 B1+B3；同集合内按去向向量优先归 A
    joined = sorted(body["optimal_joint"]["plan_a"] + body["optimal_joint"]["plan_b"])
    assert joined == ["B1", "B3"]
    assert body["max_cleared"] == 2


def test_check_endpoint_reports_violations(client):
    response = client.post("/api/clearance/check", json={
        "scenario": "standard",
        "plans": [{"plan_key": "A", "box_ids": ["B2", "B4"]},
                  {"plan_key": "B", "box_ids": []}]})
    body = response.get_json()
    assert body["ok"] is False
    assert any(item["code"] == "BOX_NOT_CLEARABLE" for item in body["violations"])


def test_issue_endpoint_and_retry_over_http(client):
    payload = {"scenario": "standard", "request_key": "http-1", "plans": good_payload()}
    first = client.post("/api/clearance/issue", json=payload)
    assert first.status_code == 200
    second = client.post("/api/clearance/issue", json=payload)
    assert second.status_code == 200
    assert second.get_json()["batch_id"] == first.get_json()["batch_id"]
    assert second.get_json()["retried"] is True

    batches = client.get("/api/clearance/batches").get_json()["batches"]
    assert len(batches) == 1


def test_issue_endpoint_rejects_gate_violation(client):
    response = client.post("/api/clearance/issue", json={
        "scenario": "standard", "request_key": "http-bad",
        "plans": [{"plan_key": "A", "box_ids": ["B2", "B4"]},
                  {"plan_key": "B", "box_ids": []}]})
    assert response.status_code == 409
    assert response.get_json()["code"] == "CLEARANCE_GATE_BLOCKED"


def test_scenarios_catalog_and_detail(client):
    catalog = client.get("/api/clearance/scenarios").get_json()
    assert "standard" in catalog["scenarios"]
    detail = client.get("/api/clearance/scenarios/standard")
    assert detail.status_code == 200
    assert detail.get_json()["data"]["monthly_limit"] == 10


def test_clearance_page_renders(client):
    response = client.get("/clearance")
    assert response.status_code == 200
    assert "月度腾库计划核对".encode() in response.data


def test_missing_fields_are_bad_request(client):
    response = client.post("/api/clearance/check", json={"scenario": "standard"})
    assert response.status_code == 400
