# Changelog

All notable changes to Harava are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The user-facing notes
are in `doc/whats_new.md` (shown under *✨ What's new* in the app).

---

## [2026-11-09]

### Changed
- `deploy/harava.service`: `Environment=TZ=Europe/Helsinki`, so days and times on the server
  follow Finnish time (the server itself runs in UTC).

### Added
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

## [2026-11-08]

### Added
- First version (as *Fieldwatch Analyzer*): import of Fieldwatch log CSV, JSON lines and KML
  (NUL characters of hidden Wi-Fi names removed, so Fieldwatch's KML files that Google Earth
  refuses load), duplicate files skipped by SHA-1, a KML overlapping a log switched off; the map
  with the phone's path and one pin per radio at its loudest hear; the timeline of hears and new
  radios per 10 minutes; the Devices list with search, filters, sorting and CSV export; tags for
  *travelled* radios, *separated* trackers (Apple Find My, Google Find Hub, DULT) and Ruuvi
  RAWv5 readings; the classroom Phone check with auto-calibration; Leaflet and Chart.js bundled.
