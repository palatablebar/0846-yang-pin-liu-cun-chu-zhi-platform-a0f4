# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 腾库计划

工作台页面：`GET /clearance`。

- `GET /api/clearance/scenarios`：列出腾库场景（`sample_data/clearance/*.json`）。
- `GET /api/clearance/scenarios/{name}`：场景明细与来源摘要。
- `POST /api/clearance/check`：核对两份计划，body 为 `{"scenario","plans":[{"plan_key","box_ids"}...]}`，返回违规码、占用件数、腾空箱数与最优解。
- `POST /api/clearance/issue`：核对通过后同时签发两份计划，body 额外携带幂等 `request_key` 与 `comment`；同键重试返回原批次且 `retried=true`，不重复占额度。
- `GET /api/clearance/batches`：已签发批次列表。
- `GET /api/clearance/batches/{id}`：单批次详情。

违规码：`UNKNOWN_BOX`、`BOX_NOT_CLEARABLE`、`PLAN_OVER_LIMIT`、`DUPLICATE_BOX`、`OVERLAP_BOX`、`TOTAL_OVER_LIMIT`、`NOT_OPTIMAL_COUNT`、`NOT_OPTIMAL_DUE`。
