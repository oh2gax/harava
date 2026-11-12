/* Harava — helpers shared by the full page (app.js) and the phone app (m-app.js).
   Classic script: everything here is a global of the page. */
'use strict';

// Device layers: one per Fieldwatch signature class (names match CLASS_CATEGORY in the server), plus
// Harava's own splits (EXTRA_LAYERS in the server): of Home & IoT (TV & media, EV charger, Printer, Sensor)
// and of Phone / PC (Apple device, Android / Google, Samsung, Windows PC).
const PHONE_LAYERS = ['Phone / PC', 'Apple device', 'Android / Google', 'Samsung', 'Windows PC'];
// Watch-list layers get an icon pin on the map so they stand out from the dots.
const CAT = [
    ['Watch list', 'Glasses & VR', '#e879f9', '🕶️'],
    ['Watch list', 'Tracker', '#f43f5e', '🏷️'],
    ['Watch list', 'Surveillance', '#e11d48', '🎥'],
    ['Watch list', 'Police / body cam', '#3b82f6', '🚓'],
    ['Watch list', 'Drone', '#fb923c', '🚁'],
    ['Watch list', 'Hacking tool', '#d946ef', '💀'],
    ['Personal', 'Phone / PC', '#38bdf8'],
    ['Personal', 'Apple device', '#0ea5e9'],
    ['Personal', 'Android / Google', '#22c55e'],
    ['Personal', 'Samsung', '#1d4ed8'],
    ['Personal', 'Windows PC', '#ea580c'],
    ['Personal', 'Audio', '#a78bfa'],
    ['Personal', 'Wearable', '#c084fc'],
    ['Personal', 'Health', '#f472b6'],
    ['Places & things', 'Vehicle', '#f59e0b'],
    ['Places & things', 'EV charger', '#0d9488'],
    ['Places & things', 'Camera', '#fbbf24'],
    ['Places & things', 'Smart lock', '#a3e635'],
    ['Places & things', 'Home & IoT', '#4ade80'],
    ['Places & things', 'TV & media', '#6366f1'],
    ['Places & things', 'Printer', '#db2777'],
    ['Places & things', 'Sensor', '#65a30d'],
    ['Places & things', 'Thermostat', '#34d399'],
    ['Places & things', 'Shelf label / sign', '#22d3ee'],
    ['Places & things', 'Beacon', '#2dd4bf'],
    ['Places & things', 'Mesh radio', '#facc15'],
    ['Places & things', 'Two-way radio', '#b45309'],
    ['Background', 'Network', '#60a5fa'],
    ['Background', 'Unidentified', '#94a3b8'],
];
const CAT_COLORS = Object.fromEntries(CAT.map(c => [c[1], c[2]]));
const CAT_ICON = Object.fromEntries(CAT.filter(c => c[3]).map(c => [c[1], c[3]]));
const CAT_ORDER = CAT.map(c => c[1]);

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// A text as an argument of an inline handler (onclick="f(${jsArg(x)})"): esc() alone is not enough
// there, because the browser turns &#39; back into a quote before it runs the handler.
const jsArg = v => esc(JSON.stringify(String(v ?? '')));
// Uploads: the server takes at most FWA.maxUploadMb at a time (max_upload_mb in config.ini).
// Returns the message to show when these files are over it, '' when they fit.
function uploadTooBig(files) {
    const max = Number((window.FWA || {}).maxUploadMb) || 0, mb = b => (b / 1048576).toFixed(0);
    if (!max) return '';
    const list = [...files], big = list.find(f => f.size > max * 1048576);
    if (big) return `${big.name} is ${mb(big.size)} MB; the limit is ${max} MB.`;
    const sum = list.reduce((a, f) => a + f.size, 0);
    return sum > max * 1048576 ? `These files are ${mb(sum)} MB together; at most ${max} MB at a time. Send them in smaller lots.` : '';
}
const TOO_LARGE = () => `The upload is too large: at most ${(window.FWA || {}).maxUploadMb || '?'} MB at a time.`;
const pad = n => String(n).padStart(2, '0');
function fmtTime(ms, withDate) {
    if (!ms) return '';
    const d = new Date(ms);
    const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    return withDate ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${t}` : t;
}
function fmtM(m) { return m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m'; }
// The Fieldwatch signature table behind the layers (/api/ids → signatures): where it comes from, and a
// warning when this data has signature names it does not know (Fieldwatch was updated; Harava was not).
function sigTableHtml(s) {
    if (!s) return { line: '', warn: '' };
    const line = `Fieldwatch signature table: catalog ${s.catalog ?? '?'}${s.app_version ? ' (Fieldwatch ' + esc(s.app_version) + ')' : ''}, ` +
        `${s.names} names${s.fetched ? ', fetched ' + esc(s.fetched) : ''}`;
    const list = a => a.map(x => `<b>${esc(x.name)}</b> (${x.radios})`).join(', ');
    let warn = '';
    if (s.unknown && s.unknown.length) warn += `<div class="sig-warn">⚠ Signature names in this data that the table does not know: ${list(s.unknown)}. ` +
        'Fieldwatch has probably been updated: refresh the table (<span class="mono">python tools/update_fieldwatch_signatures.py</span>).</div>';
    if (s.no_layer && s.no_layer.length) warn += `<div class="sig-warn">⚠ Signatures of a class that has no layer yet: ${list(s.no_layer)}. They show as Unidentified.</div>`;
    return { line, warn };
}
function fmtDur(ms) { const m = Math.round(ms / 60000); return m >= 60 ? `${Math.floor(m / 60)} h ${pad(m % 60)} min` : `${m} min`; }

// Bluetooth coverage on the timeline (/api/timeline → bt_gaps, bt_cov). When the screen goes dark,
// many phones (Samsung above all) park the Bluetooth scan while Wi-Fi goes on: those stretches are
// shaded, because "nothing heard" there does not mean nothing was there.
const BT_GAP_TITLE = 'Stretches of at least 3 min where the log has Wi-Fi rows but no Bluetooth rows: the phone ' +
    'paused its Bluetooth scan (usually the screen was off). Bluetooth radios there were not heard. ' +
    "Keep Fieldwatch in front with 'Keep screen on' to avoid it.";
function btGapNote(tl) {
    const g = (tl && tl.bt_gaps) || [], c = (tl && tl.bt_cov) || {};
    if (!g.length) return '';
    const pct = c.logged_ms ? Math.round(100 * c.silent_ms / c.logged_ms) : null;
    const tip = `${BT_GAP_TITLE}\n\n${g.length} stretch(es), ${fmtDur(c.silent_ms)}` +
        (pct != null ? ` = ${pct} % of the time the log was running (${fmtDur(c.logged_ms)}).` : '.');
    return `<span class="bt-gap-note" title="${esc(tip)}">▨ Bluetooth paused ${fmtDur(c.silent_ms)}${pct != null ? ` (${pct} %)` : ''}</span>`;
}
function btGapsIn(tl, t0, t1) { return ((tl && tl.bt_gaps) || []).filter(g => g.to > t0 && g.from < t1); }
function btGapFooter(tl, binIndex) {
    const b = tl && tl.bins[binIndex];
    if (!b) return '';
    return btGapsIn(tl, b.t, b.t + tl.bin_ms).map(g =>
        `Bluetooth paused ${fmtTime(g.from).slice(0, 5)}–${fmtTime(g.to).slice(0, 5)}${g.phone ? ' (' + g.phone + ')' : ''}`).join('\n');
}
// Chart.js plugin: shades the stretches under the bars. Options: plugins.btGaps = { tl, color }.
const BT_GAP_PLUGIN = {
    id: 'btGaps',
    beforeDatasetsDraw(chart, args, opts) {
        const tl = opts && opts.tl;
        if (!tl || !tl.bins || !tl.bins.length || !(tl.bt_gaps || []).length) return;
        const x = chart.scales.x, area = chart.chartArea, ctx = chart.ctx;
        const t0 = tl.bins[0].t, t1 = tl.bins[tl.bins.length - 1].t + tl.bin_ms;
        const px = t => x.left + (Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0) * (x.right - x.left);
        ctx.save();
        for (const g of tl.bt_gaps) {
            const a = px(g.from), b = px(g.to);
            if (b - a < 0.5) continue;
            ctx.globalAlpha = 0.15; ctx.fillStyle = opts.color || '#f59e0b';
            ctx.fillRect(a, area.top, b - a, area.bottom - area.top);
            ctx.globalAlpha = 0.8;
            ctx.fillRect(a, area.top, Math.max(b - a, 1), 3);
        }
        ctx.restore();
    },
};

// My devices: a rule matches a radio by address, address start, name or part of the name. A watch or a phone
// has none of these for long (its address and its advertisement change, and it sends no name), so there is one
// more kind: "always with me" claims every device of a layer that travelled with the phone and came very close
// (d.companion, decided by the server).
const OWN_MATCH_TXT = { mac: 'Address is', mac_prefix: 'Address starts with', name: 'Name is', name_contains: 'Name contains', companion: 'Always with me, on layer' };
function ownRuleHits(r, d) {
    if (r.kind && r.kind !== d.kind) return false;
    const v = r.value.toLowerCase(), name = (d.name || d.group_name || '').toLowerCase(), mac = (d.mac || '').toLowerCase();
    if (r.match === 'companion') return !!d.companion && (v === 'any' || (d.category || '').toLowerCase() === v);
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
        if (g._n > 1) {
            g.hears = g._hears; g.first = g._first; g.last = g._last; if (!g.name && g.group_name) g.name = g.group_name;
            if (g.group_spread_m != null) g.spread_m = g.group_spread_m;     // the device's spread, not one address's
        }
    }
    return out;
}

// A radio's own page is built from that address alone. What is judged across all radios (which addresses
// are one device, and how that device travelled along the phone's whole track) comes from the list.
function carryAcross(d, base) {
    for (const k of ['group', 'group_size', 'group_name', 'group_spread_m', 'travel', 'travelled', 'travel_stops', 'travel_km', 'travel_min', 'travel_at', 'companion']) d[k] = base[k];
}

// ── Travelled with the phone ──
// The server cuts the phone's own track into stops and movement, and gives each device a level:
// 'stops' = heard at two stops far apart (it was with the phone at both: the strong sign),
// 'road'  = heard for minutes while the phone moved (shared the road: expected in traffic).
function travelTag(d, cls) {
    if (d.travel === 'stops') return `<span class="${cls} amb" title="Heard at ${d.travel_stops} of your stops, ${d.travel_km} km apart">at ${d.travel_stops} stops</span>`;
    if (d.travel === 'road') return `<span class="${cls}" title="Heard for ${d.travel_min} min while you moved ${d.travel_km} km; never at two stops">same road</span>`;
    return '';
}
function travelText(d) {
    if (d.travel === 'stops') return `With you at ${d.travel_stops} stops, ${d.travel_km} km apart`;
    if (d.travel === 'road') return (d.travel_km < 1 ? `Beside you for ${d.travel_min} min while you moved ${Math.round(d.travel_km * 1000)} m` : `Same road for ${d.travel_min} min, ${d.travel_km} km`) + (d.travel_stops === 1 ? ', from a stop' : '');
    return '';
}
// very close and travelling with you: yours, your passenger's, or something left in your bag
// "Always with me" is offered only where nothing else can hold the device: Bluetooth, no name.
function companionOffer(d) { return !!d.companion && !d.name && d.kind === 'BLE'; }
const COMPANION_HINT = 'Very close to you all the way. If it is your own watch, earbuds or phone, ＋ My device marks everything that travels with you like this as yours.';
function travelWhen(d) {            // the stops where it was heard: "11:21–13:46, 14:25–14:59"
    const hm = ms => { const t = new Date(ms); return pad(t.getHours()) + ':' + pad(t.getMinutes()); };
    const day = ms => { const t = new Date(ms); return t.getDate() + '.' + (t.getMonth() + 1) + '. '; };
    const at = d.travel_at || [], many = at.length && new Date(at[0][0]).toDateString() !== new Date(at[at.length - 1][1]).toDateString();
    return at.map(v => (many ? day(v[0]) : '') + hm(v[0]) + '–' + hm(v[1])).join(', ');
}

// ── Phone check (classroom): which radios count as phones, and where the box limit goes ──
function phoneType(d, opts) {
    if (d.kind !== 'BLE') return null;
    if (!PHONE_LAYERS.includes(d.category) && d.category !== 'Unidentified') return null;
    // a phone's Find My message comes from a second address: the phone is counted by its other messages
    if ((d.ident_id || '').startsWith('findmy-')) return null;
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
