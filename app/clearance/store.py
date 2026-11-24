"""腾库计划签发存储。

两份计划同时提交、同一批事务内签发。request_key 相同的重试直接返回
首次记录，不会二次写入，因此不重复占用月限额。
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone


class ClearanceStore:
    def __init__(self, path):
        self.path = path
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS clearance_batches(
              id TEXT PRIMARY KEY,
              request_key TEXT UNIQUE NOT NULL,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload TEXT NOT NULL,
              comment TEXT NOT NULL DEFAULT ''
            );
            ''')

    def find_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM clearance_batches WHERE request_key=?",
                (request_key,)).fetchone()
        return dict(row) if row else None

    def create_batch(self, request_key, payload):
        existing = self.find_by_request(request_key)
        if existing:
            return self._serialize(existing), True
        batch_id = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute(
                "INSERT INTO clearance_batches(id,request_key,created_at,status,revision,payload)"
                " VALUES(?,?,?,?,?,?)",
                (batch_id, request_key, datetime.now(timezone.utc).isoformat(),
                 "ISSUED", 1, json.dumps(payload, ensure_ascii=False)))
        return self._serialize(self.get(batch_id)), False

    def get(self, batch_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM clearance_batches WHERE id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def list_batches(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM clearance_batches ORDER BY created_at DESC").fetchall()
        return [self._serialize(dict(row)) for row in rows]

    def quota_used(self, month_key: str) -> int:
        """已签发批次实际占用的样品件数额度（重试不会重复计入）。"""
        used = 0
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM clearance_batches").fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if payload.get("month_key") != month_key:
                continue
            used += int(payload.get("used_items", 0))
        return used

    def review(self, batch_id, decision, comment, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM clearance_batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                return None, "missing"
            if row["revision"] != expected_revision:
                return None, "stale"
            db.execute(
                "UPDATE clearance_batches SET status=?, revision=revision+1, comment=?"
                " WHERE id=?", (decision, comment, batch_id))
        return self.get(batch_id), None

    @staticmethod
    def _serialize(row):
        if row is None:
            return None
        payload = json.loads(row["payload"])
        payload.update(batch_id=row["id"], status=row["status"],
                       revision=row["revision"], comment=row["comment"],
                       created_at=row["created_at"], request_key=row["request_key"])
        return payload
