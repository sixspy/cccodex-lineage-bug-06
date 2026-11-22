import { api } from '../api.js';

export async function renderCoverage(view, setMsg) {
  const reports = await api('/api/coverage/reports');
  const state = await api('/api/owner_group/state');
  view.innerHTML = `
    <section class="card">
      <h2>覆盖对账</h2>
      <p class="muted">改组口径：<span class="tag ${state.ok ? 'ok' : 'bad'}">${state.panel_chip}</span> 组号 ${state.group_no || '未落'} · ${state.state}</p>
      <div class="row">
        <button class="primary" id="go" type="button">跑覆盖对账</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(reports.items || []).map((r) => `<article class="card">
        <h3>报告 #${r.id}</h3>
        <span class="tag ${r.group_ok ? 'ok' : 'bad'}">${r.group_ok ? '已齐套' : '有缺口'}</span>
        <span class="tag ${r.group_ok ? 'ok' : 'bad'}">组号 ${r.group_no || '未落'} · ${r.group_name || '—'}</span>
        <p>${r.summary}</p>
        <p class="muted">节点 ${r.node_count} · 已发布边 ${r.edge_count} · 孤儿 ${r.orphan_count} · 草稿边 ${r.uncovered_edges}</p>
        <p class="muted">改组口径：${r.group_state || '未落'}</p>
      </article>`).join('') || '<p class="muted">暂无对账报告</p>'}
    </section>`;
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/coverage/reports', { method: 'POST', body: '{}' });
      setMsg(r.report.summary);
      await renderCoverage(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
}
