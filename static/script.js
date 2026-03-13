/* ═══════════════════════════════════════════════════════════
   ANSHHOSTING v4.1 — Frontend JS
   Dev: AnshAPI · t.me/crystaapi
═══════════════════════════════════════════════════════════ */

// ── TOAST SYSTEM ──────────────────────────────────────────
function toast(msg, type='info', duration=3500) {
  const colors = { success:'var(--c1)', error:'var(--c2)', warn:'var(--c3)', info:'var(--c0)' };
  const icons  = { success:'✓', error:'✕', warn:'⚠', info:'ℹ' };
  const el = document.createElement('div');
  el.style.cssText = `
    position:fixed;bottom:22px;right:22px;z-index:9999;
    background:rgba(8,16,28,0.97);border:1px solid ${colors[type]};
    border-radius:10px;padding:12px 18px;font-size:13px;font-weight:600;
    color:${colors[type]};box-shadow:0 8px 32px rgba(0,0,0,.6),0 0 20px ${colors[type]}22;
    display:flex;align-items:center;gap:9px;
    animation:toastIn .3s cubic-bezier(.34,1.4,.64,1) both;
    font-family:'Space Grotesk',sans-serif;max-width:340px;
    backdrop-filter:blur(20px);
  `;
  el.innerHTML = `<span style="font-size:16px">${icons[type]}</span> ${msg}`;
  const style = document.createElement('style');
  style.textContent = '@keyframes toastIn{from{opacity:0;transform:translateX(20px) scale(.9)}to{opacity:1;transform:none}}@keyframes toastOut{to{opacity:0;transform:translateX(16px) scale(.9)}}';
  document.head.appendChild(style);
  document.body.appendChild(el);
  setTimeout(() => { el.style.animation='toastOut .3s ease forwards'; setTimeout(()=>el.remove(),300); }, duration);
}

// ── PROJECT ACTIONS ────────────────────────────────────────
async function projAction(pid, action) {
  const btn = event?.target;
  const orig = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = '⏳'; }
  try {
    const r = await fetch(`/api/project/${pid}/${action}`, { method:'POST' });
    const d = await r.json();
    if (d.status === 'already_running') {
      toast('⚡ Already running! Use Restart to apply changes.', 'warn');
    } else if (d.status === 'started') {
      toast(`✅ Started on port :${d.port}`, 'success');
      setTimeout(() => location.reload(), 900);
    } else if (d.status === 'stopped') {
      toast('⏸ Stopped.', 'info');
      setTimeout(() => location.reload(), 700);
    } else if (d.status === 'restarted') {
      toast('↺ Restarted!', 'success');
      setTimeout(() => location.reload(), 900);
    } else if (d.error) {
      toast('❌ ' + d.error, 'error');
    } else {
      setTimeout(() => location.reload(), 800);
    }
  } catch(e) {
    toast('Network error', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
  }
}

// ── TERMINAL LIVE LOGS ─────────────────────────────────────
function initTerminal(pid) {
  const body    = document.getElementById('term-body');
  const counter = document.getElementById('log-count');
  const status  = document.getElementById('status-badge');
  const mem     = document.getElementById('mem-val');
  const uptime  = document.getElementById('uptime-val');
  if (!body || !pid) return;

  let lastIdx = 0, running = true, failCount = 0;

  function colorLine(line) {
    if (!line.trim()) return `<span class="tl-space"> </span>`;
    const esc = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    if (/\[ERR\]|\[ERROR\]|Error:|Exception:|Traceback|FAILED|✗/.test(esc))
      return `<span class="tl-err">${esc}</span>`;
    if (/\[WARN\]|Warning:|⚠/.test(esc))
      return `<span class="tl-warn">${esc}</span>`;
    if (/\[INFO\]|SETUP|✅|🚀|✓|Started|Running/.test(esc))
      return `<span class="tl-info">${esc}</span>`;
    if (/\[OUT\]/.test(esc))
      return `<span class="tl-out">${esc}</span>`;
    return `<span class="tl-def">${esc}</span>`;
  }

  async function poll() {
    if (!running) return;
    try {
      const d = await (await fetch(`/api/logs/${pid}?after=${lastIdx}`)).json();
      if (d.logs && d.logs.length) {
        const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
        d.logs.forEach(l => {
          const div = document.createElement('div');
          div.innerHTML = colorLine(l);
          body.appendChild(div);
        });
        lastIdx += d.logs.length;
        if (counter) counter.textContent = lastIdx + ' lines';
        if (atBottom) body.scrollTop = body.scrollHeight;
      }
      if (status) {
        const st = d.status;
        status.className = 'status-badge ' + (st==='running'?'running':'stopped');
        status.textContent = st==='running'?'● Running':'○ '+st.charAt(0).toUpperCase()+st.slice(1);
      }
      if (mem)    mem.textContent    = d.memory + ' MB';
      if (uptime) uptime.textContent = d.uptime;
      failCount = 0;
    } catch(e) {
      failCount++;
      if (failCount > 5) { running=false; return; }
    }
    setTimeout(poll, d?.status==='running' ? 1000 : 2500);
  }
  poll();
  document.getElementById('btn-clear')?.addEventListener('click', () => {
    body.innerHTML=''; lastIdx=0;
    if(counter) counter.textContent='0 lines';
  });
}

// ── SIDEBAR COLLAPSIBLE ────────────────────────────────────
function initSidebar() {
  const sb     = document.getElementById('sidebar');
  const toggle = document.getElementById('sb-toggle');
  if (!sb || !toggle) return;

  // Restore state
  if (localStorage.getItem('sb_collapsed') === '1') sb.classList.add('collapsed');

  toggle.addEventListener('click', () => {
    sb.classList.toggle('collapsed');
    localStorage.setItem('sb_collapsed', sb.classList.contains('collapsed') ? '1' : '0');
  });
}

function toggleSection(id) {
  const sec = document.getElementById(id);
  if (!sec) return;
  sec.classList.toggle('closed');
  localStorage.setItem('sb_sec_' + id, sec.classList.contains('closed') ? '1' : '0');
}

function initSectionStates() {
  document.querySelectorAll('.sb-section[id]').forEach(sec => {
    if (localStorage.getItem('sb_sec_' + sec.id) === '1') sec.classList.add('closed');
  });
}

// ── SIDEBAR LIVE STATS ─────────────────────────────────────
function initSidebarStats() {
  const cpuTxt  = document.getElementById('sb-cpu-txt');
  const ramTxt  = document.getElementById('sb-ram-txt');
  const cpuBar  = document.getElementById('sb-cpu-bar');
  const ramBar  = document.getElementById('sb-ram-bar');
  if (!cpuTxt) return;
  async function refresh() {
    try {
      const d = await (await fetch('/api/server/stats')).json();
      if (cpuTxt) cpuTxt.textContent = d.cpu + '%';
      if (ramTxt) ramTxt.textContent = d.ram_pct + '%';
      if (cpuBar) cpuBar.style.width = d.cpu + '%';
      if (ramBar) ramBar.style.width = d.ram_pct + '%';
    } catch {}
  }
  setInterval(refresh, 5000);
}

// ── ADMIN LIVE STATS ───────────────────────────────────────
function initAdminRefresh() {
  const statsArea = document.getElementById('admin-stats');
  if (!statsArea) return;
  async function refresh() {
    try {
      const d = await (await fetch('/api/server/stats')).json();
      const set = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
      const sw  = (id, w) => { const el = document.getElementById(id); if(el) el.style.width = w; };
      set('s-cpu',   d.cpu + '%');
      set('s-ram',   d.ram_pct + '%');
      set('s-disk',  d.disk_pct + '%');
      set('s-ram-d', d.ram_used + 'GB / ' + d.ram_total + 'GB');
      set('s-disk-d',d.disk_used + 'GB / ' + d.disk_total + 'GB');
      sw('b-cpu',  d.cpu + '%');
      sw('b-ram',  d.ram_pct + '%');
      sw('b-disk', d.disk_pct + '%');
    } catch {}
  }
  setInterval(refresh, 3000);
}

// ── COPY TO CLIPBOARD ──────────────────────────────────────
function copyText(txt, el) {
  navigator.clipboard.writeText(txt).then(() => {
    const orig = el.textContent;
    el.textContent = '✓ Copied!';
    setTimeout(() => el.textContent = orig, 1500);
    toast('Copied to clipboard!', 'success', 1800);
  }).catch(() => {
    toast('Copy failed — do it manually', 'warn');
  });
}

// ── CONFIRM DELETE ─────────────────────────────────────────
function confirmDelete(msg) {
  return confirm(msg || 'Are you sure? This cannot be undone.');
}

// ── AUTO-DISMISS FLASH ─────────────────────────────────────
function initFlashDismiss() {
  document.querySelectorAll('.alert').forEach(a => {
    setTimeout(() => {
      a.style.transition = 'opacity .5s,transform .5s';
      a.style.opacity    = '0';
      a.style.transform  = 'translateX(8px)';
      setTimeout(() => a.remove(), 500);
    }, 5500);
  });
}

// ── INIT ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initSidebar();
  initSectionStates();
  initSidebarStats();
  initAdminRefresh();
  initFlashDismiss();
});

// ── CARD MOUSE GLOW ────────────────────────────────────────
document.addEventListener('mousemove', (e) => {
  document.querySelectorAll('.card').forEach(card => {
    const rect = card.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width * 100).toFixed(1);
    const y = ((e.clientY - rect.top)  / rect.height * 100).toFixed(1);
    card.style.setProperty('--mx', x + '%');
    card.style.setProperty('--my', y + '%');
  });
});

// ── NUMBER COUNT-UP ANIMATION ──────────────────────────────
function animateCount(el, target, duration=800) {
  const start = 0;
  const step = (timestamp) => {
    if (!el._start) el._start = timestamp;
    const progress = Math.min((timestamp - el._start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    el.textContent = Math.floor(eased * target);
    if (progress < 1) requestAnimationFrame(step);
    else { el.textContent = target; delete el._start; }
  };
  requestAnimationFrame(step);
}

// Run count-up on stat values that are pure numbers
document.querySelectorAll('.stat-val').forEach(el => {
  const text = el.textContent.trim();
  const num = parseInt(text);
  if (!isNaN(num) && text === String(num) && num > 0) {
    animateCount(el, num);
  }
});

// ── SIDEBAR SECTION OPEN/CLOSE SMOOTH ─────────────────────
// Override toggleSection to animate height
function toggleSection(id) {
  const sec  = document.getElementById(id);
  const body = sec?.querySelector('.sb-sec-body');
  if (!sec || !body) return;

  if (sec.classList.contains('closed')) {
    // Open
    sec.classList.remove('closed');
    body.style.maxHeight = '0px';
    body.style.overflow  = 'hidden';
    body.style.display   = 'block';
    requestAnimationFrame(() => {
      body.style.transition = 'max-height .25s ease';
      body.style.maxHeight  = body.scrollHeight + 'px';
      setTimeout(() => { body.style.maxHeight=''; body.style.overflow=''; body.style.transition=''; }, 280);
    });
    localStorage.setItem('sb_sec_' + id, '0');
  } else {
    // Close
    body.style.maxHeight  = body.scrollHeight + 'px';
    body.style.overflow   = 'hidden';
    body.style.transition = 'max-height .22s ease';
    requestAnimationFrame(() => {
      body.style.maxHeight = '0px';
      setTimeout(() => { sec.classList.add('closed'); body.style.maxHeight=''; body.style.overflow=''; body.style.transition=''; }, 230);
    });
    localStorage.setItem('sb_sec_' + id, '1');
  }
}
