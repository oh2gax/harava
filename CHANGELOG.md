# Changelog

All notable changes to Harava are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The user-facing notes
are in `doc/whats_new.md` (shown under *✨ What's new* in the app).

---

## [2026-11-09]

### Changed
- **Data store (schema 2, `PRAGMA user_version = 2`)** — `hears` holds every hear once per phone:
  `collector_id` + `dkey` UNIQUE (`L<ts>|kind|mac|rssi` for log rows, `K…` for KML rows,
  `S<sit_key>|kind|mac` for Sit rows); new `import_rows(import_id, hear_id)` links each import to
  the hears it contained; `hears.import_id` is the first import. A repeated row fills empty values
  and keeps the longer `raw` / `uuids` / `fleets` / `flags` (CSV cuts raw to 80 chars); a Sit row is
  replaced by a newer export (more hits). `imports` gained `owner_id`, `collector_id`, `verdict`,
  `new_rows`, `known_rows`, `shared` ('' / '*' / ',id,id,'), `archived`, `size`, `sit_key`; `sha1`
  is unique per phone (UNIQUE(collector_id, sha1)). New table `collectors(id, owner_id, name)`;
  `own_devices.owner_id`. `_migrate_v2` converts an older database at the first start in one
  transaction (BEGIN IMMEDIATE … COMMIT / ROLLBACK), keeping import ids, switches and paths; the
  author's data: 4 imports, 17,084 stored rows → 12,186 hears, the same 11,530 visible hears as before.
- **Import** — `import_bytes(filename, data, path, owner_id, collector_id)` → `store_hears` (count
  new / known), `_verdict`: new · extends (all new rows after the newest known one) · overlaps ·
  duplicate · sit update. `_sit_key`: a Sit export with ≥ 80 % of its (kind, mac, first_seen)
  pairs in an earlier Sit of the same phone is the same sit. The KML rule now looks only at the
  same phone; the old "newer sit switches the older off" rule is replaced by merging. Notes are
  stored in `imports.note`. `scan_folder` imports oldest file first; `import_upload` opens `.zip`
  files (members oldest first). Originals are kept as `archive/<owner>/<sha1><ext>.gz`
  (`keep_originals`, default true; not for Scan folder files, which keep their path).
  `delete_import` removes links and only the hears no other import contains.
- **Whose data** — `visible_imports(con, user, scope)` is the single choke point (`scope_ids`
  caches it per request); `query_hears` filters by `EXISTS (import_rows …)` (skipped when every
  import is visible) and returns `collector_id`; `build_devices` adds `phones`. Scope in the
  session: `mine+shared` (default) · `mine` · `u:<id>` · `all` (admin). Without users (desktop)
  everything is visible. The Workbench always uses every user's data. `claim_open_data()` gives
  owner-0 rows to the first admin at start and when the first user is added.
- **My devices per user** — `/api/own` lists, adds and deletes only the caller's rules.
- `update.sh --rollback` restores the pre-update copy of `harava.db` when the database's
  `user_version` is newer than the rolled-back code's `SCHEMA_VERSION` (keeps the newer one as
  `harava.db.newer-<time>`).
- `config.ini.example`: sign-in comments updated (`--add-admin`, `harava_users.db`), `keep_originals`.
- `deploy/harava.service`: `Environment=TZ=Europe/Helsinki`, so days and times on the server
  follow Finnish time (the server itself runs in UTC).

### Added
- **Database snapshot** (Maintenance, admin) — `make_snapshot`: SQLite online backup of
  `harava.db` into a temporary file; for *My data only* the other owners' imports, links, paths,
  orphan hears, phones and My devices are deleted; then `journal_mode=DELETE` + `VACUUM` (no free
  pages with removed data); zipped as `snapshots/harava_snapshot_YYYYMMDD_HHMMSS[_mine].zip` with
  `local_rules.json` and `snapshot_info.json` (version, schema, created, made_by, contents, imports,
  hears, first/last, owners), mode 0600, 10 newest kept. APIs `GET/POST /api/snapshots`,
  `GET /api/snapshots/<name>` (download), `DELETE /api/snapshots/<name>`; names checked against
  a pattern; every action in the auth log. `snapshots/` in `.gitignore` and skipped by Scan folder.
- **Share target** — the phone app's manifest has `share_target` (POST multipart `files` to
  `/app/share`; CSV, JSON lines, KML, zip) and `id`. `static/app-sw.js`, served as `/app/sw.js`
  (scope `/app/`, open endpoint, `Cache-Control: no-cache`), catches that POST on the phone, keeps
  the files in cache storage `harava-share` (with `/app/shared-meta`) and redirects to
  `/app?shared=N`; `m-app.js` registers it and uploads pending shares through `/api/import`
  (at once with one phone, otherwise after choosing one; Analyze needed; shares older than 24 h
  dropped; kept across a sign-in). Nothing else is cached. Without the worker, `POST /app/share`
  only shows a help page and never imports (no Origin check there, so it must not act).
- **APIs** — `GET/POST /api/scope`, `GET /api/people`, `GET/POST /api/collectors`,
  `POST /api/imports/<id>/share`, `POST /api/imports/<id>/reread`; `/api/import` takes `collector`
  and `.zip`; `/api/imports` returns owner, phone, verdict, sharing and `can_edit`; toggle, delete,
  share and re-read are allowed for the owner or an admin (JSON 403 otherwise); Scan folder for
  admins (or without users) only.
- **Pages** — sidebar *Whose data* and *Until*; Imports page with *Phone*, coverage bars, Result,
  Owner · phone, Shared, Share dialog, Re-read; "N phones" tag and pill. Phone app: *Until* and
  *Whose data* in the menu, phone choice, results, coverage, sharing and Re-read on Imports.
- **Phone app** — `/app` (`templates/app.html`, `static/m-app.js`, `static/m-app.css`, icons in
  `static/icons/`), the WSPR Logger phone view's design: top bar (☰, title + day, local clock, theme,
  data status dot: newest hear < 6 h green, < 48 h amber), one view at a time (Map, Day, Watch list,
  Devices, Device, Phone check, Identification, Imports, Workbench, Unknown radio), ☰ drawer with a
  day stepper, view tiles and one folding option section per view (`select[data-chips]` → chips);
  options in `localStorage` (`harava_app_opts`, `harava_app_view`, `harava_app_day`), theme shared
  with the full page (`fwa_theme_mode`). Uses the same APIs and permissions; History API for Back
  (menu, sheet, views). `/app/manifest.webmanifest` (open) for *Add to Home Screen*;
  `/api/app/build` = newest mtime of `templates/` + `static/` (without `vendor/`): the app reloads
  when it differs or after 1 h in the background.
- **Shared helpers** — `static/common.js` (layers and colours, formatting, `ownRuleHits`,
  `groupSame`, `phoneType`, `phoneBoxLimit`, `phoneZones`) used by `app.js` and `m-app.js`.
- **Workbench (admin)** — `/workbench` (`templates/workbench.html`) and the phone app's Workbench.
  `_fingerprint(d)`: BLE radios of all active imports bucketed by company + first byte of the
  manufacturer data (Apple / Microsoft message types named), service data UUIDs, custom 128-bit
  UUIDs, other services and AD types; the name's shape (`_name_shape`: alphanumeric runs with 3+
  digits → `#`) only when nothing else tells. Tabs: `unknown` (data, no rule), `weak` (company only,
  custom UUID only or a low-confidence rule), `nodata` (flags only). Cached in `_WB_CACHE` until the
  active imports, the hear count or the rules change. APIs: `GET /api/wb?tab=` (items, counts,
  kinds, per-day progress, statuses), `GET /api/wb/fp?fp=` (decoded AD structures, variants,
  changing byte spans, hints, search links, heard together with — other radios sharing ≥ ⅓ of the
  5-minute slots —, suggested match, up to 60 radios), `POST /api/wb/note`, `POST /api/wb/preview`,
  `GET /api/wb/rules`, `POST /api/wb/rule`, `DELETE /api/wb/rule/<id>`, `GET /api/wb/export`.
- **Table `wb_notes`** in `harava.db`: `fp` (12 hex), `status` ∈ {new, investigating, identified,
  ignore, cannot}, `note`, `updated`, `by_user`. Saving a rule from a row marks it *identified*.
- **Local rules** — `ids/local_rules.json` (not in git, written atomically): rules saved in the
  Workbench get the id prefix `local-`, are checked (hex fields, 4-digit / 128-bit UUID lists,
  `name_regex` compiles, a known layer, at least one condition) and are loaded before
  `harava_ids.json`, so they win while being tried out. `/api/ids` shows `db.local` and
  `rules[].local`. `update.sh` copies the file with the database backups.
- **Users and permissions** — users in their own SQLite file `harava_users.db` (tables `users`:
  username (unique, case-insensitive), first / last name, `role` ∈ {`view`, `analyze`, `admin`},
  Werkzeug scrypt `password_hash`, `active`, `must_change`, created / updated / last_login; and
  `auth_log`: time, username, address, event, the newest 2000 kept). Without any user Harava is
  open (desktop mode, treated as admin). `@needs(role)` marks the least permission of a route;
  `guard()` (`before_request`) answers `401` (API) or redirects to `/login` (pages), `403` for a
  missing permission, and sends a user with a one-time password to `/account`. *Analyze* routes:
  `POST /api/import`, `POST /api/scan`, `POST /api/imports/<id>/toggle`, `DELETE /api/imports/<id>`,
  `POST /api/ids/reload`, `POST /api/own`, `DELETE /api/own/<id>`; *Admin*: `/maintenance`,
  `/api/users*`.
- **Sign-in** — `/login` (`templates/login.html`), `POST /logout`, `/account` with
  `POST /api/account/password` (current + new, at least 12 characters; keeps this session, signs
  out the others). The session stores the user id and a fingerprint of the password hash, so a new
  password, a disabled user or a removed user ends every old session at once; the role is read
  from the database on every request. *Keep me signed in* = a permanent session of `session_days`.
  Failed sign-ins: 1 s delay, 5 per address or 30 in all per 15 min → *Too many failed sign-ins*
  (`429`); unknown usernames take the same hashing time (`_DUMMY_HASH`).
- **Maintenance page** — `/maintenance` (`templates/maintenance.html`, `static/admin.css`): users
  with editable first / last name, permission and *active*, last sign-in, *New password*
  (`POST /api/users/<id>/password` → one-time password, `must_change`), *Remove*
  (`DELETE /api/users/<id>`), *Add user* (`POST /api/users`; without a password a one-time password
  is made and shown once), and the newest 60 `auth_log` rows. Guards: at least one active admin
  stays; nobody removes or disables themselves; the first user added on an open Harava is always
  an admin.
- **Command line** — `python harava.py --add-admin` adds an admin or sets a new password for an
  existing user (and makes them an admin). `--set-password` is an alias.
- **Server mode** — `config.ini` keys `require_login` (refuse to start without users),
  `behind_proxy` (Werkzeug `ProxyFix` for one proxy: `X-Forwarded-For / -Proto / -Host`, and
  `SESSION_COOKIE_SECURE`) and `session_days`. `GET /healthz` answers `ok` without a sign-in.
- **Deployment** — `deploy/harava.service` (systemd, user `ubuntu`, `/home/ubuntu/harava`,
  `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, `UMask=0077`),
  `deploy/harava.oh2gax.org.nginx` (one `server_name`, `client_max_body_size 512m`, proxy to
  `127.0.0.1:5057`, `X-Robots-Tag: noindex`), and `update.sh` adapted from WSPR Logger's: backs up
  `harava.db` and `harava_users.db` (SQLite online backup, 7 kept), syntax-checks changed `.py`,
  templates **and `.json`** files from git before changing anything, `git merge --ff-only`,
  `pip install` only when `requirements.txt` changed, restarts only the `harava` service and waits
  for `/healthz`; `--rollback`; warns about a missing `api_keys.py`, `require_login` not true and
  no users. Tested against a simulated origin: update, a broken `ids/harava_ids.json` refused,
  rollback.
- **Mask addresses** — `static/privacy.js` and a switch under *Map layers* (`localStorage
  harava_mask`, or `?mask=1`): a `MutationObserver` rewrites text nodes, `title` attributes and the
  My devices form on the screen only — MAC addresses keep the OUI (`AA:BB:CC:••:••:••`), every
  Wi-Fi name (own ones too, from `S.devices` and `S.own`) becomes *Wi-Fi network N*, possessive
  names become `•••'s …`, other Windows PC names *PC N*. The form keeps the real value in
  `dataset.real`, so *Add* still saves the full address. All guide screenshots were retaken with
  it, and a page-wide check found no full address left.
- **Identification database** — `ids/bt_assigned_numbers.json` (Bluetooth SIG: 4065 company IDs,
  819 service / SDO / member UUIDs, 51 AD types; `tools/update_bt_numbers.py` fetches the YAML
  files from the SIG's public repository with a line parser, no PyYAML) and `ids/harava_ids.json`
  (44 rules, first match wins; match fields `kind`, `name_regex`, `mfg`, `mfg_data_prefix`,
  `ad_type`, `ad_data_prefix`, `service_data_uuid`, `service_uuid_any`, `uuid128_any`; optional
  `group` = bytes [from, to) of an AD type, the manufacturer data or service data, with an
  optional shared `key`). `parse_ad()`, `adv_features()`, `identify()`; fallbacks *company device*
  and *device with custom service*. `build_devices()` adds `ident`, `ident_id`, `ident_conf`,
  `ident_note`, `company`, `group`, `group_size`, `group_name`; an Unidentified radio takes the
  rule's layer. A nameless address of a named group is identified again with the group's name
  (name rules only). `/api/ids`, `/api/ids/reload`; the *🔎 Identification* page (counts, rules
  with hits and devices, top 30 unknown patterns from `_pattern()`).
- **Rules of 9 November** — Bluetooth Mesh: beacon not set up (2B/00) and PB-GATT provisioning
  service 1827 share the group key `mesh-device` (the device UUID), network beacon 2B/01, proxy
  1828 = set up; EV charging points (*Charge* + Mesh provisioning / beacon, medium, *DEFA?*);
  Signify / Philips Hue (FE0F; FD8A / FE4B; company 060F); Windows service
  3E1D50CD-7E3E-427D-8E1C-B78AA87FE624 → Windows PC (medium); Apple Continuity types, Samsung,
  Microsoft, Oral-B, BEHA, OnePlus, Google FEF3 / FCF1 / FE9F, LE Audio, heart rate, exposure
  notification, name rules.
- **Front end** — *Identified as* and *Company / vendor* columns and detail rows; the *Identified
  as* filter (options grouped by layer, counts after the other filters); *Group same device*
  (`groupSame()`: the loudest address represents the group, hears summed, *N addr.* tag); the
  signed-in user and ⏻ in the sidebar; *⚙️ Maintenance* for admins; elements marked
  `needs-analyze` hidden for *View only* (`body.role-view`); `api()` follows `401` to `/login` and
  a must-change `403` to `/account`.
- **Documents** — `doc/Harava_Admin_Guide.pdf` (server installation, users, updating, backups,
  troubleshooting), the User guide's new sections (Identification, sign-in and permissions, Mask
  addresses), README in the WSPR Logger structure, this changelog, `LICENSE` (MIT).
- Name **Harava**, What's new / User guides / About window (`static/info.js`), `doc/whats_new.md`,
  `doc/guides.json`, `doc/Harava_User_Guide.pdf`; Sit exports (CSV, JSON lines, KML); device
  layers with Glasses & VR; icon pins; My devices (`own_devices`); the timeline's *Show* selector;
  the CARTO key from `api_keys.py`; the project folder layout (`data/`, `tmp_files/` in
  `.gitignore`); `fieldwatch.db` → `harava.db` on the first start.

### Changed
- Grouping by a shared custom 128-bit service now needs the same name too: the Ruuvi tags (Nordic
  UART 6E400001…) and the Windows PCs (3E1D50CD…) had been joined into one device each.
- Only groups of two or more addresses are counted on the Identification page (957 addresses in
  23 devices instead of 1029 in 34).
- Rule notes in `ids/harava_ids.json` no longer name devices or places from the author's data.
- Responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin` and (except static files) `Cache-Control: no-store`.
- `requirements.txt`: `flask>=3.0` only; Harava runs on Flask's own server behind nginx, as WSPR
  Logger does.

### Security
- State-changing requests (`POST`, `PUT`, `DELETE`, `PATCH`) must come from Harava's own origin
  (`Origin` / `Referer` checked against `request.host_url`), in addition to `SameSite=Lax`.
- The `/login?next=` target must be a local path (no `//`, no backslash).
- Session cookie `harava_session`: `HttpOnly`, `SameSite=Lax`, `Secure` behind the proxy; the
  signing key is random and kept in `.harava_secret` (mode 600, not in git).
- `harava_users.db` is created with mode 600; the systemd unit sets `UMask=0077`.

### Fixed
- Workbench, Maintenance and Account pages could not scroll: `app.css` locks `html` and `body`
  (`overflow: hidden`) for the map view, and `body.admin-page` only set `overflow: auto` with
  `height: auto`, so nothing scrolled and the bottom of a long page (the Workbench's *Make a rule*
  and *Local rules*) was out of reach. The admin body is now its own scroll area (`height: 100dvh`,
  `overflow-y: auto`).

## [2026-11-08]

### Added
- First version (as *Fieldwatch Analyzer*): import of Fieldwatch log CSV, JSON lines and KML
  (NUL characters of hidden Wi-Fi names removed, so Fieldwatch's KML files that Google Earth
  refuses load), duplicate files skipped by SHA-1, a KML overlapping a log switched off; the map
  with the phone's path and one pin per radio at its loudest hear; the timeline of hears and new
  radios per 10 minutes; the Devices list with search, filters, sorting and CSV export; tags for
  *travelled* radios, *separated* trackers (Apple Find My, Google Find Hub, DULT) and Ruuvi
  RAWv5 readings; the classroom Phone check with auto-calibration; Leaflet and Chart.js bundled.
