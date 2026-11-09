#!/usr/bin/env python3
"""
Harava
======
Harava (Finnish for "rake") rakes through what your phone heard. It is a small local
web app for the logs of the Fieldwatch Android app: it imports Fieldwatch log and Sit
exports (CSV / JSON lines / KML) and lists, filters and maps the radios they contain.

Run:   python harava.py        (or start_analyzer.bat; then open http://127.0.0.1:5057)

Everything stays on this computer: the data goes into harava.db (SQLite) next to
this file. Only the map tiles are loaded from the internet by the browser; Leaflet
and Chart.js are bundled in static/vendor.

Remember when reading the data: every latitude / longitude in a Fieldwatch log
is where THIS PHONE was when it heard the radio, not where the radio is.
"""

import configparser
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import threading
import time
import webbrowser
import getpass
import hmac
import secrets
from datetime import timedelta
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from urllib.parse import quote_plus
from datetime import datetime, timezone

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

APP_NAME = "Harava"
HERE = os.path.dirname(os.path.abspath(__file__))
DOC_DIR = os.path.join(HERE, "doc")

# ─────────────────────────── configuration ───────────────────────────

CONFIG_DEFAULTS = {
    "host": "127.0.0.1",
    "port": "5057",
    "database": "harava.db",
    # Folder that "Scan folder" looks through (sub-folders included), relative to this
    # project folder. Keep your Fieldwatch exports there: data/ is never committed to git.
    "watch_folder": "data",
    "open_browser": "true",
    # A radio heard this far apart (metres) in at least two places is
    # marked "travelled with me".
    "travel_min_m": "500",
    # Sign-in. Once a user exists (python harava.py --add-admin) every page needs a sign-in.
    # require_login = true refuses to start without users (use it on a server).
    "require_login": "false",
    # true when Harava runs behind nginx with HTTPS: trust X-Forwarded-* and send cookies over HTTPS only
    "behind_proxy": "false",
    # how long "Keep me signed in" lasts
    "session_days": "30",
    # keep every uploaded export (gzip) in archive/<user>/ so it can be read again (Re-read)
    "keep_originals": "true",
}


def load_config():
    cp = configparser.ConfigParser()
    cp["app"] = CONFIG_DEFAULTS
    cp.read(os.path.join(HERE, "config.ini"), encoding="utf-8")
    c = cp["app"]
    watch = c.get("watch_folder")
    if not os.path.isabs(watch):
        watch = os.path.normpath(os.path.join(HERE, watch))
    db = c.get("database")
    if not os.path.isabs(db):
        db = os.path.join(HERE, db)
    return {
        "host": c.get("host"),
        "port": c.getint("port"),
        "db": db,
        "watch_folder": watch,
        "open_browser": c.getboolean("open_browser"),
        "travel_min_m": c.getfloat("travel_min_m"),
        "require_login": c.getboolean("require_login"),
        "behind_proxy": c.getboolean("behind_proxy"),
        "session_days": c.getint("session_days"),
        "keep_originals": c.getboolean("keep_originals"),
    }


# CARTO Basemaps API key (removes the "API key required" watermark on the map).
# Kept in api_keys.py next to this file, same as in wspr_logger; never commit or share that file.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from api_keys import CARTO_API_KEY
except ImportError:
    CARTO_API_KEY = ""
if not CARTO_API_KEY:
    print("[WARN] No CARTO key: the map shows a watermark. "
          "Copy api_keys.py.example to api_keys.py and add your key.")


CFG = load_config()


# ─────────────────────────── What's new and guides ───────────────────────────
# doc/whats_new.md holds short notes for users, newest first, one section per day of development:
#     ## v.09112026 — A headline in a few words
#     - **Short lead** — one or two sentences of what changed.
# The newest heading is the version shown under the app name (as in WSPR Logger).

_WN_HEAD = re.compile(r"^##\s+v\.(\d{2})(\d{2})(\d{4})\s*(?:[—–-]+\s*(.*))?$")
_WN_LEAD = re.compile(r"^\*\*(.+?)\*\*\s*(?:[—–:-]+\s*)?(.*)$")


def parse_whats_new(text):
    entries, cur, bullet = [], None, None

    def flush():
        nonlocal bullet
        if bullet is not None and cur is not None:
            t = " ".join(" ".join(bullet).split())
            m = _WN_LEAD.match(t)
            cur["items"].append({"lead": m.group(1).strip(), "text": m.group(2).strip()} if m
                                else {"lead": "", "text": t})
        bullet = None

    for line in text.splitlines():
        h = _WN_HEAD.match(line)
        if h:
            flush()
            dd, mm, yyyy, head = h.groups()
            cur = {"date": f"{yyyy}-{mm}-{dd}", "version": f"v.{dd}{mm}{yyyy}",
                   "headline": (head or "").strip(), "items": []}
            entries.append(cur)
            continue
        if line.startswith("- "):
            flush()
            bullet = [line[2:]]
        elif bullet is not None:
            if line.startswith("  ") and line.strip():
                bullet.append(line.strip())
            else:
                flush()
    flush()
    entries = [e for e in entries if e["items"]]
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def read_whats_new():
    try:
        with open(os.path.join(DOC_DIR, "whats_new.md"), encoding="utf-8") as fh:
            return parse_whats_new(fh.read())
    except OSError:
        return []


_wn = read_whats_new()
APP_VERSION = _wn[0]["version"] if _wn else "v.dev"

_PDF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$")


def list_guides():
    """The PDFs in doc/, with title / description / date from doc/guides.json (which also sets the order)."""
    manifest = []
    try:
        with open(os.path.join(DOC_DIR, "guides.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        manifest = [g for g in data if isinstance(g, dict) and isinstance(g.get("file"), str)]
    except (OSError, ValueError):
        pass
    meta = {g["file"]: g for g in manifest}
    try:
        present = {n for n in os.listdir(DOC_DIR)
                   if _PDF_NAME.match(n) and os.path.isfile(os.path.join(DOC_DIR, n))}
    except OSError:
        return []
    order = [g["file"] for g in manifest if g["file"] in present] + sorted(present - set(meta))
    return [{"file": n, "title": str(meta.get(n, {}).get("title") or n[:-4].replace("_", " ")),
             "description": str(meta.get(n, {}).get("description") or ""),
             "date": str(meta.get(n, {}).get("date") or ""),
             "size_kb": round(os.path.getsize(os.path.join(DOC_DIR, n)) / 1024)} for n in order]


def move_old_database():
    """Before the name Harava the database was fieldwatch.db: move it to harava.db once."""
    new, old = CFG["db"], os.path.join(HERE, "fieldwatch.db")
    if os.path.basename(new) != "harava.db" or os.path.exists(new) or not os.path.exists(old):
        return
    try:
        con = sqlite3.connect(old)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        os.replace(old, new)
        for ext in ("-wal", "-shm"):
            try:
                os.remove(old + ext)
            except OSError:
                pass
        print("  moved fieldwatch.db to harava.db (the new name of the app)")
    except (OSError, sqlite3.Error) as e:
        CFG["db"] = old
        print(f"[WARN] could not rename fieldwatch.db to harava.db ({e}); using fieldwatch.db as it is")

# Signature name -> class (FINDER, VEHICLE, PHONE ...) from the Fieldwatch catalog.
try:
    with open(os.path.join(HERE, "signature_classes.json"), encoding="utf-8") as fh:
        SIG_CLASS = json.load(fh)["classes"]
except Exception:
    SIG_CLASS = {}

# Signature class -> the category shown in the app. The first match in
# CLASS_PRIORITY wins when a radio matched several signatures.
# Every class is its own map layer (the front end lists the same names in CAT).
CLASS_CATEGORY = {
    "GLASSES": "Glasses & VR",
    "FINDER": "Tracker",
    "SURVEILLANCE": "Surveillance",
    "LAW_ENFORCEMENT": "Police / body cam",
    "DRONE": "Drone",
    "HACKING": "Hacking tool",
    "PHONE": "Phone / PC",
    "AUDIO": "Audio",
    "WEARABLE": "Wearable",
    "HEALTH": "Health",
    "VEHICLE": "Vehicle",
    "CAMERA": "Camera",
    "LOCK": "Smart lock",
    "HOME": "Home & IoT",
    "THERMOSTAT": "Thermostat",
    "SIGNAGE": "Shelf label / sign",
    "BEACON": "Beacon",
    "MESH": "Mesh radio",
    "ISP": "Network",
}
CLASS_PRIORITY = ["HACKING", "SURVEILLANCE", "LAW_ENFORCEMENT", "DRONE", "GLASSES", "FINDER",
                  "VEHICLE", "CAMERA", "LOCK", "MESH", "WEARABLE", "HEALTH", "AUDIO", "HOME",
                  "THERMOSTAT", "SIGNAGE", "BEACON", "PHONE", "ISP"]
# Glasses and VR headsets share one layer. Meta uses the same Bluetooth ID (0x058E / FEB8) for
# Ray-Ban glasses and Quest headsets; a name like this tells that it is probably a headset.
VR_NAME = re.compile(r"\bquest\b|oculus|\bpico\b|vision pro|valve index|\bvive\b", re.I)
VEHICLE_NAME = re.compile(r"polestar|tesla|obdlink|mitsubishi|audi_mmi|mbux|bmw|volvo|skoda|"
                          r"vw_|toyota|my ?car|carplay", re.I)

# ─────────────────────────── database ───────────────────────────

SCHEMA = """
-- One row per imported file. owner_id = the user who imported it (0 = no users yet, the desktop);
-- collector_id = the phone that recorded it. sha1 is unique per phone, so two users can import the same file.
CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT,
    sha1         TEXT NOT NULL,
    ftype        TEXT NOT NULL,
    rows         INTEGER DEFAULT 0,
    path_pts     INTEGER DEFAULT 0,
    first_ts     INTEGER,
    last_ts      INTEGER,
    imported_at  TEXT,
    active       INTEGER DEFAULT 1,
    note         TEXT,
    owner_id     INTEGER DEFAULT 0,
    collector_id INTEGER DEFAULT 0,
    verdict      TEXT DEFAULT '',     -- new | extends | overlaps | duplicate | sit update
    new_rows     INTEGER DEFAULT 0,   -- rows this file added
    known_rows   INTEGER DEFAULT 0,   -- rows that were already stored (from an earlier export)
    shared       TEXT DEFAULT '',     -- '' private, '*' all users, or user ids ',3,7,'
    archived     TEXT DEFAULT '',     -- the original file, compressed, under archive/ (for Re-read)
    size         INTEGER DEFAULT 0,
    sit_key      TEXT DEFAULT '',
    UNIQUE(collector_id, sha1)
);
-- The phones that record (Fieldwatch exports do not tell which phone made them).
CREATE TABLE IF NOT EXISTS collectors (
    id        INTEGER PRIMARY KEY,
    owner_id  INTEGER DEFAULT 0,
    name      TEXT NOT NULL,
    created   TEXT
);
-- Every hear once per phone: dkey is what makes a row the same row in another export
-- (log: time|kind|address|signal; KML: the same with K; Sit: the sit and the radio).
CREATE TABLE IF NOT EXISTS hears (
    id           INTEGER PRIMARY KEY,
    import_id    INTEGER,             -- the import that first brought it
    collector_id INTEGER NOT NULL DEFAULT 0,
    dkey         TEXT NOT NULL,
    ts        INTEGER,
    kind      TEXT,
    mac       TEXT,
    name      TEXT,
    rssi      INTEGER,
    channel   INTEGER,
    freq      INTEGER,
    oui       TEXT,
    vendor    TEXT,
    fleets    TEXT,
    mfg       TEXT,
    uuids     TEXT,
    flags     TEXT,
    raw       TEXT,
    lat       REAL,
    lon       REAL,
    vendor_ie TEXT,
    src       TEXT DEFAULT 'log',
    hits      INTEGER DEFAULT 1,
    last_ts   INTEGER,
    rssi_min  INTEGER,
    UNIQUE(collector_id, dkey)
);
CREATE INDEX IF NOT EXISTS ix_hears_ts  ON hears(ts);
CREATE INDEX IF NOT EXISTS ix_hears_dev ON hears(kind, mac);
-- which imports contain which hear (a hear can be in many overlapping exports)
CREATE TABLE IF NOT EXISTS import_rows (
    import_id INTEGER NOT NULL,
    hear_id   INTEGER NOT NULL,
    PRIMARY KEY (hear_id, import_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_import_rows_imp ON import_rows(import_id);
CREATE TABLE IF NOT EXISTS paths (
    import_id INTEGER NOT NULL,
    part      INTEGER,
    seq       INTEGER,
    ts        INTEGER,
    lat       REAL,
    lon       REAL
);
CREATE INDEX IF NOT EXISTS ix_paths_imp ON paths(import_id);
-- "My devices": rules for radios that are your own (hidden from lists, map and phone check), per user
CREATE TABLE IF NOT EXISTS own_devices (
    id       INTEGER PRIMARY KEY,
    owner_id INTEGER DEFAULT 0,
    label    TEXT NOT NULL,
    kind     TEXT DEFAULT '',      -- '' = any, 'WIFI' or 'BLE'
    match    TEXT NOT NULL,        -- mac | mac_prefix | name | name_contains
    value    TEXT NOT NULL,
    created  TEXT
);
-- Workbench (admins): what has been found out about one kind of unknown advertisement (a fingerprint)
CREATE TABLE IF NOT EXISTS wb_notes (
    fp       TEXT PRIMARY KEY,
    status   TEXT DEFAULT 'new',   -- new | investigating | identified | ignore | cannot
    note     TEXT DEFAULT '',
    updated  TEXT,
    by_user  TEXT
);
"""

_db_lock = threading.Lock()


def db():
    con = sqlite3.connect(CFG["db"], timeout=30)
    con.row_factory = sqlite3.Row
    return con


SCHEMA_VERSION = 2


def init_db():
    with db() as con:
        old = {r[1] for r in con.execute("PRAGMA table_info(hears)")}
        if old and "dkey" not in old:
            _migrate_v2(con)                         # before SCHEMA: its indexes need the new tables
        con.executescript(SCHEMA)
        con.execute("PRAGMA journal_mode=WAL")
        have = {r[1] for r in con.execute("PRAGMA table_info(own_devices)")}
        if "owner_id" not in have:
            con.execute("ALTER TABLE own_devices ADD COLUMN owner_id INTEGER DEFAULT 0")
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _migrate_v2(con):
    """Harava before 10.11.2026 stored the rows of every import separately. Move them into one row per
    hear (per phone) with links to the imports; the imports keep their ids, files, switches and paths.
    Everything belongs to owner 0 (the desktop) until claim_open_data() gives it to the first admin."""
    print("  database     : updating to the merged store (one-time, keeps all imports) …", flush=True)
    t0 = time.time()
    con.isolation_level = None                       # one transaction: all or nothing
    con.execute("BEGIN IMMEDIATE")
    try:
        _migrate_v2_steps(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.isolation_level = ""
    print(f"  database     : updated in {time.time() - t0:.1f} s", flush=True)


def _schema_statements():
    return [x.strip() for x in SCHEMA.split(";\n") if x.strip() and not all(
        ln.strip().startswith("--") or not ln.strip() for ln in x.splitlines())]


def _migrate_v2_steps(con):
    cols_old = {r[1] for r in con.execute("PRAGMA table_info(hears)")}
    for col, decl in (("src", "TEXT DEFAULT 'log'"), ("hits", "INTEGER DEFAULT 1"),
                      ("last_ts", "INTEGER"), ("rssi_min", "INTEGER")):
        if col not in cols_old:
            con.execute(f"ALTER TABLE hears ADD COLUMN {col} {decl}")
    con.execute("UPDATE hears SET src='kml' WHERE COALESCE(src,'log')='log' AND import_id IN "
                "(SELECT id FROM imports WHERE ftype='kml')")
    con.execute("ALTER TABLE hears RENAME TO hears_v1")
    con.execute("ALTER TABLE imports RENAME TO imports_v1")
    con.execute("DROP INDEX IF EXISTS ix_hears_ts")
    con.execute("DROP INDEX IF EXISTS ix_hears_dev")
    for st in _schema_statements():
        con.execute(st)
    con.execute("INSERT INTO collectors(id, owner_id, name, created) VALUES (1, 0, 'Phone 1', ?)",
                (datetime.now().isoformat(timespec="seconds"),))
    con.execute("""INSERT INTO imports(id, filename, path, sha1, ftype, rows, path_pts, first_ts, last_ts, imported_at,
                   active, note, owner_id, collector_id, verdict, new_rows, known_rows)
                   SELECT id, filename, path, sha1, ftype, rows, path_pts, first_ts, last_ts, imported_at,
                   active, note, 0, 1, '', 0, 0 FROM imports_v1 ORDER BY id""")
    imps = con.execute("SELECT * FROM imports ORDER BY id").fetchall()
    cols = ",".join(HEAR_COLS)
    for imp in imps:
        hs = [dict(r) for r in con.execute(f"SELECT {cols} FROM hears_v1 WHERE import_id=? ORDER BY id", (imp["id"],))]
        for h in hs:
            h["src"] = h["src"] or "log"
            h["hits"] = h["hits"] or 1
        sit_key, sit_match = "", None
        if imp["ftype"] == "sit":
            sit_key, sit_match = _sit_key(con, 1, hs, f"1-{imp['sha1'][:12]}")
        n, new, known, new_ts, known_ts = store_hears(con, imp["id"], 1, hs, sit_key)
        verdict, _ = _verdict(imp["ftype"], new, known, new_ts, known_ts, sit_match)
        con.execute("UPDATE imports SET rows=?, new_rows=?, known_rows=?, verdict=?, sit_key=? WHERE id=?",
                    (n, new, known, verdict, sit_key, imp["id"]))
        if imp["ftype"] == "sit" and sit_match and not imp["active"]:
            con.execute("UPDATE imports SET active=1 WHERE id=?", (imp["id"],))   # merged now: no double count
    n_old = con.execute("SELECT COUNT(*) FROM hears_v1").fetchone()[0]
    n_new = con.execute("SELECT COUNT(*) FROM hears").fetchone()[0]
    con.execute("DROP TABLE hears_v1")
    con.execute("DROP TABLE imports_v1")
    print(f"  database     : {len(imps)} imports, {n_old} stored rows → {n_new} distinct hears", flush=True)


# ─────────────────────────── helpers ───────────────────────────

ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b")
TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s):
    """Drop characters XML / JSON cannot carry (hidden SSIDs are often NUL bytes)."""
    if s is None:
        return ""
    return ILLEGAL_XML.sub("", str(s))


def clean_name(s):
    s = clean_text(s).strip()
    if s.lower() in ("<hidden>", "(hidden ssid)", "(hidden)", "hidden"):
        return ""
    return s


def to_int(v, default=None):
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def to_float(v):
    try:
        if v is None or v == "" or str(v).lower() == "null":
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def iso_to_ms(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def freq_band(freq, kind):
    if kind != "WIFI" or not freq:
        return ""
    if freq < 3000:
        return "2.4 GHz"
    if freq < 5925:
        return "5 GHz"
    return "6 GHz"


def norm_hear(d):
    """Return a hear dict with every column, normalised."""
    kind = (d.get("kind") or "").upper().replace("-", "")
    if kind not in ("WIFI", "BLE"):
        kind = "BLE" if "BLE" in kind else ("WIFI" if "WIFI" in kind else kind or "BLE")
    mac = (d.get("mac") or "").upper()
    lat, lon = to_float(d.get("lat")), to_float(d.get("lon"))
    if lat is not None and lon is not None and abs(lat) < 1e-9 and abs(lon) < 1e-9:
        lat = lon = None
    return {
        "ts": to_int(d.get("ts")),
        "kind": kind,
        "mac": mac,
        "name": clean_name(d.get("name")),
        "rssi": to_int(d.get("rssi")),
        "channel": to_int(d.get("channel"), 0),
        "freq": to_int(d.get("freq"), 0),
        "oui": (d.get("oui") or mac[:8]).upper(),
        "vendor": clean_text(d.get("vendor")).strip(),
        "fleets": clean_text(d.get("fleets")).strip(),
        "mfg": clean_text(d.get("mfg")).strip(),
        "uuids": clean_text(d.get("uuids")).strip(),
        "flags": clean_text(d.get("flags")).strip(),
        "raw": clean_text(d.get("raw")).strip(),
        "lat": lat,
        "lon": lon,
        "vendor_ie": clean_text(d.get("vendor_ie")).strip(),
        "src": d.get("src") or "log",
        "hits": to_int(d.get("hits"), 1),
        "last_ts": to_int(d.get("last_ts")),
        "rssi_min": to_int(d.get("rssi_min")),
    }


# ─────────────────────────── importers ───────────────────────────

SIT_MARKERS = ("first_seen", "hits")


def _truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def sit_row_to_hear(o):
    """One row of a Fieldwatch *Sit export* (CSV or JSON lines): one radio for the whole sit.
    Stored as one hear at first_seen that spans to last_seen, with the loudest signal and hear-point."""
    flags = []
    if _truthy(o.get("randomized")):
        flags.append("RAND")
    if _truthy(o.get("hidden")):
        flags.append("HIDDEN")
    sigs = [s.strip() for s in str(o.get("signatures") or "").split(";") if s.strip()]
    h = norm_hear({
        "ts": iso_to_ms(o.get("first_seen")), "last_ts": iso_to_ms(o.get("last_seen")),
        "kind": o.get("kind"), "mac": o.get("mac"), "name": o.get("name"),
        "rssi": o.get("rssi_max") if o.get("rssi_max") not in (None, "") else o.get("rssi"),
        "rssi_min": o.get("rssi_min"), "hits": o.get("hits"),
        "channel": o.get("channel"), "freq": o.get("frequency_mhz"),
        "fleets": "+".join(sigs), "flags": " ".join(flags),
        "lat": o.get("lat"), "lon": o.get("lon"), "src": "sit",
    })
    mine = None
    if _truthy(o.get("mine")):
        label = clean_text(o.get("custom_name")).strip() or h["name"] or h["mac"]
        mine = {"kind": h["kind"], "mac": h["mac"], "label": label}
    return h, mine

CSV_HEADER = "timestamp,iso,kind,mac,name,rssi,channel,freq,oui,vendor,fleets,mfg,uuids,flags,raw,lat,lon,vendor_ie".split(",")


def parse_csv(text):
    hears, mine = [], []
    text = text.replace("\x00", "")
    header = CSV_HEADER
    sit = False
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if row[0].strip().lower() in ("timestamp", "kind"):
            header = [h.strip().lower() for h in row]
            sit = all(m in header for m in SIT_MARKERS)
            continue
        d = dict(zip(header, row))
        if sit:
            h, m = sit_row_to_hear(d)
            if h["mac"] and h["ts"]:
                hears.append(h)
                if m:
                    mine.append(m)
            continue
        ts = to_int(d.get("timestamp")) or iso_to_ms(d.get("iso"))
        d["ts"] = ts
        h = norm_hear(d)
        if h["mac"] and h["ts"]:
            hears.append(h)
    return hears, [], mine


def parse_jsonl(text):
    hears, mine = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if all(m in o for m in SIT_MARKERS):
            h, m = sit_row_to_hear(o)
            if h["mac"] and h["ts"]:
                hears.append(h)
                if m:
                    mine.append(m)
            continue
        flags = []
        if o.get("rand"):
            flags.append("RAND")
        if o.get("hidden"):
            flags.append("HIDDEN")
        mfg = o.get("mfg")
        d = dict(o)
        d["ts"] = o.get("ts") or iso_to_ms(o.get("iso"))
        d["mfg"] = format(mfg, "X") if isinstance(mfg, int) else (mfg or "")
        d["uuids"] = (o.get("uuids") or "").replace(",", "|")
        d["flags"] = o.get("flags") or " ".join(flags)
        d["src"] = "log"
        d["hits"] = 1
        h = norm_hear(d)
        if h["mac"] and h["ts"]:
            hears.append(h)
    return hears, [], mine


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _parse_kml_desc(name, desc, folder):
    """Read one KML placemark description. Works with Fieldwatch's own KML
    ('WIFI AA:BB:.. · vendor · -78 dBm · ch 11 · ...') and with the analysis KML."""
    text = TAG_RE.sub("\n", desc or "")
    m = MAC_RE.search(text) or MAC_RE.search(name or "")
    if not m:
        return None
    mac = m.group(1).upper()
    ctx = f"{text} {name} {folder}"
    if re.search(r"\bWIFI\b|Wi-?Fi", ctx, re.I):
        kind = "WIFI"
    elif re.search(r"\bBLE\b", ctx):
        kind = "BLE"
    else:
        kind = "WIFI" if re.search(r"\bch \d+|MHz", text) else "BLE"
    rssi = None
    r = re.search(r"(-\d{2,3})\s*dBm", text)
    if r:
        rssi = int(r.group(1))
    channel = 0
    c = re.search(r"\bch (\d+)", text)
    if c:
        channel = int(c.group(1))
    freq = 0
    f = re.search(r"(\d{4}) MHz", text)
    if f:
        freq = int(f.group(1))
    vendor, fleets = "", []
    segs = [s.strip() for s in re.split(r"\s·\s|\n", text) if s.strip()]
    for s in segs:
        if MAC_RE.search(s) or "dBm" in s or s.startswith(("ch ", "Heard", "Observer", "log rows", "loudest")):
            continue
        parts = [p.strip() for p in re.split(r",\s*|\+", s) if p.strip()]
        if parts and all(p in SIG_CLASS for p in parts):
            fleets.extend(parts)
        elif not vendor and len(s) < 60 and not re.search(r"\d{2}:\d{2}:\d{2}", s):
            vendor = s
    nm = clean_name(name)
    if MAC_RE.fullmatch(nm or "x") or nm.upper() == mac or re.match(r"^(BLE|Wi-?Fi)$", nm, re.I):
        nm = ""
    # names in the analysis KML can carry " -78 dBm" at the end
    nm = re.sub(r"\s-?\d{2,3} dBm$", "", nm)
    if nm in SIG_CLASS or "+" in nm and all(p in SIG_CLASS for p in nm.split("+")):
        fleets.extend(nm.split("+"))
        nm = ""
    return {"kind": kind, "mac": mac, "name": nm, "rssi": rssi, "channel": channel,
            "freq": freq, "vendor": vendor, "fleets": "+".join(dict.fromkeys(fleets))}


def parse_kml(text):
    text = ILLEGAL_XML.sub("", text)
    root = ET.fromstring(text.encode("utf-8"))
    _strip_ns(root)
    hears, paths = [], []

    def walk(el, folder):
        for ch in el:
            tag = ch.tag
            if tag in ("Folder", "Document"):
                fn = ch.findtext("name") or ""
                walk(ch, (folder + " / " + fn) if folder else fn)
            elif tag == "Placemark":
                name = ch.findtext("name") or ""
                desc = ch.findtext("description") or ""
                when = ch.findtext("TimeStamp/when") or ch.findtext("TimeSpan/begin")
                ts = iso_to_ms(when)
                ls = ch.find(".//LineString/coordinates")
                if ls is not None and ls.text:
                    pts = []
                    for tok in ls.text.split():
                        xy = tok.split(",")
                        if len(xy) >= 2:
                            lon, lat = to_float(xy[0]), to_float(xy[1])
                            if lat is not None and lon is not None:
                                pts.append((lat, lon))
                    if len(pts) > 1:
                        paths.append({"name": name, "ts": ts, "pts": pts})
                    continue
                pt = ch.find(".//Point/coordinates")
                if pt is None or not pt.text:
                    continue
                xy = pt.text.strip().split(",")
                lon, lat = to_float(xy[0]), to_float(xy[1]) if len(xy) > 1 else None
                info = _parse_kml_desc(name, desc, folder)
                if not info:
                    continue
                info.update({"ts": ts, "lat": lat, "lon": lon, "src": "kml"})
                hears.append(norm_hear(info))

    walk(root, "")
    return hears, paths, []


def detect_type(filename, text):
    fn = filename.lower()
    if fn.endswith(".kml"):
        return "kml"
    if fn.endswith((".jsonl", ".json")):
        return "jsonl"
    if fn.endswith(".csv"):
        return "csv"
    head = text.lstrip()[:200]
    if head.startswith("<"):
        return "kml"
    if head.startswith("{"):
        return "jsonl"
    return "csv"


# ─────────────────────────── the data store ───────────────────────────
# Fieldwatch's log export has no time range: it copies the phone's whole rolling log (12 parts of
# about 1 MB) every time, so exports made on different days repeat each other. Harava therefore
# keeps every hear once per phone (collector) and links each import to the rows it contained:
# importing today's export after yesterday's only adds the new rows, and the import tells so
# (its verdict). A Sit export is one row per radio for the whole sit; a later export of the same
# running sit updates those rows. The original file is kept (compressed) for Re-read.

HEAR_COLS = ["ts", "kind", "mac", "name", "rssi", "channel", "freq", "oui", "vendor", "fleets",
             "mfg", "uuids", "flags", "raw", "lat", "lon", "vendor_ie", "src", "hits", "last_ts", "rssi_min"]
_FILL_TEXT = ("name", "vendor", "oui", "mfg", "vendor_ie")          # keep the first non-empty value
_LONGER_TEXT = ("raw", "uuids", "fleets", "flags")                  # keep the longer value (CSV cuts raw)


def dkey_of(h, sit_key=""):
    """What makes a hear the same hear in another export of the same phone."""
    if h["src"] == "sit":
        return f"S{sit_key}|{h['kind']}|{h['mac']}"
    return f"{'K' if h['src'] == 'kml' else 'L'}{h['ts']}|{h['kind']}|{h['mac']}|{h['rssi']}"


def _merged(old, new):
    """The stored row after another export brought the same hear: fill gaps, keep the richer value;
    for a Sit row the newer export (more hits) wins."""
    out = {}
    if new["src"] == "sit":
        if (new["hits"] or 0) >= (old["hits"] or 0):
            out = {c: new[c] for c in HEAR_COLS if new[c] not in (None, "")}
            out["ts"] = min(x for x in (old["ts"], new["ts"]) if x is not None) if (old["ts"] or new["ts"]) else None
        return {k: v for k, v in out.items() if old[k] != v}
    for c in _FILL_TEXT:
        if not old[c] and new[c]:
            out[c] = new[c]
    for c in _LONGER_TEXT:
        if len(new[c] or "") > len(old[c] or ""):
            out[c] = new[c]
    for c in ("channel", "freq"):
        if not old[c] and new[c]:
            out[c] = new[c]
    if old["lat"] is None and new["lat"] is not None:
        out["lat"], out["lon"] = new["lat"], new["lon"]
    return out


def store_hears(con, import_id, collector_id, hears, sit_key=""):
    """Add or merge the hears of one import and link them to it.
    Returns (rows, new, known, ts of new rows, ts of known rows)."""
    seen = set()
    new_ts, known_ts = [], []
    cols = ",".join(HEAR_COLS)
    for h in hears:
        k = dkey_of(h, sit_key)
        if k in seen:                       # the same row twice in one file
            continue
        seen.add(k)
        old = con.execute(f"SELECT id, {cols} FROM hears WHERE collector_id=? AND dkey=?", (collector_id, k)).fetchone()
        if old:
            upd = _merged(old, h)
            if upd:
                con.execute(f"UPDATE hears SET {','.join(c + '=?' for c in upd)} WHERE id=?", (*upd.values(), old["id"]))
            hid = old["id"]
            known_ts.append(h["ts"])
        else:
            cur = con.execute(f"INSERT INTO hears(import_id, collector_id, dkey, {cols}) "
                              f"VALUES (?,?,?,{','.join('?' * len(HEAR_COLS))})",
                              (import_id, collector_id, k, *[h[c] for c in HEAR_COLS]))
            hid = cur.lastrowid
            new_ts.append(h["ts"])
        con.execute("INSERT OR IGNORE INTO import_rows(import_id, hear_id) VALUES (?,?)", (import_id, hid))
    return len(seen), len(new_ts), len(known_ts), new_ts, known_ts


def _sit_key(con, collector_id, hears, fallback):
    """A later export of the same (still running) sit shares most (radio, first seen) pairs with the
    earlier one; Fieldwatch writes no sit id into the file. Returns (key, matched import or None)."""
    pairs = {(h["kind"], h["mac"], h["ts"]) for h in hears if h["src"] == "sit"}
    if not pairs:
        return fallback, None
    lo = min(p[2] for p in pairs if p[2]) if any(p[2] for p in pairs) else None
    cands = con.execute("SELECT id, filename, sit_key FROM imports WHERE collector_id=? AND ftype='sit' AND sit_key<>''"
                        " AND (? IS NULL OR last_ts >= ?) ORDER BY id DESC", (collector_id, lo, lo)).fetchall()
    for c in cands:
        theirs = {(r[0], r[1], r[2]) for r in con.execute(
            "SELECT h.kind, h.mac, h.ts FROM hears h JOIN import_rows r ON r.hear_id = h.id "
            "WHERE r.import_id=? AND h.src='sit'", (c["id"],))}
        if theirs and len(pairs & theirs) >= 0.8 * min(len(pairs), len(theirs)):
            return c["sit_key"], c
    return fallback, None


def _verdict(ftype, new, known, new_ts, known_ts, sit_match):
    if ftype == "sit":
        return ("sit update" if sit_match else "new"), (
            f"same sit as {sit_match['filename']}: {new} radio(s) more" if sit_match else "")
    if new == 0:
        return "duplicate", "every row was already stored from an earlier export"
    if known == 0:
        return "new", ""
    kmax = max((t for t in known_ts if t), default=None)
    nmin = min((t for t in new_ts if t), default=None)
    if kmax is not None and nmin is not None and nmin >= kmax:
        return "extends", f"{known} rows were already stored; {new} new rows from {fmt_local(nmin)} on"
    return "overlaps", f"{known} rows were already stored; {new} new rows"


def fmt_local(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m. %H:%M") if ms else ""


def _archive(owner_id, sha1, filename, data):
    """Keep the original export (gzip) so it can be read again when Harava reads more from it."""
    if not CFG.get("keep_originals", True):
        return ""
    ext = os.path.splitext(filename)[1].lower()[:8] or ".dat"
    rel = os.path.join("archive", str(int(owner_id)), f"{sha1}{ext}.gz")
    full = os.path.join(HERE, rel)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if not os.path.exists(full):
            with gzip.open(full, "wb") as fh:
                fh.write(data)
        return rel.replace(os.sep, "/")
    except OSError:
        return ""


def _parse(filename, data):
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    ftype = detect_type(filename, text)
    if ftype == "kml":
        hears, paths, mine = parse_kml(text)
    elif ftype == "jsonl":
        hears, paths, mine = parse_jsonl(text)
    else:
        hears, paths, mine = parse_csv(text)
    if hears and all(h["src"] == "sit" for h in hears):
        ftype = "sit"            # a Sit export (one row per radio), CSV or JSON lines
    return ftype, hears, paths, mine


def default_collector(con, owner_id):
    r = con.execute("SELECT id FROM collectors WHERE owner_id=? ORDER BY id LIMIT 1", (owner_id,)).fetchone()
    if r:
        return r[0]
    return con.execute("INSERT INTO collectors(owner_id, name, created) VALUES (?,?,?)",
                       (owner_id, "Phone 1", datetime.now().isoformat(timespec="seconds"))).lastrowid


def import_bytes(filename, data, path=None, owner_id=0, collector_id=None):
    sha1 = hashlib.sha1(data).hexdigest()
    with _db_lock, db() as con:
        if collector_id is None:
            collector_id = default_collector(con, owner_id)
        old = con.execute("SELECT id, filename FROM imports WHERE sha1=? AND collector_id=?", (sha1, collector_id)).fetchone()
        if old:
            return {"file": filename, "status": "skipped", "verdict": "duplicate",
                    "reason": f"this very file was already imported as {old['filename']}"}
    try:
        ftype, hears, paths, mine = _parse(filename, data)
    except ET.ParseError as e:
        return {"file": filename, "status": "error", "reason": f"KML could not be read: {e}"}
    except Exception as e:  # noqa: BLE001 – report anything to the user
        return {"file": filename, "status": "error", "reason": str(e)}
    if not hears and not paths:
        return {"file": filename, "status": "error", "reason": "no Fieldwatch rows found"}
    ts_list = ([h["ts"] for h in hears if h["ts"]] + [h["last_ts"] for h in hears if h["last_ts"]]
               + [p["ts"] for p in paths if p["ts"]])
    first_ts, last_ts = (min(ts_list), max(ts_list)) if ts_list else (None, None)
    archived = "" if path else _archive(owner_id, sha1, filename, data)
    with _db_lock, db() as con:
        cur = con.execute(
            "INSERT INTO imports(filename, path, sha1, ftype, rows, path_pts, first_ts, last_ts, imported_at,"
            " owner_id, collector_id, archived, size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (filename, path, sha1, ftype, 0, 0, first_ts, last_ts, datetime.now().isoformat(timespec="seconds"),
             owner_id, collector_id, archived, len(data)))
        iid = cur.lastrowid
        sit_key, sit_match = "", None
        if ftype == "sit":
            sit_key, sit_match = _sit_key(con, collector_id, hears, f"{collector_id}-{sha1[:12]}")
        n, new, known, new_ts, known_ts = store_hears(con, iid, collector_id, hears, sit_key)
        npts = _store_paths(con, iid, paths)
        verdict, vnote = _verdict(ftype, new, known, new_ts, known_ts, sit_match)
        con.execute("UPDATE imports SET rows=?, path_pts=?, new_rows=?, known_rows=?, verdict=?, sit_key=? WHERE id=?",
                    (n, npts, new, known, verdict, sit_key, iid))
        notes = [vnote, _avoid_double_counting(con, iid, ftype, first_ts, last_ts, collector_id)]
        if mine:
            added = _add_mine_rules(con, mine, owner_id)
            if added:
                notes.append(f"{added} radio(s) marked Mine in Fieldwatch added to My devices")
    r = {"file": filename, "status": "imported", "type": ftype, "rows": n, "path_pts": npts,
         "new_rows": new, "known_rows": known, "verdict": verdict}
    note = "; ".join(x for x in notes if x)
    if note:
        r["note"] = note
        with _db_lock, db() as con:
            con.execute("UPDATE imports SET note=? WHERE id=?", (note, iid))
    return r


def _store_paths(con, iid, paths):
    npts = 0
    for part, p in enumerate(paths):
        con.executemany("INSERT INTO paths(import_id, part, seq, ts, lat, lon) VALUES (?,?,?,?,?,?)",
                        [(iid, part, i, p["ts"], la, lo) for i, (la, lo) in enumerate(p["pts"])])
        npts += len(p["pts"])
    return npts


def reread_import(iid):
    """Read an import's original file again (from the archive, or its path) and merge what it gives."""
    with db() as con:
        imp = con.execute("SELECT * FROM imports WHERE id=?", (iid,)).fetchone()
    if not imp:
        return {"error": "no such import"}
    data = None
    if imp["archived"] and os.path.exists(os.path.join(HERE, imp["archived"])):
        with gzip.open(os.path.join(HERE, imp["archived"]), "rb") as fh:
            data = fh.read()
    elif imp["path"] and os.path.exists(imp["path"]):
        with open(imp["path"], "rb") as fh:
            data = fh.read()
    if data is None:
        return {"error": "the original file is not kept on this server"}
    ftype, hears, paths, _ = _parse(imp["filename"], data)
    with _db_lock, db() as con:
        n, new, known, _, _ = store_hears(con, iid, imp["collector_id"], hears, imp["sit_key"])
        con.execute("DELETE FROM paths WHERE import_id=?", (iid,))
        npts = _store_paths(con, iid, paths)
        con.execute("UPDATE imports SET rows=?, path_pts=?, note=? WHERE id=?",
                    (n, npts, f"read again {datetime.now():%d.%m.%Y %H:%M}", iid))
    return {"ok": True, "rows": n, "new_rows": new}


def delete_import(iid):
    """Remove an import; hears that no other import contains go with it."""
    with _db_lock, db() as con:
        imp = con.execute("SELECT archived FROM imports WHERE id=?", (iid,)).fetchone()
        con.execute("CREATE TEMP TABLE IF NOT EXISTS _gone(id INTEGER PRIMARY KEY)")
        con.execute("DELETE FROM _gone")
        con.execute("INSERT INTO _gone SELECT hear_id FROM import_rows WHERE import_id=?", (iid,))
        con.execute("DELETE FROM import_rows WHERE import_id=?", (iid,))
        con.execute("DELETE FROM hears WHERE id IN (SELECT id FROM _gone) "
                    "AND NOT EXISTS (SELECT 1 FROM import_rows r WHERE r.hear_id = hears.id)")
        con.execute("UPDATE hears SET import_id = (SELECT MIN(import_id) FROM import_rows r WHERE r.hear_id = hears.id) "
                    "WHERE import_id=?", (iid,))
        con.execute("DELETE FROM paths WHERE import_id=?", (iid,))
        con.execute("DELETE FROM imports WHERE id=?", (iid,))
        still = imp and imp["archived"] and con.execute("SELECT 1 FROM imports WHERE archived=?", (imp["archived"],)).fetchone()
    if imp and imp["archived"] and not still:
        try:
            os.remove(os.path.join(HERE, imp["archived"]))
        except OSError:
            pass


def _add_mine_rules(con, mine, owner_id=0):
    added = 0
    for m in mine:
        if con.execute("SELECT 1 FROM own_devices WHERE owner_id=? AND match='mac' AND value=? AND kind IN ('', ?)",
                       (owner_id, m["mac"], m["kind"])).fetchone():
            continue
        con.execute("INSERT INTO own_devices(owner_id, label, kind, match, value, created) VALUES (?,?,?,?,?,?)",
                    (owner_id, m["label"], m["kind"], "mac", m["mac"], datetime.now().isoformat(timespec="seconds")))
        added += 1
    return added


def _avoid_double_counting(con, iid, ftype, first_ts, last_ts, collector_id):
    """A KML is made from the same log, but its rows cannot be matched to the log's one by one
    (it has one point per radio): a KML of the same phone and time as a log or sit is switched off,
    and a log or sit switches such KML files off. Everything else is merged row by row instead.
    Anything switched off can be switched on again on the Data page."""
    if not first_ts or not last_ts:
        return ""
    overlap = ("SELECT id FROM imports WHERE id<>? AND active=1 AND collector_id=? AND {}"
               " AND first_ts <= ? AND last_ts >= ?")
    args = (iid, collector_id, last_ts, first_ts)
    if ftype == "kml":
        if con.execute(overlap.format("ftype<>'kml'"), args).fetchone():
            con.execute("UPDATE imports SET active=0 WHERE id=?", (iid,))
            return "switched off: a log or sit of the same time is already imported"
        return ""
    ids = [r[0] for r in con.execute(overlap.format("ftype='kml'"), args)]
    if ids:
        con.execute(f"UPDATE imports SET active=0 WHERE id IN ({','.join('?' * len(ids))})", ids)
        return f"switched off {len(ids)} KML file(s) of the same time"
    return ""


# Folders that Scan folder never enters (the app's own folders, also when the scan folder is the project root)
SCAN_SKIP_DIRS = {".git", "__pycache__", "static", "templates", "doc", "venv", ".venv", "env",
                  "node_modules", "tmp_files", "ids", "tools", "archive"}


IMPORT_EXT = (".csv", ".jsonl", ".json", ".kml")


def _importable(fn):
    f = os.path.basename(fn).lower()
    return f.endswith(IMPORT_EXT) and not f.startswith(("devices-export", "harava_workbench"))


def scan_folder(folder, owner_id=0, collector_id=None):
    """Import every new export in the folder (sub-folders included), oldest file first, so that each
    newer export is compared with what the older ones already brought."""
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in SCAN_SKIP_DIRS and not d.startswith("."))
        for fn in filenames:
            if _importable(fn):
                p = os.path.join(dirpath, fn)
                try:
                    found.append((os.path.getmtime(p), p, fn, dirpath))
                except OSError:
                    pass
    results = []
    for _, p, fn, dirpath in sorted(found):
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError as e:
            results.append({"file": fn, "status": "error", "reason": str(e)})
            continue
        r = import_bytes(fn, data, p, owner_id, collector_id)
        r["folder"] = os.path.relpath(dirpath, folder)
        results.append(r)
    return results


def import_upload(filename, data, owner_id=0, collector_id=None):
    """One uploaded file; a .zip is opened and its exports imported oldest first."""
    if filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return [{"file": filename, "status": "error", "reason": "not a readable zip file"}]
        members = sorted((i for i in zf.infolist() if not i.is_dir() and _importable(i.filename)
                          and i.file_size < 512 * 1024 * 1024), key=lambda i: (i.date_time, i.filename))
        if not members:
            return [{"file": filename, "status": "error", "reason": "no Fieldwatch exports in the zip"}]
        return [dict(import_bytes(os.path.basename(i.filename), zf.read(i), None, owner_id, collector_id),
                     folder=filename) for i in members]
    return [import_bytes(filename, data, None, owner_id, collector_id)]


# ─────────────────────────── Harava identification database ───────────────────────────
# ids/bt_assigned_numbers.json : Bluetooth SIG company IDs, 16-bit UUIDs and AD types
#                                (refresh with tools/update_bt_numbers.py)
# ids/harava_ids.json          : Harava's own rules for radios Fieldwatch leaves unidentified

IDS_DIR = os.path.join(HERE, "ids")
BT = {"companies": {}, "uuids": {}, "ad_types": {}}
HV = {"rules": []}
HV_RULES = []
_WB_CACHE = {}


NAME_RULE_IDS = set()     # rules that need a name; used when a nameless address takes its group's name
LOCAL_RULES = os.path.join(IDS_DIR, "local_rules.json")
LOCAL = {"rules": []}


def load_ids():
    """(Re)load both identification files; returns a short status for the Identification page."""
    global BT, HV, HV_RULES
    status = {}
    for fn, target in (("bt_assigned_numbers.json", "bt"), ("harava_ids.json", "hv")):
        try:
            with open(os.path.join(IDS_DIR, fn), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            status[fn] = f"not loaded: {e}"
            continue
        if target == "bt":
            BT = {k: data.get(k, {}) for k in ("companies", "uuids", "ad_types")}
            BT["source"], BT["fetched"] = data.get("source", ""), data.get("fetched", "")
        else:
            HV = data
        status[fn] = "ok"
    # ids/local_rules.json: rules made on this server in the Workbench (not in git). They come first,
    # so a local rule wins over the shared database while it is being tried out.
    LOCAL["rules"] = []
    try:
        with open(LOCAL_RULES, encoding="utf-8") as fh:
            LOCAL["rules"] = [dict(r, local=True) for r in json.load(fh).get("rules", [])]
        status["local_rules.json"] = f"ok, {len(LOCAL['rules'])} rule(s)"
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        status["local_rules.json"] = f"not loaded: {e}"
    rules = []
    for r in LOCAL["rules"] + HV.get("rules", []):
        m = dict(r.get("match", {}))
        if m.get("name_regex"):
            try:
                m["_name_re"] = re.compile(m["name_regex"], re.I)
            except re.error as e:
                status.setdefault("rule errors", []).append(f"{r.get('id')}: {e}")
                continue
        for k in ("mfg", "service_data_uuid"):
            if m.get(k):
                m[k] = m[k].upper().zfill(4)
        for k in ("mfg_data_prefix", "ad_data_prefix", "ad_type"):
            if m.get(k):
                m[k] = m[k].upper()
        if m.get("service_uuid_any"):
            m["service_uuid_any"] = {u.upper().zfill(4) for u in m["service_uuid_any"]}
        if m.get("uuid128_any"):
            m["uuid128_any"] = {u.upper() for u in m["uuid128_any"]}
        rules.append({**r, "_m": m})
    HV_RULES = rules
    _WB_CACHE.clear()
    NAME_RULE_IDS.clear()
    NAME_RULE_IDS.update(r["id"] for r in rules if r["_m"].get("name_regex"))
    return status


def parse_ad(raw_hex):
    """Split a raw advertisement into [(type 'XX', data bytes)]; None when it is not one."""
    try:
        b = bytes.fromhex(raw_hex)
    except ValueError:
        return None
    out, i = [], 0
    while i < len(b):
        n = b[i]
        if n == 0:
            break
        if i + 1 >= len(b):
            return out or None
        out.append((format(b[i + 1], "02X"), b[i + 2:i + 1 + n]))   # the last one may be cut short (CSV keeps 40 bytes)
        i += 1 + n
    return out or None


def _uuid16(u):
    u = (u or "").strip().upper()
    if re.fullmatch(r"[0-9A-F]{4}", u):
        return u
    m = re.fullmatch(r"0000([0-9A-F]{4})-0000-1000-8000-00805F9B34FB", u)
    return m.group(1) if m else None


def adv_features(mfg, raw, uuids):
    """What the advertisement says: company + manufacturer data, AD structures, service data, service UUIDs."""
    f = {"mfg": "", "mfg_data": b"", "ads": [], "sd": {}, "svc": set(), "uuid128": []}
    raw = (raw or "").strip().upper()
    if mfg:
        f["mfg"] = mfg.upper().zfill(4)
        try:
            f["mfg_data"] = bytes.fromhex(raw)       # with manufacturer data, Fieldwatch's raw is that data
        except ValueError:
            pass
    elif raw:
        ads = parse_ad(raw) or []                    # without it, raw is the whole advertisement
        f["ads"] = ads
        for t, data in ads:
            if t == "16" and len(data) >= 2:
                f["sd"][format(int.from_bytes(data[:2], "little"), "04X")] = data[2:]
            elif t in ("02", "03"):
                for k in range(0, len(data) - 1, 2):
                    f["svc"].add(format(int.from_bytes(data[k:k + 2], "little"), "04X"))
            elif t in ("06", "07", "21") and len(data) >= 16:      # 128-bit service UUIDs / service data
                u = data[:16][::-1].hex().upper()
                f["uuid128"].append(f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}")
            elif t == "FF" and len(data) >= 2 and not f["mfg"]:
                f["mfg"] = format(int.from_bytes(data[:2], "little"), "04X")
                f["mfg_data"] = data[2:]
    for u in (uuids or "").replace(",", "|").split("|"):
        u16 = _uuid16(u)
        if u16:
            f["svc"].add(u16)
        elif re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", u.strip()):
            if u.strip().upper() not in f["uuid128"]:
                f["uuid128"].append(u.strip().upper())
    f["svc"].update(f["sd"].keys())
    return f


def _rule_matches(m, kind, name, f):
    if m.get("kind") and m["kind"] != kind:
        return False
    if m.get("_name_re") and not m["_name_re"].search(name or ""):
        return False
    if m.get("mfg") and f["mfg"] != m["mfg"]:
        return False
    if m.get("mfg_data_prefix") and not f["mfg_data"].hex().upper().startswith(m["mfg_data_prefix"]):
        return False
    if m.get("ad_type"):
        hit = [d for t, d in f["ads"] if t == m["ad_type"]]
        if not hit:
            return False
        if m.get("ad_data_prefix") and not any(d.hex().upper().startswith(m["ad_data_prefix"]) for d in hit):
            return False
    if m.get("service_data_uuid") and m["service_data_uuid"] not in f["sd"]:
        return False
    if m.get("service_uuid_any") and not (m["service_uuid_any"] & f["svc"]):
        return False
    if m.get("uuid128_any") and not (m["uuid128_any"] & set(f["uuid128"])):
        return False
    return True


def identify(kind, name, mfg, raw, uuids):
    """Harava's identification of one radio: {'id','name','layer','confidence','note','company','group'}."""
    f = adv_features(mfg, raw, uuids) if kind == "BLE" else {"mfg": "", "mfg_data": b"", "ads": [], "sd": {}, "svc": set(), "uuid128": []}
    custom = next((u for u in f["uuid128"] if not u.endswith("-0000-1000-8000-00805F9B34FB")), "")
    company = BT["companies"].get(f["mfg"], "") if f["mfg"] else ""
    if not company:
        for u in sorted(f["svc"]):
            e = BT["uuids"].get(u)
            if e and e.get("kind") == "member":
                company = e["name"]
                break
    for r in HV_RULES:
        if _rule_matches(r["_m"], kind, name, f):
            group = ""
            g = r.get("group")
            if g:
                if g.get("source") == "mfg":
                    src = f["mfg_data"]
                elif g.get("source") == "service_data":
                    src = f["sd"].get(g.get("uuid", "").upper(), b"")
                else:
                    src = next((d for t, d in f["ads"] if t == g.get("ad_type", "").upper()), b"")
                part = src[g.get("from", 0):g.get("to", len(src))]
                if part:                                  # rules with the same group key share groups
                    group = f"{g.get('key', r['id'])}:{part.hex().upper()}"
            if not group and custom:
                group = f"uuid128:{custom}"
            return {"id": r["id"], "name": r["name"], "layer": r.get("layer", ""),
                    "confidence": r.get("confidence", "low"), "note": r.get("note", ""),
                    "company": company, "group": group}
    if company:                                       # no rule: at least say whose radio it is
        return {"id": "company", "name": f"{company} device", "layer": "", "confidence": "low",
                "note": "Only the company is known (Bluetooth SIG company ID or member UUID).",
                "company": company, "group": f"uuid128:{custom}" if custom else ""}
    if custom:                                        # a vendor's own 128-bit service: same kind of device
        return {"id": "custom-uuid", "name": f"Device with custom service {custom[:8]}…", "layer": "",
                "confidence": "low",
                "note": f"Advertises the 128-bit service UUID {custom}, which is not a Bluetooth SIG number. "
                        "Radios with the same UUID are the same kind of device, often one device under changing addresses.",
                "company": "", "group": f"uuid128:{custom}"}
    return None


load_ids()


# ─────────────────────────── analysis ───────────────────────────

def classify(fleets, name, kind):
    names = [f for f in (fleets or "").split("+") if f]
    classes = [SIG_CLASS.get(f, "") for f in names]
    for c in CLASS_PRIORITY:
        if c in classes:
            return c, CLASS_CATEGORY[c]
    if kind == "BLE" and VR_NAME.search(name or ""):
        return "GLASSES", CLASS_CATEGORY["GLASSES"]
    if VEHICLE_NAME.search(name or ""):
        return "VEHICLE", "Vehicle"
    if kind == "WIFI":
        return "ISP", "Network"
    return "", "Unidentified"


def tracker_mode(raw, fleets):
    raw = (raw or "").upper()
    f = fleets or ""
    if "Find Hub" in f and "16AAFE" in raw:
        b = raw.split("16AAFE", 1)[1][:2]
        return {"41": "separated", "40": "nearby"}.get(b, "")
    if "DULT" in f and "16B2FC" in raw:
        b = raw.split("16B2FC", 1)[1][2:4]
        try:
            return "near owner" if int(b, 16) & 1 else "separated"
        except ValueError:
            return ""
    if "AirTag" in f or raw.startswith("12"):
        if raw.startswith("1219"):
            return "separated"
        if raw.startswith("1202"):
            return "near owner"
    return ""


def ruuvi_decode(raw):
    try:
        b = bytes.fromhex(raw)
    except ValueError:
        return None
    if len(b) < 15 or b[0] != 5:
        return None
    t = int.from_bytes(b[1:3], "big", signed=True) * 0.005
    hum = int.from_bytes(b[3:5], "big") * 0.0025
    pres = (int.from_bytes(b[5:7], "big") + 50000) / 100
    batt = (int.from_bytes(b[13:15], "big") >> 5) + 1600
    return {"temp_c": round(t, 2), "humidity": round(hum, 1), "pressure_hpa": round(pres, 1),
            "battery_mv": batt}


def all_active_ids(con):
    return [r[0] for r in con.execute("SELECT id FROM imports WHERE active=1")]


def _shared_to(shared, uid):
    return shared == "*" or (bool(shared) and f",{uid}," in shared)


def visible_imports(con, user=None, scope=None, active_only=True):
    """The imports this request may use: the choke point of per-user data.
    scope: 'mine' · 'mine+shared' (default) · 'u:<id>' (one user's data) · 'all' (admins).
    Outside a request (command line, tests) or without users: every import."""
    try:
        user = user or current_user()
        scope = scope or session.get("scope") or "mine+shared"
    except RuntimeError:                          # no request context
        user = None
    rows = con.execute("SELECT id, owner_id, shared, active FROM imports").fetchall()
    if active_only:
        rows = [r for r in rows if r["active"]]
    if not user or user.get("open"):
        return [r["id"] for r in rows]
    uid, admin = user["id"], user["role"] == "admin"
    if scope == "all" and admin:
        keep = rows
    elif scope.startswith("u:") and scope[2:].isdigit():
        oid = int(scope[2:])
        keep = [r for r in rows if r["owner_id"] == oid and (admin or oid == uid or _shared_to(r["shared"], uid))]
    elif scope == "mine":
        keep = [r for r in rows if r["owner_id"] == uid]
    else:
        keep = [r for r in rows if r["owner_id"] == uid or _shared_to(r["shared"], uid)]
    return [r["id"] for r in keep]


def scope_ids(con):
    """visible_imports for this request, computed once per request."""
    try:
        if "harava_ids" not in g:
            g.harava_ids = visible_imports(con)
        return g.harava_ids
    except RuntimeError:
        return visible_imports(con)


def _ids_filter(con, ids):
    """SQL (and args) that keeps hears contained in one of the imports `ids`."""
    if not ids:
        return "0", []
    total = con.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    if len(ids) == total:
        return "1", []                            # everything: no need to look at the links
    return (f"EXISTS (SELECT 1 FROM import_rows r WHERE r.hear_id = h.id AND r.import_id IN ({','.join('?' * len(ids))}))",
            list(ids))


def query_hears(con, t_from=None, t_to=None, kind=None, mac=None, no_sit=False, ids=None):
    ids = scope_ids(con) if ids is None else ids
    if not ids:
        return []
    f, args = _ids_filter(con, ids)
    where = [f]
    if t_from:
        # a Sit-export row covers first..last seen, so it counts when that span overlaps the range
        where.append("COALESCE(h.last_ts, h.ts) >= ?")
        args.append(t_from)
    if t_to:
        where.append("h.ts < ?")
        args.append(t_to)
    if kind:
        where.append("h.kind = ?")
        args.append(kind)
    if mac:
        where.append("h.mac = ?")
        args.append(mac)
    if no_sit:
        where.append("COALESCE(h.src, 'log') <> 'sit'")
    sql = ("SELECT h.ts, h.kind, h.mac, h.name, h.rssi, h.channel, h.freq, h.oui, h.vendor, h.fleets, h.mfg, h.uuids,"
           " h.flags, h.raw, h.lat, h.lon, h.vendor_ie, COALESCE(h.src, 'log') src, COALESCE(h.hits, 1) hits,"
           " h.last_ts, h.rssi_min, h.collector_id"
           f" FROM hears h WHERE {' AND '.join(where)} ORDER BY h.ts")
    return [dict(r) for r in con.execute(sql, args)]


def build_devices(hears):
    groups = defaultdict(list)
    for h in hears:
        groups[(h["kind"], h["mac"])].append(h)
    out = []
    for (kind, mac), hs in groups.items():
        names = Counter(h["name"] for h in hs if h["name"])
        fleets = []
        for h in hs:
            for f in (h["fleets"] or "").split("+"):
                if f and f not in fleets:
                    fleets.append(f)
        fleet_s = "+".join(fleets)
        name = names.most_common(1)[0][0] if names else ""
        cls, cat = classify(fleet_s, name, kind)
        log_hs = [h for h in hs if h["src"] != "sit"]
        sit_hs = [h for h in hs if h["src"] == "sit"]
        rssis = [h["rssi"] for h in hs if h["rssi"] is not None]
        log_rssis = [h["rssi"] for h in log_hs if h["rssi"] is not None] or rssis
        sit_hits = sum(h["hits"] or 1 for h in sit_hs)
        sit_min = min((h["rssi_min"] for h in sit_hs if h["rssi_min"] is not None), default=None)
        located = [h for h in hs if h["lat"] is not None and h["lon"] is not None]
        loud = max(located, key=lambda h: (h["rssi"] if h["rssi"] is not None else -999)) if located else None
        spread = 0.0
        cells = set()
        if located:
            lats = [h["lat"] for h in located]
            lons = [h["lon"] for h in located]
            spread = haversine_m(min(lats), min(lons), max(lats), max(lons))
            cells = {(round(h["lat"] / 0.002), round(h["lon"] / 0.004)) for h in located}
        flags = sorted({f for h in hs for f in (h["flags"] or "").split() if f})
        vendor = next((h["vendor"] for h in hs if h["vendor"]), "")
        raw = next((h["raw"] for h in reversed(hs) if h["raw"]), "")
        mfg = next((h["mfg"] for h in hs if h["mfg"]), "")
        channels = sorted({h["channel"] for h in hs if h["channel"]})
        freq = next((h["freq"] for h in hs if h["freq"]), 0)
        uuids = next((h["uuids"] for h in hs if h["uuids"]), "")
        hv = identify(kind, name, mfg, raw, uuids)
        if hv and cat == "Unidentified" and hv["layer"]:
            cls, cat = "HARAVA", hv["layer"]          # Harava's own database identified it
        tmode = tracker_mode(raw, fleet_s) if cat == "Tracker" else ""
        sensor = ruuvi_decode(raw) if ("Ruuvi" in fleet_s or mfg == "499") and raw else None
        out.append({
            "kind": kind, "mac": mac, "name": name, "vendor": vendor,
            "fleets": fleet_s, "class": cls, "category": cat,
            "hears": max(len(log_hs), sit_hits), "log_rows": len(log_hs),
            "in_sit": bool(sit_hs), "sit_hits": sit_hits, "sit_rssi_min": sit_min,
            "vr": bool(cat == "Glasses & VR" and VR_NAME.search(name or "")),
            "first": min(h["ts"] for h in hs), "last": max(h["last_ts"] or h["ts"] for h in hs),
            "rssi_max": max(rssis) if rssis else None,
            "rssi_med": round(statistics.median(log_rssis)) if log_rssis else None,
            "channel": ",".join(map(str, channels)), "freq": freq, "band": freq_band(freq, kind),
            "flags": " ".join(flags), "mfg": mfg,
            "uuids": uuids,
            "ident": hv["name"] if hv else "", "ident_id": hv["id"] if hv else "",
            "ident_conf": hv["confidence"] if hv else "", "ident_note": hv["note"] if hv else "",
            "company": hv["company"] if hv else "", "group": hv["group"] if hv else "",
            "vendor_ie": next((h["vendor_ie"] for h in hs if h["vendor_ie"]), ""),
            "raw": raw,
            "lat": loud["lat"] if loud else None, "lon": loud["lon"] if loud else None,
            "spread_m": round(spread), "places": len(cells),
            "travelled": bool(spread >= CFG["travel_min_m"] and len(cells) >= 2),
            "tracker_mode": tmode, "sensor": sensor,
            "phones": len({h.get("collector_id") for h in hs}),
        })
    # addresses that share a fingerprint: how many, and a name if any of them told it
    by_group = defaultdict(list)
    for d in out:
        if d["group"]:
            by_group[d["group"]].append(d)
    for g, ds in by_group.items():
        names = Counter(x["name"] for x in ds if x["name"])
        if g.startswith("uuid128:"):
            # a shared custom service UUID alone is not proof: many devices of one maker use the same one
            # (Ruuvi tags, Windows PCs, Galaxy Buds). Only addresses that also tell the same name are joined.
            named = [x for x in ds if x["name"]] if len(names) == 1 else []
            for x in ds:
                if x not in named:
                    x["group"] = ""
            ds = named
            if len(ds) < 2:
                for x in ds:
                    x["group"] = ""
                continue
        gname = names.most_common(1)[0][0] if names else ""
        for x in ds:
            x["group_size"] = len(ds)
            x["group_name"] = gname
    for d in out:
        d.setdefault("group_size", 1)
        d.setdefault("group_name", "")
        # a nameless address of a named group: identify it again with the group's name
        # (the Mesh beacons of a "Charge" device carry no name, its other adverts do)
        if not d["name"] and d["group_name"] and d["group_size"] > 1 and d["kind"] == "BLE":
            hv = identify(d["kind"], d["group_name"], d["mfg"], d["raw"], d["uuids"])
            if hv and hv["id"] != d["ident_id"] and hv["id"] in NAME_RULE_IDS:
                d.update({"ident": hv["name"], "ident_id": hv["id"], "ident_conf": hv["confidence"],
                          "ident_note": hv["note"]})
                if d["class"] == "HARAVA" and hv["layer"]:
                    d["category"] = hv["layer"]
    out.sort(key=lambda d: (-(d["rssi_max"] if d["rssi_max"] is not None else -999)))
    return out


def build_path(hears, max_pts=6000):
    pts = []
    for h in hears:
        if h["lat"] is None or h["lon"] is None or not h["ts"]:
            continue
        if pts:
            lt, la, lo = pts[-1]
            if la == h["lat"] and lo == h["lon"]:
                continue
            if haversine_m(la, lo, h["lat"], h["lon"]) < 8 and h["ts"] - lt < 300000:
                continue
        pts.append((h["ts"], h["lat"], h["lon"]))
    if len(pts) > max_pts:
        step = len(pts) / max_pts
        pts = [pts[int(i * step)] for i in range(max_pts)]
    # split where the phone jumped (no fix for a while)
    parts, cur = [], []
    for p in pts:
        if cur and (p[0] - cur[-1][0] > 30 * 60000):
            parts.append(cur)
            cur = []
        cur.append(p)
    if cur:
        parts.append(cur)
    return [[[la, lo, t] for t, la, lo in part] for part in parts if len(part) > 1]


def range_args():
    return to_int(request.args.get("from")), to_int(request.args.get("to"))


# ─────────────────────────── web app ───────────────────────────

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


# ───────────────────────── users and sign-in ─────────────────────────
# Users live in their own SQLite file (harava_users.db, not in git), apart from the radio data, so
# clearing harava.db never removes a sign-in. Without any user Harava is open, as on a desktop.
# Permissions: admin (everything + /maintenance), analyze (also changes data: imports, My devices,
# identification reload), view (looks only). The session cookie is signed with a random key kept in
# .harava_secret (not in git).
USERS_DB = os.path.join(HERE, "harava_users.db")
SECRET_FILE = os.path.join(HERE, ".harava_secret")
ROLES = {"view": "View only", "analyze": "Analyze", "admin": "Admin"}
ROLE_RANK = {"view": 1, "analyze": 2, "admin": 3}
OPEN_ENDPOINTS = {"login", "logout", "static", "healthz", "app_manifest"}
PW_MIN = 12
_fails = defaultdict(list)                                   # ip -> times of failed sign-ins
_fails_lock = threading.Lock()
FAIL_WINDOW, FAIL_MAX_IP, FAIL_MAX_ALL = 15 * 60, 5, 30


def needs(role):
    """Decorator: the least permission a route needs (view < analyze < admin)."""
    def deco(fn):
        fn.harava_role = role
        return fn
    return deco


def _write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def secret_key():
    try:
        with open(SECRET_FILE, encoding="utf-8") as fh:
            k = fh.read().strip()
        if len(k) >= 32:
            return k
    except OSError:
        pass
    k = secrets.token_hex(32)
    _write_private(SECRET_FILE, k)
    return k


def udb():
    con = sqlite3.connect(USERS_DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_users_db():
    new = not os.path.exists(USERS_DB)
    with udb() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            first_name    TEXT NOT NULL DEFAULT '',
            last_name     TEXT NOT NULL DEFAULT '',
            role          TEXT NOT NULL DEFAULT 'view' CHECK (role IN ('view', 'analyze', 'admin')),
            password_hash TEXT NOT NULL,
            active        INTEGER NOT NULL DEFAULT 1,
            must_change   INTEGER NOT NULL DEFAULT 0,
            created       TEXT NOT NULL,
            updated       TEXT NOT NULL,
            last_login    TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_log (
            id       INTEGER PRIMARY KEY,
            ts       TEXT NOT NULL,
            username TEXT,
            ip       TEXT,
            event    TEXT NOT NULL
        );
        """)
    if new:
        try:
            os.chmod(USERS_DB, 0o600)
        except OSError:
            pass


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def users_exist():
    with udb() as con:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def auth_log(event, username="", ip=""):
    with udb() as con:
        con.execute("INSERT INTO auth_log (ts, username, ip, event) VALUES (?,?,?,?)", (_now(), username, ip, event))
        con.execute("DELETE FROM auth_log WHERE id NOT IN (SELECT id FROM auth_log ORDER BY id DESC LIMIT 2000)")


def _pw_version(pw_hash):
    """Changes when the password changes, so a new password signs out the old sessions."""
    return hashlib.sha256(pw_hash.encode()).hexdigest()[:16]


def _public_user(r):
    return {"id": r["id"], "username": r["username"], "first_name": r["first_name"], "last_name": r["last_name"],
            "role": r["role"], "role_name": ROLES[r["role"]], "active": bool(r["active"]),
            "must_change": bool(r["must_change"]), "created": r["created"], "last_login": r["last_login"]}


def current_user():
    """The signed-in user (dict) for this request, or None. Without any users: the open desktop mode."""
    if "harava_user" in g:
        return g.harava_user
    u = None
    if not users_exist():
        u = {"id": 0, "username": "", "first_name": "", "last_name": "", "role": "admin", "role_name": "Admin",
             "open": True, "must_change": False}
    elif session.get("uid"):
        with udb() as con:
            r = con.execute("SELECT * FROM users WHERE id = ?", (session["uid"],)).fetchone()
        if r and r["active"] and session.get("pwv") == _pw_version(r["password_hash"]):
            u = _public_user(r)
    g.harava_user = u
    return u


init_users_db()
app.secret_key = secret_key()
app.config.update(SESSION_COOKIE_NAME="harava_session", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=CFG["behind_proxy"],
                  PERMANENT_SESSION_LIFETIME=timedelta(days=CFG["session_days"]))


def _same_origin():
    """State-changing requests must come from Harava's own page (a second guard next to SameSite)."""
    src = request.headers.get("Origin") or request.headers.get("Referer")
    if not src:
        return True
    want = request.host_url.rstrip("/")
    return src == want or src.startswith(want + "/")


@app.before_request
def guard():
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and not _same_origin():
        return jsonify({"error": "request from another site refused"}), 403
    if request.endpoint in OPEN_ENDPOINTS:
        return None
    api = request.path.startswith(("/api/", "/doc/"))
    u = current_user()
    if u is None:
        if api:
            return jsonify({"error": "sign in first", "login": "/login"}), 401
        nxt = request.full_path.rstrip("?") if request.path != "/" else ""
        return redirect("/login" + (f"?next={nxt}" if nxt else ""))
    if u.get("must_change") and request.endpoint not in ("account", "api_account_password"):
        return (jsonify({"error": "change your password first", "account": "/account"}), 403) if api else redirect("/account")
    view = app.view_functions.get(request.endpoint)
    need = getattr(view, "harava_role", "view")
    if ROLE_RANK[u["role"]] < ROLE_RANK[need]:
        msg = f"needs the {ROLES[need]} permission (you have {u['role_name']})"
        return (jsonify({"error": msg}), 403) if api else (Response(msg, 403, mimetype="text/plain"))
    return None


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.endpoint != "static":
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def _too_many(ip, now):
    with _fails_lock:
        for k in list(_fails):
            _fails[k] = [t for t in _fails[k] if now - t < FAIL_WINDOW]
            if not _fails[k]:
                del _fails[k]
        mine = _fails.get(ip, [])
        total = sum(len(v) for v in _fails.values())
        if len(mine) >= FAIL_MAX_IP:
            return int(FAIL_WINDOW - (now - mine[0])) // 60 + 1
        if total >= FAIL_MAX_ALL:
            return 15
    return 0


_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))   # same work for unknown usernames


@app.route("/login", methods=["GET", "POST"])
def login():
    if not users_exist():
        return redirect("/")
    nxt = request.values.get("next", "")
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        nxt = "/"
    error = ""
    if request.method == "POST":
        ip, now = request.remote_addr or "?", time.time()
        wait = _too_many(ip, now)
        if wait:
            error = f"Too many failed sign-ins. Try again in {wait} min."
        else:
            name = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            with udb() as con:
                r = con.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
            ok = check_password_hash(r["password_hash"] if r else _DUMMY_HASH, pw) and bool(r) and bool(r["active"])
            if ok:
                with _fails_lock:
                    _fails.pop(ip, None)
                session.clear()
                session["uid"], session["pwv"] = r["id"], _pw_version(r["password_hash"])
                session.permanent = bool(request.form.get("remember"))
                with udb() as con:
                    con.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), r["id"]))
                auth_log("sign-in", r["username"], ip)
                print(f"  sign-in ok   : {r['username']} from {ip}")
                return redirect("/account" if r["must_change"] else nxt)
            with _fails_lock:
                _fails[ip].append(now)
            auth_log("failed sign-in" + ("" if r is None or r["active"] else " (disabled user)"), name[:64], ip)
            print(f"  sign-in FAIL : {name[:64]!r} from {ip}")
            time.sleep(1.0)
            error = "Wrong username or password."
    return render_template("login.html", app_name=APP_NAME, version=APP_VERSION, error=error, next=nxt), \
        (429 if error.startswith("Too many") else 200)


@app.route("/logout", methods=["POST"])
def logout():
    u = current_user() if users_exist() else None
    if u and not u.get("open"):
        auth_log("sign-out", u["username"], request.remote_addr or "")
    session.clear()
    return redirect("/login")


@app.route("/healthz")
def healthz():
    """For update.sh and monitoring: answers without a sign-in and tells nothing else."""
    return Response("ok\n", mimetype="text/plain")


def _check_password(pw):
    if len(pw or "") < PW_MIN:
        return f"the password must have at least {PW_MIN} characters"
    if len(pw) > 200:
        return "the password is too long"
    return ""


def _temp_password():
    return secrets.token_urlsafe(12)


@app.route("/account")
def account():
    u = current_user()
    return render_template("account.html", app_name=APP_NAME, version=APP_VERSION, user=u)


@app.route("/api/account/password", methods=["POST"])
def api_account_password():
    u = current_user()
    if u.get("open"):
        return jsonify({"error": "no users yet: Harava is open on this computer"}), 400
    body = request.get_json(silent=True) or {}
    with udb() as con:
        r = con.execute("SELECT * FROM users WHERE id = ?", (u["id"],)).fetchone()
        if not check_password_hash(r["password_hash"], body.get("current", "")):
            return jsonify({"error": "the current password is wrong"}), 400
        err = _check_password(body.get("new", ""))
        if err:
            return jsonify({"error": err}), 400
        if body["new"] == body.get("current"):
            return jsonify({"error": "choose a new password, not the current one"}), 400
        h = generate_password_hash(body["new"])
        con.execute("UPDATE users SET password_hash = ?, must_change = 0, updated = ? WHERE id = ?", (h, _now(), u["id"]))
    session["pwv"] = _pw_version(h)                  # this session stays, the others are signed out
    auth_log("password changed", u["username"], request.remote_addr or "")
    return jsonify({"ok": True})


# ── maintenance (admin) ──
@app.route("/maintenance")
@needs("admin")
def maintenance():
    return render_template("maintenance.html", app_name=APP_NAME, version=APP_VERSION, user=current_user(),
                           roles=ROLES, pw_min=PW_MIN)


def _active_admins(con, exclude_id=None):
    q = "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
    if exclude_id is not None:
        return con.execute(q + " AND id != ?", (exclude_id,)).fetchone()[0]
    return con.execute(q).fetchone()[0]


@app.route("/api/users")
@needs("admin")
def api_users():
    with udb() as con:
        users = [_public_user(r) for r in con.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")]
        log = [dict(r) for r in con.execute("SELECT ts, username, ip, event FROM auth_log ORDER BY id DESC LIMIT 60")]
    return jsonify({"users": users, "log": log, "roles": ROLES, "me": current_user()["id"]})


def _user_fields(body):
    out = {}
    for k in ("first_name", "last_name"):
        if k in body:
            v = str(body[k]).strip()
            if len(v) > 80:
                raise ValueError(f"{k.replace('_', ' ')} is too long")
            out[k] = v
    if "role" in body:
        if body["role"] not in ROLES:
            raise ValueError("unknown permission")
        out["role"] = body["role"]
    if "active" in body:
        out["active"] = 1 if body["active"] else 0
    return out


@app.route("/api/users", methods=["POST"])
@needs("admin")
def api_users_add():
    body = request.get_json(silent=True) or {}
    name = str(body.get("username", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,64}", name):
        return jsonify({"error": "the username must be 3-64 letters, digits or . _ @ -"}), 400
    try:
        f = _user_fields(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not users_exist():
        f["role"] = "admin"                          # the first user is always an admin
    pw = body.get("password") or ""
    temp = not pw
    if temp:
        pw = _temp_password()
    elif _check_password(pw):
        return jsonify({"error": _check_password(pw)}), 400
    with udb() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            return jsonify({"error": f"the username {name} is taken"}), 400
        con.execute("""INSERT INTO users (username, first_name, last_name, role, password_hash, active, must_change,
                       created, updated) VALUES (?,?,?,?,?,1,1,?,?)""",
                    (name, f.get("first_name", ""), f.get("last_name", ""), f.get("role", "view"),
                     generate_password_hash(pw), _now(), _now()))
    auth_log(f"added user {name} ({ROLES[f.get('role', 'view')]})", current_user()["username"], request.remote_addr or "")
    claim_open_data()
    return jsonify({"ok": True, "temp_password": pw if temp else ""})


@app.route("/api/users/<int:uid>", methods=["PUT"])
@needs("admin")
def api_users_edit(uid):
    body = request.get_json(silent=True) or {}
    try:
        f = _user_fields(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    me = current_user()
    with udb() as con:
        r = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not r:
            return jsonify({"error": "no such user"}), 404
        losing_admin = r["role"] == "admin" and r["active"] and (f.get("role", "admin") != "admin" or f.get("active", 1) == 0)
        if losing_admin and _active_admins(con, exclude_id=uid) == 0:
            return jsonify({"error": "Harava needs at least one active admin"}), 400
        if uid == me["id"] and f.get("active", 1) == 0:
            return jsonify({"error": "you cannot disable yourself"}), 400
        if f:
            sets = ", ".join(f"{k} = ?" for k in f)
            con.execute(f"UPDATE users SET {sets}, updated = ? WHERE id = ?", (*f.values(), _now(), uid))
    auth_log(f"changed user {r['username']}" + (f" ({ROLES[f['role']]})" if "role" in f else "") + ("" if "active" not in f else (" (active)" if f["active"] else " (disabled)")), me["username"], request.remote_addr or "")
    return jsonify({"ok": True})


@app.route("/api/users/<int:uid>/password", methods=["POST"])
@needs("admin")
def api_users_reset(uid):
    pw = _temp_password()
    with udb() as con:
        r = con.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
        if not r:
            return jsonify({"error": "no such user"}), 404
        con.execute("UPDATE users SET password_hash = ?, must_change = 1, updated = ? WHERE id = ?",
                    (generate_password_hash(pw), _now(), uid))
    auth_log(f"new one-time password for {r['username']}", current_user()["username"], request.remote_addr or "")
    return jsonify({"ok": True, "temp_password": pw})


@app.route("/api/users/<int:uid>", methods=["DELETE"])
@needs("admin")
def api_users_delete(uid):
    me = current_user()
    if uid == me["id"]:
        return jsonify({"error": "you cannot remove yourself"}), 400
    with udb() as con:
        r = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not r:
            return jsonify({"error": "no such user"}), 404
        if r["role"] == "admin" and r["active"] and _active_admins(con, exclude_id=uid) == 0:
            return jsonify({"error": "Harava needs at least one active admin"}), 400
        con.execute("DELETE FROM users WHERE id = ?", (uid,))
    auth_log(f"removed user {r['username']}", me["username"], request.remote_addr or "")
    return jsonify({"ok": True})


def admin_cli():
    """python harava.py --add-admin : create an admin, or set a new password for an existing user
    (who then becomes an admin). For the first user on a server, and for a forgotten password."""
    init_users_db()
    print(f"{APP_NAME}: add an admin or reset a password (users are stored in {USERS_DB})")
    name = input("Username: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,64}", name or ""):
        sys.exit("The username must be 3-64 letters, digits or . _ @ -")
    with udb() as con:
        r = con.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
    first = input("First name: ").strip() if not r else (input(f"First name [{r['first_name']}]: ").strip() or r["first_name"])
    last = input("Last name: ").strip() if not r else (input(f"Last name [{r['last_name']}]: ").strip() or r["last_name"])
    while True:
        pw = getpass.getpass(f"Password (at least {PW_MIN} characters): ")
        err = _check_password(pw)
        if err:
            print("  " + err)
            continue
        if getpass.getpass("Repeat the password: ") != pw:
            print("  the passwords differ, try again")
            continue
        break
    with udb() as con:
        if r:
            con.execute("""UPDATE users SET first_name = ?, last_name = ?, role = 'admin', active = 1, must_change = 0,
                           password_hash = ?, updated = ? WHERE id = ?""",
                        (first, last, generate_password_hash(pw), _now(), r["id"]))
        else:
            con.execute("""INSERT INTO users (username, first_name, last_name, role, password_hash, active, must_change,
                           created, updated) VALUES (?,?,?,'admin',?,1,0,?,?)""",
                        (name, first, last, generate_password_hash(pw), _now(), _now()))
    auth_log("admin set from the command line", name, "console")
    init_db()
    claim_open_data()
    print(f"Saved: {name} is an admin. Other users are added on the /maintenance page.")


@app.route("/")
def index():
    wf = CFG["watch_folder"]
    if os.path.commonpath([os.path.abspath(wf), HERE]) == HERE:
        wf = os.path.relpath(wf, HERE)                      # e.g. "data", inside Harava's folder
    return render_template("index.html", version=APP_VERSION, app_name=APP_NAME,
                           watch_folder=wf, travel_min_m=int(CFG["travel_min_m"]),
                           carto_api_key=CARTO_API_KEY, user=current_user())


# ───────────────────────── the phone app /app ─────────────────────────
# The user interface of the WSPR Logger phone view: one view at a time, every choice in the ☰ menu
# (templates/app.html, static/m-app.js, static/m-app.css). The same data and permissions as the page.
def _app_build():
    """The build of the phone app: the newest change of its templates and static files. After a deploy
    a phone that has the app open sees a new build and reloads itself (m-app.js)."""
    newest = 0
    for base in (os.path.join(HERE, "templates"), os.path.join(HERE, "static")):
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x != "vendor"]
            for f in files:
                try:
                    newest = max(newest, int(os.path.getmtime(os.path.join(root, f))))
                except OSError:
                    pass
    return str(newest)


@app.route("/app")
def app_page():
    return render_template("app.html", app_name=APP_NAME, version=APP_VERSION, build=_app_build(),
                           user=current_user(), carto_api_key=CARTO_API_KEY,
                           travel_min_m=int(CFG["travel_min_m"]), layers=LAYERS)


@app.route("/api/app/build")
def api_app_build():
    resp = jsonify({"build": _app_build()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/app/manifest.webmanifest")
def app_manifest():
    """Web-app manifest for "Add to Home Screen": opens /app full screen (no sign-in needed to read it)."""
    man = {
        "name": APP_NAME, "short_name": APP_NAME,
        "description": "What your phone heard with Fieldwatch: map, devices, watch list, phone check",
        "start_url": "/app", "scope": "/app", "display": "standalone", "orientation": "any",
        "background_color": "#111827", "theme_color": "#111827",
        "icons": [
            {"src": "/static/icons/app-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/app-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(man), mimetype="application/manifest+json")


@app.route("/api/summary")
def api_summary():
    with db() as con:
        ids = scope_ids(con)
        imports = len(visible_imports(con, active_only=False))
        if not ids:
            return jsonify({"imports": imports, "days": [], "first": None, "last": None})
        f, args = _ids_filter(con, ids)
        rows = con.execute(f"SELECT h.ts FROM hears h WHERE {f} AND h.ts IS NOT NULL", args).fetchall()
    days = Counter(datetime.fromtimestamp(r[0] / 1000).strftime("%Y-%m-%d") for r in rows)
    first = min((r[0] for r in rows), default=None)
    last = max((r[0] for r in rows), default=None)
    return jsonify({"imports": imports, "days": [{"day": d, "hears": n} for d, n in sorted(days.items())],
                    "first": first, "last": last})


@app.route("/api/devices")
def api_devices():
    t_from, t_to = range_args()
    no_sit = request.args.get("nosit") == "1"     # the phone check works from the log only
    with db() as con:
        hears = query_hears(con, t_from, t_to, no_sit=no_sit)
    return jsonify({"devices": build_devices(hears), "hears": len(hears)})


@app.route("/api/path")
def api_path():
    t_from, t_to = range_args()
    with db() as con:
        hears = query_hears(con, t_from, t_to, no_sit=True)   # Sit rows carry one hear-point, not a track
        parts = build_path(hears)
        # KML LineStrings carry no times; show them when their import overlaps the range
        ids = scope_ids(con)
        kml_parts = []
        if ids:
            q = ",".join("?" * len(ids))
            imps = con.execute(f"SELECT id, first_ts, last_ts FROM imports WHERE id IN ({q}) AND path_pts>0", ids)
            for imp in imps.fetchall():
                if t_from and imp["last_ts"] and imp["last_ts"] < t_from:
                    continue
                if t_to and imp["first_ts"] and imp["first_ts"] >= t_to:
                    continue
                rows = con.execute("SELECT part, lat, lon FROM paths WHERE import_id=? ORDER BY part, seq",
                                   (imp["id"],)).fetchall()
                byp = defaultdict(list)
                for r in rows:
                    byp[r["part"]].append([r["lat"], r["lon"], None])
                kml_parts.extend(byp.values())
    if not parts:
        parts = kml_parts
    return jsonify({"parts": parts})


@app.route("/api/timeline")
def api_timeline():
    t_from, t_to = range_args()
    cat = request.args.get("cat") or ""
    with db() as con:
        hears = query_hears(con, t_from, t_to)
    all_lo = hears[0]["ts"] if hears else None
    all_hi = hears[-1]["ts"] + 1 if hears else None
    if cat:
        # only radios of one layer, e.g. "Glasses & VR"
        keys = {(d["kind"], d["mac"]) for d in build_devices(hears) if d["category"] == cat}
        hears = [h for h in hears if (h["kind"], h["mac"]) in keys]
    # Bars come from the log (hear by hear). A Sit export has only first..last per radio, so its
    # radios count once, when first heard: they fill the bars only for radios the log does not have.
    logged = {(h["kind"], h["mac"]) for h in hears if h["src"] != "sit"}
    sit_only = sum(1 for h in hears if h["src"] == "sit" and (h["kind"], h["mac"]) not in logged)
    hears = [h for h in hears if h["src"] != "sit" or (h["kind"], h["mac"]) not in logged]
    lo = t_from or all_lo
    hi = t_to or all_hi
    if lo is None or hi is None:
        return jsonify({"bin_ms": 0, "bins": [], "cat": cat, "hears": 0})
    span = hi - lo
    bin_ms = 600000 if span <= 2 * 86400000 else (3600000 if span <= 14 * 86400000 else 86400000)
    start = lo - lo % bin_ms
    bins = defaultdict(lambda: {"WIFI": 0, "BLE": 0, "new": 0})
    seen = set()
    for h in hears:
        t0 = max(h["ts"], start)          # a Sit row may start before the range
        b = t0 - (t0 - start) % bin_ms
        bins[b][h["kind"] if h["kind"] in ("WIFI", "BLE") else "BLE"] += 1
        k = (h["kind"], h["mac"])
        if k not in seen:
            seen.add(k)
            bins[b]["new"] += 1
    out = []
    t = start
    while t < hi:
        v = bins.get(t, {"WIFI": 0, "BLE": 0, "new": 0})
        out.append({"t": t, **v})
        t += bin_ms
    return jsonify({"bin_ms": bin_ms, "bins": out, "cat": cat, "hears": len(hears), "sit_only": sit_only})


@app.route("/api/device")
def api_device():
    t_from, t_to = range_args()
    kind = (request.args.get("kind") or "").upper()
    mac = (request.args.get("mac") or "").upper()
    with db() as con:
        hears = query_hears(con, t_from, t_to, kind, mac)
        all_hears = query_hears(con, None, None, kind, mac)
    dev = build_devices(hears)
    return jsonify({
        "device": dev[0] if dev else None,
        "hears": [{"ts": h["ts"], "rssi": h["rssi"], "lat": h["lat"], "lon": h["lon"], "name": h["name"],
                   "channel": h["channel"], "src": h["src"], "last_ts": h["last_ts"], "hits": h["hits"]}
                  for h in hears],
        "days_seen": sorted({datetime.fromtimestamp(h["ts"] / 1000).strftime("%Y-%m-%d") for h in all_hears if h["ts"]}),
    })


@app.route("/api/export.csv")
def api_export():
    t_from, t_to = range_args()
    macs = set(filter(None, (request.args.get("keys") or "").split(",")))
    with db() as con:
        devs = build_devices(query_hears(con, t_from, t_to))
    if macs:
        devs = [d for d in devs if f"{d['kind']}|{d['mac']}" in macs]
    cols = ["kind", "mac", "name", "vendor", "category", "class", "fleets", "hears", "log_rows", "sit_hits", "first", "last",
            "rssi_max", "rssi_med", "channel", "freq", "band", "flags", "spread_m", "places", "travelled",
            "tracker_mode", "lat", "lon", "mfg", "uuids", "raw"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for d in devs:
        row = []
        for c in cols:
            v = d.get(c)
            if c in ("first", "last") and v:
                v = datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d %H:%M:%S")
            row.append("" if v is None else v)
        w.writerow(row)
    name = "devices-export-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv"
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


# ─────────────── data: whose data, phones, imports ───────────────

def people():
    """Every user: id → display name (first + last name, or the username)."""
    try:
        with udb() as con:
            rows = con.execute("SELECT id, username, first_name, last_name, active FROM users").fetchall()
    except sqlite3.Error:
        return {}
    return {r["id"]: {"name": (f"{r['first_name']} {r['last_name']}".strip() or r["username"]),
                      "username": r["username"], "active": bool(r["active"])} for r in rows}


def owner_name(oid, ppl):
    if not oid:
        return "this computer"
    p = ppl.get(oid)
    return p["name"] if p else f"removed user #{oid}"


def claim_open_data():
    """Data imported while Harava had no users (owner 0, e.g. on the desktop) belongs to the first admin."""
    if not users_exist():
        return
    with udb() as ucon:
        r = ucon.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    if not r:
        return
    with _db_lock, db() as con:
        for t in ("imports", "collectors", "own_devices"):
            con.execute(f"UPDATE {t} SET owner_id=? WHERE owner_id=0", (r["id"],))


def _can_edit(imp, user):
    return bool(user) and (user.get("open") or user["role"] == "admin" or imp["owner_id"] == user["id"])


def _scope_options(con, user):
    if not user or user.get("open"):
        return [{"value": "mine+shared", "label": "All data"}]
    uid, admin = user["id"], user["role"] == "admin"
    ppl = people()
    rows = con.execute("SELECT owner_id, shared FROM imports").fetchall()
    owners = sorted({r["owner_id"] for r in rows if r["owner_id"] != uid and (admin or _shared_to(r["shared"], uid))},
                    key=lambda o: owner_name(o, ppl).lower())
    opts = [{"value": "mine+shared", "label": "My data + shared with me"},
            {"value": "mine", "label": "My data only"}]
    opts += [{"value": f"u:{o}", "label": f"{owner_name(o, ppl)}'s data"} for o in owners]
    if admin:
        opts.append({"value": "all", "label": "All users (admin)"})
    return opts


@app.route("/api/scope")
def api_scope():
    u = current_user()
    with db() as con:
        opts = _scope_options(con, u)
    cur = session.get("scope") or "mine+shared"
    if cur not in {o["value"] for o in opts}:
        cur = "mine+shared"
    return jsonify({"scope": cur, "options": opts, "label": next(o["label"] for o in opts if o["value"] == cur)})


@app.route("/api/scope", methods=["POST"])
def api_scope_set():
    want = str((request.get_json(silent=True) or {}).get("scope", ""))
    with db() as con:
        opts = _scope_options(con, current_user())
    if want not in {o["value"] for o in opts}:
        return jsonify({"error": "you cannot see that data"}), 403
    session["scope"] = want
    return jsonify({"ok": True, "scope": want})


@app.route("/api/people")
def api_people():
    """Users to share with (signed-in users only; names, no other details)."""
    u = current_user()
    return jsonify({"people": [{"id": k, "name": v["name"]} for k, v in sorted(people().items(), key=lambda x: x[1]["name"].lower())
                               if v["active"] and (not u or k != u["id"])]})


@app.route("/api/collectors")
def api_collectors():
    u = current_user()
    with _db_lock, db() as con:
        if u and (u.get("open") or u["role"] != "view"):
            default_collector(con, u["id"])
        rows = [dict(r) for r in con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM imports i WHERE i.collector_id = c.id) imports FROM collectors c "
            "WHERE owner_id=? ORDER BY id", (u["id"] if u else 0,))]
    return jsonify({"collectors": rows})


@app.route("/api/collectors", methods=["POST"])
@needs("analyze")
def api_collectors_add():
    name = clean_text((request.get_json(silent=True) or {}).get("name", "")).strip()[:40]
    if not name:
        return jsonify({"error": "give the phone a name"}), 400
    u = current_user()
    with _db_lock, db() as con:
        if con.execute("SELECT 1 FROM collectors WHERE owner_id=? AND name=? COLLATE NOCASE", (u["id"], name)).fetchone():
            return jsonify({"error": "you already have a phone with that name"}), 400
        cid = con.execute("INSERT INTO collectors(owner_id, name, created) VALUES (?,?,?)",
                          (u["id"], name, datetime.now().isoformat(timespec="seconds"))).lastrowid
    return jsonify({"ok": True, "id": cid})


def _my_collector(con, user, want):
    """The phone an import goes to: one of the user's own, else their first one."""
    if want and str(want).isdigit():
        r = con.execute("SELECT id FROM collectors WHERE id=? AND owner_id=?", (int(want), user["id"])).fetchone()
        if r:
            return r[0]
    return default_collector(con, user["id"])


@app.route("/api/imports")
def api_imports():
    u = current_user()
    ppl = people()
    with db() as con:
        ids = set(visible_imports(con, active_only=False))
        cols = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM collectors")}
        rows = []
        for r in con.execute("SELECT * FROM imports ORDER BY COALESCE(first_ts, 0) DESC, id DESC"):
            if r["id"] not in ids:
                continue
            d = {k: r[k] for k in r.keys() if k not in ("path", "archived", "sha1")}
            sh = r["shared"] or ""
            d.update(owner_name=owner_name(r["owner_id"], ppl), phone=cols.get(r["collector_id"], ""),
                     mine=bool(u) and (u.get("open") or r["owner_id"] == u["id"]), can_edit=_can_edit(r, u),
                     rereadable=bool(r["archived"] or (r["path"] and os.path.exists(r["path"]))),
                     shared_with=("all users" if sh == "*" else ", ".join(
                         owner_name(int(x), ppl) for x in sh.strip(",").split(",") if x.isdigit())),
                     shared_ids=[int(x) for x in sh.strip(",").split(",") if x.isdigit()] if sh != "*" else [])
            rows.append(d)
    return jsonify({"imports": rows, "watch_folder": CFG["watch_folder"] if (u and (u.get("open") or u["role"] == "admin")) else ""})


@app.route("/api/import", methods=["POST"])
@needs("analyze")
def api_import():
    u = current_user()
    with _db_lock, db() as con:
        cid = _my_collector(con, u, request.form.get("collector"))
    results = []
    for f in request.files.getlist("files"):
        results.extend(import_upload(f.filename, f.read(), u["id"], cid))
    return jsonify({"results": results})


@app.route("/api/scan", methods=["POST"])
@needs("analyze")
def api_scan():
    u = current_user()
    if not (u.get("open") or u["role"] == "admin"):
        return jsonify({"error": "Scan folder reads the server's own folder: admins only"}), 403
    if not os.path.isdir(CFG["watch_folder"]):
        return jsonify({"results": [], "error": f"folder not found: {CFG['watch_folder']}"}), 400
    with _db_lock, db() as con:
        cid = _my_collector(con, u, (request.get_json(silent=True) or {}).get("collector"))
    return jsonify({"results": scan_folder(CFG["watch_folder"], u["id"], cid)})


def _editable_import(iid):
    with db() as con:
        r = con.execute("SELECT * FROM imports WHERE id=?", (iid,)).fetchone()
    if not r:
        abort(Response(json.dumps({"error": "no such import"}), 404, mimetype="application/json"))
    if not _can_edit(r, current_user()):
        abort(Response(json.dumps({"error": "only its owner (or an admin) can change this import"}), 403,
                       mimetype="application/json"))
    return r


@app.route("/api/imports/<int:iid>/toggle", methods=["POST"])
@needs("analyze")
def api_toggle(iid):
    _editable_import(iid)
    with _db_lock, db() as con:
        con.execute("UPDATE imports SET active = 1 - active WHERE id=?", (iid,))
    return jsonify({"ok": True})


@app.route("/api/imports/<int:iid>/share", methods=["POST"])
@needs("analyze")
def api_share(iid):
    _editable_import(iid)
    b = request.get_json(silent=True) or {}
    w = b.get("shared", "")
    if w == "*":
        val = "*"
    elif isinstance(w, list):
        known = people()
        ids = sorted({int(x) for x in w if str(x).isdigit() and int(x) in known})
        val = ("," + ",".join(map(str, ids)) + ",") if ids else ""
    else:
        val = ""
    with _db_lock, db() as con:
        con.execute("UPDATE imports SET shared=? WHERE id=?", (val, iid))
    return jsonify({"ok": True, "shared": val})


@app.route("/api/imports/<int:iid>/reread", methods=["POST"])
@needs("analyze")
def api_reread(iid):
    _editable_import(iid)
    r = reread_import(iid)
    return (jsonify(r), 400) if r.get("error") else jsonify(r)


@app.route("/api/imports/<int:iid>", methods=["DELETE"])
@needs("analyze")
def api_delete(iid):
    _editable_import(iid)
    delete_import(iid)
    return jsonify({"ok": True})


# ─────────────── identification database ───────────────

def _pattern(d):
    """A short description of what a still-unidentified radio advertises, to find the next rule to write."""
    if d["kind"] != "BLE":
        return "Wi-Fi"
    f = adv_features(d["mfg"], d["raw"], d["uuids"])
    if f["mfg"]:
        comp = BT["companies"].get(f["mfg"], "unknown company")
        return f"manufacturer data {f['mfg']} ({comp}), starts {f['mfg_data'][:1].hex().upper() or '—'}"
    short = {"02": "16-bit services", "03": "16-bit services", "06": "128-bit services", "07": "128-bit services",
             "08": "short name", "09": "name", "0A": "Tx power", "16": "Service Data", "21": "128-bit service data",
             "2B": "Mesh beacon", "FF": "manufacturer data"}
    parts = []
    for t, data in f["ads"]:
        if t == "01":
            continue
        nm = short.get(t) or BT["ad_types"].get(t, f"AD {t}")
        if t == "16" and len(data) >= 2:
            u = format(int.from_bytes(data[:2], "little"), "04X")
            nm += f" {u}" + (f" ({BT['uuids'][u]['name']})" if u in BT["uuids"] else "")
        parts.append(nm)
    if f["svc"] and not any(p.startswith("Service Data") for p in parts):
        parts.append("services " + ", ".join(sorted(f["svc"])[:3]))
    custom = next((u for u in f["uuid128"] if not u.endswith("-0000-1000-8000-00805F9B34FB")), "")
    if custom:
        parts.append(f"custom service {custom[:8]}…")
    if d["name"]:
        parts.append("named")
    if d.get("group_name") and d["group_name"] != d["name"]:
        parts.append(f"same device as '{d['group_name']}'")
    return " + ".join(dict.fromkeys(parts)) or ("flags only" if f["ads"] else "no data")


@app.route("/api/ids")
def api_ids():
    t_from, t_to = range_args()
    with db() as con:
        devs = build_devices(query_hears(con, t_from, t_to))
    ble = [d for d in devs if d["kind"] == "BLE"]
    fw_left = [d for d in ble if d["class"] in ("", "HARAVA")]          # Fieldwatch did not identify these
    still = [d for d in ble if d["category"] == "Unidentified"]
    hits = Counter(d["ident_id"] for d in devs if d["ident_id"])
    groups = defaultdict(set)
    for d in devs:
        if d["group"]:
            groups[d["ident_id"]].add(d["group"])
    rules = [{"id": r["id"], "name": r["name"], "layer": r.get("layer", ""), "confidence": r.get("confidence", ""),
              "note": r.get("note", ""), "match": {k: v for k, v in r.get("match", {}).items()},
              "hits": hits.get(r["id"], 0), "devices": len(groups.get(r["id"], ())) or None,
              "local": bool(r.get("local"))}
             for r in HV_RULES]
    pats = defaultdict(list)
    for d in still:
        pats[_pattern(d)].append(d)
    patterns = sorted(({"pattern": k, "count": len(v), "example": v[0]["mac"],
                        "example_raw": v[0]["raw"][:60], "rssi_max": max((x["rssi_max"] or -999) for x in v)}
                       for k, v in pats.items()), key=lambda x: -x["count"])[:30]
    return jsonify({
        "bt": {"source": BT.get("source", ""), "fetched": BT.get("fetched", ""),
               "companies": len(BT["companies"]), "uuids": len(BT["uuids"]), "ad_types": len(BT["ad_types"])},
        "db": {"name": HV.get("name", ""), "version": HV.get("version", ""), "rules": len(HV_RULES),
               "local": len(LOCAL["rules"])},
        "counts": {"ble": len(ble), "fieldwatch_left": len(fw_left), "harava_identified": len(fw_left) - len(still),
                   "still_unidentified": len(still), "company_only": hits.get("company", 0),
                   "grouped_addresses": sum(1 for d in devs if d["group"] and d.get("group_size", 1) > 1),
                   "grouped_devices": len({d["group"] for d in devs if d["group"] and d.get("group_size", 1) > 1})},
        "rules": rules, "patterns": patterns,
    })


@app.route("/api/ids/reload", methods=["POST"])
@needs("analyze")
def api_ids_reload():
    return jsonify({"status": load_ids(), "rules": len(HV_RULES)})


# ─────────────── workbench (admins): radios Harava cannot name yet ───────────────
# Every BLE address heard on any day is put in a bucket by what it advertises, without its address or
# changing bytes (a fingerprint): the manufacturer and the first byte of its data, the service data UUID,
# a vendor's own 128-bit UUID, the AD types; a name's shape only when nothing else tells. One bucket is
# one kind of device to find out. The admin keeps a status and a note per bucket (wb_notes) and can turn
# one into a rule: rules made here go to ids/local_rules.json on this server only (not in git).

WB_STATUS = ("new", "investigating", "identified", "ignore", "cannot")
WB_TABS = ("unknown", "weak", "nodata")
AD_SHORT = {"02": "16-bit services", "03": "16-bit services", "06": "128-bit services", "07": "128-bit services",
            "08": "short name", "09": "name", "0A": "Tx power", "16": "service data", "21": "128-bit service data",
            "2B": "Mesh beacon", "2A": "Mesh message", "29": "Mesh provisioning", "FF": "manufacturer data",
            "19": "appearance", "1B": "LE address", "24": "URI"}
# The first byte of Apple's and Microsoft's manufacturer data says what kind of message it is
APPLE_TYPES = {"02": "iBeacon", "03": "AirPrint", "05": "AirDrop", "06": "HomeKit", "07": "Proximity pairing (AirPods, Beats)",
               "08": "Hey Siri", "09": "AirPlay target", "0A": "AirPlay source", "0B": "Magic Switch (Watch)",
               "0C": "Handoff", "0D": "Tethering target", "0E": "Tethering source", "0F": "Nearby action",
               "10": "Nearby info (iPhone, iPad, Mac, Watch)", "12": "Find My (lost or separated device)",
               "16": "Find My (Unknown Tracker alert)"}
MS_TYPES = {"01": "CDP beacon (Windows PC, Xbox; Swift Pair when byte 2 is 03)", "03": "Swift Pair"}
_NAME_RUN = re.compile(r"[0-9A-Za-z]*\d[0-9A-Za-z]*")
GENERIC_AD = {"01", "08", "09", "0A", "12", "19", "1B", "1A", "0D", "0E", "0F", "14", "15", "1F"}
WB_CONF = ("high", "medium", "low")


def _name_shape(name):
    """'DEV-1A2B3C' and 'DEV-9F8E7D' have the same shape 'DEV-#'."""
    return _NAME_RUN.sub(lambda m: "#" if sum(c.isdigit() for c in m.group()) >= 3 else m.group(),
                         name or "").strip()[:40]


def _fingerprint(d):
    """(fp, label, parts) of one BLE device: what kind of advertisement it is, without what changes."""
    f = adv_features(d["mfg"], d["raw"], d["uuids"])
    key, label = [], []
    if f["mfg"]:
        b0 = f["mfg_data"][:1].hex().upper()
        comp = BT["companies"].get(f["mfg"], "unknown company")
        key.append(f"mfg={f['mfg']}:{b0}")
        kind = (APPLE_TYPES if f["mfg"] == "004C" else MS_TYPES if f["mfg"] == "0006" else {}).get(b0)
        label.append(f"{comp} (0x{f['mfg']}) data {b0 or '—'}…" + (f" = {kind}" if kind else ""))
    for u in sorted(f["sd"]):
        key.append(f"sd={u}")
        nm = BT["uuids"].get(u, {}).get("name")
        label.append(f"service data {u}" + (f" ({nm})" if nm else ""))
    custom = sorted(u for u in f["uuid128"] if not u.endswith("-0000-1000-8000-00805F9B34FB"))
    for u in custom[:2]:
        key.append(f"u128={u}")
        label.append(f"custom service {u[:8]}…")
    svc = sorted(f["svc"] - set(f["sd"]))
    if svc and not f["mfg"]:
        key.append("svc=" + ",".join(svc[:4]))
        label.append("services " + ", ".join(
            u + (f" ({BT['uuids'][u]['name']})" if u in BT["uuids"] else "") for u in svc[:3]))
    ads = sorted({t for t, _ in f["ads"] if t not in ("01", "09", "08", "16", "FF", "02", "03", "06", "07", "21")})
    if ads:
        key.append("ad=" + ",".join(ads))
        label.extend(AD_SHORT.get(t) or BT["ad_types"].get(t, f"AD {t}") for t in ads)
    data = bool(key)
    if not data or not (f["mfg"] or f["sd"] or custom):
        shape = _name_shape(d["name"] or d.get("group_name", ""))
        if shape:
            key.append(f"name={shape}")
            label.append(f"name '{shape}'")
    if not key:
        key, label = ["nodata"], ["flags only / no advertising data"]
    fp = hashlib.sha1("|".join(key).encode()).hexdigest()[:12]
    return fp, " + ".join(label), {"data": data, "f": f, "custom": custom}


def _wb_tab(d, data):
    if d["kind"] != "BLE":
        return None
    if d["ident_id"] in ("company", "custom-uuid") or (d["ident_id"] and d["ident_conf"] == "low"):
        return "weak"
    if d["category"] != "Unidentified" or d["ident_id"]:
        return None
    return "unknown" if data else "nodata"


def _wb_state():
    """Every BLE device of all days, with its fingerprint and the days and 5-min slots it was heard in.
    Cached until the imports or the rules change."""
    with db() as con:
        ids = tuple(all_active_ids(con))           # the Workbench looks at everyone's data
        hears = query_hears(con, ids=list(ids))
    ck = (ids, len(hears), len(HV_RULES))
    if _WB_CACHE.get("key") == ck:
        return _WB_CACHE["state"]
    devs = build_devices(hears)
    days, slots = defaultdict(set), defaultdict(set)
    for h in hears:
        if h["kind"] == "BLE" and h["ts"]:
            k = h["mac"]
            days[k].add(datetime.fromtimestamp(h["ts"] / 1000).strftime("%Y-%m-%d"))
            slots[k].add(h["ts"] // 300000)
    buckets = {}
    for d in devs:
        if d["kind"] != "BLE":
            continue
        fp, label, x = _fingerprint(d)
        d["_fp"], d["_tab"] = fp, _wb_tab(d, x["data"])
        d["_days"], d["_slots"] = days[d["mac"]], slots[d["mac"]]
        b = buckets.setdefault(fp, {"fp": fp, "label": label, "devs": []})
        b["devs"].append(d)
    state = {"devs": [d for d in devs if d["kind"] == "BLE"], "buckets": buckets}
    _WB_CACHE.update(key=ck, state=state)
    return state


def _wb_notes():
    with db() as con:
        return {r["fp"]: dict(r) for r in con.execute("SELECT * FROM wb_notes")}


def _wb_item(b, tab, notes):
    ds = [d for d in b["devs"] if d["_tab"] == tab] if tab else b["devs"]
    names = Counter(d["name"] or d["group_name"] for d in ds if d["name"] or d["group_name"])
    idents = Counter(d["ident"] for d in ds if d["ident"])
    n = notes.get(b["fp"], {})
    return {"fp": b["fp"], "label": b["label"], "count": len(ds),
            "devices": len({d["group"] or d["mac"] for d in ds}),
            "days": sorted(set().union(*(d["_days"] for d in ds))) if ds else [],
            "names": [k for k, _ in names.most_common(6)],
            "ident": idents.most_common(1)[0][0] if idents else "",
            "rssi_max": max((d["rssi_max"] for d in ds if d["rssi_max"] is not None), default=None),
            "hears": sum(d["hears"] for d in ds),
            "travelled": sum(1 for d in ds if d["travelled"]),
            "status": n.get("status", "new"), "note": n.get("note", ""), "updated": n.get("updated", "")}


@app.route("/api/wb")
@needs("admin")
def api_wb():
    tab = request.args.get("tab", "unknown")
    if tab not in WB_TABS:
        tab = "unknown"
    st, notes = _wb_state(), _wb_notes()
    items = [_wb_item(b, tab, notes) for b in st["buckets"].values() if any(d["_tab"] == tab for d in b["devs"])]
    items.sort(key=lambda x: (x["status"] in ("ignore", "cannot", "identified"), -x["count"], -x["hears"]))
    counts = {t: sum(1 for d in st["devs"] if d["_tab"] == t) for t in WB_TABS}
    kinds = {t: sum(1 for b in st["buckets"].values() if any(d["_tab"] == t for d in b["devs"])) for t in WB_TABS}
    per_day = defaultdict(Counter)
    for d in st["devs"]:
        for day in d["_days"]:
            per_day[day]["ble"] += 1
            if d["_tab"]:
                per_day[day][d["_tab"]] += 1
    progress = [{"day": k, "ble": v["ble"], **{t: v[t] for t in WB_TABS}} for k, v in sorted(per_day.items())]
    done = Counter(n["status"] for n in notes.values())
    return jsonify({"tab": tab, "items": items, "counts": counts, "kinds": kinds, "progress": progress,
                    "statuses": dict(done), "local_rules": len(LOCAL["rules"])})


def _decode(d, f):
    out = []
    if d["mfg"]:
        out.append({"what": "Company ID", "value": f"0x{f['mfg']} {BT['companies'].get(f['mfg'], '(not in the SIG list)')}"})
        out.append({"what": "Manufacturer data", "value": f["mfg_data"].hex().upper() or "—"})
        b0 = f["mfg_data"][:1].hex().upper()
        kind = (APPLE_TYPES if f["mfg"] == "004C" else MS_TYPES if f["mfg"] == "0006" else {}).get(b0)
        if kind:
            out.append({"what": "Message type", "value": f"{b0} = {kind}"})
    for t, data in f["ads"]:
        nm = AD_SHORT.get(t) or BT["ad_types"].get(t, "unknown")
        val = data.hex().upper()
        if t == "01":
            nm, val = "flags", val + (" (LE general discoverable)" if val in ("02", "06", "1A", "1E") else "")
        elif t in ("08", "09"):
            val = data.decode("utf-8", "replace")
        elif t == "16" and len(data) >= 2:
            u = format(int.from_bytes(data[:2], "little"), "04X")
            nm = f"service data {u}" + (f" ({BT['uuids'][u]['name']})" if u in BT["uuids"] else "")
            val = data[2:].hex().upper() or "—"
        elif t == "FF" and len(data) >= 2:
            continue                                  # shown above as company + data
        out.append({"what": f"AD {t} {nm}", "value": val})
    for u in sorted(f["svc"] - set(f["sd"])):
        out.append({"what": "Service UUID", "value": u + (f" {BT['uuids'][u]['name']}" if u in BT["uuids"] else "")})
    for u in f["uuid128"]:
        out.append({"what": "128-bit UUID", "value": u})
    if d["name"]:
        out.append({"what": "Name", "value": d["name"]})
    return out


def _changing(raws):
    """Byte positions that differ between same-length advertisements: counters, rotating ids."""
    by_len = Counter(len(r) for r in raws)
    if not by_len:
        return []
    ln = by_len.most_common(1)[0][0]
    same = [r for r in raws if len(r) == ln]
    if len(same) < 2:
        return []
    diff = [i for i in range(ln // 2) if len({r[2 * i:2 * i + 2] for r in same}) > 1]
    spans, start = [], None
    for i in range(ln // 2 + 1):
        if i in diff and start is None:
            start = i
        elif i not in diff and start is not None:
            spans.append(f"{start}" if i - 1 == start else f"{start}–{i - 1}")
            start = None
    return spans


def _suggest(d, f, custom):
    """A starting point for a rule: the most specific thing every radio of the bucket shares."""
    m = {"kind": "BLE"}
    if f["mfg"]:
        m["mfg"] = f["mfg"]
        if f["mfg_data"][:1]:
            m["mfg_data_prefix"] = f["mfg_data"][:1].hex().upper()
    elif f["sd"]:
        m["service_data_uuid"] = sorted(f["sd"])[0]
    elif custom:
        m["uuid128_any"] = [custom[0]]
    elif f["svc"]:
        m["service_uuid_any"] = sorted(f["svc"])[:1]
    else:
        ads = [t for t, _ in f["ads"] if t not in GENERIC_AD]
        if ads:
            m["ad_type"] = ads[0]
    shape = _name_shape(d["name"] or d.get("group_name", ""))
    if shape and len(m) == 1:
        m["name_regex"] = "^" + re.escape(shape).replace("\\#", "[0-9A-Za-z]+").replace("#", "[0-9A-Za-z]+") + "$"
    return m


def _hints(b, ds, f, custom):
    h = []
    if f["mfg"]:
        comp = BT["companies"].get(f["mfg"])
        h.append(f"Company 0x{f['mfg']} is {comp} in the Bluetooth SIG list." if comp else
                 f"Company 0x{f['mfg']} is not in the Bluetooth SIG list: maybe a made-up ID or the raw is cut short.")
    for u in sorted(f["sd"]) + sorted(f["svc"] - set(f["sd"])):
        e = BT["uuids"].get(u)
        if e:
            h.append(f"UUID {u} is {e['name']} ({'a member UUID of that company' if e.get('kind') == 'member' else e.get('kind', 'SIG')}).")
    for u in custom:
        h.append(f"{u} is a vendor's own UUID: searching it on GitHub often finds the app or library that uses it.")
    days = set().union(*(d["_days"] for d in ds)) if ds else set()
    trav = sum(1 for d in ds if d["travelled"])
    if trav and trav >= len(ds) / 2:
        h.append(f"{trav} of {len(ds)} travelled with you: possibly your own device, or one in your car or bag.")
    places = {(round(d["lat"], 3), round(d["lon"], 3)) for d in ds if d["lat"] is not None}
    if len(days) >= 3 and len(places) <= 2:
        h.append(f"Heard on {len(days)} days at the same place: probably fixed equipment there.")
    if len(ds) >= 5 and len({d['group'] or d['mac'] for d in ds}) < len(ds) / 2:
        h.append("Many addresses but few devices: the device changes its address (privacy), the group keeps it together.")
    elif len(ds) >= 10:
        h.append("Many addresses with the same advertisement: a common product, or one device rotating its address.")
    names = {d["name"] for d in ds if d["name"]}
    if names:
        h.append("Names heard: " + ", ".join(sorted(names)[:6]) + ". A product's default name is often the best search term.")
    if not f["mfg"] and not f["sd"] and not custom and not f["svc"]:
        h.append("Only flags (or nothing): the device sends the rest in a scan response, which Fieldwatch does not log.")
    return h


@app.route("/api/wb/fp")
@needs("admin")
def api_wb_fp():
    fp = request.args.get("fp", "")
    st = _wb_state()
    b = st["buckets"].get(fp)
    if not b:
        return jsonify({"error": "not found (the data or the rules changed; reload)"}), 404
    notes = _wb_notes()
    tab = next((t for t in WB_TABS if any(d["_tab"] == t for d in b["devs"])), None)
    ds = sorted(b["devs"], key=lambda d: -d["hears"])
    ex = ds[0]
    _, _, x = _fingerprint(ex)
    f, custom = x["f"], x["custom"]
    raws = [d["raw"] for d in ds if d["raw"]]
    variants = [{"raw": r, "count": n} for r, n in Counter(raws).most_common(20)]
    # heard together with: other BLE radios in the same 5-minute slots, by what they are
    slots = set().union(*(d["_slots"] for d in ds)) if ds else set()
    near = Counter()
    if slots:
        for d in st["devs"]:
            if d["_fp"] == fp:
                continue
            shared = len(d["_slots"] & slots)
            if shared and shared >= max(2, len(slots) // 3):
                what = d["ident"] or (d["category"] if d["category"] != "Unidentified" else "") or "unknown: " + st["buckets"][d["_fp"]]["label"][:60]
                near[what] += 1
    q = custom[0] if custom else (f"0x{f['mfg']} {f['mfg_data'][:1].hex().upper()}" if f["mfg"] else (sorted(f["sd"])[0] if f["sd"] else ""))
    names = sorted({d["name"] for d in ds if d["name"]})
    search = []
    if q:
        search.append({"label": "Search the web", "url": "https://www.google.com/search?q=" + quote_plus(f'"{q}" bluetooth')})
        search.append({"label": "Search GitHub code", "url": "https://github.com/search?type=code&q=" + quote_plus(q.replace("0x", "").split()[0])})
    if names:
        search.append({"label": f"Search '{names[0][:24]}'", "url": "https://www.google.com/search?q=" + quote_plus(f'"{names[0]}" bluetooth')})
    search.append({"label": "SIG assigned numbers", "url": "https://www.bluetooth.com/specifications/assigned-numbers/"})
    item = _wb_item(b, None, notes)
    item["tab"] = tab
    return jsonify({
        "item": item, "decoded": _decode(ex, f), "variants": variants, "changing": _changing(raws),
        "hints": _hints(b, ds, f, custom), "search": search,
        "together": [{"what": k, "n": n} for k, n in near.most_common(8)],
        "suggest": _suggest(ex, f, custom),
        "radios": [{"kind": d["kind"], "mac": d["mac"], "name": d["name"], "rssi_max": d["rssi_max"], "hears": d["hears"],
                    "days": sorted(d["_days"]), "lat": d["lat"], "lon": d["lon"], "travelled": d["travelled"],
                    "ident": d["ident"], "group": d["group"]} for d in ds[:60]],
    })


@app.route("/api/wb/note", methods=["POST"])
@needs("admin")
def api_wb_note():
    b = request.get_json(silent=True) or {}
    fp, status, note = str(b.get("fp", ""))[:40], str(b.get("status", "new")), str(b.get("note", ""))[:2000]
    if not re.fullmatch(r"[0-9a-f]{12}", fp) or status not in WB_STATUS:
        return jsonify({"error": "bad fingerprint or status"}), 400
    u = current_user()
    with _db_lock, db() as con:
        con.execute("INSERT INTO wb_notes(fp, status, note, updated, by_user) VALUES (?,?,?,?,?)"
                    " ON CONFLICT(fp) DO UPDATE SET status=excluded.status, note=excluded.note,"
                    " updated=excluded.updated, by_user=excluded.by_user",
                    (fp, status, note, datetime.now().isoformat(timespec="seconds"), u.get("username", "")))
    return jsonify({"ok": True})


_HEX = re.compile(r"^[0-9A-F]*$")
_UUID128 = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")
LAYERS = sorted(set(CLASS_CATEGORY.values()) - {"Network"})


def _clean_rule(r, need_meta=True):
    """Check a rule made in the Workbench; returns (rule, error)."""
    m_in = r.get("match") or {}
    if not isinstance(m_in, dict):
        return None, "match must be an object"
    m = {"kind": "BLE"}
    for k in ("mfg", "service_data_uuid"):
        v = str(m_in.get(k) or "").strip().upper().replace("0X", "")
        if v:
            if not re.fullmatch(r"[0-9A-F]{1,4}", v):
                return None, f"{k}: 4 hex digits"
            m[k] = v.zfill(4)
    for k in ("mfg_data_prefix", "ad_data_prefix"):
        v = str(m_in.get(k) or "").strip().upper().replace(" ", "")
        if v:
            if not _HEX.match(v) or len(v) % 2 or len(v) > 64:
                return None, f"{k}: whole bytes of hex"
            m[k] = v
    v = str(m_in.get("ad_type") or "").strip().upper()
    if v:
        if not re.fullmatch(r"[0-9A-F]{2}", v):
            return None, "ad_type: 2 hex digits"
        m["ad_type"] = v
    for k, pat in (("service_uuid_any", r"[0-9A-F]{4}"), ("uuid128_any", _UUID128.pattern[1:-1])):
        v = m_in.get(k)
        if v:
            if isinstance(v, str):
                v = [x for x in re.split(r"[,\s]+", v) if x]
            v = [str(x).strip().upper() for x in v][:10]
            if not all(re.fullmatch(pat, x) for x in v):
                return None, f"{k}: list of {'4-digit' if k == 'service_uuid_any' else '8-4-4-4-12'} UUIDs"
            m[k] = v
    v = str(m_in.get("name_regex") or "").strip()
    if v:
        if len(v) > 200:
            return None, "name_regex too long"
        try:
            re.compile(v)
        except re.error as e:
            return None, f"name_regex: {e}"
        m["name_regex"] = v
    if len(m) == 1:
        return None, "the rule needs at least one condition"
    if not need_meta:
        return {"id": "preview", "name": "preview", "match": m}, None
    name = str(r.get("name") or "").strip()[:80]
    layer = str(r.get("layer") or "")
    conf = str(r.get("confidence") or "low")
    if not name:
        return None, "give the rule a name"
    if layer and layer not in LAYERS:
        return None, "unknown layer"
    if conf not in WB_CONF:
        return None, "confidence: high, medium or low"
    rid = re.sub(r"[^a-z0-9]+", "-", str(r.get("id") or name).lower()).strip("-")[:48] or "rule"
    if not rid.startswith("local-"):
        rid = "local-" + rid
    out = {"id": rid, "name": name, "layer": layer, "confidence": conf, "match": m,
           "note": str(r.get("note") or "").strip()[:1000]}
    if r.get("fp"):
        out["from_fp"] = str(r["fp"])[:12]
    return out, None


def _compile_one(rule):
    load = {"_m": dict(rule["match"])}
    m = load["_m"]
    if m.get("name_regex"):
        m["_name_re"] = re.compile(m["name_regex"], re.I)
    for k in ("service_uuid_any", "uuid128_any"):
        if m.get(k):
            m[k] = set(m[k])
    return m


@app.route("/api/wb/preview", methods=["POST"])
@needs("admin")
def api_wb_preview():
    rule, err = _clean_rule(request.get_json(silent=True) or {}, need_meta=False)
    if err:
        return jsonify({"error": err}), 400
    m = _compile_one(rule)
    st = _wb_state()
    hit = [d for d in st["devs"] if _rule_matches(m, d["kind"], d["name"] or d["group_name"], adv_features(d["mfg"], d["raw"], d["uuids"]))]
    by = Counter(d["_tab"] or "identified" for d in hit)
    others = Counter(d["ident"] for d in hit if not d["_tab"] and d["ident"])
    return jsonify({"match": rule["match"], "matches": len(hit), "by_tab": dict(by),
                    "kinds": len({d["_fp"] for d in hit}),
                    "already": [{"ident": k, "n": n} for k, n in others.most_common(6)],
                    "examples": [{"mac": d["mac"], "name": d["name"], "label": st["buckets"][d["_fp"]]["label"]} for d in hit[:8]]})


def _save_local(rules):
    os.makedirs(IDS_DIR, exist_ok=True)
    tmp = LOCAL_RULES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"name": "Harava local rules", "note": "Made in the Workbench on this server; not in git. "
                   "Send good ones to be added to harava_ids.json.", "rules": rules}, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, LOCAL_RULES)


@app.route("/api/wb/rules")
@needs("admin")
def api_wb_rules():
    return jsonify({"rules": [{k: v for k, v in r.items() if k != "local"} for r in LOCAL["rules"]], "layers": LAYERS})


@app.route("/api/wb/rule", methods=["POST"])
@needs("admin")
def api_wb_rule():
    body = request.get_json(silent=True) or {}
    rule, err = _clean_rule(body)
    if err:
        return jsonify({"error": err}), 400
    rules = [{k: v for k, v in r.items() if k != "local"} for r in LOCAL["rules"]]
    rule["created"] = datetime.now().isoformat(timespec="seconds")
    rules = [r for r in rules if r["id"] != rule["id"]] + [rule]
    with _db_lock:
        _save_local(rules)
        status = load_ids()
    if rule.get("from_fp"):
        with _db_lock, db() as con:
            con.execute("INSERT INTO wb_notes(fp, status, note, updated, by_user) VALUES (?, 'identified', ?, ?, ?)"
                        " ON CONFLICT(fp) DO UPDATE SET status='identified', updated=excluded.updated",
                        (rule["from_fp"], f"rule {rule['id']}", datetime.now().isoformat(timespec="seconds"),
                         current_user().get("username", "")))
    return jsonify({"ok": True, "id": rule["id"], "status": status, "rules": len(HV_RULES)})


@app.route("/api/wb/rule/<rid>", methods=["DELETE"])
@needs("admin")
def api_wb_rule_delete(rid):
    rules = [{k: v for k, v in r.items() if k != "local"} for r in LOCAL["rules"]]
    keep = [r for r in rules if r["id"] != rid]
    if len(keep) == len(rules):
        return jsonify({"error": "no such local rule"}), 404
    with _db_lock:
        _save_local(keep)
        load_ids()
    return jsonify({"ok": True})


@app.route("/api/wb/export")
@needs("admin")
def api_wb_export():
    """The local rules and the notes as one JSON file, to move them to harava_ids.json or another server."""
    st, notes = _wb_state(), _wb_notes()
    data = {"exported": datetime.now().isoformat(timespec="seconds"), "harava": APP_VERSION,
            "rules": [{k: v for k, v in r.items() if k != "local"} for r in LOCAL["rules"]],
            "notes": [dict(n, label=st["buckets"][n["fp"]]["label"] if n["fp"] in st["buckets"] else "")
                      for n in notes.values()]}
    resp = app.response_class(json.dumps(data, ensure_ascii=False, indent=1), mimetype="application/json")
    resp.headers["Content-Disposition"] = f"attachment; filename=harava_workbench_{datetime.now():%Y%m%d}.json"
    return resp


@app.route("/workbench")
@needs("admin")
def workbench():
    return render_template("workbench.html", app_name=APP_NAME, version=APP_VERSION, user=current_user(),
                           layers=LAYERS)


# ─────────────── my devices ───────────────

OWN_MATCHES = ("mac", "mac_prefix", "name", "name_contains")


@app.route("/api/own")
def api_own():
    u = current_user()
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM own_devices WHERE owner_id=? ORDER BY label COLLATE NOCASE",
                                             (u["id"] if u else 0,))]
    return jsonify({"own": rows})


@app.route("/api/own", methods=["POST"])
@needs("analyze")
def api_own_add():
    o = request.get_json(force=True, silent=True) or {}
    match = o.get("match", "")
    value = clean_text(o.get("value", "")).strip()
    kind = (o.get("kind") or "").upper()
    label = clean_text(o.get("label", "")).strip() or value
    if match not in OWN_MATCHES or not value:
        return jsonify({"error": "choose how to match and give a value"}), 400
    if kind not in ("", "WIFI", "BLE"):
        return jsonify({"error": "radio must be Any, WIFI or BLE"}), 400
    if match.startswith("mac"):
        value = value.upper().replace("-", ":")
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){0,5}:?", value):
            return jsonify({"error": "address must look like AA:BB:CC:DD:EE:FF (or its start)"}), 400
        value = value.rstrip(":")
        if match == "mac" and len(value) != 17:
            return jsonify({"error": "a full address has 6 parts, AA:BB:CC:DD:EE:FF"}), 400
    with _db_lock, db() as con:
        uid = current_user()["id"]
        dup = con.execute("SELECT id FROM own_devices WHERE owner_id=? AND kind=? AND match=? AND value=? COLLATE NOCASE",
                          (uid, kind, match, value)).fetchone()
        if dup:
            return jsonify({"error": "that rule already exists"}), 400
        cur = con.execute("INSERT INTO own_devices(owner_id, label, kind, match, value, created) VALUES (?,?,?,?,?,?)",
                          (uid, label, kind, match, value, datetime.now().isoformat(timespec="seconds")))
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/own/<int:oid>", methods=["DELETE"])
@needs("analyze")
def api_own_delete(oid):
    with _db_lock, db() as con:
        con.execute("DELETE FROM own_devices WHERE id=? AND owner_id=?", (oid, current_user()["id"]))
    return jsonify({"ok": True})


@app.route("/api/whatsnew")
def api_whatsnew():
    entries = read_whats_new()
    return jsonify({"version": entries[0]["version"] if entries else "", "entries": entries[:10]})


@app.route("/api/guides")
def api_guides():
    return jsonify({"guides": list_guides()})


@app.route("/doc/<name>")
def serve_doc(name):
    """One PDF from doc/ (plain *.pdf file names only)."""
    if not _PDF_NAME.match(name or "") or ".." in name:
        abort(404)
    return send_from_directory(DOC_DIR, name, mimetype="application/pdf")


def main():
    if "--add-admin" in sys.argv or "--set-password" in sys.argv:
        admin_cli()
        return
    print(f"{APP_NAME} {APP_VERSION} — rakes through what your phone heard")
    if CFG["require_login"] and not users_exist():
        sys.exit("ERROR: require_login = true but there are no users yet. Run:  python harava.py --add-admin")
    move_old_database()
    init_db()
    claim_open_data()
    os.makedirs(CFG["watch_folder"], exist_ok=True)
    url = f"http://{CFG['host']}:{CFG['port']}/"
    print(f"  database     : {CFG['db']}")
    print(f"  scan folder  : {CFG['watch_folder']}")
    with udb() as con:
        n_users = con.execute("SELECT COUNT(*) FROM users WHERE active = 1").fetchone()[0]
    print(f"  sign-in      : {f'on, {n_users} active user(s); admin page /maintenance' if users_exist() else 'off (no users; python harava.py --add-admin)'}")
    if CFG["behind_proxy"]:
        print("  proxy        : behind nginx (X-Forwarded-*, secure cookies)")
    print(f"  open         : {url}   (Ctrl+C stops)")
    if CFG["open_browser"] and "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # Flask's own server, as in WSPR Logger; behind nginx it trusts nginx's X-Forwarded-* headers
    # (one proxy) so sign-in logs and secure cookies see the real address and https.
    if CFG["behind_proxy"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.run(host=CFG["host"], port=CFG["port"], debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
