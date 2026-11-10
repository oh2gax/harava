/**
 * m-app.js — the Harava phone app /app (from 2026-11-09).
 *
 * The user interface of the WSPR Logger phone view (itself the MODE-S Wind phone view): a slim top
 * bar (☰, the view's title, the local clock, the theme button, the data's status dot), one view at a
 * time filling the screen, and every choice in the ☰ menu — the day, the views as tiles, each view's
 * options in a folding section (the shown view's opens by itself), selects marked data-chips shown as
 * tap buttons.
 *
 *   Map             the phone's path and one pin per radio (/api/devices, /api/path); a tap shows a card
 *   Day             counts, hears per 10 min (/api/timeline), the layers and the most common identifications
 *   Watch list      glasses & VR, trackers, surveillance, police, drones, hacking tools
 *   Devices         every radio with search and sorting; a tap opens Device (/api/device)
 *   Phone check     the classroom phone box (/api/devices of a window, log only)
 *   Identification  Harava's database (/api/ids)
 *   Imports         upload Fieldwatch exports from the phone (/api/import), switch or delete imports
 *   Workbench       (admins) unknown radios grouped by what they advertise (/api/wb, /api/wb/fp, /api/wb/note)
 *
 * The same data, sign-in and permissions as the full page; the helpers shared with it are in common.js.
 * Back on the page after more than an hour, or when the server has a newer build (a deploy,
 * /api/app/build), the page reloads itself; the view and the options are kept in localStorage.
 * The theme is the full page's setting (fwa_theme_mode), What's new its seen version (harava_seen_version).
 */
/* global L, Chart, CAT, CAT_COLORS, CAT_ICON, CAT_ORDER, esc, pad, fmtTime, fmtM, ownRuleHits, groupSame,
          phoneType, phoneBoxLimit, phoneZones, OWN_MATCH_TXT */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (_) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, String(v)); } catch (_) { /* private mode */ } };
  const lsJson = (k, d) => { try { const v = JSON.parse(lsGet(k, 'null')); return v && typeof v === 'object' ? Object.assign({}, d, v) : Object.assign({}, d); } catch (_) { return Object.assign({}, d); } };
  const F = window.FWA;
  const RANK = { view: 1, analyze: 2, admin: 3 };
  const can = r => RANK[F.role] >= RANK[r];
  const n0 = v => (v || v === 0) ? Number(v).toLocaleString('en-GB') : '—';
  const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  async function api(url, opts) {
    const r = await fetch(url, Object.assign({ cache: 'no-store' }, opts || {}));
    if (r.status === 401) { location.href = '/login?next=/app'; throw new Error('sign in first'); }
    const j = await r.json().catch(() => ({}));
    if (r.status === 403 && j.account) { location.href = j.account; }
    if (!r.ok) throw new Error(j.error || String(r.status));
    return j;
  }
  function toast(msg) {
    const t = $('toast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 3000);
  }
  const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  // ── views ────────────────────────────────────────────────────────────────
  const VIEWS = {
    map:    { title: 'Map',            el: 'v-map',    secs: ['map', 'filter'] },
    day:    { title: 'Day',            el: 'v-day',    secs: ['day', 'filter'] },
    watch:  { title: 'Watch list',     el: 'v-watch',  secs: ['filter'] },
    dev:    { title: 'Devices',        el: 'v-dev',    secs: ['dev', 'filter'] },
    device: { title: 'Device',         el: 'v-device', secs: [] },
    phone:  { title: 'Phone check',    el: 'v-phone',  secs: ['phone'] },
    ids:    { title: 'Identification', el: 'v-ids',    secs: ['ids'] },
    imp:    { title: 'Imports',        el: 'v-imp',    secs: ['imp'] },
    wb:     { title: 'Workbench',      el: 'v-wb',     secs: ['wb'], role: 'admin' },
    wbd:    { title: 'Unknown radio',  el: 'v-wbd',    secs: ['wb'], role: 'admin' },
  };
  const okView = v => VIEWS[v] && (!VIEWS[v].role || can(VIEWS[v].role)) && v !== 'device' && v !== 'wbd';
  let view = lsGet('harava_app_view', 'map');
  if (!okView(view)) view = 'map';

  // options, remembered on the phone
  const O = lsJson('harava_app_opts', {
    kind: 'ALL', rssi: '-200', cat: '', group: true, own: true, trav: false, named: false, sit: false,
    path: true, card: true, spread: true, keep: false, sort: 'rssi', tlcat: '',
    pcstat: 'max', pcms: false, pcun: false, wbtab: 'unknown', wbst: '',
  });
  const saveO = () => lsSet('harava_app_opts', JSON.stringify(O));

  // the data of the chosen day; window.S is read by privacy.js (Mask addresses)
  const S = { summary: null, days: [], day: lsGet('harava_app_day', ''), dayTo: '', scope: '', devices: [], hears: 0, path: [], own: [],
              loadedFor: null, sel: null, device: null };
  window.S = S;

  // ── menu (the WSPR Logger phone view's: Back closes the menu or a sheet, otherwise the previous view) ──
  const drawer = $('app-drawer'), backdrop = $('app-backdrop');
  const hist = () => (history.state && history.state.app) ? history.state : {};
  function histPush(state) {
    try { if (hist().app === 'menu') history.replaceState(state, ''); else history.pushState(state, ''); } catch (_) { /* */ }
  }
  function menu(open, fromHistory) {
    const was = drawer.classList.contains('open');
    drawer.classList.toggle('open', open);
    backdrop.hidden = !open;
    drawer.setAttribute('aria-hidden', String(!open));
    $('app-menu-btn').setAttribute('aria-expanded', String(open));
    if (open && !was && hist().app !== 'menu') { try { history.pushState({ app: 'menu', view }, ''); } catch (_) { /* */ } }
    if (!open && was && !fromHistory && hist().app === 'menu') { try { history.back(); } catch (_) { /* */ } }
  }
  $('app-menu-btn').addEventListener('click', () => menu(!drawer.classList.contains('open')));
  $('app-menu-close').addEventListener('click', () => menu(false));
  backdrop.addEventListener('click', () => menu(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') menu(false); });

  // chips: a select marked data-chips is shown as a row of tap buttons
  const chipSels = [];
  function chipify(sel) {
    const box = document.createElement('div');
    box.className = 'app-chips'; box.setAttribute('role', 'group');
    for (const o of sel.options) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'app-chip'; b.dataset.v = o.value; b.textContent = o.textContent;
      b.addEventListener('click', () => {
        if (sel.value !== o.value) { sel.value = o.value; sel.dispatchEvent(new Event('change', { bubbles: true })); }
        syncChip(sel);
      });
      box.appendChild(b);
    }
    sel.hidden = true; sel.after(box); sel._chips = box;
    sel.addEventListener('change', () => syncChip(sel));
    chipSels.push(sel);
    syncChip(sel);
  }
  function syncChip(sel) {
    for (const b of sel._chips.children) { const on = b.dataset.v === sel.value; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }
  }
  document.querySelectorAll('select[data-chips]').forEach(chipify);
  function bindSel(id, key, onChange) {
    const sel = $(id); if (!sel) return;
    if ([...sel.options].some(o => o.value === String(O[key]))) sel.value = String(O[key]);
    if (sel._chips) syncChip(sel);
    sel.addEventListener('change', () => { O[key] = sel.value; saveO(); onChange(sel.value); });
  }
  function bindSwitch(id, key, onChange) {
    const cb = $(id); if (!cb) return;
    cb.checked = !!O[key];
    cb.addEventListener('change', () => { O[key] = cb.checked; saveO(); onChange(cb.checked); });
  }

  // ── charts ───────────────────────────────────────────────────────────────
  const charts = {};
  function mkChart(id, cfg) {
    if (charts[id]) charts[id].destroy();
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, cfg.options || {});
    charts[id] = new Chart($(id).getContext('2d'), cfg);
    return charts[id];
  }
  const axis = () => ({ ticks: { color: cssVar('--text-secondary'), font: { size: 10 } }, grid: { color: cssVar('--border') } });

  // ── maps ─────────────────────────────────────────────────────────────────
  function makeTiles(dark) {
    const key = F.cartoApiKey ? `?key=${encodeURIComponent(F.cartoApiKey)}` : '';
    return dark
      ? L.tileLayer(`https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png${key}`, { attribution: '© OpenStreetMap © CARTO', maxZoom: 20, subdomains: 'abcd' })
      : L.tileLayer(`https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png${key}`, { attribution: '© OpenStreetMap © CARTO', maxZoom: 20, subdomains: 'abcd' });
  }
  const maps = [];
  function mkMap(id, home) {
    const map = L.map(id, { center: [60.17, 24.94], zoom: 11, zoomControl: false, preferCanvas: true });
    const m = { map, tiles: makeTiles(isDark()).addTo(map) };
    L.control.zoom({ position: 'topleft' }).addTo(map);
    const Home = L.Control.extend({
      options: { position: 'topleft' },
      onAdd() {
        const bar = L.DomUtil.create('div', 'leaflet-bar');
        const a = L.DomUtil.create('a', 'm-home', bar);
        a.href = '#'; a.title = 'Show all'; a.setAttribute('role', 'button'); a.textContent = '⌂';
        L.DomEvent.disableClickPropagation(bar);
        L.DomEvent.on(a, 'click', e => { L.DomEvent.preventDefault(e); home(); });
        return bar;
      },
    });
    new Home().addTo(map);
    maps.push(m);
    return map;
  }
  const rssiRadius = r => (r == null ? 4 : Math.max(3, Math.min(9, 3 + (r + 100) / 9)));

  // ── theme (the full page's setting) ─────────────────────────────────────
  const themeBtn = $('app-theme-btn');
  const themeMq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  const THEME_LBL = { dark: 'Dark', light: 'Light', auto: 'Auto — follows the phone' };
  let themeMode = (() => { const m = lsGet('fwa_theme_mode', 'auto'); return m === 'dark' || m === 'light' ? m : 'auto'; })();
  const isDark = () => !document.documentElement.classList.contains('light');
  function applyTheme(redraw) {
    const root = document.documentElement, was = isDark();
    const light = themeMode === 'auto' ? !!(themeMq && themeMq.matches) : themeMode === 'light';
    root.classList.toggle('light', light);
    root.dataset.themeMode = themeMode;
    const tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.content = light ? '#f1f5f9' : '#111827';
    const lbl = `Theme: ${THEME_LBL[themeMode]}${themeMode === 'auto' ? ` (now ${light ? 'light' : 'dark'})` : ''} — tap to change`;
    themeBtn.title = lbl; themeBtn.setAttribute('aria-label', lbl);
    if (redraw && was !== isDark()) {
      for (const m of maps) { m.map.removeLayer(m.tiles); m.tiles = makeTiles(isDark()).addTo(m.map); }
      redrawCharts();
    }
  }
  let themeNoteT = null;
  function themeNote(txt) {
    const n = $('app-theme-note');
    n.textContent = txt; n.hidden = false; n.classList.remove('fade');
    clearTimeout(themeNoteT);
    themeNoteT = setTimeout(() => { n.classList.add('fade'); themeNoteT = setTimeout(() => { n.hidden = true; }, 300); }, 1600);
  }
  applyTheme(false);
  themeBtn.addEventListener('click', () => {
    themeMode = { dark: 'light', light: 'auto', auto: 'dark' }[themeMode];
    lsSet('fwa_theme_mode', themeMode);
    applyTheme(true);
    themeNote(`Theme: ${THEME_LBL[themeMode]}`);
  });
  if (themeMq) {
    const onMq = () => { if (themeMode === 'auto') applyTheme(true); };
    if (themeMq.addEventListener) themeMq.addEventListener('change', onMq); else if (themeMq.addListener) themeMq.addListener(onMq);
  }
  function redrawCharts() {
    if (view === 'day' && S.timeline) drawTimeline();
    if (view === 'device' && S.device) drawDevice();
    if (view === 'phone') runPhone();
  }

  // ══ Data ════════════════════════════════════════════════════════════════
  function rangeMs() {
    if (!S.day || S.day === 'all') return [null, null];
    const [y, m, d] = S.day.split('-').map(Number);
    const [y2, m2, d2] = (S.dayTo && S.dayTo > S.day ? S.dayTo : S.day).split('-').map(Number);   // Until: several days
    return [new Date(y, m - 1, d).getTime(), new Date(y2, m2 - 1, d2 + 1).getTime()];
  }
  function rangeQuery() { const [a, b] = rangeMs(); return (a ? `from=${a}` : '') + (b ? `&to=${b}` : ''); }
  const shortDay = x => new Date(x + 'T12:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const dayLabel = () => (!S.day || S.day === 'all') ? 'All days'
    : S.dayTo ? `${shortDay(S.day)} – ${shortDay(S.dayTo)} ${S.dayTo.slice(0, 4)}`
    : new Date(S.day + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  const wide = () => S.day === 'all' || !!S.dayTo;
  const whenTxt = t => wide() ? fmtTime(t, true).slice(5, 16).replace('-', '.').replace(' ', ' ') : fmtTime(t).slice(0, 5);

  // whose data (the server keeps the choice in the session, shared with the full site)
  async function loadScope() {
    const r = await api('/api/scope');
    S.scope = r.scope;
    $('m-scope').innerHTML = r.options.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('');
    $('m-scope').value = r.scope;
    $('m-scope-row').hidden = r.options.length < 2;
  }
  $('m-scope').addEventListener('change', async e => {
    try { await post('/api/scope', { scope: e.target.value }); } catch (err) { toast(err.message); }
    S.scope = e.target.value; await loadSummary(); loadView(true);
  });
  function fillDayTo() {
    const days = S.days.map(d => d.day).filter(d => S.day !== 'all' && d > S.day);
    if (S.dayTo && !days.includes(S.dayTo)) S.dayTo = '';
    $('m-day-to').innerHTML = '<option value="">— the same day —</option>' + days.map(d => `<option value="${d}">${d}</option>`).join('');
    $('m-day-to').value = S.dayTo;
    $('m-day-to').disabled = S.day === 'all' || !days.length;
  }
  $('m-day-to').addEventListener('change', e => { S.dayTo = e.target.value; $('app-sub').textContent = dayLabel(); loadView(true); });

  async function loadSummary() {
    try { await loadScope(); } catch (_) { /* older server */ }
    S.summary = await api('/api/summary');
    S.days = S.summary.days || [];
    const sel = $('m-day');
    sel.innerHTML = '<option value="all">All days</option>' + [...S.days].reverse()
      .map(d => `<option value="${d.day}">${d.day} · ${n0(d.hears)} hears</option>`).join('');
    if (S.day !== 'all' && !S.days.some(d => d.day === S.day)) S.day = S.days.length ? S.days[S.days.length - 1].day : 'all';
    sel.value = S.day;
    fillDayTo();
    setHealth();
  }
  async function loadData(force) {
    const key = `${S.day}|${S.dayTo}|${S.scope}`;
    if (!force && S.loadedFor === key) return;
    const q = rangeQuery();
    const [dv, pa, own] = await Promise.all([api('/api/devices?' + q), api('/api/path?' + q), api('/api/own')]);
    S.devices = dv.devices; S.hears = dv.hears; S.path = pa.parts; S.own = own.own || [];
    S.devices.forEach(d => { const r = S.own.find(x => ownRuleHits(x, d)); d.own = r ? r.label : ''; });
    S.loadedFor = key; S.sel = null; fitDone = false;
    if (window.haravaMask && window.haravaMask.on) setTimeout(() => window.haravaMask.refresh(), 0);
  }
  function setDay(v) {
    S.day = v; lsSet('harava_app_day', v); $('m-day').value = v;
    if (S.dayTo && (v === 'all' || S.dayTo <= v)) S.dayTo = '';
    fillDayTo();
    $('app-sub').textContent = dayLabel();
    loadView(true);
  }
  $('m-day').addEventListener('change', e => setDay(e.target.value));
  function stepDay(n) {
    const days = S.days.map(d => d.day);
    if (!days.length) return;
    let i = days.indexOf(S.day);
    const span = S.dayTo ? days.indexOf(S.dayTo) - i : 0;              // keep the length of a range of days
    i = i < 0 ? days.length - 1 : Math.min(days.length - 1 - Math.max(0, span), Math.max(0, i + n));
    S.dayTo = span > 0 ? days[i + span] : '';
    setDay(days[i]);
  }
  $('m-day-prev').addEventListener('click', () => stepDay(-1));
  $('m-day-next').addEventListener('click', () => stepDay(1));

  function setHealth() {
    const el = $('app-health'), last = S.summary && S.summary.last;
    el.classList.remove('st-ok', 'st-warn', 'st-err');
    if (!last) { el.title = 'No data yet'; $('app-health-txt').textContent = 'No data imported yet.'; return; }
    const h = (Date.now() - last) / 3600e3;
    el.classList.add(h < 6 ? 'st-ok' : (h < 48 ? 'st-warn' : 'st-err'));
    const txt = `Newest data: ${fmtTime(last, true).slice(0, 16)} (${h < 1 ? Math.round(h * 60) + ' min' : h < 48 ? Math.round(h) + ' h' : Math.round(h / 24) + ' days'} ago)`;
    el.title = txt; $('app-health-txt').textContent = txt;
  }

  // the filter of the Filter section (as the full page's sidebar)
  function filtered(skipCat) {
    const minR = parseFloat(O.rssi);
    let list = S.devices.filter(d => {
      if (O.own && d.own) return false;
      if (O.kind !== 'ALL' && d.kind !== O.kind) return false;
      if (!skipCat && O.cat && d.category !== O.cat) return false;
      if (minR > -200 && (d.rssi_max ?? -999) < minR) return false;
      if (O.trav && !d.travelled) return false;
      if (O.named && !(d.name || d.fleets || d.ident)) return false;
      if (O.sit && !(d.in_sit && !d.log_rows)) return false;
      return true;
    });
    return O.group ? groupSame(list) : list;
  }
  function fillCatSelect(id, firstLabel, counts) {
    const sel = $(id), cur = sel.value || (id === 'm-f-cat' ? O.cat : O.tlcat);
    sel.innerHTML = `<option value="">${firstLabel}</option>` + CAT_ORDER.filter(k => counts[k] || k === cur)
      .map(k => `<option value="${esc(k)}">${CAT_ICON[k] ? CAT_ICON[k] + ' ' : ''}${esc(k)} (${counts[k] || 0})</option>`).join('');
    sel.value = cur;
  }
  function refreshCatSelects() {
    const c = {}; filtered(true).forEach(d => { c[d.category] = (c[d.category] || 0) + 1; });
    fillCatSelect('m-f-cat', 'All layers', c);
    const all = {}; S.devices.forEach(d => { all[d.category] = (all[d.category] || 0) + 1; });
    fillCatSelect('m-o-tlcat', 'All radios', all);
  }
  $('m-f-cat').addEventListener('change', e => { O.cat = e.target.value; saveO(); redraw(); });
  $('m-o-tlcat').addEventListener('change', e => { O.tlcat = e.target.value; saveO(); if (view === 'day') loadTimeline(); });
  bindSel('m-f-kind', 'kind', () => redraw());
  bindSel('m-f-rssi', 'rssi', () => redraw());
  ['group', 'own', 'trav', 'named', 'sit'].forEach(k => bindSwitch('m-f-' + ({ group: 'group', own: 'own', trav: 'trav', named: 'named', sit: 'sit' }[k]), k, () => redraw()));
  bindSwitch('m-o-path', 'path', () => drawMap());
  bindSwitch('m-o-card', 'card', () => drawMap());
  bindSwitch('m-o-spread', 'spread', () => drawMap());
  bindSwitch('m-o-keep', 'keep', () => {});
  bindSel('m-o-sort', 'sort', () => drawDevices());
  bindSel('m-o-pcstat', 'pcstat', () => runPhone());
  bindSwitch('m-o-pcms', 'pcms', () => runPhone());
  bindSwitch('m-o-pcun', 'pcun', () => runPhone());
  bindSel('m-o-wbtab', 'wbtab', () => loadWb(true));
  bindSel('m-o-wbst', 'wbst', () => drawWb());
  function redraw() {
    refreshCatSelects();
    if (view === 'map') drawMap();
    else if (view === 'day') drawDay();
    else if (view === 'watch') drawWatch();
    else if (view === 'dev') drawDevices();
  }

  // a row for a radio (Devices, Watch list, Phone check)
  function tagsOf(d) {
    const t = [];
    if (d.own) t.push('<span class="tg acc">★</span>');
    if (d._n > 1) t.push(`<span class="tg">${d._n} addr.</span>`);
    if (d.tracker_mode === 'separated') t.push('<span class="tg red">separated</span>');
    if (d.travelled) t.push('<span class="tg amb">travelled</span>');
    if (d.phones > 1) t.push(`<span class="tg">${d.phones} phones</span>`);
    if (d.vr) t.push('<span class="tg">VR?</span>');
    if (d.in_sit && !d.log_rows) t.push('<span class="tg">sit only</span>');
    if (d.sensor) t.push(`<span class="tg grn">${d.sensor.temp_c.toFixed(1)} °C</span>`);
    return t.join(' ');
  }
  function devRow(d, extra) {
    const name = d.name || (d.kind === 'WIFI' ? '(hidden network)' : '(no name)');
    const what = d.ident || d.fleets || d.vendor || '';
    return `<div class="app-card m-dev${d.own ? ' own' : ''}"><button type="button" class="hd" data-k="${d.kind}" data-m="${esc(d.mac)}">
      <div class="l1"><span class="cdot" style="background:${CAT_COLORS[d.category] || '#94a3b8'}"></span><b>${CAT_ICON[d.category] ? CAT_ICON[d.category] + ' ' : ''}${esc(name)}</b>
        <span class="rssi">${d.rssi_max ?? '?'} dBm</span></div>
      <div class="l2">${esc(d.category)}${what ? ' · ' + esc(what) : ''} · ${d.hears} hears · ${whenTxt(d.last)}</div>
      <div class="l2"><span class="mono">${esc(d.mac)}</span> ${tagsOf(d)}${extra || ''}</div></button></div>`;
  }
  function bindRows(box) {
    box.querySelectorAll('button.hd[data-m]').forEach(b => b.addEventListener('click', () => openDevice(b.dataset.k, b.dataset.m)));
  }

  // ══ Map ═════════════════════════════════════════════════════════════════
  let lmap = null, pinLayer = null, pathLayer = null, selLayer = null, fitDone = false;
  function fitAll() {
    const pts = [];
    S.path.forEach(p => p.forEach(q => pts.push([q[0], q[1]])));
    filtered().forEach(d => { if (d.lat != null) pts.push([d.lat, d.lon]); });
    if (pts.length) lmap.fitBounds(L.latLngBounds(pts).pad(0.08), { maxZoom: 17 });
  }
  function initMap() {
    if (lmap) return;
    lmap = mkMap('m-map', fitAll);
    pathLayer = L.layerGroup().addTo(lmap); pinLayer = L.layerGroup().addTo(lmap); selLayer = L.layerGroup().addTo(lmap);
    lmap.on('moveend', () => { if (O.keep) lsSet('harava_app_pos', JSON.stringify([lmap.getCenter().lat, lmap.getCenter().lng, lmap.getZoom()])); });
    lmap.on('click', () => selectPin(null));
  }
  function spread(list) {
    const groups = new Map(), out = new Map();
    list.forEach(d => { if (d.lat == null) return; const k = d.lat.toFixed(4) + ',' + d.lon.toFixed(4); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(d); });
    groups.forEach(arr => arr.forEach((d, i) => {
      if (!O.spread || arr.length === 1) { out.set(d, [d.lat, d.lon]); return; }
      const r = 3.2 * Math.sqrt(i + 0.5), a = i * 2.39996;
      out.set(d, [d.lat + (r * Math.cos(a)) / 111320, d.lon + (r * Math.sin(a)) / (111320 * Math.cos(d.lat * Math.PI / 180))]);
    }));
    return out;
  }
  function drawMap() {
    if (!lmap) return;
    const list = filtered();
    pathLayer.clearLayers(); pinLayer.clearLayers();
    if (O.path) S.path.forEach(p => { if (p.length > 1) L.polyline(p.map(q => [q[0], q[1]]), { color: '#38bdf8', weight: 2, opacity: 0.7, dashArray: '5 6' }).addTo(pathLayer); });
    const pos = spread(list), canvas = L.canvas({ padding: 0.3 });
    [...list].sort((a, b) => (a.rssi_max ?? -999) - (b.rssi_max ?? -999)).forEach(d => {
      const p = pos.get(d); if (!p) return;
      const col = CAT_COLORS[d.category] || '#94a3b8', icon = CAT_ICON[d.category], wifi = d.kind === 'WIFI';
      const m = icon
        ? L.marker(p, { icon: L.divIcon({ className: '', iconSize: [26, 26], iconAnchor: [13, 13],
            html: `<div class="emo-pin${d.tracker_mode === 'separated' ? ' alert' : ''}" style="border-color:${col}">${icon}</div>` }) })
        : L.circleMarker(p, { renderer: canvas, radius: rssiRadius(d.rssi_max) + 1, color: col, weight: wifi ? 2 : 1, fillColor: col, fillOpacity: wifi ? 0.12 : 0.75 });
      m.on('click', e => { L.DomEvent.stopPropagation(e); selectPin(d); });
      m.addTo(pinLayer);
    });
    $('m-stat-card').hidden = !O.card;
    $('m-st-day').textContent = dayLabel();
    $('m-st-time').textContent = S.summary && S.summary.last ? 'newest ' + fmtTime(S.summary.last, true).slice(5, 16) : '';
    $('m-st-dev').textContent = n0(list.length);
    $('m-st-wifi').textContent = n0(list.filter(d => d.kind === 'WIFI').length);
    $('m-st-ble').textContent = n0(list.filter(d => d.kind === 'BLE').length);
    const w = list.filter(d => CAT_ICON[d.category]).length;
    $('m-st-watch').innerHTML = w ? `<b>${w}</b> watch list` : '';
    if (!fitDone) {
      fitDone = true;
      const saved = O.keep ? JSON.parse(lsGet('harava_app_pos', 'null') || 'null') : null;
      if (saved) lmap.setView([saved[0], saved[1]], saved[2]); else fitAll();
    }
    if (S.sel) selectPin(list.find(d => d.kind === S.sel.kind && d.mac === S.sel.mac) || null);
  }
  function selectPin(d) {
    S.sel = d; selLayer.clearLayers();
    const card = $('m-sel-card');
    if (!d) { card.hidden = true; return; }
    $('m-sel-dot').style.background = CAT_COLORS[d.category] || '#94a3b8';
    $('m-sel-name').textContent = (d.own ? '★ ' : '') + (d.name || d.mac);
    $('m-sel-rssi').textContent = `${d.rssi_max ?? '?'} dBm`;
    $('m-sel-cat').textContent = `${d.kind === 'WIFI' ? 'Wi-Fi' : 'BLE'} · ${d.category}`;
    $('m-sel-ident').textContent = d.ident || d.fleets || '';
    card.hidden = false;
    if (d.lat != null) L.circleMarker([d.lat, d.lon], { radius: 14, color: '#f59e0b', weight: 2, fill: false }).addTo(selLayer);
  }
  $('m-sel-card').addEventListener('click', e => {
    if (e.target.id === 'm-sel-x') { selectPin(null); return; }
    if (S.sel) openDevice(S.sel.kind, S.sel.mac);
  });
  async function loadMap() { initMap(); await loadData(); refreshCatSelects(); drawMap(); setTimeout(() => lmap.invalidateSize(), 60); }

  // ══ Day ═════════════════════════════════════════════════════════════════
  async function loadDay() { await loadData(); refreshCatSelects(); drawDay(); loadTimeline(); }
  function drawDay() {
    const list = filtered();
    $('m-day-t').textContent = dayLabel();
    const ble = list.filter(d => d.kind === 'BLE');
    $('m-d-dev').textContent = n0(list.length); $('m-d-hears').textContent = n0(S.hears);
    $('m-d-wifi').textContent = n0(list.length - ble.length); $('m-d-ble').textContent = n0(ble.length);
    $('m-d-ident').textContent = ble.length ? Math.round(100 * ble.filter(d => d.category !== 'Unidentified').length / ble.length) + ' %' : '—';
    const trk = list.filter(d => d.category === 'Tracker');
    $('m-d-trk').textContent = n0(trk.length);
    const sep = trk.filter(d => d.tracker_mode === 'separated').length;
    $('m-d-sep').textContent = sep ? `${sep} separated` : '';
    $('m-d-gl').textContent = n0(list.filter(d => d.category === 'Glasses & VR').length);
    $('m-d-trav').textContent = n0(list.filter(d => d.travelled).length);
    const ts = list.map(d => d.first).filter(Boolean), te = list.map(d => d.last).filter(Boolean);
    $('m-d-span').textContent = ts.length ? `${fmtTime(Math.min(...ts)).slice(0, 5)}–${fmtTime(Math.max(...te)).slice(0, 5)}` : '—';
    const c = {}; list.forEach(d => { c[d.category] = (c[d.category] || 0) + 1; });
    const ks = CAT_ORDER.filter(k => c[k]), max = Math.max(1, ...ks.map(k => c[k]));
    $('m-day-cats').innerHTML = ks.map(k => `<div class="m-cbar" data-cat="${esc(k)}"><span>${CAT_ICON[k] ? CAT_ICON[k] + ' ' : ''}${esc(k)}</span>
      <div class="bw"><i style="width:${100 * c[k] / max}%;background:${CAT_COLORS[k]}"></i></div><span class="n">${c[k]}</span></div>`).join('') || '<div class="m-empty">No radios.</div>';
    $('m-day-cats').querySelectorAll('.m-cbar').forEach(r => r.addEventListener('click', () => {
      O.cat = r.dataset.cat; saveO(); $('m-f-cat').value = O.cat; setView('dev');
    }));
    const ic = {}; list.forEach(d => { if (d.ident) ic[d.ident] = (ic[d.ident] || 0) + 1; });
    const top = Object.entries(ic).sort((a, b) => b[1] - a[1]).slice(0, 10), imax = Math.max(1, ...top.map(x => x[1]));
    $('m-day-ident').innerHTML = top.map(([k, n]) => `<div class="m-cbar" data-q="${esc(k)}"><span>${esc(k)}</span>
      <div class="bw"><i style="width:${100 * n / imax}%;background:var(--accent)"></i></div><span class="n">${n}</span></div>`).join('') || '<div class="m-empty">Nothing identified.</div>';
    $('m-day-ident').querySelectorAll('.m-cbar').forEach(r => r.addEventListener('click', () => {
      $('m-dev-q').value = r.dataset.q; setView('dev');
    }));
  }
  async function loadTimeline() {
    S.timeline = await api('/api/timeline?' + rangeQuery() + (O.tlcat ? '&cat=' + encodeURIComponent(O.tlcat) : ''));
    drawTimeline();
  }
  function drawTimeline() {
    const tl = S.timeline; if (!tl) return;
    const bin = { 600000: '10 min', 3600000: 'hour', 86400000: 'day' }[tl.bin_ms] || '';
    $('m-tl-t').textContent = `Hears per ${bin || '10 min'}`;
    $('m-tl-sub').textContent = O.tlcat ? O.tlcat + ' only' : '';
    $('m-tl-note').innerHTML = [O.tlcat ? (tl.hears ? `${tl.hears} hear(s)` : '<span style="color:var(--amber)">none heard</span>') : '',
      tl.sit_only ? `${tl.sit_only} only in Sit` : '', btGapNote(tl)].filter(Boolean).join(' · ');
    const labels = tl.bins.map(b => { const d = new Date(b.t); return tl.bin_ms >= 86400000 ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}` : `${pad(d.getHours())}:${pad(d.getMinutes())}`; });
    const ax = axis();
    mkChart('m-tl-c', {
      data: { labels, datasets: [
        { type: 'bar', label: 'Wi-Fi', data: tl.bins.map(b => b.WIFI), backgroundColor: 'rgba(96,165,250,.75)', stack: 's', yAxisID: 'y' },
        { type: 'bar', label: 'Bluetooth', data: tl.bins.map(b => b.BLE), backgroundColor: 'rgba(74,222,128,.75)', stack: 's', yAxisID: 'y' },
        { type: 'line', label: 'New', data: tl.bins.map(b => b.new), borderColor: cssVar('--accent'), borderWidth: 1.5, pointRadius: 0, tension: .3, yAxisID: 'y2' },
      ] },
      plugins: [BT_GAP_PLUGIN],
      options: { plugins: { legend: { labels: { color: cssVar('--text-secondary'), boxWidth: 10, font: { size: 10 } } },
                            tooltip: { mode: 'index', intersect: false, callbacks: { footer: items => items.length ? btGapFooter(tl, items[0].dataIndex) : '' } },
                            btGaps: { tl, color: cssVar('--amber') } },
        scales: { x: Object.assign(ax, { stacked: true, ticks: Object.assign(ax.ticks, { maxTicksLimit: 8 }) }), y: Object.assign(axis(), { stacked: true }),
                  y2: Object.assign(axis(), { position: 'right', grid: { display: false } }) } },
    });
  }

  // ══ Watch list ══════════════════════════════════════════════════════════
  async function loadWatch() { await loadData(); refreshCatSelects(); drawWatch(); }
  function drawWatch() {
    const list = filtered(true);
    const watch = CAT.filter(c => c[0] === 'Watch list').map(c => c[1]);
    $('m-watch').innerHTML = watch.map(k => {
      const ds = list.filter(d => d.category === k)
        .sort((a, b) => (b.tracker_mode === 'separated') - (a.tracker_mode === 'separated') || (b.rssi_max ?? -999) - (a.rssi_max ?? -999));
      const sep = ds.filter(d => d.tracker_mode === 'separated').length;
      return `<div class="m-card" style="margin-bottom:8px"><div class="m-card-hd">${CAT_ICON[k]} ${esc(k)} <span class="m-hd-r">${ds.length ? ds.length + (sep ? ` · <b style="color:var(--red)">${sep} separated</b>` : '') : 'none heard'}</span></div>
        <div class="app-hist-list">${ds.slice(0, 40).map(d => devRow(d)).join('')}${ds.length > 40 ? `<div class="m-small">… and ${ds.length - 40} more in Devices.</div>` : ''}</div></div>`;
    }).join('') + '<div class="m-small" style="margin:0 4px">A tracker that is <i>separated</i> from its owner and heard in several places along your way deserves a closer look; one <i>near its owner</i> is normal. Smart glasses usually advertise without a name.</div>';
    bindRows($('m-watch'));
  }

  // ══ Devices ═════════════════════════════════════════════════════════════
  let devLimit = 150;
  async function loadDevices() { await loadData(); refreshCatSelects(); drawDevices(); }
  function drawDevices() {
    const q = $('m-dev-q').value.trim().toLowerCase();
    let list = filtered();
    if (q) list = list.filter(d => `${d.name} ${d.group_name || ''} ${d.mac} ${d.vendor} ${d.fleets} ${d.category} ${d.ident || ''} ${d.company || ''}`.toLowerCase().includes(q));
    const sorters = {
      rssi: (a, b) => (b.rssi_max ?? -999) - (a.rssi_max ?? -999), hears: (a, b) => b.hears - a.hears, last: (a, b) => b.last - a.last,
      name: (a, b) => (a.name || '~').localeCompare(b.name || '~'), cat: (a, b) => CAT_ORDER.indexOf(a.category) - CAT_ORDER.indexOf(b.category) || (b.rssi_max ?? -999) - (a.rssi_max ?? -999),
    };
    list.sort(sorters[O.sort] || sorters.rssi);
    $('m-dev-n').textContent = `${n0(list.length)}${O.cat ? ' · ' + O.cat : ''}`;
    $('m-dev-list').innerHTML = list.slice(0, devLimit).map(d => devRow(d)).join('') +
      (list.length > devLimit ? `<button type="button" class="app-btn m-more" id="m-dev-more">Show ${Math.min(150, list.length - devLimit)} more (${n0(list.length - devLimit)} left)</button>` : '') +
      (!list.length ? '<div class="m-empty">No radios match. Check the Filter in ☰.</div>' : '');
    bindRows($('m-dev-list'));
    const more = $('m-dev-more');
    if (more) more.addEventListener('click', () => { devLimit += 150; drawDevices(); });
  }
  let qT = null;
  $('m-dev-q').addEventListener('input', () => { clearTimeout(qT); qT = setTimeout(() => { devLimit = 150; drawDevices(); }, 200); });

  // ══ Device ══════════════════════════════════════════════════════════════
  let dmap = null, dLayer = null;
  async function openDevice(kind, mac) {
    S.device = null;
    setView('device');
    $('m-dv-card').innerHTML = '<div class="m-empty">Loading…</div>';
    const r = await api(`/api/device?kind=${encodeURIComponent(kind)}&mac=${encodeURIComponent(mac)}&${rangeQuery()}`);
    const base = S.devices.find(x => x.kind === kind && x.mac === mac);
    if (r.device && base) { r.device.group_size = base.group_size; r.device.group_name = base.group_name; r.device.group = base.group; r.device.own = base.own; }
    S.device = r;
    drawDevice();
  }
  function drawDevice() {
    const r = S.device; if (!r) return;
    const d = r.device;
    if (!d) { $('m-dv-card').innerHTML = '<div class="m-empty">This radio was not heard on the chosen day.</div>'; return; }
    const col = CAT_COLORS[d.category] || '#94a3b8';
    const own = S.own.find(x => ownRuleHits(x, d));
    const conf = { high: '', medium: ' ·', low: ' ?' }[d.ident_conf] || '';
    $('m-dv-card').innerHTML = `
      <div class="m-dev-name">${CAT_ICON[d.category] ? CAT_ICON[d.category] + ' ' : ''}${esc(d.name || (d.kind === 'WIFI' ? '(hidden network)' : '(no name)'))}</div>
      <div class="m-dev-mac">${esc(d.mac)}</div>
      <div class="m-pills"><span class="m-pill" style="background:${col}">${esc(d.category)}</span><span class="m-pill ghost">${d.kind === 'WIFI' ? 'Wi-Fi ' + esc(d.band || '') : 'Bluetooth LE'}</span>${d.travelled ? `<span class="m-pill" style="background:#d97706">travelled ${fmtM(d.spread_m)}</span>` : ''}${d.tracker_mode ? `<span class="m-pill" style="background:${d.tracker_mode === 'separated' ? '#dc2626' : '#16a34a'}">${esc(d.tracker_mode)}</span>` : ''}</div>
      <div class="m-kv">
        ${d.ident ? `<span>Identified as</span><b class="id-${d.ident_conf === 'low' ? 'low' : 'high'}">${esc(d.ident)}${conf}</b>` : ''}
        ${d.company ? `<span>Company</span><b>${esc(d.company)}</b>` : ''}
        <span>Signatures</span><b>${esc(d.fleets || '–')}${d.reclass ? ' <span style="color:var(--text-muted);font-weight:400">· layer by Harava</span>' : ''}</b>
        <span>Vendor</span><b>${esc(d.vendor || '–')}</b>
        ${(d.group_size || 1) > 1 ? `<span>Same device</span><b>${d.group_size} addresses${d.group_name ? ' · ' + esc(d.group_name) : ''}</b>` : ''}
        ${d.sensor ? `<span>Sensor</span><b>${d.sensor.temp_c.toFixed(1)} °C · ${d.sensor.humidity.toFixed(0)} % · ${d.sensor.pressure_hpa} hPa · ${(d.sensor.battery_mv / 1000).toFixed(2)} V</b>` : ''}
        <span>Hears</span><b>${d.hears}${d.in_sit ? ` (log ${d.log_rows} · Sit ${d.sit_hits})` : ''}${d.phones > 1 ? ` · by ${d.phones} phones` : ''}</b>
        <span>First / last</span><b>${fmtTime(d.first)} – ${fmtTime(d.last)}</b>
        <span>RSSI max / med</span><b>${d.rssi_max ?? '?'} / ${d.rssi_med ?? '?'} dBm</b>
        ${d.kind === 'WIFI' ? `<span>Channel</span><b>${esc(d.channel)} · ${d.freq} MHz</b>` : ''}
        <span>Places</span><b>${d.places} · spread ${fmtM(d.spread_m)}</b>
        ${d.mfg ? `<span>Company ID</span><b class="mono">0x${esc(d.mfg)}</b>` : ''}
        ${d.uuids ? `<span>Services</span><b class="mono">${esc(d.uuids.replace(/0000(\w{4})-0000-1000-8000-00805F9B34FB/gi, '$1'))}</b>` : ''}
        ${d.raw ? `<span>Raw</span><b class="mono">${esc(d.raw)}</b>` : ''}
        <span>Days seen</span><b>${(r.days_seen || []).join(', ')}</b>
      </div>
      ${d.ident_note ? `<div class="m-note">${esc(d.ident_note)}</div>` : ''}
      ${d.vr ? '<div class="m-note warn">The name says this is probably a VR headset, not glasses.</div>' : ''}
      ${d.in_sit && !d.log_rows ? '<div class="m-note warn">Heard only in the Sit export: the log missed it.</div>' : ''}
      ${own ? `<div class="m-note warn">★ My device: <b>${esc(own.label)}</b></div>` : ''}
      <div class="m-btn-row"><button type="button" class="app-btn" id="m-dv-onmap">Show on map</button>
        ${own ? '' : '<button type="button" class="app-btn needs-analyze" id="m-dv-mine">＋ My device</button>'}</div>`;
    $('m-dv-onmap').addEventListener('click', () => {
      setView('map');
      setTimeout(() => {
        const t = filtered().find(x => x.kind === d.kind && x.mac === d.mac) || d;
        if (t.lat != null) { lmap.setView([t.lat, t.lon], Math.max(lmap.getZoom(), 17)); selectPin(t); }
      }, 120);
    });
    const mine = $('m-dv-mine');
    if (mine) mine.addEventListener('click', () => addMine(d));
    // the signal over time
    const pts = r.hears.filter(h => h.rssi != null);
    $('m-dv-chart-t').textContent = `${pts.length} hears`;
    mkChart('m-dv-c', {
      type: 'line',
      data: { labels: pts.map(h => fmtTime(h.ts).slice(0, 5)), datasets: [{ data: pts.map(h => h.rssi), borderColor: col, backgroundColor: col, pointRadius: 2, borderWidth: 1.5, tension: 0.2 }] },
      options: { plugins: { legend: { display: false } }, scales: { x: Object.assign(axis(), { ticks: Object.assign(axis().ticks, { maxTicksLimit: 6 }) }), y: Object.assign(axis(), { title: { display: true, text: 'dBm', color: cssVar('--text-secondary'), font: { size: 10 } } }) } },
    });
    // where it was heard
    if (!dmap) { dmap = mkMap('m-dv-map', () => fitDev()); dLayer = L.layerGroup().addTo(dmap); }
    setTimeout(() => { dmap.invalidateSize(); drawDevMap(); }, 80);
  }
  function drawDevMap() {
    dLayer.clearLayers();
    const pts = (S.device && S.device.hears || []).filter(h => h.lat != null);
    const col = CAT_COLORS[S.device.device.category] || '#94a3b8';
    if (pts.length > 1) L.polyline(pts.map(h => [h.lat, h.lon]), { color: '#f59e0b', weight: 2, dashArray: '4 5' }).addTo(dLayer);
    pts.forEach(h => L.circleMarker([h.lat, h.lon], { radius: rssiRadius(h.rssi), color: '#f59e0b', weight: 2, fillColor: col, fillOpacity: .9 }).addTo(dLayer));
    fitDev();
  }
  function fitDev() {
    const pts = (S.device && S.device.hears || []).filter(h => h.lat != null).map(h => [h.lat, h.lon]);
    if (!pts.length) return;
    const b = L.latLngBounds(pts);
    if (b.getNorthEast().distanceTo(b.getSouthWest()) < 60) dmap.setView(b.getCenter(), 17); else dmap.fitBounds(b.pad(0.3));
  }
  async function addMine(d) {
    const byName = d.name && (d.kind === 'BLE' || (d.flags || '').includes('RAND'));
    const label = prompt(`Mark as my device (${byName ? 'name is "' + d.name + '"' : 'address ' + d.mac}).\nLabel:`, d.name || d.vendor || '');
    if (label === null) return;
    try {
      await post('/api/own', { kind: d.kind, match: byName ? 'name' : 'mac', value: byName ? d.name : d.mac, label: label.trim() || d.mac });
      toast('Added to My devices'); await loadData(true); drawDevice();
    } catch (e) { toast('Not added: ' + e.message); }
  }

  // ══ Phone check ═════════════════════════════════════════════════════════
  let pcData = [];
  ['m-pc-start', 'm-pc-len'].forEach(id => $(id).addEventListener('change', () => { lsSet('harava_app_pc_' + id, $(id).value); loadPhone(); }));
  ['m-pc-exp', 'm-pc-box', 'm-pc-room'].forEach(id => $(id).addEventListener('change', () => { lsSet('harava_app_pc_' + id, $(id).value); runPhone(); }));
  ['m-pc-start', 'm-pc-len', 'm-pc-exp', 'm-pc-box', 'm-pc-room'].forEach(id => { const v = lsGet('harava_app_pc_' + id, null); if (v !== null) $(id).value = v; });
  $('m-pc-auto').addEventListener('click', () => {
    const ph = pcPhones(), n = Math.max(1, parseInt($('m-pc-exp').value) || 30);
    if (!ph.length) { toast('No phones in this window'); return; }
    const r = phoneBoxLimit(ph, n, parseFloat($('m-pc-room').value));
    if (!r) { toast('No clear box group found. Try a window when all phones are in the box.'); return; }
    $('m-pc-box').value = r.lim; lsSet('harava_app_pc_m-pc-box', r.lim);
    runPhone(); toast(`Box limit ${r.lim} dBm (${r.why})`);
  });
  function pcRange() {
    let day = S.day;
    if (!day || day === 'all') day = S.days.length ? S.days[S.days.length - 1].day : null;
    const st = $('m-pc-start').value;
    if (!day || !st) return null;
    const [y, m, d] = day.split('-').map(Number), [h, mi] = st.split(':').map(Number);
    const a = new Date(y, m - 1, d, h, mi).getTime();
    return { day, a, b: a + Math.max(1, parseFloat($('m-pc-len').value) || 3) * 60000 };
  }
  async function loadPhone() {
    if (!S.own.length) { try { S.own = (await api('/api/own')).own || []; } catch (_) { /* */ } }
    const r = pcRange();
    if (!r) { pcData = []; $('m-pc-win').textContent = ''; runPhone(true); return; }
    $('m-pc-win').textContent = `${r.day} ${fmtTime(r.a).slice(0, 5)}–${fmtTime(r.b).slice(0, 5)}`;
    pcData = (await api(`/api/devices?from=${r.a}&to=${r.b}&nosit=1`)).devices;
    runPhone();
  }
  function pcPhones() {
    const opts = { unident: O.pcun, ms: O.pcms };
    return pcData.filter(d => !S.own.some(x => ownRuleHits(x, d)))
      .map(d => ({ d, type: phoneType(d, opts), rssi: O.pcstat === 'med' ? d.rssi_med : d.rssi_max }))
      .filter(x => x.type && x.rssi != null).sort((a, b) => b.rssi - a.rssi);
  }
  function runPhone(noWindow) {
    const v = $('m-pc-verdict');
    if (noWindow || !$('m-pc-start').value) {
      v.className = 'm-verdict'; v.textContent = 'Choose the day in ☰ and the window start: a moment when the phones are in the box.';
      ['m-pc-box-n', 'm-pc-room-n', 'm-pc-out-n'].forEach(id => { $(id).textContent = '—'; });
      $('m-pc-list').innerHTML = ''; return;
    }
    const box = parseFloat($('m-pc-box').value), room = parseFloat($('m-pc-room').value), n = parseInt($('m-pc-exp').value) || 0;
    const ph = phoneZones(pcPhones(), box, room);
    const inBox = ph.filter(x => x.zone === 'box').length, inRoom = ph.filter(x => x.zone === 'room').length, out = ph.filter(x => x.zone === 'out').length;
    $('m-pc-box-n').textContent = inBox; $('m-pc-room-n').textContent = inRoom; $('m-pc-out-n').textContent = out;
    if (!pcData.length) { v.className = 'm-verdict'; v.textContent = 'No hears in this window.'; }
    else if (inRoom > 0) { v.className = 'm-verdict bad'; v.textContent = `⚠ ${inRoom} phone${inRoom > 1 ? 's' : ''} heard in the room but not in the box.` + (inBox < n ? ` ${inBox} of ${n} look like they are in the box.` : ''); }
    else if (inBox >= n) { v.className = 'm-verdict ok'; v.textContent = `✔ ${inBox} phones in the box (expected ${n}); nothing else heard in the room.`; }
    else { v.className = 'm-verdict info'; v.textContent = `ℹ ${inBox} of ${n} phones heard in the box, none elsewhere in the room. The rest may be off, in flight mode, or shielded by the box.`; }
    const bins = []; for (let b = -104; b <= -20; b += 2) bins.push(b);
    mkChart('m-pc-c', {
      type: 'bar',
      data: { labels: bins.map(String), datasets: [{ data: bins.map(b => ph.filter(x => x.rssi >= b && x.rssi < b + 2).length),
        backgroundColor: bins.map(b => b >= box ? 'rgba(74,222,128,.85)' : (b >= room ? 'rgba(245,158,11,.85)' : 'rgba(148,163,184,.6)')) }] },
      options: { plugins: { legend: { display: false } }, scales: { x: Object.assign(axis(), { grid: { display: false }, ticks: Object.assign(axis().ticks, { maxTicksLimit: 9 }) }), y: Object.assign(axis(), { ticks: Object.assign(axis().ticks, { precision: 0 }) }) } },
    });
    const zl = { box: '<span class="tg grn">in box</span>', room: '<span class="tg red">in room</span>', out: '<span class="tg">weak</span>' };
    const order = { room: 0, box: 1, out: 2 };
    $('m-pc-list').innerHTML = [...ph].sort((a, b) => order[a.zone] - order[b.zone] || b.rssi - a.rssi)
      .map(x => devRow(x.d, ` ${zl[x.zone]} <span class="tg">${esc(x.type)}</span>`)).join('') || '<div class="m-empty">No phones found in this window.</div>';
    bindRows($('m-pc-list'));
  }

  // ══ Identification ══════════════════════════════════════════════════════
  async function loadIds() {
    const r = await api('/api/ids?' + rangeQuery()), c = r.counts;
    $('m-id-db').textContent = `${r.db.rules} rules`;
    const sg = sigTableHtml(r.signatures);
    $('m-id-sig').innerHTML = sg.line + sg.warn;
    $('m-id-ble').textContent = n0(c.ble); $('m-id-left').textContent = n0(c.fieldwatch_left);
    $('m-id-ok').textContent = `${n0(c.harava_identified)} (${c.fieldwatch_left ? Math.round(100 * c.harava_identified / c.fieldwatch_left) : 0} %)`;
    $('m-id-still').textContent = n0(c.still_unidentified);
    $('m-id-grp').textContent = `${n0(c.grouped_addresses)} → ${n0(c.grouped_devices)}`;
    $('m-id-pats').innerHTML = r.patterns.map(p => `<div class="m-pat"><b>${p.count}</b>${esc(p.pattern)}<div class="raw">${esc(p.example)} · ${esc(p.example_raw)}</div></div>`).join('')
      || '<div class="m-empty">Everything heard on this day is identified.</div>';
    const rules = r.rules.filter(x => x.hits).sort((a, b) => b.hits - a.hits), max = Math.max(1, ...rules.map(x => x.hits));
    $('m-id-rules').innerHTML = rules.map(x => `<div class="m-cbar" style="cursor:default"><span title="${esc(x.note)}">${esc(x.name)}</span>
      <div class="bw"><i style="width:${100 * x.hits / max}%;background:${CAT_COLORS[x.layer] || 'var(--accent)'}"></i></div><span class="n">${x.hits}</span></div>`).join('')
      || '<div class="m-empty">No rule matched on this day.</div>';
  }

  // ══ Imports ═════════════════════════════════════════════════════════════
  async function loadPhones() {
    if (!can('analyze')) return;
    const r = await api('/api/collectors');
    const sel = $('m-imp-phone'), cur = sel.value || lsGet('harava_phone', '');
    sel.innerHTML = r.collectors.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
    if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
  }
  $('m-imp-phone').addEventListener('change', e => lsSet('harava_phone', e.target.value));
  $('m-imp-newphone').addEventListener('click', async () => {
    const name = prompt('Name of the phone (for example "Pixel 8"):');
    if (!name || !name.trim()) return;
    try { const r = await post('/api/collectors', { name: name.trim() }); lsSet('harava_phone', String(r.id)); await loadPhones(); $('m-imp-phone').value = String(r.id); }
    catch (e) { toast(e.message); }
  });
  const vTag = x => x.verdict ? `<span class="m-verdict-tag v-${x.verdict.split(' ')[0]}">${esc(x.verdict)}</span>` : '';
  $('m-imp-pick').addEventListener('click', () => $('m-imp-file').click());
  $('m-imp-file').addEventListener('change', async e => {
    const files = [...e.target.files].sort((a, b) => a.lastModified - b.lastModified); e.target.value = '';
    if (!files.length) return;
    const fd = new FormData(); files.forEach(f => fd.append('files', f));
    if ($('m-imp-phone').value) fd.append('collector', $('m-imp-phone').value);
    await uploadFormData(fd, files.length, $('m-imp-res'));
  });
  const resultsHtml = r => r.results.map(x => x.status === 'imported'
    ? `<div class="ok">✔ ${esc(x.file)} — ${vTag(x)} ${n0(x.rows)} rows${x.verdict !== 'new' && x.verdict !== 'sit update' ? `, ${n0(x.new_rows)} new` : ''}${x.note ? ' (' + esc(x.note) + ')' : ''}</div>`
    : x.status === 'skipped' ? `<div class="skip">– ${esc(x.file)} — ${esc(x.reason)}</div>` : `<div class="err">✖ ${esc(x.file)} — ${esc(x.reason)}</div>`).join('');
  async function uploadFormData(fd, n, out) {
    out.innerHTML = `<div class="skip">Uploading and importing ${n} file(s)…</div>`;
    try {
      const r = await api('/api/import', { method: 'POST', body: fd });
      out.innerHTML = resultsHtml(r);
      await loadSummary(); S.loadedFor = null; loadImports();
      return true;
    } catch (err) { out.innerHTML = `<div class="err">✖ ${esc(err.message)}</div>`; return false; }
  }

  // ── "Share → Harava": files another app (Fieldwatch) shared to the installed app ──
  // The service worker (/app/sw.js) keeps them in cache storage; here they are uploaded like
  // "Choose files…": at once with one phone, after choosing the phone with several.
  const SHARE_CACHE = 'harava-share';
  async function sharedFiles() {
    if (!('caches' in window)) return null;
    const cache = await caches.open(SHARE_CACHE);
    const metaR = await cache.match('/app/shared-meta');
    if (!metaR) return null;
    const meta = await metaR.json();
    if (Date.now() - (meta.at || 0) > 24 * 3600e3) { await caches.delete(SHARE_CACHE); return null; }
    const files = [];
    for (let i = 0; i < meta.n; i++) {
      const r = await cache.match(`/app/shared-file/${i}`);
      if (!r) continue;
      const name = decodeURIComponent(r.headers.get('X-Harava-Name') || `shared-${i + 1}`);
      files.push(new File([await r.blob()], name, { type: r.headers.get('Content-Type') || '' }));
    }
    return { meta, files };
  }
  async function dropShared() { try { await caches.delete(SHARE_CACHE); } catch (_) { /* */ } }
  async function handleShared(fromShare) {
    let got = null;
    try { got = await sharedFiles(); } catch (_) { got = null; }
    const card = $('m-share-card'), body = $('m-share-body');
    if (fromShare === '-1' && !got) { setView('imp'); card.hidden = false; body.innerHTML = '<div class="err">✖ The shared file could not be read. Share it again, or use Choose files…</div>'; return; }
    if (!got || !got.files.length) return;
    setView('imp');
    card.hidden = false;
    $('m-share-n').textContent = `${got.files.length} file${got.files.length > 1 ? 's' : ''}`;
    const names = got.files.map(f => `<div class="m-small">📄 ${esc(f.name)} · ${(f.size / 1024).toFixed(0)} kB</div>`).join('');
    if (!can('analyze')) {
      body.innerHTML = names + '<div class="err">Importing needs the <i>Analyze</i> permission. Ask the admin.</div>';
      await dropShared(); return;
    }
    let phones = [];
    try { phones = (await api('/api/collectors')).collectors; } catch (_) { /* */ }
    const go = async cid => {
      const fd = new FormData();
      got.files.sort((a, b) => a.name.localeCompare(b.name)).forEach(f => fd.append('files', f));
      if (cid) fd.append('collector', cid);
      body.innerHTML = names + '<div class="m-res" id="m-share-res"></div>';
      const ok = await uploadFormData(fd, got.files.length, $('m-share-res'));
      if (ok) await dropShared();
      else body.insertAdjacentHTML('beforeend', '<div class="m-btn-row"><button type="button" class="app-btn" id="m-share-retry">Try again</button></div>');
      const rt = $('m-share-retry'); if (rt) rt.addEventListener('click', () => handleShared(''));
    };
    if (phones.length <= 1) { await go(phones.length ? phones[0].id : ''); return; }
    const last = lsGet('harava_phone', '');
    body.innerHTML = names + `<div class="app-subrow"><span>Phone</span><select id="m-share-phone" class="m-sel">${phones.map(c =>
      `<option value="${c.id}" ${String(c.id) === last ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div>
      <div class="m-btn-row"><button type="button" class="app-btn" id="m-share-drop">Discard</button><button type="button" class="app-btn app-btn-primary" id="m-share-go">Import</button></div>`;
    $('m-share-go').addEventListener('click', () => { lsSet('harava_phone', $('m-share-phone').value); go($('m-share-phone').value); });
    $('m-share-drop').addEventListener('click', async () => { await dropShared(); card.hidden = true; toast('Discarded'); });
  }
  let IMPS = [];
  async function loadImports() {
    loadPhones().catch(() => {});
    const r = await api('/api/imports');
    IMPS = r.imports;
    const multi = new Set(r.imports.map(i => i.owner_name)).size > 1;
    $('m-imp-n').textContent = `${r.imports.length}`;
    const ed = i => i.can_edit && can('analyze');
    $('m-imp-list').innerHTML = r.imports.map(i => `<div class="app-card m-imp">
        <label class="app-switch"><input type="checkbox" data-id="${i.id}" ${i.active ? 'checked' : ''} ${ed(i) ? '' : 'disabled'}></label>
        <div class="t"><b>${esc(i.filename)}</b>
          <small>${vTag(i)} ${i.ftype.toUpperCase()} · ${n0(i.rows)} rows${i.verdict === 'extends' || i.verdict === 'overlaps' ? ` (+${n0(i.new_rows)})` : ''} · ${i.first_ts ? fmtTime(i.first_ts, true).slice(5, 16) + ' → ' + fmtTime(i.last_ts, true).slice(5, 16) : ''}</small>
          <small>${multi || !i.mine ? esc(i.owner_name) + ' · ' : ''}${esc(i.phone)}${i.shared_with ? ' · shared: ' + esc(i.shared_with) : ''}</small>
          ${i.note ? `<small style="color:var(--text-3)">${esc(i.note)}</small>` : ''}
          ${i.mine && ed(i) && !F.open ? `<select data-share="${i.id}" aria-label="Sharing"><option value="">Private</option><option value="*" ${i.shared_with === 'all users' ? 'selected' : ''}>Shared with all users</option>${i.shared_with && i.shared_with !== 'all users' ? '<option value="keep" selected>Shared with chosen users</option>' : ''}</select>` : ''}</div>
        ${ed(i) ? `<div class="acts">${i.rereadable ? `<button type="button" class="app-btn" data-rr="${i.id}">Re-read</button>` : ''}<button type="button" class="app-btn del" data-del="${i.id}" data-name="${esc(i.filename)}">Delete</button></div>` : ''}</div>`).join('')
      || '<div class="m-empty">Nothing imported yet.</div>';
    $('m-imp-list').querySelectorAll('input[data-id]').forEach(cb => cb.addEventListener('change', async () => {
      try { await api(`/api/imports/${cb.dataset.id}/toggle`, { method: 'POST' }); S.loadedFor = null; await loadSummary(); loadImports(); } catch (e) { toast(e.message); }
    }));
    $('m-imp-list').querySelectorAll('select[data-share]').forEach(sel => sel.addEventListener('change', async () => {
      if (sel.value === 'keep') return;
      try { await post(`/api/imports/${sel.dataset.share}/share`, { shared: sel.value }); toast(sel.value ? 'Shared with all users' : 'Private'); loadImports(); } catch (e) { toast(e.message); }
    }));
    $('m-imp-list').querySelectorAll('button[data-rr]').forEach(b => b.addEventListener('click', async () => {
      try { const x = await post(`/api/imports/${b.dataset.rr}/reread`, {}); toast(`Read again: ${n0(x.rows)} rows, ${n0(x.new_rows)} new`); S.loadedFor = null; loadImports(); } catch (e) { toast(e.message); }
    }));
    $('m-imp-list').querySelectorAll('button[data-del]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm(`Remove ${b.dataset.name} from the database? Rows that another import also has stay.`)) return;
      try { await api(`/api/imports/${b.dataset.del}`, { method: 'DELETE' }); S.loadedFor = null; await loadSummary(); loadImports(); } catch (e) { toast(e.message); }
    }));
    drawCov(r.imports);
  }
  function drawCov(list) {
    const xs = list.filter(i => i.first_ts && i.last_ts).sort((a, b) => a.first_ts - b.first_ts || a.id - b.id);
    $('m-cov-card').hidden = !xs.length;
    if (!xs.length) return;
    const lo = Math.min(...xs.map(i => i.first_ts)), hi = Math.max(...xs.map(i => i.last_ts)), span = Math.max(1, hi - lo);
    const ty = i => !i.active ? 't-off' : i.ftype === 'sit' ? 't-sit' : i.ftype === 'kml' ? 't-kml' : 't-log';
    $('m-cov').innerHTML = xs.map(i => `<div class="m-cov-row" data-id="${i.id}"><small>${esc(i.filename)}</small>
        <div class="m-cov-track"><div class="m-cov-bar ${ty(i)}${i.verdict === 'duplicate' ? ' dup' : ''}" style="left:${(100 * (i.first_ts - lo) / span).toFixed(2)}%;width:${(100 * (i.last_ts - i.first_ts) / span).toFixed(2)}%"></div></div></div>`).join('') +
      `<div class="m-cov-axis"><span>${fmtTime(lo, true).slice(5, 16)}</span><span>${fmtTime(hi, true).slice(5, 16)}</span></div>`;
    $('m-cov').querySelectorAll('.m-cov-row').forEach(row => row.addEventListener('click', () => {
      const i = IMPS.find(x => x.id === +row.dataset.id); if (!i) return;
      const d1 = fmtTime(i.first_ts, true).slice(0, 10), d2 = fmtTime(i.last_ts, true).slice(0, 10);
      if (!S.days.some(d => d.day === d1)) { toast('That file is switched off or has no hears'); return; }
      S.day = d1; lsSet('harava_app_day', d1); $('m-day').value = d1;
      fillDayTo(); S.dayTo = d2 > d1 && S.days.some(d => d.day === d2) ? d2 : ''; $('m-day-to').value = S.dayTo;
      setView('map');
    }));
  }

  // ══ Workbench (admins) ═════════════════════════════════════════════════
  const ST = { new: 'New', investigating: 'Investigating', identified: 'Identified', ignore: 'Ignore', cannot: "Can't identify" };
  const WB = { tab: null, items: [], fp: null };
  async function loadWb(force) {
    if (!can('admin')) return;
    if (force || WB.tab !== O.wbtab) {
      $('m-wb-list').innerHTML = '<div class="m-empty">Loading…</div>';
      const r = await api('/api/wb?tab=' + encodeURIComponent(O.wbtab));
      WB.items = r.items; WB.tab = O.wbtab; WB.r = r;
      const last = r.progress[r.progress.length - 1];
      const st = r.statuses || {};
      $('m-wb-prog').innerHTML = `${n0(r.counts.unknown)} unknown · ${n0(r.counts.weak)} weak · ${n0(r.counts.nodata)} no data (radios, all days)` +
        (last ? `<br>Newest day ${last.day}: ${last.ble ? Math.round(100 * (last.ble - last.unknown - last.nodata) / last.ble) : 0} % of ${n0(last.ble)} BLE radios named` : '') +
        ((st.identified || st.investigating) ? ` · ${st.identified || 0} identified, ${st.investigating || 0} investigating` : '') +
        (r.local_rules ? ` · ${r.local_rules} local rule${r.local_rules > 1 ? 's' : ''}` : '');
    }
    drawWb();
  }
  function drawWb() {
    const q = $('m-wb-q').value.trim().toLowerCase();
    const items = WB.items.filter(x => (!O.wbst || (x.status || 'new') === O.wbst) &&
      (!q || `${x.label} ${x.names.join(' ')} ${x.note || ''} ${x.ident || ''}`.toLowerCase().includes(q)));
    $('m-wb-n').textContent = `${items.length} kinds`;
    $('m-wb-list').innerHTML = items.slice(0, 200).map(x => `<div class="app-card m-dev"><button type="button" class="hd" data-fp="${esc(x.fp)}">
        <div class="l1"><b>${esc(x.label)}</b><span class="rssi">${x.rssi_max ?? '?'} dBm</span></div>
        <div class="l2">${x.count} radio${x.count > 1 ? 's' : ''}${x.devices < x.count ? ' · ' + x.devices + ' devices' : ''} · ${x.days.length} day${x.days.length > 1 ? 's' : ''}${x.names.length ? ' · ' + esc(x.names.slice(0, 3).join(', ')) : ''}${x.ident ? ' · ' + esc(x.ident) : ''}</div>
        <div class="l2"><span class="tg${x.status === 'identified' ? ' grn' : x.status === 'investigating' ? ' amb' : ''}">${ST[x.status || 'new']}</span> ${x.note ? esc(x.note) : ''}</div></button></div>`).join('')
      || '<div class="m-empty">Nothing here.</div>';
    $('m-wb-list').querySelectorAll('button[data-fp]').forEach(b => b.addEventListener('click', () => openWb(b.dataset.fp)));
  }
  $('m-wb-q').addEventListener('input', () => { clearTimeout(qT); qT = setTimeout(drawWb, 200); });
  const INP = 'width:100%;box-sizing:border-box;font:inherit;font-size:14px;padding:8px;border-radius:10px;background:var(--bg-2);color:var(--text-1);border:1px solid var(--border)';
  async function openWb(fp) {
    setView('wbd');
    $('m-wbd').innerHTML = '<div class="m-empty">Loading…</div>';
    let r;
    try { r = await api('/api/wb/fp?fp=' + encodeURIComponent(fp)); } catch (e) { $('m-wbd').innerHTML = `<div class="m-empty">${esc(e.message)}</div>`; return; }
    WB.fp = r;
    const it = r.item;
    const sug = r.suggest || { kind: 'BLE' };
    $('m-wbd').innerHTML = `
      <div class="m-card"><div class="m-card-hd">${esc(it.label)}</div>
        <div class="m-small">${it.count} radio(s) · ${it.devices} device(s) · ${it.hears} hears · strongest ${it.rssi_max ?? '?'} dBm<br>Days: ${esc(it.days.join(', '))}${it.names.length ? '<br>Names: ' + esc(it.names.join(', ')) : ''}${it.ident ? '<br>Now: ' + esc(it.ident) : ''}</div></div>
      <div class="m-card"><div class="m-card-hd">Status and note</div>
        <select id="m-wbd-st" data-chips>${Object.entries(ST).map(([k, v]) => `<option value="${k}"${(it.status || 'new') === k ? ' selected' : ''}>${v}</option>`).join('')}</select>
        <textarea id="m-wbd-note" rows="3" style="${INP};margin-top:8px" placeholder="What could it be?">${esc(it.note || '')}</textarea>
        <div class="m-btn-row"><button type="button" class="app-btn app-btn-primary" id="m-wbd-save">Save</button></div></div>
      <div class="m-card"><div class="m-card-hd">The advertisement, decoded</div><div class="m-kv">${r.decoded.map(x => `<span>${esc(x.what)}</span><b class="mono">${esc(x.value)}</b>`).join('') || '<span>—</span><b>no advertising data</b>'}</div></div>
      ${r.variants.length > 1 ? `<div class="m-card"><div class="m-card-hd">Variants <span class="m-hd-r">${r.variants.length}</span></div>${r.variants.slice(0, 12).map(v => `<div class="m-pat"><b>${v.count}</b><span class="raw">${esc(v.raw)}</span></div>`).join('')}${r.changing.length ? `<div class="m-small">Bytes that change: ${esc(r.changing.join(', '))}</div>` : ''}</div>` : ''}
      <div class="m-card"><div class="m-card-hd">Hints</div>${r.hints.map(h => `<div class="m-note">• ${esc(h)}</div>`).join('') || '<div class="m-note">No hints.</div>'}
        <div class="m-btn-row" style="flex-wrap:wrap">${r.search.map(s => `<a class="app-btn" style="text-decoration:none;text-align:center" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join('')}</div></div>
      <div class="m-card"><div class="m-card-hd">Heard together with</div>${r.together.map(t => `<div class="m-pat"><b>${t.n}</b>${esc(t.what)}</div>`).join('') || '<div class="m-empty">Nothing stands out.</div>'}</div>
      <div class="m-card"><div class="m-card-hd">Radios <span class="m-hd-r">${r.radios.length}${it.count > r.radios.length ? ' of ' + it.count : ''}</span></div>
        ${r.radios.map(d => `<div class="m-pat"><b>${d.rssi_max ?? '?'}</b><span class="mono">${esc(d.mac)}</span> ${esc(d.name || '')}<div class="raw">${d.hears} hears · ${esc(d.days.join(', '))}${d.travelled ? ' · travelled' : ''}</div></div>`).join('')}</div>
      <div class="m-card"><div class="m-card-hd">Make a rule</div>
        <div class="m-form one">
          <label>Name <input id="m-wbr-name" style="${INP}" maxlength="80" placeholder="e.g. Nexo smart lock" value="${esc(it.status === 'identified' ? '' : (it.names[0] || ''))}"></label>
          <label>Layer <select id="m-wbr-layer" style="${INP}"><option value="">(none: name only)</option>${(F.layers || []).map(l => `<option>${esc(l)}</option>`).join('')}</select></label>
          <label>Confidence <select id="m-wbr-conf" data-chips><option value="high">High</option><option value="medium">Medium</option><option value="low" selected>Low</option></select></label>
          <label>Match <textarea id="m-wbr-match" rows="4" class="mono" style="${INP};font-size:12px" spellcheck="false">${esc(JSON.stringify(sug, null, 1))}</textarea></label>
          <label>Note <textarea id="m-wbr-note" rows="2" style="${INP}" placeholder="What tells it, where the information came from"></textarea></label>
        </div>
        <div class="m-btn-row"><button type="button" class="app-btn" id="m-wbr-prev">Preview</button><button type="button" class="app-btn app-btn-primary" id="m-wbr-save">Save rule</button></div>
        <div id="m-wbr-out" class="m-small"></div>
        <div class="m-small">The match is the suggestion of what all these radios share; edit it if needed (keys: mfg, mfg_data_prefix, service_data_uuid, service_uuid_any, uuid128_any, ad_type, ad_data_prefix, name_regex). Preview tells what else it would catch.</div></div>`;
    chipify($('m-wbd-st'));
    chipify($('m-wbr-conf'));
    $('m-wbd-save').addEventListener('click', async () => {
      try {
        await post('/api/wb/note', { fp: it.fp, status: $('m-wbd-st').value, note: $('m-wbd-note').value });
        const x = WB.items.find(y => y.fp === it.fp); if (x) { x.status = $('m-wbd-st').value; x.note = $('m-wbd-note').value; }
        toast('Saved');
      } catch (e) { toast('Not saved: ' + e.message); }
    });
    const ruleBody = () => {
      let match;
      try { match = JSON.parse($('m-wbr-match').value); } catch (_) { throw new Error('the match is not valid JSON'); }
      return { fp: it.fp, name: $('m-wbr-name').value, layer: $('m-wbr-layer').value, confidence: $('m-wbr-conf').value, note: $('m-wbr-note').value, match };
    };
    const out = $('m-wbr-out');
    $('m-wbr-prev').addEventListener('click', async () => {
      try {
        const p = await post('/api/wb/preview', ruleBody());
        const tb = p.by_tab || {};
        out.innerHTML = `Would match <b>${p.matches}</b> radio(s) in ${p.kinds} kind(s): ${tb.unknown || 0} unknown, ${tb.weak || 0} weak, ${tb.nodata || 0} no data, ${tb.identified || 0} already identified.` +
          (p.already.length ? `<br><span style="color:var(--amber)">Would take over: ${p.already.map(a => esc(a.ident) + ' (' + a.n + ')').join(', ')}</span>` : '');
      } catch (e) { out.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
    });
    $('m-wbr-save').addEventListener('click', async () => {
      try {
        const p = await post('/api/wb/rule', ruleBody());
        toast(`Rule ${p.id} saved`); out.textContent = `Saved as ${p.id}. Harava now has ${p.rules} rules.`;
        S.loadedFor = null; WB.tab = null;
      } catch (e) { out.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
    });
  }

  // ══ Views ═══════════════════════════════════════════════════════════════
  const LOADERS = { map: loadMap, day: loadDay, watch: loadWatch, dev: loadDevices, device: () => {}, phone: loadPhone, ids: loadIds, imp: loadImports, wb: () => loadWb(false), wbd: () => {} };
  async function loadView(force) {
    try {
      if (force) S.loadedFor = null;
      if (force && view === 'wb') { await loadWb(true); return; }
      await LOADERS[view]();
    } catch (e) { toast('Could not load: ' + e.message); }
  }
  function setView(v, fromMenu, fromHistory) {
    if (!VIEWS[v] || (VIEWS[v].role && !can(VIEWS[v].role))) v = 'map';
    const tookMenuEntry = !fromHistory && v !== view && hist().app === 'menu';
    if (!fromHistory && v !== view) histPush({ app: 'view', view: v });
    view = v;
    if (okView(v)) lsSet('harava_app_view', v);
    const cfg = VIEWS[v];
    for (const k of Object.keys(VIEWS)) $(VIEWS[k].el).hidden = VIEWS[k].el !== cfg.el;
    document.querySelectorAll('.app-sec[data-for]').forEach(s => { const cur = cfg.secs.includes(s.dataset.for); s.open = cur; s.classList.toggle('cur', cur); });
    document.querySelectorAll('.app-tile').forEach(t => { const on = t.dataset.view === v; t.classList.toggle('on', on); t.setAttribute('aria-pressed', String(on)); });
    $('app-title').textContent = cfg.title;
    $('app-sub').textContent = v === 'wb' || v === 'wbd' || v === 'imp' ? 'all days' : dayLabel();
    setTimeout(() => { for (const m of [lmap, dmap]) if (m && m.getContainer().offsetParent) m.invalidateSize(); }, 60);
    if (v !== 'device' && v !== 'wbd') loadView(false);
    if (fromMenu) setTimeout(() => menu(false, tookMenuEntry), 150);
  }
  document.querySelectorAll('.app-tile').forEach(t => t.addEventListener('click', () => setView(t.dataset.view, true)));
  let rt = null;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { for (const m of [lmap, dmap]) if (m && m.getContainer().offsetParent) m.invalidateSize(); }, 150); });

  // ── back on the page: reload after an hour away or a new build (a deploy) ──
  const RELOAD_AFTER_MS = 3600e3;
  let hiddenAt = document.hidden ? Date.now() : 0, reloading = false;
  async function newBuild() {
    if (!F.build) return false;
    try { const d = await api('/api/app/build'); return !!d.build && d.build !== F.build; } catch (_) { return false; }
  }
  async function wake() {
    if (reloading) return;
    const away = hiddenAt ? Date.now() - hiddenAt : 0;
    hiddenAt = 0;
    if (away > RELOAD_AFTER_MS || await newBuild()) { reloading = true; location.reload(); return; }
    tick();
    try { await loadSummary(); } catch (_) { /* */ }
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) { if (!hiddenAt) hiddenAt = Date.now(); } else wake(); });
  window.addEventListener('pagehide', () => { if (!hiddenAt) hiddenAt = Date.now(); });
  window.addEventListener('pageshow', e => { if (e.persisted) wake(); });

  // ── the clock of the top bar (local time) ──
  function tick() { const d = new Date(); $('app-utc-t').textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  (function schedule() { tick(); setTimeout(schedule, 60000 - (Date.now() % 60000) + 50); })();

  // ══ What's new, User guides and About (the WSPR Logger phone view's sheet) ══
  const sheet = $('app-sheet');
  let guidesLoaded = false, newsLoaded = false;
  function newsDots() {
    const unseen = !!F.version && lsGet('harava_seen_version', null) !== F.version;
    $('app-menu-dot').hidden = !unseen; $('app-news-dot').hidden = !unseen; $('app-news-dot2').hidden = !unseen;
  }
  newsDots();
  const inlineMd = t => esc(t).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\*([^*]+)\*/g, '<i>$1</i>');
  const niceDay = d => d ? new Date(d + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' }) : '';
  async function loadNews() {
    const el = $('app-sheet-news');
    try {
      const r = await api('/api/whatsnew'), es = r.entries || r.versions || [];
      el.innerHTML = es.length
        ? '<p class="app-small">Short notes for users, newest first. Each version is one day of development.</p>' +
          es.map((e, i) => {
            const head = `<span class="app-news-ver">${esc(e.version)}</span><span class="app-news-date">${niceDay(e.date)}</span>` + (e.version === F.version ? '<span class="app-news-cur">current</span>' : '');
            const body = (e.headline ? `<div class="app-news-title">${inlineMd(e.headline)}</div>` : '') +
              '<ul>' + (e.items || []).map(it => '<li>' + (it.lead ? `<b>${inlineMd(it.lead)}</b>` + (it.text ? ' — ' : '') : '') + inlineMd(it.text || '') + '</li>').join('') + '</ul>';
            return i === 0 ? `<section class="app-news-entry"><h3>${head}</h3>${body}</section>` : `<details class="app-news-entry"><summary><h3>${head}</h3></summary>${body}</details>`;
          }).join('') +
          '<p class="app-small">Older versions are folded — tap to open. The technical history: CHANGELOG.md in the <a href="https://github.com/oh2gax/harava" target="_blank" rel="noopener">GitHub repository</a>.</p>'
        : '<p class="app-small">No notes are available.</p>';
      newsLoaded = true;
    } catch (_) { el.innerHTML = '<p class="app-small">Could not load the notes.</p>'; }
  }
  async function loadGuides() {
    const el = $('app-sheet-guides');
    try {
      const gs = (await api('/api/guides')).guides || [];
      el.innerHTML = gs.length ? '<p class="app-small">The guides open as PDF.</p>' + gs.map(g => {
        const url = '/doc/' + encodeURIComponent(g.file), meta = [g.date, 'PDF', g.size_kb + ' kB'].filter(Boolean).join(' · ');
        return `<section class="app-guide"><h3>${esc(g.title)}</h3>${g.description ? `<div class="app-guide-desc">${esc(g.description)}</div>` : ''}<div class="app-guide-meta">${esc(meta)}</div>` +
          `<div class="app-guide-btns"><a class="app-btn app-btn-primary" href="${url}" target="_blank" rel="noopener">Open PDF</a><a class="app-btn" href="${url}" download>Download</a></div></section>`;
      }).join('') : '<p class="app-small">No guides yet.</p>';
      guidesLoaded = true;
    } catch (_) { el.innerHTML = '<p class="app-small">Could not load the guides.</p>'; }
  }
  function openSheet(kind) {
    const NAME = { news: "What's new", guides: 'User guides', about: 'About' };
    $('app-sheet-title').textContent = NAME[kind];
    $('app-sheet-about').hidden = kind !== 'about'; $('app-sheet-guides').hidden = kind !== 'guides'; $('app-sheet-news').hidden = kind !== 'news';
    const others = { news: ['guides', 'about'], guides: ['news', 'about'], about: ['news', 'guides'] }[kind];
    ['app-sheet-alt', 'app-sheet-alt2'].forEach((id, k) => { const b = $(id), o = others[k]; b.hidden = !o; if (o) { b.textContent = NAME[o]; b.dataset.kind = o; } });
    if (kind === 'guides' && !guidesLoaded) { $('app-sheet-guides').innerHTML = '<p class="app-small">Loading…</p>'; loadGuides(); }
    if (kind === 'news') {
      if (!newsLoaded) { $('app-sheet-news').innerHTML = '<p class="app-small">Loading…</p>'; loadNews(); }
      if (F.version) lsSet('harava_seen_version', F.version);
      newsDots();
    }
    const wasOpen = !sheet.hidden;
    sheet.hidden = false; $('app-sheet-body').scrollTop = 0;
    if (!wasOpen) histPush({ app: 'sheet', view });
    menu(false, true);
  }
  function closeSheet(fromHistory) {
    if (sheet.hidden) return;
    sheet.hidden = true;
    if (!fromHistory && hist().app === 'sheet') { try { history.back(); } catch (_) { /* */ } }
  }
  $('app-news-btn').addEventListener('click', () => openSheet('news'));
  $('app-news-btn2').addEventListener('click', () => openSheet('news'));
  $('app-about-btn').addEventListener('click', () => openSheet('about'));
  $('app-guides-btn').addEventListener('click', () => openSheet('guides'));
  $('app-sheet-close').addEventListener('click', () => closeSheet(false));
  $('app-sheet-alt').addEventListener('click', e => openSheet(e.currentTarget.dataset.kind));
  $('app-sheet-alt2').addEventListener('click', e => openSheet(e.currentTarget.dataset.kind));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSheet(false); });
  window.addEventListener('popstate', () => {
    const s = hist();
    if (s.app !== 'sheet') closeSheet(true);
    if (s.app !== 'menu') menu(false, true);
    if (s.view && s.view !== view) {
      if (s.view === 'device' && !S.device) setView('dev', false, true);
      else if (s.view === 'wbd' && !WB.fp) setView('wb', false, true);
      else { const was = view; setView(s.view, false, true); if (s.view === 'device' || s.view === 'wbd') { view = s.view; } void was; }
    }
  });
  const so = $('app-signout');
  if (so) so.addEventListener('click', async () => { try { await fetch('/logout', { method: 'POST' }); } catch (_) { /* */ } location.href = '/login?next=/app'; });
  // Mask addresses (privacy.js is loaded after this file)
  document.addEventListener('DOMContentLoaded', () => {
    const cb = $('m-o-mask');
    if (!cb || !window.haravaMask) return;
    cb.checked = window.haravaMask.on;
    cb.addEventListener('change', () => window.haravaMask.set(cb.checked));
  });

  // ── start ──
  $('app-sub').textContent = '';
  const sharedParam = new URLSearchParams(location.search).get('shared');
  try { history.replaceState({ app: 'view', view }, '', sharedParam !== null ? '/app' : location.href); } catch (_) { /* */ }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => { /* sharing just stays off */ });
  }
  loadSummary().then(() => { setView(view, false, true); return handleShared(sharedParam); })
    .catch(e => toast('Could not load data: ' + e.message));
})();
