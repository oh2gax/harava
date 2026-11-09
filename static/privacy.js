// Harava — "Mask addresses" for screenshots and presentations.
// Wi-Fi network names together with their addresses (BSSIDs) can be looked up in public Wi-Fi
// maps and point to a home. With masking on, the page shows:
//   * every MAC address with only its maker part:  40:86:CB:••:••:••
//   * every Wi-Fi network name (your own too) as  Wi-Fi network 1, 2 …
//   * names of other people's PCs and personal names ("Anna's iPhone") as  PC 1 … / •••'s iPhone
// Your own Bluetooth devices (My devices) keep their names. Masking only changes what is drawn on the
// screen; the data, the CSV export and the database are not changed.
(function () {
    const KEY = 'harava_mask';
    const MAC = /\b([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}):[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\b/g;
    const POSSESSIVE = /^(.*?)(['’]s\b)(.*)$/;
    let on = false, names = new Map(), observer = null, busy = false;

    try {
        if (new URLSearchParams(location.search).get('mask') === '1') localStorage.setItem(KEY, '1');
        on = localStorage.getItem(KEY) === '1';
    } catch (e) {}

    // a name of only digits and signs ("1", "42") says nothing about anyone, and masking it would also
    // change every count on the page that happens to be the same number
    const maskable = n => n.length >= 2 && !/^[\d\s.,:;%+\-−–#()]+$/.test(n);

    // which names to hide: built from the radios the page has loaded
    function buildNames() {
        names = new Map();
        const devs = (window.S && window.S.devices) || [];
        let w = 0, pc = 0;
        const seen = new Set();
        const wifi = n => { if (n && maskable(n) && !seen.has(n)) { seen.add(n); names.set(n, `Wi-Fi network ${++w}`); } };
        // every Wi-Fi name, your own included: your own network name with its address is what points to a home
        for (const d of devs) if (d.kind === 'WIFI') wifi((d.name || '').trim());
        for (const r of (window.S && window.S.own) || []) {
            if (r.kind === 'WIFI' || /^([0-9A-F]{2}:)/i.test(r.value || '')) {
                const dev = devs.find(d => d.kind === 'WIFI' && (d.mac === r.value || d.name === r.value || d.name === r.label));
                if (dev || r.kind === 'WIFI') { wifi((r.label || '').trim()); if (r.match !== 'mac' && r.match !== 'mac_prefix') wifi((r.value || '').trim()); }
            }
        }
        for (const d of devs) {
            const n = (d.name || '').trim();
            if (!n || !maskable(n) || d.own || seen.has(n) || d.kind === 'WIFI') continue;
            const m = n.match(POSSESSIVE);
            if (m) { seen.add(n); names.set(n, `•••${m[2]}${m[3]}`); continue; }
            if (/^(DESKTOP|LAPTOP)-[A-Z0-9]{5,}$/i.test(n) || d.ident === 'Windows PC' || d.ident === 'Windows device') {
                seen.add(n); names.set(n, `PC ${++pc}`);
            }
        }
    }

    function maskText(t) {
        if (!t) return t;
        let out = t.replace(MAC, '$1:••:••:••');
        const exact = names.get(out.trim());
        if (exact) return out.replace(out.trim(), exact);
        for (const [n, rep] of names) {
            if (n.length >= 6 && out.includes(n)) out = out.split(n).join(rep);
        }
        return out;
    }

    function walk(root) {
        if (!on || !root) return;
        busy = true;
        try {
            const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                acceptNode: n => (n.parentNode && /^(SCRIPT|STYLE|TEXTAREA)$/.test(n.parentNode.nodeName))
                    ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
            });
            const nodes = [];
            while (tw.nextNode()) nodes.push(tw.currentNode);
            for (const n of nodes) {
                const v = maskText(n.nodeValue);
                if (v !== n.nodeValue) n.nodeValue = v;
            }
            if (root.querySelectorAll) {
                root.querySelectorAll('[title]').forEach(el => {
                    const v = maskText(el.getAttribute('title'));
                    if (v !== el.getAttribute('title')) el.setAttribute('title', v);
                });
                root.querySelectorAll('input[type=text]').forEach(el => {
                    if (!el.closest('.own-form')) return;
                    const v = maskText(el.value);
                    if (v !== el.value) { el.value = v; el.dataset.shown = v; }   // app.js uses dataset.real for Add
                });
            }
        } finally { busy = false; }
    }

    function start() {
        buildNames();
        walk(document.body);
        if (observer) return;
        observer = new MutationObserver(muts => {
            if (busy || !on) return;
            if (window.S && window.S.devices && window.S.devices.length && !names.size) buildNames();
            for (const m of muts) {
                if (m.type === 'characterData') walk(m.target.parentNode);
                else m.addedNodes.forEach(n => walk(n.nodeType === 3 ? n.parentNode : n));
            }
        });
        observer.observe(document.body, { subtree: true, childList: true, characterData: true });
    }

    window.haravaMask = {
        get on() { return on; },
        set(v) {
            on = !!v;
            try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) {}
            if (on) start(); else location.reload();          // unmasking: draw the page again
        },
        refresh() { if (on) { buildNames(); walk(document.body); } },
    };
    if (on) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
        else start();
    }
})();
