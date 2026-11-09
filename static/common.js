/* Harava — helpers shared by the full page (app.js) and the phone app (m-app.js).
   Classic script: everything here is a global of the page. */
'use strict';

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

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = n => String(n).padStart(2, '0');
function fmtTime(ms, withDate) {
    if (!ms) return '';
    const d = new Date(ms);
    const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    return withDate ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${t}` : t;
}
function fmtM(m) { return m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m'; }

// My devices: a rule matches a radio by address, address start, name or part of the name
const OWN_MATCH_TXT = { mac: 'Address is', mac_prefix: 'Address starts with', name: 'Name is', name_contains: 'Name contains' };
function ownRuleHits(r, d) {
    if (r.kind && r.kind !== d.kind) return false;
    const v = r.value.toLowerCase(), name = (d.name || d.group_name || '').toLowerCase(), mac = (d.mac || '').toLowerCase();
    return (r.match === 'mac' && mac === v) || (r.match === 'mac_prefix' && mac.startsWith(v)) ||
        (r.match === 'name' && !!name && name === v) || (r.match === 'name_contains' && name.includes(v));
}


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

// ── Phone check (classroom): which radios count as phones, and where the box limit goes ──
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

// ph: [{rssi, …}] strongest first. Only phones loud enough to be in the room take part; the box limit
// never goes below the room limit + 3 dB. Returns {lim, why} or null (no clear box group).
function phoneBoxLimit(ph, n, room) {
    const floor = room + 3;
    const inRoom = ph.filter(x => x.rssi >= room);
    if (inRoom.length >= n) return { lim: Math.max(floor, inRoom[n - 1].rssi - 2), why: `just below the ${n}th strongest phone` };
    // fewer phones than expected: look for a clear gap (≥ 6 dB) between the box group and the rest of the room
    let best = 0, at = null;
    for (let i = 0; i < inRoom.length - 1; i++) {
        const gap = inRoom[i].rssi - inRoom[i + 1].rssi;
        if (gap > best) { best = gap; at = Math.floor((inRoom[i].rssi + inRoom[i + 1].rssi) / 2); }
    }
    if (at !== null && best >= 6) return { lim: Math.max(floor, at), why: `in the ${best} dB gap between the strong group and the rest` };
    return null;
}
// the verdict of a phone check: zone counts and a short text (html classes left to the caller)
function phoneZones(ph, box, room) {
    return ph.map(x => ({ ...x, zone: x.rssi >= box ? 'box' : (x.rssi >= room ? 'room' : 'out') }));
}
