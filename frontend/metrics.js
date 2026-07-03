// metrics.js - Metrics tab. Uses /metrics (counts), /health (status), and
// /api/metrics/detailed (real aggregates from the FULL activity log:
// true success rate, avg routing confidence, low-confidence routings).

(function () {
  async function loadMetrics() {
    const [health, metrics, detail] = await Promise.all([
      fetch('/health').then(r => r.json()).catch(() => ({})),
      fetch('/metrics').then(r => r.json()).catch(() => ({})),
      fetch('/api/metrics/detailed').then(r => r.json()).catch(() => null),
    ]);
    renderHealth(health);
    set('mx-docs', metrics.total_docs ?? '-');
    set('mx-vectors', metrics.total_vectors ?? '-');
    set('mx-agents', metrics.agents ?? '-');
    if (detail) renderDetail(detail);
  }

  function renderHealth(h) {
    const svcs = ['qdrant', 'neo4j', 'ollama', 'planner', 'supervisor'];
    const el = document.getElementById('mx-health');
    if (!el) return;
    el.innerHTML = svcs.map(s => {
      const up = h[s] === 'connected' || h[s] === 'running' || h[s] === 'active';
      return `<div class="mx-svc"><span class="mx-dot ${up ? 'on' : 'off'}"></span>
        <span class="mx-svc-name">${s}</span>
        <span class="mx-svc-state">${up ? (h[s] || 'up') : 'down'}</span></div>`;
    }).join('');
  }

  function renderDetail(d) {
    set('mx-events', d.total_events ?? '-');

    // real success rate with the actual counts, so 100% isn't misleading
    const sr = (d.success_rate ?? '-') + (d.success_rate != null ? '%' : '');
    set('mx-success', sr);
    set('mx-success-sub', `${d.success_count} ok · ${d.failed_count} failed`);

    // average routing confidence - the real quality signal
    const cEl = document.getElementById('mx-conf');
    if (d.avg_confidence != null) {
      const color = d.avg_confidence >= 0.6 ? '#22c55e'
                  : d.avg_confidence >= 0.35 ? '#f59e0b' : '#ef4444';
      if (cEl) {
        // override the gradient-text styling so a solid color shows reliably
        cEl.style.background = 'none';
        cEl.style.webkitBackgroundClip = 'initial';
        cEl.style.backgroundClip = 'initial';
        cEl.style.webkitTextFillColor = color;
        cEl.style.color = color;
        cEl.textContent = d.avg_confidence.toFixed(2);
      }
      set('mx-conf-sub', `${d.total_routings} routings · ${d.low_confidence_count} below ${d.low_confidence_threshold}`);
    } else {
      if (cEl) { cEl.style.webkitTextFillColor = '#9ca3af'; cEl.textContent = 'n/a'; }
      set('mx-conf-sub', 'no routing data yet');
    }

    bars('mx-agent-bars', d.agent_usage || {});
    bars('mx-event-bars', d.event_types || {});

    // confidence distribution histogram
    if (d.confidence_buckets) bars('mx-conf-dist', d.confidence_buckets);

    // documents by type
    if (d.docs_by_type) bars('mx-doctypes', d.docs_by_type);

    // diagram stats
    const ds = d.diagram_stats || {};
    set('mx-diagrams', ds.diagram_count ?? '-');
    set('mx-dnodes', ds.total_nodes ?? '-');
    set('mx-dedges', ds.total_edges ?? '-');
    if (ds.biggest && ds.biggest.file) {
      set('mx-diagrams-sub', `biggest: ${ds.biggest.file} (${ds.biggest.nodes} nodes)`);
    }

    const feed = document.getElementById('mx-feed');
    if (feed) {
      feed.innerHTML = (d.recent || []).map(a => `
        <div class="mx-feed-row"><span class="mx-dot ${(a.status || '').toLowerCase() === 'success' ? 'on' : 'off'}"></span>
        <span class="mx-feed-ev">${a.event || ''}</span>
        <span class="mx-feed-src">${a.source || ''}</span></div>`).join('');
    }
  }

  function bars(id, obj) {
    const el = document.getElementById(id);
    if (!el) return;
    const e = Object.entries(obj).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...e.map(x => x[1]));
    el.innerHTML = e.map(([k, v]) => `
      <div class="mx-bar-row"><span class="mx-bar-label">${k}</span>
      <div class="mx-bar-track"><div class="mx-bar-fill" style="width:${v / max * 100}%"></div></div>
      <span class="mx-bar-val">${v}</span></div>`).join('') || '<div class="mx-empty">No data yet</div>';
  }

  function set(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

  document.addEventListener('DOMContentLoaded', () => {
    const navItem = document.querySelector('[data-target="metrics"]');
    if (navItem) {
      navItem.addEventListener('click', () => {
        document.querySelectorAll('.nav-links li').forEach(n => n.classList.remove('active'));
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        navItem.classList.add('active');
        const page = document.getElementById('metrics');
        if (page) page.classList.add('active');
        loadMetrics();
      });
    }
    setInterval(() => {
      const page = document.getElementById('metrics');
      if (page && page.classList.contains('active')) loadMetrics();
    }, 10000);
  });
})();
