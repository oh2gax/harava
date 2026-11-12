# Changelog

All notable changes to Harava are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The user-facing notes
are in `doc/whats_new.md` (shown under *✨ What's new* in the app).

---

## [2026-11-12]

From the data check of an 11 Nov drive (23 km): "travelled with you" marked 57 addresses, of which
38 were one watch and the rest other traffic.

### Added
- **Stops.** `find_stops()` cuts a phone's track (the positions on rows that have a time and place
  of their own: log and KML rows, not Sit rows) into stops: the phone stayed within
  `STOP_RADIUS_M` (150 m) of where the stop began for at least `STOP_MIN_S` (300 s). Stops within
  `PLACE_JOIN_M` (250 m) are the same place. `phone_stops()` does it per phone; `/api/devices`
  returns them as `stops`.
- **Travel levels.** `travel_of()` judges a device against the stops: `stops` = heard at two stops
  at least `travel_min_m` apart; `road` = heard over `ROAD_MIN_S` (120 s) and `ROAD_FACTOR` × 
  `travel_min_m` within one stretch of movement, or at a stop and again (twice, 30 s apart) that
  far from it. A row without a GPS fix counts at the stop its time falls in. New device fields
  `travel`, `travel_stops`, `travel_km`, `travel_min`, `travel_at` ([from, to, place] of the stops
  where it was heard) and `group_spread_m`; `travelled` stays as `bool(travel)`. Before:
  `spread ≥ travel_min_m` and two cells, per address.
- **Travel is decided per linked device**: the hears of all addresses of a group are judged
  together and every address gets the result.
- **Android addresses linked.** Rules `google-fcf1` and `google-fef3` get a `group` on their whole
  service data (the same advertisement under several addresses; in the data 164 and 106 such
  advertisements, never two addresses at once). New rule `google-fef3-day` for the form
  `4A 17 23` + four characters + `11`: grouped by the four characters (key `google-day-code`), which
  behave like a code of one device lasting about a day (270 codes in four days; the only addresses
  that overlap in time start together and differ by 2.5 dB in the median: two parallel streams of
  one device). Identification database `2026-11-12.1`, 50 rules.
- Pages: tags *at N stops* (amber) and *same road* (grey) in place of *travelled*; a *Travelled*
  row in the radio panels with the stops' times; the *Travelled* count shows devices at stops plus
  the same-road ones; `travelTag()`, `travelText()`, `travelWhen()` and `carryAcross()` in
  `common.js` (a radio's own page is built from one address, so the group and the travel come from
  the list); group labels for the new keys. CSV exports: `travel`, `travel_stops`, `travel_km`,
  `travel_min` (and `group`, `group_size` in the server's export).
- Tests: 11 more (`Linking`, `Travel`, the form of every rule's `group`), 66 in all.

### Fixed
- **Different devices were joined by name.** Addresses sharing a custom service UUID and a name
  were one device, which joined twenty rental scooters named "Scooter1" (and made them travel
  12 km) and seven "Seos" door readers. Only resolvable private addresses (`rotating_address()`,
  top two bits 01) are joined now; static and public addresses stay apart.
- With *Group same device* on, a group showed the spread of its loudest address; it now shows the
  spread of all its addresses.

### Changed
- User guide 1.6 (Travelled rewritten; one device, many addresses), Mobile guide 1.5, README,
  `config.ini.example` (what `travel_min_m` now means).

---

## [2026-11-10]

### Added
- **Apple Find My: the kind of device.** `findmy_info()` reads Apple message type 0x12 from the
  manufacturer data: the length (0x19 separated / 0x02 near owner) and the status byte, whose bits
  4–5 are the kind of device (0 Apple device, 1 AirTag, 2 Find My accessory, 3 AirPods) and bits
  6–7 the battery, as read by AirGuard (seemoo-lab). `identify()` checks it before the rules and
  returns one of the built-in identifications `FINDMY_IDENTS`: `findmy-apple-device`,
  `findmy-apple-offline` (Phone / PC), `findmy-airpods` (Audio; kind 3, or kind 2 with a proximity
  pairing message 0x07 in the same advertisement), `findmy-airtag`, `findmy-accessory` (Tracker; also
  service data FD44 without message 0x12) and `findmy-unknown` (the AirTag signature without any
  advertisement, i.e. Sit rows; stays Tracker). In `build_devices()` a radio with Fieldwatch's
  *Apple AirTags* signature whose message says "not a tag" takes its layer from its other signatures
  or from the identification; the new device field `reclass` holds the layer Fieldwatch gave, and
  the radio panels show *layer by Harava*. Tracker mode and battery come from the same message.
  `/api/ids` lists the built-in identifications with their hits. The Phone check does not count a
  Find My address as a phone (the phone is counted by its other messages).
  Reason: Fieldwatch's signature matches every Find My message; in the author's data of 8–10 Nov
  320 of 352 "AirTag" addresses were Apple devices, 12 AirPods-type, 18 accessories, 0 AirTags of
  kind 1.
- **Following Fieldwatch.** `tools/update_fieldwatch_signatures.py` downloads Fieldwatch's stock
  catalog (`dist/fieldwatch-signatures-v2.json`) and version note (`dist/app-update.json`) and
  rewrites `signature_classes.json`, which now also carries `catalog`, `app_version`, `fetched` and
  `retired` (names Fieldwatch removed; kept for old logs). `--check` only lists the differences
  (new names, changed classes, classes without a layer in `CLASS_CATEGORY`) and exits 1 when there
  are any. `/api/ids` returns `signatures` (catalog, app_version, fetched, names, `unknown`: signature
  names in the data that the table lacks, `no_layer`: names whose class has no layer); the
  Identification page and the phone app show the table's catalog and an amber warning for both.
- `signature_classes.json` updated from catalog 100 to **103** (Fieldwatch 1.1.43): 10 new names,
  268 in all. New class `TWO_WAY` → layer **Two-way radio** (Places & things, after Mesh radio in
  `CLASS_PRIORITY`). `SIG_CLASS_OVERRIDE` keeps the new *Meta Quest* signature (a wearable in
  Fieldwatch) on Glasses & VR, tagged *VR?*.
- User guide 1.5 (*An "AirTag" is usually an iPhone*; *Keeping up with Fieldwatch*; the Two-way radio layer; figures of the Tracker layer and the Tracker
  timeline retaken) and Mobile guide 1.4 (Watch list; Day and Watch list figures retaken).

- **Home & IoT split.** Four layers of Harava's own, `EXTRA_LAYERS`: **TV & media**, **EV charger**,
  **Printer**, **Sensor** (Places & things; in `CAT` of `common.js`, in `LAYERS` for the Workbench).
  `SIG_LAYER` gives Fieldwatch signatures of class HOME a layer by name (LG webOS TV, Roku → TV &
  media; HP, Epson → Printer; Ruuvi, Blue Maestro, SensorPush, RadiaCode → Sensor); `classify()`
  uses it. Identification database 2026-11-10.1 (49 rules): the layers of `name-samsung-tv`,
  `apple-airplay-target` (TV & media), `ev-charge-gatt`, `ev-charge-beacon` (EV charger),
  `apple-airprint` (Printer) and `name-ruuvi` (Sensor) changed; new rules `name-tv` (BLE name says
  TV, OLED, BRAVIA, [AV] or soundbar), `wifi-direct-tv` and `wifi-direct-printer` (the first rules
  for Wi-Fi), `ev-charge-uuid-gatt` and `ev-charge-uuid-beacon`: a Mesh device that has not told
  its name "Charge" but whose device UUID has the charge points' form (8 random bytes, 80, 7 zero
  bytes) — all 28 named charge points have it — so a charge point heard only by its nameless
  beacons no longer stays on Home & IoT. For this the rules got two match fields, `ad_data_regex`
  and `service_data_regex` (regular expressions on the hex of the data). A rule may now set the layer of a Wi-Fi network that has no signature (before, only
  of an unidentified Bluetooth radio). On 8–10 Nov: Home & IoT 111 devices → 26; TV & media 41,
  EV charger 28, Printer 26, Sensor 7. Per day Home & IoT is 8, 13 and 8 devices.

- **Phone / PC split.** Four more layers in `EXTRA_LAYERS`: **Apple device**, **Android / Google**,
  **Samsung**, **Windows PC** (Personal). `SIG_LAYER`: Apple Device → Apple device, Google →
  Android / Google, Microsoft Device → Windows PC (Fast Pair and Phone hotspot stay on Phone / PC).
  Identification database 2026-11-10.2: the layers of the `apple-*` phone rules, `google-*`,
  `oneplus`, `samsung-*`, `microsoft`, `name-windows-pc` and `windows-3e1d` changed; the Find My
  identifications of Apple devices go to Apple device. `PHONE_LAYERS` (harava.py, common.js) lists
  the five phone layers; `phoneType()` of the Phone check uses it, so the check counts the same
  radios as before. On 8–10 Nov: Android / Google 1,505 addresses, Apple device 846, Windows PC 588,
  Samsung 128, Phone / PC 21.

### Security
Found in a code audit on 10 Nov 2026. All three need a signed-in user with the Analyze permission
to upload a prepared file (or someone to import a file they were sent); none can be triggered from
the air or without a sign-in.
- **Script in a file name or an address.** The pages put the import's file name, a My-devices label
  and the radio's kind and address into inline `onclick` handlers with `esc()` only; the browser
  turns `&#39;` back into a quote before it runs a handler, so a file named `a');…('.csv` ran its
  script when someone pressed *Delete* on that import (confirmed), and the address was written into
  the Devices table without `esc()` at all. Now: `jsArg()` in `common.js` (JSON string, then
  `esc()`) for every text in an inline handler, `esc()` on the address and the row key, and
  `norm_hear()` accepts only WIFI / BLE as the kind and only `AA:BB:CC:DD:EE:FF` as an address
  (rows with anything else are skipped; the OUI is checked the same way).
- **Spreadsheet formulas in the CSV exports.** A radio's advertised name is written by whoever owns
  the radio; a name such as `=HYPERLINK(…)` went into the export as it was. Both exports
  (`/api/export.csv` and *Export CSV* on the Devices page) now put an apostrophe before text that
  starts with `=`, `+`, `-`, `@`, tab or CR.
- **Zip uploads** are refused above 500 exports or 1 GB unpacked (`ZIP_MAX_FILES`,
  `ZIP_MAX_BYTES`); before, only each member was limited to 512 MB.
- **Upload limit 100 MB, and a setting.** `MAX_CONTENT_LENGTH` was 512 MB, while an import holds
  the whole file as rows in memory: measured about 15 times the file's size (18 MB → 0.3 GB,
  53 MB → 0.8 GB, 95 MB with 473,000 rows → 1.4 GB), enough to exhaust a small server. New setting
  `max_upload_mb` in `config.ini` (default 100; `MAX_UPLOAD`); it also limits each export inside a
  zip, which is now reported as an error row instead of being left out silently. Over the limit
  the server answers 413 with a JSON message, and the pages check the size before sending
  (`uploadTooBig()` in `common.js`, limit from `FWA.maxUploadMb`), also for files shared to the
  phone app. `deploy/harava.oh2gax.org.nginx`: `client_max_body_size` 512m → 110m. Fieldwatch's
  whole rolling log is about 12 MB, so real exports are far below the limit.
- Every text argument of an inline handler now goes through `jsArg()` or a `data-` attribute, also
  where the text is a constant or checked by the server (layer names, sort columns, info tabs,
  and usernames, snapshot names and the temporary password on the Maintenance page), so there is
  one rule and a test can hold it.

### Tests
- **`tests/test_harava.py`**, the first automated tests: 55 tests with Python's own `unittest`
  (`python -m unittest discover -s tests`), about a second, no new dependency. They work on
  made-up radios in a temporary database and do not touch `harava.db`, `harava_users.db`,
  `archive/` or the local rules. Covered: the importers (log CSV with and without header, JSON
  lines, Sit, KML, zip) and what they refuse; both forms of Fieldwatch's `raw` field through
  `adv_features()`, `mfg_payload()`, `ruuvi_decode()` and the Find My kinds; that every rule of
  `ids/harava_ids.json` is well formed; that every Fieldwatch signature class has a layer and
  every layer a colour in `CAT` of `common.js`; storing without duplicates, verdicts, deleting,
  Bluetooth pauses; the upload limits, the CSV export, the Origin check and the sign-in guard;
  and that no page script puts quoted text into an inline handler.
- **`LocalExports`** (skipped when `data/` has no exports, as in git): reads the newest real
  Fieldwatch exports of `data/` (or of `HARAVA_TEST_EXPORTS`) and cross-checks Fieldwatch's own
  signatures against what Harava decodes from the same rows: the log's columns are the known
  ones, every signature name is in the table, `raw` without manufacturer data is a whole
  advertisement, rows with the Ruuvi signature give a temperature and rows with the Apple AirTags
  signature carry a Find My message (each in at least 95 % of the rows). With the code of 8 Nov
  these would have failed on the first log of Fieldwatch 1.1.43.

### Fixed
- **Fieldwatch 1.1.43 logs.** From 1.1.43 (9 Nov 2026) the log's `raw` field is the whole
  advertisement also when the row has manufacturer data; before, it was the manufacturer data.
  `adv_features()` took it for manufacturer data whenever `mfg` was set, so the first byte (an AD
  length such as 07 or 1E) was read as the message type: Apple Find My frames became "AirPods or
  Beats", Nearby Info became "Apple device", Samsung, Google and Microsoft rules with a data prefix
  missed, and `ruuvi_decode()` returned nothing. `adv_features()` now recognises the whole
  advertisement (it splits into AD structures and one of them is the manufacturer data of the
  row's own company) and reads the AD structures, service data and UUIDs from it; `mfg_payload()`
  gives the manufacturer data in either form. On 9–10 Nov this changed the identification of 210
  and 302 addresses.
- `.conf-pill` no longer wraps inside the radio panel.
- The main page answered with an error on Windows when `watch_folder` was on another drive than
  Harava (`os.path.commonpath()` raises `ValueError` there). Found while writing the tests.

## [2026-11-09]

### Added
- **Bluetooth coverage on the timeline.** `bluetooth_gaps()` finds, per phone, stretches of at least
  3 min (`BT_GAP_MIN_MS`) where the log has Wi-Fi rows but no Bluetooth rows, with at least 3 Wi-Fi
  rows inside (`BT_GAP_MIN_WIFI`); a stretch where Wi-Fi also stops for 10 min (`BT_GAP_HOLE_MS`) is
  the log being off and is not marked; stretches less than 60 s apart are joined (`BT_GAP_JOIN_MS`).
  `/api/timeline` returns `bt_gaps` (from, to, wifi, collector, phone) and `bt_cov` (silent_ms,
  logged_ms, min_gap_ms), worked out from all radios before the *Show* filter. The full page and the
  phone app shade the stretches (Chart.js plugin `BT_GAP_PLUGIN` in `common.js`), show
  *▨ Bluetooth paused h min (%)* with an explanation, and name the stretch in the bar tooltip.
  Reason: on Samsung phones Android parks the Bluetooth scan when the screen goes dark; a running
  scan writes a Bluetooth row about every second (99 % of gaps < 10 s), and in the author's data of
  8–9 Nov 2026 Bluetooth was paused 70–80 % of the logged time. Checked against a Sit: new Bluetooth
  radios appeared 20 times less often inside the marked stretches (0.4/min) than outside (8.5/min).
- User guide 1.4 (*Keep the screen on for Bluetooth*, *Bluetooth paused* with a figure, limitation,
  quick reference) and Mobile guide 1.3 (Day view, troubleshooting).

### Changed
- All pages stamp their CSS and scripts with the build (the newest change of templates and static
  files, a context processor), not the day's version: browsers fetch the new files after every
  deploy without Ctrl+F5.

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
