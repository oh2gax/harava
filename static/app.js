/* Harava — front end (vanilla JS + Leaflet + Chart.js) */
'use strict';

// ─────────────── state ───────────────
// Device layers: one per Fieldwatch signature class (names match CLASS_CATEGORY in the server).
// Watch-list layers get an icon pin on the map so they stand out from the dots.
const CAT = [
    ['Watch list', 'Glasses & VR', '#e879f9', '🕶️'],
    ['Watch list', 'Tracker', '#f43f5e', '🏷️'],
    ['Watch list', 'Surveillance', '#e11d48', '🎥'],
    ['Watch list', 'Police / body cam', '#3b82f6', '🚓'],
    ['Watch list', 'Drone', '#fb923c', '🚁'],
    ['Watch list', 'Hacking tool', '#d946ef', '💀'],
    ['Personal', 'Phone / PC', '#38bdf8'],
    ['Personal', 'Audio', '#a78bfa'],
    ['Personal', 'Wearable', '#c084fc'],
    ['Personal', 'Health', '#f472b6'],
    ['Places & things', 'Vehicle', '#f59e0b'],
    ['Places & things', 'Camera', '#fbbf24'],
    ['Places & things', 'Smart lock', '#a3e635'],
    ['Places & things', 'Home & IoT', '#4ade80'],
    ['Places & things', 'Thermostat', '#34d399'],
    ['Places & things', 'Shelf label / sign', '#22d3ee'],
    ['Places & things', 'Beacon', '#2dd4bf'],
    ['Places & things', 'Mesh radio', '#facc15'],
    ['Background', 'Network', '#60a5fa'],
    ['Background', 'Unidentified', '#94a3b8'],
];
const CAT_COLORS = Object.fromEntries(CAT.map(c => [c[1], c[2]]));
const CAT_ICON = Object.fromEntries(CAT.filter(c => c[3]).map(c => [c[1], c[3]]));
const CAT_ORDER = CAT.map(c => c[1]);

const S = {
    summary: null, days: [], day: 'all', tFrom: '', tTo: '',
    devices: [], hears: 0, path: [], timeline: null,
    kind: 'ALL', q: '', minRssi: -105, hiddenCats: new Set(),
    flags: { travel: false, ident: false, sitOnly: false },
    layers: { path: true, timeline: true, cats: true, spread: true },
    sort: { key: 'rssi_max', dir: -1 }, selected: null, view: 'map',
    own: [], hideOwn: localStorage.getItem('fwa_hide_own') !== '0',
    tlCat: localStorage.getItem('fwa_tl_cat') || '',
    groupSame: localStorage.getItem('fwa_group') !== '0',
    identAs: '',
    sitOnly: false,
};window.S = S;   // for privacy.js (masking)

try {
    const saved = JSON.parse(localStorage.getItem('fwa_layers') || 'null');
    if (saved) Object.assign(S.layers, saved);
    const hid = JSON.parse(localStorage.getItem('fwa_hidden_layers') || 'null');
    if (Array.isArray(hid)) S.hiddenCats = new Set(hid.filter(c => CAT_COLORS[c]));
} catch (e) {}
if (S.tlCat === 'Smart glasses') S.tlCat = 'Glasses & VR';
if (S.tlCat && !CAT_COLORS[S.tlCat]) S.tlCat = '';
function saveHidden() { try { localStorage.setItem('fwa_hidden_layers', JSON.stringify([...S.hiddenCats])); } catch (e) {} }

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = n => String(n).padStart(2, '0');
function fmtTime(ms, withDate) {
    if (!ms) return '';
    const d = new Date(ms);
    const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    return withDate ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${t}` : t;
}
function fmtM(m) { return m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m'; }
function toast(msg) { const t = $('toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600); }
async function api(url, opts) {
    const r = await fetch(url, opts);
    if (r.status === 401) { location.href = '/login'; throw new Error('sign in first'); }   // session ended
    if (r.status === 403) {                                    // no permission, or the password must be changed
        const j = await r.json().catch(() => ({}));
        if (j.account) { location.href = j.account; }
        throw new Error(j.error || 'not allowed');
    }
    if (!r.ok) throw new Error(await r.text());
    return r.json();
}
async function signOut() {
    await fetch('/logout', { method: 'POST' });
    location.href = '/login';
}

// ─────────────── theme / sidebar ───────────────
function themeMode() { return localStorage.getItem('fwa_theme_mode') || 'auto'; }
function applyTheme() {
    const m = themeMode();
    const light = m === 'light' || (m === 'auto' && matchMedia('(prefers-color-scheme: light)').matches);
    document.documentElement.classList.toggle('light', light);
    $('themeBtn').textContent = m === 'auto' ? '🌓' : (m === 'light' ? '☀️' : '🌙');
    $('themeBtn').title = 'Theme: ' + m[0].toUpperCase() + m.slice(1);
    if (window.map) setTiles(light);
    if (S.timeline) drawTimeline();
}
function cycleTheme() {
    const order = ['auto', 'dark', 'light'];
    localStorage.setItem('fwa_theme_mode', order[(order.indexOf(themeMode()) + 1) % 3]);
    applyTheme();
}
matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', applyTheme);
function toggleSidebar() { $('sidebar').classList.toggle('collapsed'); setTimeout(() => map.invalidateSize(), 300); }
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

// ─────────────── map ───────────────
const map = L.map('map', { preferCanvas: true, zoomControl: true }).setView([60.17, 24.94], 11);
window.map = map;
let tileLayer = null;
function setTiles(light) {
    if (tileLayer) map.removeLayer(tileLayer);
    const key = window.FWA && FWA.cartoApiKey ? `?key=${encodeURIComponent(FWA.cartoApiKey)}` : '';
    tileLayer = L.tileLayer(`https://{s}.basemaps.cartocdn.com/${light ? 'light_all' : 'dark_all'}/{z}/{x}/{y}{r}.png${key}`, {
        attribution: '© OpenStreetMap © CARTO', subdomains: 'abcd', maxZoom: 20,
    }).addTo(map);
}
const canvas = L.canvas({ padding: 0.5 });
const pinLayer = L.layerGroup().addTo(map);
const pathLayer = L.layerGroup().addTo(map);
const selLayer = L.layerGroup().addTo(map);
let didFit = false;

// ─────────────── data loading ───────────────
function rangeMs() {
    if (S.day === 'all') return [null, null];
    const [y, m, d] = S.day.split('-').map(Number);
    let a = new Date(y, m - 1, d).getTime(), b = new Date(y, m - 1, d + 1).getTime();
    if (S.tFrom) { const [h, mi] = S.tFrom.split(':').map(Number); a = new Date(y, m - 1, d, h, mi).getTime(); }
    if (S.tTo) { const [h, mi] = S.tTo.split(':').map(Number); b = new Date(y, m - 1, d, h, mi).getTime(); }
    return [a, b];
}
function rangeQuery() { const [a, b] = rangeMs(); return (a ? `from=${a}` : '') + (b ? `&to=${b}` : ''); }

async function loadSummary() {
    S.summary = await api('/api/summary');
    S.days = S.summary.days;
    const sel = $('daySel');
    sel.innerHTML = '<option value="all">All days</option>' +
        S.days.map(d => `<option value="${d.day}">${d.day}</option>`).join('');
    if (S.day !== 'all' && !S.days.find(d => d.day === S.day)) S.day = 'all';
    if (S.day === 'all' && S.days.length) S.day = S.days[S.days.length - 1].day;
    sel.value = S.day;
    $('navImpCnt').textContent = S.summary.imports || '';
    $('sbSub').innerHTML = S.days.length
        ? `Fieldwatch logs · <strong>${S.days.length}</strong> day(s) · <strong>${S.summary.imports}</strong> file(s)`
        : 'Fieldwatch logs · no data yet — open <strong>Imports</strong>';
}

async function loadAll() {
    const q = rangeQuery();
    const [dv, pa, tl] = await Promise.all([api('/api/devices?' + q), api('/api/path?' + q), api('/api/timeline?' + q + tlCatQuery())]);
    S.devices = dv.devices; S.hears = dv.hears; S.path = pa.parts; S.timeline = tl;
    if (window.haravaMask && window.haravaMask.on) setTimeout(() => window.haravaMask.refresh(), 0);
    tagOwn();
    renderAll();
    if (S.selected) selectDevice(S.selected.kind, S.selected.mac, false);
}

async function refresh() {
    await loadSummary();
    await loadOwn(false);
    await loadAll();
    if (!S.days.length) showView('imports');
}

// ─────────────── my devices ───────────────
const OWN_MATCH_TXT = { mac: 'Address is', mac_prefix: 'Address starts with', name: 'Name is', name_contains: 'Name contains' };
function ownMatch(d) { return S.own.find(r => ownRuleHits(r, d)) || null; }
function tagOwn() { S.devices.forEach(d => { const r = ownMatch(d); d.own = r ? r.label : ''; }); }
async function loadOwn(render = true) {
    S.own = (await api('/api/own')).own;
    tagOwn(); renderOwn();
    if (render) renderAll();
}
function renderOwn() {
    const counts = S.own.map(r => S.devices.filter(d => ownRuleHits(r, d)).length);
    const hidden = S.devices.filter(d => d.own).length;
    $('ownSum').textContent = S.own.length ? `· ${S.own.length} rule(s), ${hidden} radio(s) in this range` : '· none yet';
    $('ownCnt').textContent = hidden ? `(${hidden})` : '';
    const kindTxt = { '': 'Any', WIFI: 'Wi-Fi', BLE: 'Bluetooth' };
    $('ownBody').innerHTML = S.own.map((r, i) => `<tr style="cursor:default">
        <td><b>${esc(r.label)}</b></td><td>${kindTxt[r.kind || '']}</td><td>${OWN_MATCH_TXT[r.match]}</td>
        <td class="mono">${esc(r.value)}</td><td class="num">${counts[i]}</td>
        <td><button class="btn danger needs-analyze" onclick="delOwn(${r.id}, '${esc(r.label).replace(/'/g, "\\'")}')">Remove</button></td></tr>`).join('')
        || '<tr><td colspan="6" class="empty" style="padding:14px">No devices marked as yours yet.</td></tr>';
}
function ownRuleHits(r, d) {
    if (r.kind && r.kind !== d.kind) return false;
    const v = r.value.toLowerCase(), name = (d.name || d.group_name || '').toLowerCase(), mac = (d.mac || '').toLowerCase();
    return (r.match === 'mac' && mac === v) || (r.match === 'mac_prefix' && mac.startsWith(v)) ||
        (r.match === 'name' && !!name && name === v) || (r.match === 'name_contains' && name.includes(v));
}
function prefillOwn(kind, mac) {
    const d = S.devices.find(x => x.kind === kind && x.mac === mac) || { kind, mac, name: '', vendor: '', flags: '' };
    const byName = d.name && (d.kind === 'BLE' || (d.flags || '').includes('RAND'));
    $('ownKind').value = d.kind;
    $('ownMatch').value = byName ? 'name' : 'mac';
    $('ownValue').value = byName ? d.name : d.mac;
    $('ownLabel').value = d.name || d.vendor || '';
    showView('devices');
    $('ownBox').open = true;
    document.querySelector('#view-devices .page').scrollTop = 0;
    $('ownLabel').focus(); $('ownLabel').select();
    $('ownValue').dataset.real = $('ownValue').value; $('ownLabel').dataset.real = $('ownLabel').value;
    if (window.haravaMask && window.haravaMask.on) window.haravaMask.refresh();      // masked on screen only
    toast(byName ? 'Matching by name: this Bluetooth device may change its address' : 'Check the label and press Add');
}
// the form value as typed, or the real value behind a masked one (Mask addresses on)
function ownInput(id) { const el = $(id); return el.dataset.real && el.dataset.shown && el.value === el.dataset.shown ? el.dataset.real : el.value; }
async function addOwn() {
    const body = { kind: $('ownKind').value, match: $('ownMatch').value, value: ownInput('ownValue').trim(), label: ownInput('ownLabel').trim() };
    const r = await fetch('/api/own', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = '/login'; return; }
    const j = await r.json();
    if (!r.ok) { toast(j.error || 'Could not add'); return; }
    $('ownValue').value = ''; $('ownLabel').value = '';
    delete $('ownValue').dataset.real; delete $('ownLabel').dataset.real;
    await loadOwn();
    toast(`Added “${body.label || body.value}” to my devices`);
}
async function delOwn(id, label) {
    if (!confirm(`Remove “${label}” from my devices?`)) return;
    await api(`/api/own/${id}`, { method: 'DELETE' });
    await loadOwn();
}
function setHideOwn(v) { S.hideOwn = v; localStorage.setItem('fwa_hide_own', v ? '1' : '0'); renderAll(); }

// ─────────────── filters ───────────────
function isIdentified(d) { return !!(d.name || d.fleets); }
function filtered(skipIdentAs) {
    const q = S.q.toLowerCase();
    const list = S.devices.filter(d => {
        if (S.hideOwn && d.own) return false;
        if (S.kind !== 'ALL' && d.kind !== S.kind) return false;
        if (S.hiddenCats.has(d.category)) return false;
        if (S.minRssi > -105 && (d.rssi_max ?? -999) < S.minRssi) return false;
        if (S.flags.travel && !d.travelled) return false;
        if (S.flags.ident && !isIdentified(d)) return false;
        if (S.flags.sitOnly && !(d.in_sit && !d.log_rows)) return false;
        if (q && !(`${d.name} ${d.group_name || ''} ${d.mac} ${d.vendor} ${d.fleets} ${d.category} ${d.ident || ''} ${d.company || ''}`.toLowerCase().includes(q))) return false;
        return true;
    });
    const out = S.groupSame ? groupSame(list) : list;
    return S.identAs && !skipIdentAs ? out.filter(d => identKey(d) === S.identAs) : out;
}
// "Identified as" selector: one option per identification, grouped by device layer
function identKey(d) { return d.ident || (d.fleets ? d.fleets.split(/;\s*/)[0] : '') || ''; }
function renderIdentAs() {
    const sel = $('fIdentAs');
    if (!sel || sel === document.activeElement) return;
    const base = filtered(true), by = {};
    for (const d of base) {
        const k = identKey(d);
        if (!k) continue;
        const c = (by[d.category] = by[d.category] || {});
        c[k] = (c[k] || 0) + 1;
    }
    let html = `<option value="">Identified as: anything</option>`, found = !S.identAs;
    for (const cat of CAT_ORDER) {
        const c = by[cat];
        if (!c) continue;
        const items = Object.entries(c).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
        html += `<optgroup label="${esc((CAT_ICON[cat] ? CAT_ICON[cat] + ' ' : '') + cat)}">` +
            items.map(([k, n]) => { if (k === S.identAs) found = true; return `<option value="${esc(k)}">${esc(k)} (${n})</option>`; }).join('') +
            `</optgroup>`;
    }
    if (!found) html += `<option value="${esc(S.identAs)}">${esc(S.identAs)} (0)</option>`;
    sel.innerHTML = html;
    sel.value = S.identAs;
    sel.classList.toggle('active', !!S.identAs);
}
function setIdentAs(v) { S.identAs = v; renderAll(); }
// Addresses that Harava's database says belong to one device (same Mesh UUID, same custom service …)
// become one row / one pin: the loudest address represents them, hears are summed.
function groupSame(list) {
    const out = [], seen = new Map();
    for (const d of list) {
        if (!d.group || (d.group_size || 1) < 2) { out.push(d); continue; }
        let g = seen.get(d.group);
        if (!g) { g = { ...d, _n: 0, _hears: 0, _first: d.first, _last: d.last }; seen.set(d.group, g); out.push(g); }
        g._n += 1; g._hears += d.hears;
        g._first = Math.min(g._first, d.first); g._last = Math.max(g._last, d.last);
        if ((d.rssi_max ?? -999) > (g.rssi_max ?? -999)) {   // the loudest address represents the device
            const keep = { _n: g._n, _hears: g._hears, _first: g._first, _last: g._last };
            Object.assign(g, d, keep);
        }
    }
    for (const g of seen.values()) {
        if (g._n > 1) { g.hears = g._hears; g.first = g._first; g.last = g._last; if (!g.name && g.group_name) g.name = g.group_name; }
    }
    return out;
}
function setGroupSame(v) { S.groupSame = v; localStorage.setItem('fwa_group', v ? '1' : '0'); renderAll(); }
function setDay(v) { S.day = v; S.tFrom = S.tTo = ''; $('tFrom').value = $('tTo').value = ''; didFit = false; loadAll(); syncPhoneWindow(); }
function stepDay(n) {
    const days = S.days.map(d => d.day);
    if (!days.length) return;
    let i = days.indexOf(S.day);
    i = i < 0 ? days.length - 1 : Math.min(days.length - 1, Math.max(0, i + n));
    $('daySel').value = days[i]; setDay(days[i]);
}
function setTimes() { S.tFrom = $('tFrom').value; S.tTo = $('tTo').value; if (S.day === 'all') { toast('Pick a day first'); return; } loadAll(); }
let qTimer;
function setQuery(v) { clearTimeout(qTimer); qTimer = setTimeout(() => { S.q = v.trim(); renderAll(); }, 200); }
function setKind(k) { S.kind = k; document.querySelectorAll('#kindSeg button').forEach(b => b.classList.toggle('on', b.dataset.k === k)); renderAll(); }
function setMinRssi(v) { S.minRssi = +v; $('minRssiVal').textContent = S.minRssi <= -105 ? 'any' : S.minRssi + ' dBm'; renderAll(); }
function setFlag(k, v) { S.flags[k] = v; renderAll(); }
function toggleCat(c) { S.hiddenCats.has(c) ? S.hiddenCats.delete(c) : S.hiddenCats.add(c); saveHidden(); renderAll(); }
function soloCat(c) {
    // show only this layer; asking again for the same layer brings all layers back
    const onlyThis = S.hiddenCats.size === CAT_ORDER.length - 1 && !S.hiddenCats.has(c);
    S.hiddenCats = onlyThis ? new Set() : new Set(CAT_ORDER.filter(k => k !== c));
    saveHidden(); renderAll();
    if (!onlyThis) toast(`Showing only ${c} — click it again to show all layers`);
}
function soloGroup(g) {
    const names = CAT.filter(c => c[0] === g).map(c => c[1]);
    S.hiddenCats = new Set(CAT_ORDER.filter(k => !names.includes(k)));
    saveHidden(); renderAll();
}
function allCats(on) { S.hiddenCats = on ? new Set() : new Set(CAT_ORDER); saveHidden(); renderAll(); }
function resetFilters() {
    S.kind = 'ALL'; S.q = ''; S.identAs = ''; S.minRssi = -105; S.hiddenCats.clear(); saveHidden(); S.flags = { travel: false, ident: false, sitOnly: false };
    $('q').value = ''; $('minRssi').value = -105; $('fTravel').checked = $('fIdent').checked = $('fSitOnly').checked = false;
    setKind('ALL'); setMinRssi(-105);
}
function setLayer(k, v) {
    S.layers[k] = v; localStorage.setItem('fwa_layers', JSON.stringify(S.layers));
    $('timelinePanel').classList.toggle('hidden', !S.layers.timeline);
    $('catPanel').classList.toggle('hidden', !S.layers.cats);
    renderMap();
}

// ─────────────── rendering ───────────────
function renderAll() {
    const list = filtered();
    renderStats(list); renderChips(); renderCats(); renderMap(list); renderTable(list); renderOwn(); renderTlCats(); renderIdentAs();
    if (S.timeline) drawTimeline();
}

// timeline "Show" selector: all radios, or only one layer (Glasses & VR first)
function renderTlCats() {
    const sel = $('tlCat');
    if (sel === document.activeElement) return;
    const c = {};
    S.devices.forEach(d => { c[d.category] = (c[d.category] || 0) + 1; });
    sel.innerHTML = '<option value="">All radios</option>' + CAT.map(([g, k]) =>
        `<option value="${esc(k)}">${CAT_ICON[k] ? CAT_ICON[k] + ' ' : ''}${esc(k)} only (${c[k] || 0})</option>`).join('');
    sel.value = S.tlCat;
}
async function setTlCat(v) {
    S.tlCat = v;
    try { localStorage.setItem('fwa_tl_cat', v); } catch (e) {}
    S.timeline = await api('/api/timeline?' + rangeQuery() + tlCatQuery());
    drawTimeline();
}
function tlCatQuery() { return S.tlCat ? '&cat=' + encodeURIComponent(S.tlCat) : ''; }

function renderStats(list) {
    $('stDev').textContent = list.length.toLocaleString();
    $('stHears').textContent = list.reduce((a, d) => a + d.hears, 0).toLocaleString();
    $('stWifi').textContent = list.filter(d => d.kind === 'WIFI').length;
    $('stBle').textContent = list.filter(d => d.kind === 'BLE').length;
    $('stTrk').textContent = list.filter(d => d.category === 'Tracker').length;
    $('stTrv').textContent = list.filter(d => d.travelled).length;
    $('navDevCnt').textContent = list.length ? list.length.toLocaleString() : '';
}

function catCounts() {
    const c = {};
    S.devices.forEach(d => { if (S.kind === 'ALL' || d.kind === S.kind) c[d.category] = (c[d.category] || 0) + 1; });
    return c;
}
function catMark(k) {
    return CAT_ICON[k] ? `<span class="lay-ico">${CAT_ICON[k]}</span>` : `<span class="lay-dot" style="background:${CAT_COLORS[k]}"></span>`;
}
function renderChips() {
    // the "Device layers" list in the sidebar: every layer, grouped, with a switch and an "only" link
    const c = catCounts();
    let html = '', group = '';
    CAT.forEach(([g, k]) => {
        if (g !== group) { group = g; html += `<div class="lay-group" onclick="soloGroup('${g}')" title="Show only ${g}">${g}</div>`; }
        const n = c[k] || 0, k2 = k.replace(/'/g, "\\'");
        html += `<div class="lay-row ${n ? '' : 'zero'}">
            <label class="switch"><input type="checkbox" ${S.hiddenCats.has(k) ? '' : 'checked'} onchange="toggleCat('${k2}')"><span></span></label>
            ${catMark(k)}<span class="lay-name" onclick="soloCat('${k2}')" title="Show only ${esc(k)}">${esc(k)}</span>
            <small>${n}</small></div>`;
    });
    $('catChips').innerHTML = html;
}
function renderCats() {
    const c = catCounts();
    const max = Math.max(1, ...Object.values(c));
    $('catList').innerHTML = CAT_ORDER.filter(k => c[k]).map(k =>
        `<div class="cat-row ${S.hiddenCats.has(k) ? 'off' : ''}" onclick="toggleCat('${k.replace(/'/g, "\\'")}')"><span>${CAT_ICON[k] ? CAT_ICON[k] + ' ' : ''}${esc(k)}</span>
         <div class="cat-bar"><i style="width:${100 * c[k] / max}%;background:${CAT_COLORS[k]}"></i></div><b>${c[k]}</b></div>`).join('')
        || '<div class="note">No devices in this range.</div>';
}

function rssiRadius(r) { if (r == null) return 4; return Math.max(3, Math.min(9, 3 + (r + 100) / 9)); }
function spreadOffsets(list) {
    // stack radios heard at the same phone position into a small sunflower so each one can be clicked
    const groups = new Map(), out = new Map();
    list.forEach(d => {
        if (d.lat == null) return;
        const k = d.lat.toFixed(4) + ',' + d.lon.toFixed(4);
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(d);
    });
    groups.forEach(arr => {
        arr.forEach((d, i) => {
            if (!S.layers.spread || arr.length === 1) { out.set(d, [d.lat, d.lon]); return; }
            const r = 3.2 * Math.sqrt(i + 0.5), a = i * 2.39996;
            const dLat = (r * Math.cos(a)) / 111320, dLon = (r * Math.sin(a)) / (111320 * Math.cos(d.lat * Math.PI / 180));
            out.set(d, [d.lat + dLat, d.lon + dLon]);
        });
    });
    return out;
}

function renderMap(list) {
    list = list || filtered();
    pinLayer.clearLayers(); pathLayer.clearLayers();
    if (S.layers.path) {
        S.path.forEach(part => {
            L.polyline(part.map(p => [p[0], p[1]]), { color: cssVar('--accent'), weight: 2.5, opacity: .75, dashArray: '6 6', renderer: canvas }).addTo(pathLayer);
        });
    }
    const pos = spreadOffsets(list);
    const bounds = [];
    // draw weak first so loud ones end up on top
    [...list].sort((a, b) => (a.rssi_max ?? -999) - (b.rssi_max ?? -999)).forEach(d => {
        const p = pos.get(d);
        if (!p) return;
        bounds.push(p);
        const col = CAT_COLORS[d.category] || '#94a3b8';
        const wifi = d.kind === 'WIFI';
        const icon = CAT_ICON[d.category];
        const m = icon
            ? L.marker(p, {
                zIndexOffset: 1000,
                icon: L.divIcon({ className: '', iconSize: [26, 26], iconAnchor: [13, 13],
                    html: `<div class="emo-pin${d.tracker_mode === 'separated' ? ' alert' : ''}" style="border-color:${col}">${icon}</div>` }),
            })
            : L.circleMarker(p, {
                renderer: canvas, radius: rssiRadius(d.rssi_max), color: col, weight: wifi ? 2 : 1,
                fillColor: col, fillOpacity: wifi ? 0.12 : 0.75, opacity: d.travelled ? 1 : 0.9,
            });
        m.bindTooltip(`<b>${d.own ? '★ ' : ''}${esc(d.name || d.mac)}</b>${d.own ? ' <span style="color:#f59e0b">(mine: ' + esc(d.own) + ')</span>' : ''}<br>${d.kind === 'WIFI' ? 'Wi-Fi' : 'BLE'} · ${esc(d.category)}${d.fleets ? ' · ' + esc(d.fleets) : ''}${d.ident && !d.fleets ? '<br>' + esc(d.ident) + (d.ident_conf === 'low' ? ' (likely)' : '') : ''}${d._n > 1 ? '<br>one device, ' + d._n + ' addresses' : ''}<br>${d.rssi_max ?? '?'} dBm · ${d.hears} hears${d.travelled ? '<br><b style="color:#f59e0b">travelled ' + fmtM(d.spread_m) + '</b>' : ''}`,
            { className: 'dev-tip', direction: 'top', offset: [0, -4] });
        m.on('click', () => selectDevice(d.kind, d.mac, true));
        m.addTo(pinLayer);
    });
    if (!didFit) {
        const all = bounds.concat(...S.path.map(p => p.map(x => [x[0], x[1]])));
        if (all.length) { map.fitBounds(L.latLngBounds(all).pad(0.1)); didFit = true; }
    }
}

// ─────────────── timeline ───────────────
let tlChart = null;
function drawTimeline() {
    const tl = S.timeline;
    const binTxt = { 600000: '10 min', 3600000: 'hour', 86400000: 'day' }[tl.bin_ms] || '';
    $('tlBin').textContent = binTxt;
    $('tlNote').innerHTML = (S.tlCat
        ? (tl.hears ? `${tl.hears} hear(s)` : `<span style="color:var(--amber)">none heard in this range</span>`) : '') +
        (tl.sit_only ? ` <span title="Radios that only the Sit export has (the log missed them). They are counted once, when first heard.">· ${tl.sit_only} only in Sit</span>` : '');
    $('timelinePanel').classList.toggle('tl-filtered', !!S.tlCat);
    const withDate = tl.bin_ms >= 86400000 || S.day === 'all';
    const labels = tl.bins.map(b => {
        const d = new Date(b.t);
        return tl.bin_ms >= 86400000 ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}` : (withDate ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ` : '') + `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    });
    $('tlRange').textContent = tl.bins.length ? `${fmtTime(tl.bins[0].t, true).slice(0, 16)} → ${fmtTime(tl.bins[tl.bins.length - 1].t + tl.bin_ms, true).slice(0, 16)}` : '';
    const grid = cssVar('--border'), txt = cssVar('--text-secondary');
    const data = {
        labels, datasets: [
            { type: 'bar', label: 'Wi-Fi', data: tl.bins.map(b => b.WIFI), backgroundColor: 'rgba(96,165,250,.75)', stack: 's', yAxisID: 'y' },
            { type: 'bar', label: 'Bluetooth', data: tl.bins.map(b => b.BLE), backgroundColor: 'rgba(74,222,128,.75)', stack: 's', yAxisID: 'y' },
            { type: 'line', label: 'New devices', data: tl.bins.map(b => b.new), borderColor: cssVar('--accent'), borderWidth: 1.5, pointRadius: 0, tension: .3, yAxisID: 'y2' },
        ],
    };
    const opts = {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: txt, boxWidth: 10, font: { size: 10 } } }, tooltip: { mode: 'index', intersect: false } },
        scales: {
            x: { stacked: true, ticks: { color: txt, maxTicksLimit: 18, font: { size: 10 } }, grid: { color: grid } },
            y: { stacked: true, ticks: { color: txt, font: { size: 10 } }, grid: { color: grid }, title: { display: true, text: 'hears', color: txt, font: { size: 10 } } },
            y2: { position: 'right', ticks: { color: txt, font: { size: 10 } }, grid: { display: false }, title: { display: true, text: 'new', color: txt, font: { size: 10 } } },
        },
        onClick: (e, els) => {
            if (!els.length || S.day === 'all' || tl.bin_ms > 3600000) return;
            const b = tl.bins[els[0].index], d = new Date(b.t), e2 = new Date(b.t + tl.bin_ms);
            $('tFrom').value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
            $('tTo').value = `${pad(e2.getHours())}:${pad(e2.getMinutes())}`;
            setTimes(); toast('Showing ' + $('tFrom').value + '–' + $('tTo').value + ' (clear the times to see the whole day)');
        },
    };
    if (tlChart) { tlChart.data = data; tlChart.options = opts; tlChart.update(); }
    else tlChart = new Chart($('tlChart'), { data, options: opts });
}

// ─────────────── device detail ───────────────
let devChart = null;
async function selectDevice(kind, mac, fly) {
    S.selected = { kind, mac };
    const r = await api(`/api/device?kind=${encodeURIComponent(kind)}&mac=${encodeURIComponent(mac)}&${rangeQuery()}`);
    const d = r.device;
    const base = d && S.devices.find(x => x.kind === kind && x.mac === mac);
    if (base) { d.group_size = base.group_size; d.group_name = base.group_name; d.group = base.group; }  // grouping is across all addresses
    const P = $('detailPanel');
    selLayer.clearLayers();
    if (!d) { P.classList.add('hidden'); return; }
    const col = CAT_COLORS[d.category] || '#94a3b8';
    const sensor = d.sensor ? `<span>Sensor</span><b class="acc">${d.sensor.temp_c.toFixed(1)} °C · ${d.sensor.humidity.toFixed(0)} % · ${d.sensor.pressure_hpa} hPa · ${(d.sensor.battery_mv / 1000).toFixed(2)} V</b>` : '';
    const tm = d.tracker_mode ? `<span>Tracker mode</span><b class="${d.tracker_mode === 'separated' ? 'warn' : 'ok'}">${esc(d.tracker_mode)}</b>` : '';
    P.innerHTML = `
        <div class="panel-title">Selected radio <span class="r panel-close" onclick="closeDetail()">✕</span></div>
        <div class="dev-name">${esc(d.name || (d.kind === 'WIFI' ? '(hidden network)' : '(no name)'))}</div>
        <div class="dev-mac">${esc(d.mac)}</div>
        <span class="pill" style="background:${col}">${esc(d.category)}</span><span class="pill ghost">${d.kind === 'WIFI' ? 'Wi-Fi ' + esc(d.band) : 'Bluetooth LE'}</span>${d.travelled ? '<span class="pill" style="background:#f59e0b">travelled ' + fmtM(d.spread_m) + '</span>' : ''}
        <div class="kv">
            <span>Signatures</span><b>${esc(d.fleets || '–')}</b>
            ${d.ident ? `<span>Identified as</span><b>${identHtml(d)} <span class="conf-pill">${esc(d.ident_conf)}</span>${d.class === 'HARAVA' || !d.fleets ? ' <span style="color:var(--text-muted)">· Harava database</span>' : ''}</b>` : ''}
            ${d.company ? `<span>Company</span><b>${esc(d.company)}</b>` : ''}
            <span>Vendor</span><b>${esc(d.vendor || '–')}</b>
            ${(d.group_size || 1) > 1 ? `<span>Same device</span><b class="acc">${d.group_size} addresses${d.group_name ? ' · named ' + esc(d.group_name) : ''}<div class="mono" style="color:var(--text-secondary);font-weight:400">${esc(groupLabel(d.group))}</div></b>` : ''}
            ${tm}${sensor}
            <span>Hears</span><b class="acc">${d.hears}${d.in_sit ? ` <span style="font-weight:400;color:var(--text-secondary)">(log ${d.log_rows} rows · Sit ${d.sit_hits} hits)</span>` : ''}</b>
            ${d.in_sit && d.sit_rssi_min != null ? `<span>Sit RSSI</span><b>${d.sit_rssi_min} … ${d.rssi_max} dBm</b>` : ''}
            <span>First / last</span><b>${fmtTime(d.first)} – ${fmtTime(d.last)}</b>
            <span>RSSI max / med</span><b class="acc">${d.rssi_max ?? '?'} / ${d.rssi_med ?? '?'} dBm</b>
            ${d.kind === 'WIFI' ? `<span>Channel</span><b>${esc(d.channel)} · ${d.freq} MHz</b>` : ''}
            <span>Flags</span><b>${esc(d.flags || '–')}</b>
            <span>Places</span><b>${d.places} · spread ${fmtM(d.spread_m)}</b>
            ${d.mfg ? `<span>Company ID</span><b class="mono">0x${esc(d.mfg)}</b>` : ''}
            ${d.uuids ? `<span>Services</span><b class="mono">${esc(d.uuids.replace(/0000(\w{4})-0000-1000-8000-00805F9B34FB/gi, '$1'))}</b>` : ''}
            ${d.vendor_ie ? `<span>Vendor IEs</span><b class="mono">${esc(d.vendor_ie)}</b>` : ''}
            ${d.raw ? `<span>Raw</span><b class="mono">${esc(d.raw)}</b>` : ''}
            <span>Days seen</span><b>${r.days_seen.join(', ')}</b>
        </div>
        <div class="dev-chart"><canvas id="devChart"></canvas></div>
        ${d.flags.includes('RAND') ? '<div class="note">Random address: the same device can come back under a new address after a while.</div>' : ''}
        ${d.ident_note ? `<div class="note">${esc(d.ident_note)}</div>` : ''}
        ${d.vr ? '<div class="note" style="color:var(--amber)">🥽 The name says this is probably a VR headset (e.g. Meta Quest), not glasses. Meta uses the same Bluetooth ID for both.</div>' : ''}
        ${d.in_sit && !d.log_rows ? '<div class="note" style="color:var(--amber)">Heard only in the Sit export: the log missed this radio (Fieldwatch writes at most 16 log rows per scan).</div>' : ''}
        <div class="note">Dots on the map: every place your phone heard this radio (dashed line in time order).</div>
        ${ownMatch(d)
            ? `<div class="note" style="color:var(--amber)">★ My device: <b>${esc(ownMatch(d).label)}</b> (change it in Devices → My devices)</div>`
            : `<button class="btn ghost needs-analyze" style="margin-top:8px;width:100%" onclick="prefillOwn('${d.kind}','${d.mac}')">＋ Mark as my device</button>`}`;
    P.classList.remove('hidden');
    const pts = r.hears.filter(h => h.lat != null);
    if (pts.length) {
        L.polyline(pts.map(h => [h.lat, h.lon]), { color: '#f59e0b', weight: 2, dashArray: '4 5', renderer: canvas }).addTo(selLayer);
        pts.forEach(h => L.circleMarker([h.lat, h.lon], { renderer: canvas, radius: rssiRadius(h.rssi), color: '#f59e0b', weight: 2, fillColor: col, fillOpacity: .9 })
            .bindTooltip(`${fmtTime(h.ts)} · ${h.rssi} dBm`, { className: 'dev-tip' }).addTo(selLayer));
        if (fly) {
            const b = L.latLngBounds(pts.map(h => [h.lat, h.lon]));
            if (b.getNorthEast().distanceTo(b.getSouthWest()) < 60) map.setView(b.getCenter(), Math.max(map.getZoom(), 17));
            else map.fitBounds(b.pad(0.3));
        }
    }
    const txt = cssVar('--text-secondary'), grid = cssVar('--border');
    if (devChart) devChart.destroy();
    devChart = new Chart($('devChart'), {
        type: 'line',
        data: { labels: r.hears.map(h => fmtTime(h.ts).slice(0, 5)), datasets: [{ label: 'RSSI dBm', data: r.hears.map(h => h.rssi), borderColor: col, backgroundColor: col, pointRadius: 2, borderWidth: 1.2, tension: .2 }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
            scales: { x: { ticks: { color: txt, maxTicksLimit: 6, font: { size: 9 } }, grid: { color: grid } }, y: { ticks: { color: txt, font: { size: 9 } }, grid: { color: grid } } } },
    });
    document.querySelectorAll('#devBody tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.key === kind + '|' + mac));
}
function groupLabel(g) {
    if (!g) return '';
    const [kind, val] = [g.slice(0, g.indexOf(':')), g.slice(g.indexOf(':') + 1)];
    const what = { 'mesh-unprovisioned': 'Mesh device UUID', 'mesh-network': 'Mesh network ID', 'uuid128': 'custom service UUID' }[kind] || kind;
    return `${what} ${val.length > 24 ? val.slice(0, 24) + '…' : val}`;
}
function closeDetail() { S.selected = null; $('detailPanel').classList.add('hidden'); selLayer.clearLayers(); }

// ─────────────── devices table ───────────────
const COLS = [
    { k: 'category', t: 'Category' }, { k: 'kind', t: 'Radio' }, { k: 'name', t: 'Name' }, { k: 'mac', t: 'Address' },
    { k: 'ident', t: 'Identified as' }, { k: 'company', t: 'Company / vendor' }, { k: 'fleets', t: 'Signatures' }, { k: 'rssi_max', t: 'RSSI max', num: true },
    { k: 'rssi_med', t: 'Median', num: true }, { k: 'hears', t: 'Hears', num: true }, { k: 'channel', t: 'Ch' },
    { k: 'first', t: 'First' }, { k: 'last', t: 'Last' }, { k: 'spread_m', t: 'Spread', num: true }, { k: 'flags', t: 'Flags' },
];
const TABLE_MAX = 1500;
function renderTable(list) {
    $('devHead').innerHTML = '<th title="My devices: ＋ marks a radio as yours" style="cursor:default">★</th>' +
        COLS.map(c => `<th class="${S.sort.key === c.k ? 'sorted' : ''}" onclick="sortBy('${c.k}')">${c.t}${S.sort.key === c.k ? (S.sort.dir < 0 ? ' ▾' : ' ▴') : ''}</th>`).join('');
    const k = S.sort.key, dir = S.sort.dir;
    const rows = [...list].sort((a, b) => {
        const x = a[k] ?? '', y = b[k] ?? '';
        return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
    });
    const ownHidden = S.hideOwn ? S.devices.filter(d => d.own).length : 0;
    $('devSub').textContent = `${list.length.toLocaleString()} radios · ${S.day === 'all' ? 'all days' : S.day}${S.tFrom || S.tTo ? ' ' + (S.tFrom || '00:00') + '–' + (S.tTo || '24:00') : ''}` +
        (ownHidden ? ` · ${ownHidden} of my devices hidden` : '');
    const sel = S.selected ? S.selected.kind + '|' + S.selected.mac : '';
    $('devBody').innerHTML = rows.slice(0, TABLE_MAX).map(d => {
        const key = d.kind + '|' + d.mac;
        const tags = (d._n > 1 ? `<span class="tag" title="${d._n} changing addresses of one device">${d._n} addr.</span>` : '') +
            (d.travelled ? '<span class="tag amb">travelled</span>' : '') +
            (d.vr ? '<span class="tag">VR?</span>' : '') +
            (d.in_sit && !d.log_rows ? '<span class="tag" title="Only the Sit export has this radio; the log missed it">sit only</span>' : '') +
            (d.tracker_mode === 'separated' ? '<span class="tag red">separated</span>' : '') +
            (d.sensor ? `<span class="tag grn">${d.sensor.temp_c.toFixed(1)} °C</span>` : '');
        const ownCell = d.own
            ? `<span class="own-star" title="My device: ${esc(d.own)}">★</span>`
            : `<button class="own-add needs-analyze" title="Mark as my device" onclick="event.stopPropagation(); prefillOwn('${d.kind}','${d.mac}')">＋</button>`;
        return `<tr data-key="${key}" class="${key === sel ? 'sel' : ''}${d.own ? ' mine' : ''}" onclick="openFromTable('${d.kind}','${d.mac}')">
            <td class="own-cell">${ownCell}</td>
            <td><span class="cdot" style="background:${CAT_COLORS[d.category]}"></span>${esc(d.category)}</td>
            <td>${d.kind === 'WIFI' ? 'Wi-Fi' : 'BLE'}</td>
            <td title="${esc(d.name)}">${esc(d.name)}${tags}</td>
            <td class="mono">${d.mac}</td>
            <td title="${esc(d.ident_note || '')}">${identHtml(d)}</td>
            <td title="${esc(d.company || d.vendor)}">${esc(d.company || d.vendor)}</td>
            <td title="${esc(d.fleets)}">${esc(d.fleets)}</td>
            <td class="num">${d.rssi_max ?? ''}</td><td class="num">${d.rssi_med ?? ''}</td><td class="num">${d.hears}</td>
            <td>${esc(d.channel)}</td><td>${fmtTime(d.first, S.day === 'all')}</td><td>${fmtTime(d.last, S.day === 'all')}</td>
            <td class="num">${d.spread_m ? fmtM(d.spread_m) : ''}</td><td>${esc(d.flags)}</td></tr>`;
    }).join('');
    $('devMore').textContent = rows.length > TABLE_MAX ? `Showing the first ${TABLE_MAX} of ${rows.length} — narrow the filter or export CSV for all.` : '';
}
function identHtml(d) {
    if (!d.ident) return '';
    const cls = d.ident_conf === 'low' ? 'id-low' : (d.ident_conf === 'medium' ? 'id-med' : 'id-high');
    return `<span class="${cls}">${esc(d.ident)}</span>`;
}
function sortBy(k) { S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (['name', 'mac', 'vendor', 'fleets', 'category', 'kind', 'flags'].includes(k) ? 1 : -1) }; renderTable(filtered()); }
function openFromTable(kind, mac) { showView('map'); selectDevice(kind, mac, true); }
function exportCsv() {
    const list = filtered();
    const cols = ['own', 'kind', 'mac', 'name', 'vendor', 'category', 'ident', 'ident_conf', 'company', 'group', 'group_size', 'fleets', 'hears', 'log_rows', 'sit_hits', 'first', 'last', 'rssi_max', 'rssi_med', 'channel', 'freq', 'band', 'flags', 'spread_m', 'places', 'travelled', 'tracker_mode', 'lat', 'lon', 'mfg', 'uuids', 'raw'];
    const q = v => { v = v == null ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    const lines = [cols.join(',')].concat(list.map(d => cols.map(c => q(c === 'first' || c === 'last' ? fmtTime(d[c], true) : d[c])).join(',')));
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `devices-${S.day}${S.tFrom ? '-' + S.tFrom.replace(':', '') : ''}.csv`;
    a.click();
}

// ─────────────── phone check ───────────────
let pcChart = null, pcData = [];
function syncPhoneWindow() {
    if (!$('pcStart').value && S.tFrom) $('pcStart').value = S.tFrom;
}
function pcRange() {
    let day = S.day;
    if (day === 'all') day = S.days.length ? S.days[S.days.length - 1].day : null;
    if (!day) return null;
    const [y, m, d] = day.split('-').map(Number);
    const st = $('pcStart').value || S.tFrom;
    if (!st) return null;
    const [h, mi] = st.split(':').map(Number);
    const len = Math.max(1, parseFloat($('pcLen').value) || 3);
    const a = new Date(y, m - 1, d, h, mi).getTime();
    return { day, a, b: a + len * 60000 };
}
function phoneType(d, opts) {
    if (d.kind !== 'BLE') return null;
    if (!['Phone / PC', 'Unidentified'].includes(d.category)) return null;
    const f = d.fleets || '', n = (d.name || '').toLowerCase();
    if (/\btv\b|\[tv\]|qled|oled|series|monitor|soundbar|fridge|watch|buds|band|fit\d|ring|speaker/.test(n)) return null;
    if (f.includes('Microsoft Device')) return opts.ms ? 'Windows PC' : null;
    if (f.includes('Apple Device') || (d.mfg === '4C' && !f)) return 'Apple';
    if (d.mfg === '75') return 'Samsung';
    if (f.includes('Google') || f.includes('Fast Pair')) return 'Android';
    if (f.includes('Phone hotspot')) return 'Phone';
    if (opts.unident && d.category === 'Unidentified' && (d.flags || '').includes('RAND')) return 'Unidentified';
    return null;
}
function pcFromInputs() { loadPhoneCheck(); }
async function loadPhoneCheck() {
    const r = pcRange();
    if (!r) { $('pcVerdict').innerHTML = '<span style="color:var(--text-secondary)">Pick a day in the sidebar and set the window start time (for example when the phones went into the box).</span>'; return; }
    const dv = await api(`/api/devices?from=${r.a}&to=${r.b}&nosit=1`);
    pcData = dv.devices;
    $('pcWindow').textContent = `${r.day} ${fmtTime(r.a).slice(0, 5)}–${fmtTime(r.b).slice(0, 5)}`;
    runPhoneCheck();
}
function pcPhones() {
    const opts = { unident: $('pcUnident').checked, ms: $('pcMs').checked };
    const stat = $('pcStat').value;
    // my own devices (the teacher's phone, laptop…) never count as student phones
    return pcData.filter(d => !ownMatch(d)).map(d => ({ d, type: phoneType(d, opts), rssi: stat === 'med' ? d.rssi_med : d.rssi_max }))
        .filter(x => x.type && x.rssi != null).sort((a, b) => b.rssi - a.rssi);
}
function pcAuto() {
    const ph = pcPhones(), n = Math.max(1, parseInt($('pcExpected').value) || 30);
    if (!ph.length) { toast('No phones in this window'); return; }
    // only phones loud enough to be in the room take part; the box limit never goes below room limit + 3 dB
    const room = parseFloat($('pcRoom').value), floor = room + 3;
    const inRoom = ph.filter(x => x.rssi >= room);
    let lim = null, why = '';
    if (inRoom.length >= n) { lim = Math.max(floor, inRoom[n - 1].rssi - 2); why = `just below the ${n}th strongest phone`; }
    else {
        // fewer phones than expected: look for a clear gap (≥ 6 dB) between the box group and the rest of the room
        let best = 0, at = null;
        for (let i = 0; i < inRoom.length - 1; i++) {
            const g = inRoom[i].rssi - inRoom[i + 1].rssi;
            if (g > best) { best = g; at = Math.floor((inRoom[i].rssi + inRoom[i + 1].rssi) / 2); }
        }
        if (at !== null && best >= 6) { lim = Math.max(floor, at); why = `in the ${best} dB gap between the strong group and the rest`; }
    }
    if (lim === null) { toast('No clear box group found — keeping ' + $('pcBox').value + ' dBm. Try a window when all phones are in the box.'); return; }
    $('pcBox').value = lim;
    runPhoneCheck();
    toast(`Box limit set to ${lim} dBm (${why})`);
}
function runPhoneCheck() {
    const box = parseFloat($('pcBox').value), room = parseFloat($('pcRoom').value);
    const n = parseInt($('pcExpected').value) || 0;
    const ph = pcPhones().map(x => ({ ...x, zone: x.rssi >= box ? 'box' : (x.rssi >= room ? 'room' : 'out') }));
    const inBox = ph.filter(x => x.zone === 'box').length, inRoom = ph.filter(x => x.zone === 'room').length, out = ph.filter(x => x.zone === 'out').length;
    $('pcExp').textContent = n; $('pcInBox').textContent = inBox; $('pcInRoom').textContent = inRoom; $('pcOut').textContent = out;
    let v;
    if (!pcData.length) v = '<span style="color:var(--text-secondary)">No hears in this window.</span>';
    else if (inRoom > 0) v = `<span style="color:var(--red)">⚠ ${inRoom} phone${inRoom > 1 ? 's' : ''} heard in the room but not in the box.</span>` + (inBox < n ? ` <span style="color:var(--text-secondary);font-weight:400">${inBox} of ${n} look like they are in the box.</span>` : '');
    else if (inBox >= n) v = `<span style="color:var(--green)">✔ ${inBox} phones in the box (expected ${n}); nothing else heard in the room.</span>` + (inBox > n ? ` <span style="color:var(--text-secondary);font-weight:400">More than expected: address changes, tablets or watches can be counted twice.</span>` : '');
    else v = `<span style="color:var(--amber)">ℹ ${inBox} of ${n} phones heard in the box, none elsewhere in the room.</span> <span style="color:var(--text-secondary);font-weight:400">The rest may be switched off, in flight mode, quiet, or shielded by the box.</span>`;
    $('pcVerdict').innerHTML = v;
    const zl = { box: '<span class="tag grn">in box</span>', room: '<span class="tag red">in room</span>', out: '<span class="tag">weak</span>' };
    const order = { room: 0, box: 1, out: 2 };
    $('pcBody').innerHTML = [...ph].sort((a, b) => order[a.zone] - order[b.zone] || b.rssi - a.rssi).map(x =>
        `<tr onclick="openFromTable('${x.d.kind}','${x.d.mac}')"><td>${zl[x.zone]}</td><td>${x.type}</td><td>${esc(x.d.name)}</td><td class="mono">${x.d.mac}</td>
         <td class="num">${x.rssi}</td><td class="num">${x.d.hears}</td><td>${fmtTime(x.d.first)}</td><td>${fmtTime(x.d.last)}</td></tr>`).join('')
        || '<tr><td colspan="8" class="empty">No phones found in this window.</td></tr>';
    // histogram, 2 dB bins
    const bins = [];
    for (let b = -104; b <= -20; b += 2) bins.push(b);
    const cnt = bins.map(b => ph.filter(x => x.rssi >= b && x.rssi < b + 2).length);
    const colors = bins.map(b => b >= box ? 'rgba(74,222,128,.85)' : (b >= room ? 'rgba(245,158,11,.85)' : 'rgba(148,163,184,.6)'));
    const txt = cssVar('--text-secondary'), grid = cssVar('--border');
    if (pcChart) pcChart.destroy();
    pcChart = new Chart($('pcChart'), {
        type: 'bar', data: { labels: bins.map(String), datasets: [{ data: cnt, backgroundColor: colors }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
            scales: { x: { ticks: { color: txt, font: { size: 10 } }, grid: { display: false } }, y: { ticks: { color: txt, precision: 0, font: { size: 10 } }, grid: { color: grid } } } },
    });
}

// ─────────────── imports ───────────────
async function loadImports() {
    const r = await api('/api/imports');
    $('impBody').innerHTML = r.imports.map(i => `<tr>
        <td><label class="switch"><input type="checkbox" ${i.active ? 'checked' : ''} ${window.FWA.role === 'view' ? 'disabled title="Needs the Analyze permission"' : ''} onchange="toggleImport(${i.id})"><span></span></label></td>
        <td title="${esc(i.path || '')}">${esc(i.filename)}</td><td>${i.ftype.toUpperCase()}</td>
        <td class="num">${i.rows.toLocaleString()}</td><td class="num">${i.path_pts || ''}</td>
        <td>${i.first_ts ? fmtTime(i.first_ts, true).slice(0, 16) + ' → ' + fmtTime(i.last_ts, true).slice(11, 16) : ''}</td>
        <td>${esc(i.imported_at.replace('T', ' '))}</td>
        <td><button class="btn danger needs-analyze" onclick="deleteImport(${i.id}, '${esc(i.filename)}')">Delete</button></td></tr>`).join('')
        || '<tr><td colspan="8" class="empty">Nothing imported yet. Drop files above or press Scan folder.</td></tr>';
}
function showResults(res) {
    $('impResults').innerHTML = res.map(r => r.status === 'imported'
        ? `<div class="ok">✔ ${esc(r.file)} — ${r.rows.toLocaleString()} rows${r.path_pts ? ', ' + r.path_pts + ' path points' : ''}${r.note ? ' <span style="color:var(--amber)">(' + esc(r.note) + ')</span>' : ''}</div>`
        : r.status === 'skipped' ? `<div class="skip">– ${esc(r.file)} — ${esc(r.reason)}</div>` : `<div class="err">✖ ${esc(r.file)} — ${esc(r.reason)}</div>`).join('');
}
async function uploadFiles(files) {
    if (!files.length) return;
    const fd = new FormData();
    [...files].forEach(f => fd.append('files', f));
    toast('Importing…');
    const r = await api('/api/import', { method: 'POST', body: fd });
    showResults(r.results); await loadImports(); await refresh(); showView('imports');
}
async function scanFolder() {
    toast('Scanning folder…');
    try { const r = await api('/api/scan', { method: 'POST' }); showResults(r.results.filter(x => x.status !== 'skipped').concat(r.results.filter(x => x.status === 'skipped'))); }
    catch (e) { toast('Scan failed: ' + e.message); }
    await loadImports(); await refresh(); showView('imports');
}
async function toggleImport(id) { await api(`/api/imports/${id}/toggle`, { method: 'POST' }); await refresh(); showView('imports'); }
async function deleteImport(id, name) {
    if (!confirm(`Remove ${name} from the database? The file itself is not touched.`)) return;
    await api(`/api/imports/${id}`, { method: 'DELETE' }); await loadImports(); await refresh(); showView('imports');
}
const drop = $('drop');
['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', ev => uploadFiles(ev.dataTransfer.files));

// ─────────────── views ───────────────
function showView(v) {
    S.view = v;
    document.querySelectorAll('.view').forEach(e => e.classList.toggle('active', e.id === 'view-' + v));
    document.querySelectorAll('.nav-item').forEach(e => e.classList.toggle('active', e.dataset.view === v));
    if (v === 'map') setTimeout(() => map.invalidateSize(), 50);
    if (v === 'imports') loadImports();
    if (v === 'ids') loadIds();
    if (v === 'phones') { syncPhoneWindow(); loadPhoneCheck(); }
}

// ─────────────── start ───────────────
['path', 'timeline', 'cats', 'spread'].forEach(k => { $('l' + k[0].toUpperCase() + k.slice(1)).checked = S.layers[k]; });
$('timelinePanel').classList.toggle('hidden', !S.layers.timeline);
$('catPanel').classList.toggle('hidden', !S.layers.cats);
$('fHideOwn').checked = S.hideOwn;
$('fGroup').checked = S.groupSame;
applyTheme();
setTiles(document.documentElement.classList.contains('light'));
refresh().catch(e => toast('Could not load data: ' + e.message));

// ─────────────── identification database page ───────────────
async function loadIds() {
    const r = await api('/api/ids?' + rangeQuery());
    const c = r.counts;
    const pct = c.fieldwatch_left ? Math.round(100 * c.harava_identified / c.fieldwatch_left) : 0;
    $('idCards').innerHTML = `
        <div class="mini-card"><span>Bluetooth radios</span><b>${c.ble.toLocaleString()}</b></div>
        <div class="mini-card"><span>Fieldwatch left unidentified</span><b class="amb">${c.fieldwatch_left.toLocaleString()}</b></div>
        <div class="mini-card"><span>Identified by Harava</span><b class="grn">${c.harava_identified.toLocaleString()} <small style="font-size:12px">(${pct} %)</small></b></div>
        <div class="mini-card"><span>Still unidentified</span><b class="red">${c.still_unidentified.toLocaleString()}</b></div>
        <div class="mini-card"><span>Changing addresses → devices</span><b>${c.grouped_addresses.toLocaleString()} → ${c.grouped_devices}</b></div>`;
    $('idSource').innerHTML = `<b>${esc(r.db.name)}</b> version ${esc(r.db.version)} · ${r.db.rules} rules (<span class="mono">ids/harava_ids.json</span>)<br>
        Bluetooth SIG Assigned Numbers fetched ${esc(r.bt.fetched)}: ${r.bt.companies.toLocaleString()} company IDs, ${r.bt.uuids} service UUIDs, ${r.bt.ad_types} AD types
        (<span class="mono">ids/bt_assigned_numbers.json</span>, refresh with <span class="mono">python tools/update_bt_numbers.py</span>)`;
    const conf = { high: 'id-high', medium: 'id-med', low: 'id-low' };
    $('idRules').innerHTML = r.rules.map(x => `<tr title="${esc(x.note)}" style="cursor:default">
        <td class="mono">${esc(x.id)}</td><td><span class="${conf[x.confidence] || ''}">${esc(x.name)}</span></td>
        <td><span class="cdot" style="background:${CAT_COLORS[x.layer] || '#94a3b8'}"></span>${esc(x.layer)}</td>
        <td>${esc(x.confidence)}</td><td class="mono" style="max-width:320px">${esc(matchText(x.match))}</td>
        <td class="num">${x.hits ? x.hits.toLocaleString() : ''}</td><td class="num">${x.devices ?? ''}</td></tr>`).join('');
    $('idPatterns').innerHTML = r.patterns.map(p => `<tr style="cursor:default"><td class="num">${p.count}</td><td style="white-space:normal;max-width:520px">${esc(p.pattern)}</td>
        <td class="num">${p.rssi_max > -999 ? p.rssi_max : ''}</td><td class="mono">${esc(p.example)}</td><td class="mono" style="max-width:260px">${esc(p.example_raw)}</td></tr>`).join('')
        || '<tr><td colspan="5" class="empty" style="padding:14px">Everything heard in this range is identified.</td></tr>';
}
function matchText(m) {
    const parts = [];
    if (m.name_regex) parts.push(`name ~ ${m.name_regex}`);
    if (m.mfg) parts.push(`company ${m.mfg}` + (m.mfg_data_prefix ? ` data ${m.mfg_data_prefix}…` : ''));
    if (m.ad_type) parts.push(`AD ${m.ad_type}` + (m.ad_data_prefix ? ` ${m.ad_data_prefix}…` : ''));
    if (m.service_data_uuid) parts.push(`service data ${m.service_data_uuid}`);
    if (m.service_uuid_any) parts.push(`service ${m.service_uuid_any.join('/')}`);
    if (m.uuid128_any) parts.push(`service ${m.uuid128_any.map(u => u.slice(0, 8) + '…').join('/')}`);
    return parts.join(' · ');
}
async function reloadIds() {
    const r = await api('/api/ids/reload', { method: 'POST' });
    const errs = Object.entries(r.status).filter(([k, v]) => v !== 'ok');
    toast(errs.length ? 'Reload: ' + errs.map(([k, v]) => `${k}: ${v}`).join('; ') : `Database reloaded: ${r.rules} rules`);
    await loadAll(); loadIds();
}
