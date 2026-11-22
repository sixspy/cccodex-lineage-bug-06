import { api } from '../api.js';

export async function renderSettings(view, setMsg) {
  const j = await api('/api/settings');
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <div class="wall">
        ${(j.items || []).map((s) => `<article class="card">
          <h3>${s.label}</h3>
          <p class="muted">${s.key}</p>
          <div class="row">
            <input data-k="${s.key}" value="${s.value}" />
            <button data-save="${s.key}" class="primary" type="button">保存</button>
          </div>
        </article>`).join('')}
      </div>
    </section>`;
  view.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const input = view.querySelector('input[data-k="' + btn.dataset.save + '"]');
        await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
        });
        setMsg('参数已保存');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });

  try {
    const dash = await api('/api/owner_group/dash');
    view.innerHTML += `<section class="card" style="margin-top:1rem">
      <h3>责任组切换</h3>
      <p>
        <span class="tag ${dash.chip === dash.ok_chip ? 'ok' : 'bad'}">${dash.chip}</span>
        <span class="tag ${dash.chip === dash.ok_chip ? 'ok' : 'bad'}">组号 ${dash.group_no || '未落'} · ${dash.group_name || '—'}</span>
        <span class="tag">${dash.locked ? '账册已锁定' : '账册未锁'}</span>
      </p>
      <p class="muted">锁点 ${dash.pack_count} · 被锁节点 ${dash.durable_count} · 状态 ${dash.state}${
        (dash.reasons || []).length ? ' · ' + dash.reasons.join('；') : ''
      }</p>
      <div class="row">
        <label>旧组<input id="og_from" value="采集组" /></label>
        <label>新组<input id="og_to" value="仓建组" /></label>
        <label>组号<input id="og_no" value="G-02" /></label>
        <button id="go_owner_group" class="primary" type="button">改组并锁账册</button>
      </div>
      <p class="muted">改组一次写完，账册不可再改；只改展示或半修对齐一律不落。</p>
    </section>`;
    document.getElementById('go_owner_group').onclick = async () => {
      try {
        const r = await api('/api/owner_group/switch', {
          method: 'POST',
          body: JSON.stringify({
            from_group: document.getElementById('og_from').value,
            to_group: document.getElementById('og_to').value,
            group_no: document.getElementById('og_no').value,
          }),
        });
        setMsg(r.pack.message + ' · ' + r.pack.pack_key);
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  } catch (e) { /* optional surface */ }

}
