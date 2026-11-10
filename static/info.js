/* Harava — What's new / User guides / About window (three tabs of one window, as in WSPR Logger).
   Loaded after app.js and shares its scope ($, esc, toast). */
'use strict';

let _wnData = null;
let _infoTab = 'whatsnew';

function openModal(title, html) {
    $('modalTitle').textContent = title;
    $('modalBody').innerHTML = html;
    $('modalBack').style.display = 'flex';
}
function closeModal() { $('modalBack').style.display = 'none'; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

async function loadWhatsNew() {
    try { _wnData = await (await fetch('/api/whatsnew')).json(); } catch (e) { return; }
    const badge = $('navVersion');
    if (!_wnData.version) { badge.textContent = ''; return; }
    badge.textContent = _wnData.version;
    let seen = null;
    try { seen = localStorage.getItem('harava_seen_version'); } catch (e) {}
    badge.classList.toggle('new', seen !== _wnData.version);
}

// **bold**, *italic* and `code` of the notes; everything else is plain text
function inlineMd(t) {
    return esc(t)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
        .replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

function whatsNewHtml() {
    const d = _wnData || { entries: [] };
    if (!d.entries || !d.entries.length) return '<div class="wn-ver">No notes are available.</div>';
    let html = '<div class="wn-ver">Short notes for users, newest first. Each version is one day of development.</div>';
    for (const e of d.entries) {
        const nice = new Date(e.date + 'T00:00:00Z').toLocaleDateString('en-GB',
            { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
        html += `<div class="wn-version"><span class="wn-vnum">${esc(e.version)}</span><span class="wn-vdate">${nice}</span></div>`;
        if (e.headline) html += `<div class="wn-headline">${inlineMd(e.headline)}</div>`;
        html += '<ul class="wn-notes">' + e.items.map(i =>
            '<li>' + (i.lead ? `<b>${inlineMd(i.lead)}</b>` + (i.text ? ' &mdash; ' : '') : '') + inlineMd(i.text) + '</li>').join('') + '</ul>';
    }
    return html;
}

async function guidesHtml() {
    let list = [];
    try { list = (await (await fetch('/api/guides')).json()).guides || []; }
    catch (e) { return '<div class="wn-ver">The guides could not be loaded.</div>'; }
    if (!list.length) return '<div class="wn-ver">No guides yet. PDF documents placed in the doc folder appear here.</div>';
    let html = '<div class="wn-ver">How to collect with Fieldwatch and read it in Harava, and how to run Harava on a server</div>';
    for (const g of list) {
        const url = '/doc/' + encodeURIComponent(g.file);
        const meta = [g.date, 'PDF', g.size_kb + ' kB'].filter(Boolean).join(' · ');
        html += `<div class="gd-item"><div class="gd-title">${esc(g.title)}</div>` +
            (g.description ? `<div class="gd-desc">${esc(g.description)}</div>` : '') +
            `<div class="gd-meta">${esc(meta)}</div>` +
            `<div class="gd-links" style="margin-top:6px"><a href="${url}" target="_blank" rel="noopener">Open</a>` +
            `<a href="${url}" download>Download</a></div></div>`;
    }
    return html;
}

function aboutHtml() {
    const ver = (_wnData && _wnData.version) ? esc(_wnData.version) : esc(FWA.version || '');
    return `
        <div class="about-row"><div class="about-lbl">Name</div><div><b>Harava</b> &mdash; Finnish for <i>rake</i>: it rakes through what your phone heard.</div></div>
        <div class="about-row"><div class="about-lbl">What it is</div><div>A local analyzer for the logs of the
            <a href="https://github.com/OffGridPete/Fieldwatch" target="_blank" rel="noopener">Fieldwatch</a> Android app (by Off Grid Pete),
            which listens to the Wi-Fi and Bluetooth radios around the phone. Harava imports its log and Sit exports and lists, filters and maps the radios.</div></div>
        <div class="about-row"><div class="about-lbl">Author</div><div>Otso Laakso / OH2GAX</div></div>
        <div class="about-row"><div class="about-lbl">Software</div><div>Harava ${ver}<div class="about-note">
            Python + Flask + SQLite; Leaflet 1.9.4 and Chart.js 4.4.0 (bundled); signature classes from the Fieldwatch catalog (MIT)</div></div></div>
        <div class="about-row"><div class="about-lbl">Your data</div><div>Everything stays on this computer, in harava.db next to the app.
            Only the map background is loaded from the internet (CARTO / OpenStreetMap).</div></div>
        <div class="about-row"><div class="about-lbl">Remember</div><div>A pin is where your phone was when it heard a radio,
            not where the radio is. A hear is a pattern match, not an identity.</div></div>`;
}

function infoTitle(tab) {
    return tab === 'whatsnew' ? "What's new" : tab === 'guides' ? 'User guides' : 'About Harava';
}

async function renderInfo() {
    const TABS = [['whatsnew', "What's new"], ['guides', 'User guides'], ['about', 'About']];
    const tabs = '<div class="info-tabs">' + TABS.map(([k, t]) =>
        `<button class="info-tab${_infoTab === k ? ' on' : ''}" data-tab="${k}" onclick="switchInfo(this.dataset.tab)">${t}</button>`).join('') + '</div>';
    $('modalBody').innerHTML = tabs + '<div class="wn-ver">Loading&hellip;</div>';
    const tab = _infoTab;
    const body = tab === 'whatsnew' ? whatsNewHtml() : tab === 'guides' ? await guidesHtml() : aboutHtml();
    if (tab !== _infoTab) return;                 // the user switched tab while loading
    $('modalBody').innerHTML = tabs + body;
    if (tab === 'whatsnew' && _wnData && _wnData.version) {
        try { localStorage.setItem('harava_seen_version', _wnData.version); } catch (e) {}
        $('navVersion').classList.remove('new');
    }
}
function switchInfo(tab) { _infoTab = tab; $('modalTitle').textContent = infoTitle(tab); renderInfo(); }
async function openInfo(tab) {
    if (!_wnData) await loadWhatsNew();
    openModal(infoTitle(tab), '');
    _infoTab = tab;
    renderInfo();
}
function openWhatsNew() { return openInfo('whatsnew'); }
function openGuides() { return openInfo('guides'); }
function openAbout() { return openInfo('about'); }

loadWhatsNew();
