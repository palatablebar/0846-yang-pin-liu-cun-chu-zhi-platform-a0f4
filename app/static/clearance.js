(function () {
  var state = { scenario: null, detail: null, check: null };
  var $ = function (id) { return document.getElementById(id); };

  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }

  async function request(url, options) {
    var response = await fetch(url, Object.assign(
      {headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }

  function setNotice(text) { $('notice').textContent = text || ''; }

  function parseBoxIds(value) {
    return value.split(',').map(function (item) { return item.trim(); })
      .filter(function (item) { return item.length; });
  }

  function summarizeBoxes(data) {
    var byBox = {};
    data.samples.forEach(function (sample) {
      var box = byBox[sample.box_id] || (byBox[sample.box_id] = {
        box_id: sample.box_id, count: 0, due: null, blocked: false, late: false
      });
      box.count += 1;
      if (sample.blocked) box.blocked = true;
      if (!sample.disposable_from || sample.disposable_from > data.month_end) box.late = true;
      if (sample.disposable_from && (!box.due || sample.disposable_from > box.due)) {
        box.due = sample.disposable_from;
      }
    });
    return Object.values(byBox).sort(function (a, b) {
      return (a.due || '9999').localeCompare(b.due || '9999') || a.box_id.localeCompare(b.box_id);
    });
  }

  function renderScenario(detail) {
    state.detail = detail;
    var data = detail.data;
    $('m-month').textContent = data.month_end;
    $('m-limit').textContent = data.monthly_limit;
    var boxes = summarizeBoxes(data);
    var ready = boxes.filter(function (box) { return !box.blocked && !box.late; });
    $('m-ready').textContent = ready.length;
    $('box-body').innerHTML = boxes.map(function (box) {
      var clearable = !box.blocked && !box.late;
      var status = clearable ? '<span class="verdict" data-v="PASS">可腾空</span>'
        : '<span class="verdict" data-v="REJECT">' + (box.blocked ? '保全阻断' : '月底未到期') + '</span>';
      return '<tr><td>' + esc(box.box_id) + '</td><td class="num">' + box.count + '</td>'
        + '<td>' + esc(box.due || '—') + '</td><td>' + status + '</td></tr>';
    }).join('');
  }

  function renderOptimal(check) {
    state.check = check;
    $('m-max').textContent = check.max_cleared;
    $('optimal-view').innerHTML =
      '<div class="ledger-row"><span>单计划最优</span><strong>' + esc((check.optimal_single || []).join(', ') || '无') + '</strong></div>'
      + '<div class="ledger-row"><span>联合最优 A</span><strong>' + esc((check.optimal_joint.plan_a || []).join(', ') || '无') + '</strong></div>'
      + '<div class="ledger-row"><span>联合最优 B</span><strong>' + esc((check.optimal_joint.plan_b || []).join(', ') || '无') + '</strong></div>'
      + '<div class="ledger-row"><span>不可腾空箱</span><strong>' + esc((check.blocked_boxes || []).join(', ') || '无') + '</strong></div>';
  }

  function renderCheck(check) {
    renderOptimal(check);
    $('result-status').textContent = check.ok ? '通过' : '存在违规';
    $('result-status').className = 'hint ' + (check.ok ? 'check-ok' : 'check-bad');
    $('violation-list').innerHTML = check.violations.length
      ? check.violations.map(function (item) {
          return '<li><span>' + esc(item.plan_key || '联合') + '</span><span>'
            + esc(item.box_id || '') + '</span><code>' + esc(item.code)
            + (item.detail ? '：' + esc(item.detail) : '') + '</code></li>';
        }).join('')
      : '<li class="empty">全部规则通过，可提交签发</li>';
  }

  async function loadScenario() {
    var name = $('scenario').value;
    state.scenario = name;
    var detail = await request('/api/clearance/scenarios/' + encodeURIComponent(name));
    renderScenario(detail);
    setNotice('');
  }

  async function loadScenarios() {
    var data = await request('/api/clearance/scenarios');
    $('scenario').innerHTML = data.scenarios.map(function (name) {
      return '<option value="' + esc(name) + '">' + esc(name) + '</option>';
    }).join('');
    await loadScenario();
  }

  function payloadPlans() {
    return [
      {plan_key: 'A', box_ids: parseBoxIds($('plan-a').value)},
      {plan_key: 'B', box_ids: parseBoxIds($('plan-b').value)}
    ];
  }

  async function checkPlans() {
    try {
      setNotice('');
      var check = await request('/api/clearance/check', {
        method: 'POST',
        body: JSON.stringify({scenario: state.scenario, plans: payloadPlans()})
      });
      renderCheck(check);
    } catch (error) { setNotice(error.message); }
  }

  async function issuePlans() {
    var key = $('request-key').value.trim();
    if (!key) { setNotice('请填写 request_key 以支持幂等重试'); return; }
    try {
      setNotice('');
      var batch = await request('/api/clearance/issue', {
        method: 'POST',
        body: JSON.stringify({
          scenario: state.scenario, request_key: key,
          plans: payloadPlans(), comment: $('comment').value
        })
      });
      setNotice(batch.retried ? '重试命中首次签发，未重复占用额度' : '两份计划已同时签发');
      await loadBatches();
    } catch (error) { setNotice(error.message); }
  }

  async function loadBatches() {
    var data = await request('/api/clearance/batches');
    $('batch-count').textContent = data.batches.length + ' 批';
    $('batch-body').innerHTML = data.batches.length ? data.batches.map(function (batch) {
      return '<tr><td>' + esc(batch.batch_id) + '</td><td>' + esc(batch.scenario) + '</td>'
        + '<td class="num">' + esc(batch.used_items) + '</td>'
        + '<td class="num">' + esc(batch.cleared_boxes) + '</td>'
        + '<td><span class="verdict" data-v="PASS">' + esc(batch.status) + '</span></td>'
        + '<td>' + esc(batch.request_key) + '</td></tr>';
    }).join('') : '<tr><td class="empty" colspan="6">暂无已签发批次</td></tr>';
  }

  document.addEventListener('DOMContentLoaded', function () {
    loadScenarios().catch(function (error) { setNotice(error.message); });
    loadBatches().catch(function (error) { setNotice(error.message); });
    $('scenario').addEventListener('change', loadScenario);
    $('check').addEventListener('click', checkPlans);
    $('issue').addEventListener('click', issuePlans);
  });
})();
