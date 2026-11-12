"""Harava's automated tests.

Run them from the program folder:

    python -m unittest discover -s tests -v

They need nothing but Harava itself (Python's own unittest). Every test works on made-up radios in
a temporary database: your harava.db, harava_users.db, archive/ and local rules are not touched.

What they guard:
  * the importers (log CSV, JSON lines, Sit, KML, zip) and what they refuse;
  * the two forms of Fieldwatch's `raw` field (manufacturer data only up to 1.1.42, the whole
    advertisement from 1.1.43) and the decoders that read it (Find My, Ruuvi);
  * the identification database and the layers: every rule is well formed, every Fieldwatch
    signature class has a layer, every layer has a colour on the pages;
  * storing without duplicates, the limits of uploads, the CSV export, the sign-in guard;
  * linking the changing addresses of one device, and the travel levels (stops, same road).

The last class, LocalExports, reads the real Fieldwatch exports in data/ when there are any
(they are never in git, so elsewhere it is skipped). It cross-checks Fieldwatch's own signatures
against what Harava decodes from the same rows. After a Fieldwatch update, put a fresh export in
data/ and run the tests: a changed log format shows up here before it distorts the results.
"""
import gc
import glob
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_argv, sys.argv = sys.argv, ["harava-tests"]
import harava as H                                              # noqa: E402
sys.argv = _argv

from werkzeug.security import generate_password_hash            # noqa: E402

def setUpModule():
    # Harava leaves closing its SQLite connections to Python (they close when the function ends);
    # unittest would print a warning for every one of them.
    import warnings
    warnings.simplefilter("ignore", ResourceWarning)


# ── made-up material ────────────────────────────────────────────────────────────────────────────
T0 = 1794200000000                       # 9 Nov 2026, in ms
MIN = 60_000
KEY22 = "".join(f"{i:02X}" for i in range(1, 23))               # a made-up Find My key (22 bytes)
RUUVI = "0512FC5394C37C0004FFFC040CAC364200CDCBB8334C884F"      # Ruuvi's own RAWv5 test vector
RUUVI_ADVERT = "0201061BFF9904" + RUUVI                         # the same as a whole advertisement
MESH_UUID = "A1B2C3D4E5F60718" + "80" + "00" * 7                # the charge-point form of a Mesh UUID
LOG_HEADER = "timestamp,iso,kind,mac,name,rssi,channel,freq,oui,vendor,fleets,mfg,uuids,flags,raw,lat,lon,vendor_ie"


def log_row(ts, kind, mac, name="", rssi=-70, fleets="", mfg="", raw="", uuids="", lat="60.1", lon="24.9"):
    ch, fq = ("6", "2437") if kind == "WIFI" else ("", "")
    return f"{ts},,{kind},{mac},{name},{rssi},{ch},{fq},{mac[:8]},Test Vendor,{fleets},{mfg},{uuids},,{raw},{lat},{lon},"


def log_csv(rows, header=True):
    return (("\n".join(([LOG_HEADER] if header else []) + rows)) + "\n").encode("utf-8")


def sample_rows(n_extra=0):
    """A small log: a Wi-Fi network, a phone, a Ruuvi and a Find My device (old raw form)."""
    rows = [
        log_row(T0, "WIFI", "02:00:00:00:00:01", "Test-Net", -60),
        log_row(T0 + 1000, "BLE", "02:00:00:00:00:02", "", -70, "Apple Device", "4C", "1005011C0A0B0C"),
        log_row(T0 + 2000, "BLE", "02:00:00:00:00:03", "Ruuvi ABCD", -65, "Ruuvi", "499", RUUVI),
        log_row(T0 + 3000, "BLE", "02:00:00:00:00:04", "", -80, "Apple AirTags", "4C", "12020002"),
    ]
    rows += [log_row(T0 + 10 * MIN + i * 1000, "BLE", f"02:00:00:00:01:{i:02X}", "", -75) for i in range(n_extra)]
    return rows


class TempStore(unittest.TestCase):
    """A fresh, empty Harava in a temporary folder: no users (the open desktop mode), no local rules."""

    def setUp(self):
        try:                                             # Windows cannot remove a file that is still open
            self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:                                # Python 3.9
            self._tmp = tempfile.TemporaryDirectory()
        self._saved = (dict(H.CFG), H.USERS_DB, H.LOCAL_RULES, H.MAX_UPLOAD, H.app.config["MAX_CONTENT_LENGTH"])
        H.CFG["db"] = os.path.join(self._tmp.name, "harava.db")
        H.CFG["keep_originals"] = False
        H.CFG["watch_folder"] = os.path.join(self._tmp.name, "data")
        H.USERS_DB = os.path.join(self._tmp.name, "users.db")
        H.LOCAL_RULES = os.path.join(self._tmp.name, "local_rules.json")
        H.init_db()
        H.init_users_db()
        H.load_ids()

    def tearDown(self):
        cfg, H.USERS_DB, H.LOCAL_RULES, H.MAX_UPLOAD, H.app.config["MAX_CONTENT_LENGTH"] = self._saved
        H.CFG.clear()
        H.CFG.update(cfg)
        H.load_ids()
        gc.collect()                                     # let go of the SQLite files before removing them
        self._tmp.cleanup()

    def devices(self):
        with H.db() as con:
            return H.build_devices(H.query_hears(con, None, None, ids=H.all_active_ids(con)))

    def device(self, mac):
        return next(d for d in self.devices() if d["mac"] == mac)


# ── importers ───────────────────────────────────────────────────────────────────────────────────
class Importers(unittest.TestCase):
    def test_log_csv(self):
        hears, paths, mine = H.parse_csv(log_csv(sample_rows()).decode())
        self.assertEqual(len(hears), 4)
        w, phone = hears[0], hears[1]
        self.assertEqual((w["kind"], w["mac"], w["name"], w["rssi"], w["channel"], w["freq"]),
                         ("WIFI", "02:00:00:00:00:01", "Test-Net", -60, 6, 2437))
        self.assertEqual((phone["kind"], phone["mfg"], phone["raw"], phone["fleets"], phone["src"]),
                         ("BLE", "4C", "1005011C0A0B0C", "Apple Device", "log"))
        self.assertAlmostEqual(w["lat"], 60.1)
        self.assertEqual(w["ts"], T0)

    def test_log_csv_without_header_uses_the_known_columns(self):
        hears, _, _ = H.parse_csv(log_csv(sample_rows(), header=False).decode())
        self.assertEqual([h["mac"][-2:] for h in hears], ["01", "02", "03", "04"])

    def test_log_header_is_the_one_harava_expects(self):
        self.assertEqual(LOG_HEADER.split(","), H.CSV_HEADER)

    def test_rows_without_a_real_address_are_skipped(self):
        rows = [log_row(T0, "BLE", "02:00:00:00:00:02"),
                log_row(T0, "BLE", "');alert(1);('"),
                log_row(T0, "BLE", "02:00:00:00:00"),
                log_row(T0, "BLE", "<img src=x>"),
                log_row("", "BLE", "02:00:00:00:00:05")]            # no time either
        hears, _, _ = H.parse_csv(log_csv(rows).decode())
        self.assertEqual([h["mac"] for h in hears], ["02:00:00:00:00:02"])

    def test_kind_is_only_ever_wifi_or_ble(self):
        for given, want in (("WIFI", "WIFI"), ("wi-fi", "WIFI"), ("BLE", "BLE"), ("<script>", "BLE"), ("", "BLE")):
            self.assertEqual(H.norm_hear({"kind": given, "mac": "02:00:00:00:00:01"})["kind"], want)

    def test_zero_coordinates_mean_no_position(self):
        h = H.norm_hear({"kind": "BLE", "mac": "02:00:00:00:00:01", "lat": "0.0", "lon": "0.0"})
        self.assertIsNone(h["lat"])
        self.assertIsNone(h["lon"])

    def test_jsonl_log(self):
        line = json.dumps({"ts": T0, "kind": "BLE", "mac": "02:00:00:00:00:02", "rssi": -71, "mfg": 76,
                           "uuids": "FD6F,FEAA", "rand": True, "raw": "1005011C0A0B0C", "lat": 60.1, "lon": 24.9})
        hears, _, _ = H.parse_jsonl(line + "\nnot json\n{broken\n")
        self.assertEqual(len(hears), 1)
        h = hears[0]
        self.assertEqual((h["mfg"], h["uuids"], h["flags"], h["rssi"]), ("4C", "FD6F|FEAA", "RAND", -71))

    def test_sit_export(self):
        csv_text = ("kind,mac,name,first_seen,last_seen,hits,rssi_max,rssi_min,channel,frequency_mhz,signatures,"
                    "randomized,hidden,mine,custom_name,lat,lon\n"
                    "BLE,02:00:00:00:00:02,,2026-11-09T08:00:00.000Z,2026-11-09T09:00:00.000Z,12,-50,-90,,,"
                    "Apple Device;Apple AirTags,true,false,false,,60.1,24.9\n"
                    "WIFI,02:00:00:00:00:01,Car-Net,2026-11-09T08:00:00.000Z,2026-11-09T08:30:00.000Z,3,-40,-60,6,2437,"
                    ",false,false,true,My car,60.1,24.9\n")
        ftype, hears, paths, mine = H._parse("fieldwatch-sit-test.csv", csv_text.encode())
        self.assertEqual(ftype, "sit")
        ble, wifi = hears
        self.assertEqual((ble["src"], ble["hits"], ble["rssi"], ble["rssi_min"], ble["fleets"], ble["flags"]),
                         ("sit", 12, -50, -90, "Apple Device+Apple AirTags", "RAND"))
        self.assertEqual(ble["last_ts"] - ble["ts"], 60 * MIN)
        self.assertEqual(mine, [{"kind": "WIFI", "mac": "02:00:00:00:00:01", "label": "My car"}])

    def test_kml(self):
        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>Wi-Fi</name>
<Placemark><name>Test-Net</name><description>WIFI 02:00:00:00:00:01 · Test Vendor · -78 dBm · ch 11 · 2462 MHz</description>
<TimeStamp><when>2026-11-09T08:00:00Z</when></TimeStamp><Point><coordinates>24.9,60.1,0</coordinates></Point></Placemark>
<Placemark><name>no address here</name><description>nothing</description><Point><coordinates>24.9,60.1,0</coordinates></Point></Placemark>
</Folder><Placemark><name>Track</name><LineString><coordinates>24.9,60.1,0 24.91,60.11,0 24.92,60.12,0</coordinates></LineString></Placemark>
</Document></kml>"""
        ftype, hears, paths, _ = H._parse("map.kml", kml.encode())
        self.assertEqual(ftype, "kml")
        self.assertEqual(len(hears), 1)
        h = hears[0]
        self.assertEqual((h["kind"], h["mac"], h["name"], h["rssi"], h["channel"], h["freq"], h["src"]),
                         ("WIFI", "02:00:00:00:00:01", "Test-Net", -78, 11, 2462, "kml"))
        self.assertAlmostEqual(h["lat"], 60.1)
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(paths[0]["pts"]), 3)

    def test_kml_with_control_characters_is_still_read(self):
        kml = ('<kml><Document><Placemark><name>\x00\x00</name><description>WIFI 02:00:00:00:00:01 · -70 dBm</description>'
               '<Point><coordinates>24.9,60.1</coordinates></Point></Placemark></Document></kml>')
        _, hears, _, _ = H._parse("map.kml", kml.encode())
        self.assertEqual(len(hears), 1)

    def test_detect_type(self):
        self.assertEqual(H.detect_type("a.KML", ""), "kml")
        self.assertEqual(H.detect_type("a.jsonl", ""), "jsonl")
        self.assertEqual(H.detect_type("a.csv", "<kml>"), "csv")
        self.assertEqual(H.detect_type("export", "  <?xml"), "kml")
        self.assertEqual(H.detect_type("export", '{"ts":1}'), "jsonl")
        self.assertEqual(H.detect_type("export", "timestamp,iso"), "csv")


# ── the advertisement: both forms of Fieldwatch's raw field ─────────────────────────────────────
class Advertisement(unittest.TestCase):
    def test_both_raw_forms_give_the_same_manufacturer_data(self):
        old = H.adv_features("499", RUUVI, "")
        new = H.adv_features("499", RUUVI_ADVERT, "")
        self.assertEqual(old["mfg"], "0499")
        self.assertEqual(old["mfg_data"], bytes.fromhex(RUUVI))
        self.assertEqual(new["mfg_data"], old["mfg_data"])
        self.assertTrue(new["ads"])                      # only the whole advertisement has AD structures
        self.assertEqual(H.mfg_payload("499", RUUVI), H.mfg_payload("499", RUUVI_ADVERT))

    def test_ruuvi_readings_from_both_forms(self):
        want = {"temp_c": 24.3, "humidity": 53.5, "pressure_hpa": 1000.4, "battery_mv": 2977}
        self.assertEqual(H.ruuvi_decode(H.mfg_payload("499", RUUVI)), want)
        self.assertEqual(H.ruuvi_decode(H.mfg_payload("499", RUUVI_ADVERT)), want)

    def test_whole_advertisement_without_manufacturer_data(self):
        f = H.adv_features("", "020106" + "0303AAFE" + "15162718" + MESH_UUID + "0000", "")
        self.assertIn("FEAA", f["svc"])
        self.assertEqual(f["sd"]["1827"][:16], bytes.fromhex(MESH_UUID))

    def test_broken_raw_never_raises(self):
        for mfg in ("", "4C", "499", "zz"):
            for raw in ("", "1", "120", "ZZ", "FF" * 40, "02", "0201", "1EFF4C00", RUUVI[:-1], "  "):
                H.adv_features(mfg, raw, "")
                H.mfg_payload(mfg, raw)
                H.identify("BLE", "x", mfg, raw, "")
                H.tracker_mode(raw, "Apple AirTags", mfg)
        self.assertIsNone(H.ruuvi_decode("ZZ"))
        self.assertIsNone(H.parse_ad("ZZ"))


class FindMy(unittest.TestCase):
    """Apple message 0x12: Fieldwatch calls all of these "Apple AirTags"."""
    CASES = [                                             # mfg, raw, identification, mode
        ("4C", "12020002", "findmy-apple-device", "near owner"),
        ("4C", "07FF4C0012020000", "findmy-apple-device", "near owner"),            # whole advertisement
        ("4C", "121900" + KEY22 + "0100", "findmy-apple-offline", "separated"),
        ("4C", "1EFF4C00121900" + KEY22 + "0100", "findmy-apple-offline", "separated"),
        ("4C", "121910" + KEY22 + "0100", "findmy-airtag", "separated"),
        ("4C", "1EFF4C00121910" + KEY22 + "0100", "findmy-airtag", "separated"),
        ("4C", "121920" + KEY22 + "0100", "findmy-accessory", "separated"),
        ("4C", "12022C00", "findmy-accessory", "near owner"),
        ("4C", "12023801", "findmy-airpods", "near owner"),
        ("4C", "12026C00" + "0711" + "01" * 17, "findmy-airpods", "near owner"),   # kind 2 + pairing message
        ("", "181644FD" + "00" * 21, "findmy-accessory", ""),                       # service data FD44 only
    ]

    def test_kinds_and_modes(self):
        for mfg, raw, want, mode in self.CASES:
            with self.subTest(raw=raw[:24]):
                hv = H.identify("BLE", "", mfg, raw, "")
                self.assertEqual(hv["id"], want)
                self.assertEqual(hv["findmy"].get("mode", ""), mode)

    def test_layers(self):
        layer = lambda raw: H.identify("BLE", "", "4C", raw, "")["layer"]           # noqa: E731
        self.assertEqual(layer("12020002"), "Apple device")
        self.assertEqual(layer("12023801"), "Audio")
        self.assertEqual(layer("121910" + KEY22 + "0100"), "Tracker")

    def test_other_apple_messages_are_not_find_my(self):
        for raw in ("1005011C0A0B0C", "02011A0AFF4C001005011C0A0B0C"):
            self.assertEqual(H.identify("BLE", "", "4C", raw, "")["id"], "apple-nearby-info")
        for raw in ("120", "", "ZZ"):
            self.assertNotIn("findmy", H.identify("BLE", "", "4C", raw, "")["id"])

    def test_tracker_mode_reads_both_forms(self):
        self.assertEqual(H.tracker_mode("07FF4C0012022C00", "Apple AirTags", "4C"), "near owner")
        self.assertEqual(H.tracker_mode("12022C00", "Apple AirTags", "4C"), "near owner")
        self.assertEqual(H.tracker_mode("121920" + KEY22, "Apple AirTags", "4C"), "separated")


# ── the identification database and the layers ──────────────────────────────────────────────────
class Rules(TempStore):
    FIELDS = {"kind", "name_regex", "mfg", "mfg_data_prefix", "ad_type", "ad_data_prefix", "ad_data_regex",
              "service_data_uuid", "service_data_regex", "service_uuid_any", "uuid128_any"}
    HEX = re.compile(r"(?:[0-9A-F]{2})*\Z")

    def test_groups_are_well_formed(self):
        n = 0
        for r in H.HV["rules"]:
            g = r.get("group")
            if not g:
                continue
            n += 1
            with self.subTest(rule=r["id"]):
                self.assertFalse(set(g) - {"source", "uuid", "ad_type", "from", "to", "key", "label"})
                self.assertIn(g.get("source", "ad"), ("mfg", "service_data", "ad"))
                if g.get("source") == "service_data":
                    self.assertRegex(g["uuid"], r"^[0-9A-F]{4}$")
                elif g.get("source") != "mfg":
                    self.assertRegex(g["ad_type"], r"^[0-9A-F]{2}$")
                self.assertIsInstance(g.get("from", 0), int)
                self.assertIsInstance(g.get("to", 1), int)
                self.assertLess(g.get("from", 0), g.get("to", 99))
                self.assertTrue(g.get("label"))
        self.assertGreater(n, 5)

    def test_every_rule_is_well_formed(self):
        rules = H.HV["rules"]
        self.assertGreater(len(rules), 40)
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), len(set(ids)), "two rules have the same id")
        for r in rules:
            with self.subTest(rule=r["id"]):
                self.assertTrue(r.get("name"))
                self.assertIn(r.get("confidence"), ("high", "medium", "low"))
                self.assertIn(r.get("layer", ""), H.LAYERS + [""])
                m = r["match"]
                self.assertTrue(m, "a rule without conditions would match everything")
                self.assertFalse(set(m) - self.FIELDS, "unknown match field")
                self.assertIn(m.get("kind", "BLE"), ("BLE", "WIFI"))
                for k in ("name_regex", "ad_data_regex", "service_data_regex"):
                    if k in m:
                        re.compile(m[k])
                for k in ("mfg", "service_data_uuid"):
                    if k in m:
                        self.assertRegex(m[k], r"^[0-9A-F]{4}$")
                for k in ("mfg_data_prefix", "ad_data_prefix"):
                    if k in m:
                        self.assertRegex(m[k], self.HEX)
                if "ad_type" in m:
                    self.assertRegex(m["ad_type"], r"^[0-9A-F]{2}$")
                for u in m.get("service_uuid_any", []):
                    self.assertRegex(u, r"^[0-9A-F]{4}$")
                for u in m.get("uuid128_any", []):
                    self.assertRegex(u, r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")
                if m.get("kind") == "WIFI":
                    self.assertEqual(set(m) - {"kind", "name_regex"}, set(), "a Wi-Fi row has only a name")

    def test_rules_by_name(self):
        for kind, name, want, layer in (("BLE", "Ruuvi ABCD", "name-ruuvi", "Sensor"),
                                        ("WIFI", "DIRECT-ab-[TV] Test", "wifi-direct-tv", "TV & media"),
                                        ("WIFI", "DIRECT-12-HP OfficeJet", "wifi-direct-printer", "Printer")):
            hv = H.identify(kind, name, "", "", "")
            self.assertEqual((hv["id"], hv["layer"]), (want, layer))
        self.assertIsNone(H.identify("WIFI", "Test-Net", "", "", ""))
        self.assertEqual(H.identify("BLE", "[TV] Living room", "", "", "")["layer"], "TV & media")

    def test_charge_point_by_the_form_of_its_mesh_uuid(self):
        beacon = H.identify("BLE", "", "", "0201061A2B00" + MESH_UUID + "00" * 8, "")
        gatt = H.identify("BLE", "", "", "02010615162718" + MESH_UUID + "0000", "")
        self.assertEqual((beacon["id"], beacon["layer"]), ("ev-charge-uuid-beacon", "EV charger"))
        self.assertEqual((gatt["id"], gatt["layer"]), ("ev-charge-uuid-gatt", "EV charger"))
        self.assertEqual(beacon["group"], gatt["group"])            # two adverts of one device: one radio
        other = H.identify("BLE", "", "", "02010615162718" + "11" * 16 + "0000", "")
        self.assertNotEqual(other["layer"], "EV charger")

    def test_company_only(self):
        hv = H.identify("BLE", "", "499", RUUVI, "")
        self.assertEqual(hv["id"], "company")
        self.assertIn("Ruuvi", hv["company"])

    def test_rules_of_the_road(self):
        for name, want, layer in (("ECO16BT 1a2b3c", "name-ensto-thermostat", "Thermostat"),
                                  ("SE5000-0001234567", "name-tachograph", "Vehicle"),
                                  ("FMC130_1234567_LE", "name-teltonika", "Vehicle"),
                                  ("OBDII", "name-obd", "Vehicle"),
                                  ("Scooter1", "name-scooter", "Vehicle"),
                                  ("nut", "name-nut", "Tracker"),
                                  ("ET-2860 Series", "name-epson-printer", "Printer"),
                                  ("HD 450BT", "name-audio", "Audio"),
                                  ("HUAWEI Band 9-6B1", "name-wearable", "Wearable"),
                                  ("L/A_MitsubishiAdp", "name-mitsubishi-ac", "Home & IoT")):
            hv = H.identify("BLE", name, "", "", "")
            self.assertEqual((hv["id"], hv["layer"]), (want, layer), name)
        self.assertNotEqual(H.classify("", "L/A_MitsubishiAdp", "BLE")[1], "Vehicle")     # an air conditioner
        self.assertEqual(H.classify("", "Mitsubishi Outlander", "BLE")[1], "Vehicle")     # a car
        self.assertIsNone(H.identify("BLE", "Nutella jar", "", "", ""))

    def test_rules_of_12_november(self):
        for name, want, layer in (("DTCO-1234567", "name-tachograph-vdo", "Vehicle"),
                                  ("Saphe One+", "name-saphe", "Vehicle"),
                                  ("SwipBox", "name-swipbox", ""),
                                  ("Grundfos", "name-grundfos", "Home & IoT"),
                                  ("PINDORA_0012AB", "name-pindora", "Smart lock"),
                                  ("IOS-Vlink", "name-obd", "Vehicle"),
                                  ("FMB1204:1234567_LE", "name-teltonika", "Vehicle"),
                                  ("Poly VLegend 50", "name-audio", "Audio"),
                                  ("MOMENTUM 4", "name-audio", "Audio"),
                                  ("Gear S3 (5653) LE", "name-wearable", "Wearable"),
                                  ("bDW Driver (Dim)", "name-casambi-bdw", "Home & IoT")):
            hv = H.identify("BLE", name, "", "", "")
            self.assertEqual((hv["id"], hv["layer"]), (want, layer), name)
        # with its data, a Casambi lamp is found by the data (the surer rule), not by its name
        self.assertEqual(H.identify("BLE", "bDW Driver (Dim)", "3C3", "0AFFC303" + "01" * 8, "")["id"], "casambi")

    def test_an_iphone_that_sends_a_windows_service(self):
        svc = "3E1D50CD-7E3E-427D-8E1C-B78AA87FE624"
        pc = H.identify("BLE", "", "", "", svc)
        self.assertEqual((pc["id"], pc["layer"]), ("windows-3e1d", "Windows PC"))
        phone = H.identify("BLE", "4", "4C", "1005011C0A0B0C", svc)
        self.assertEqual((phone["id"], phone["layer"], phone["confidence"]), ("phone-link-iphone", "Apple device", "low"))

    def test_ibeacon_is_not_apple(self):
        frame = "0215" + "AB" * 16 + "00010002C5"
        for mfg, raw in (("4C", frame), ("4C", "0201061AFF4C00" + frame)):
            hv = H.identify("BLE", "", mfg, raw, "")
            self.assertEqual((hv["id"], hv["layer"]), ("ibeacon", "Beacon"))
            self.assertNotIn("Apple", hv["company"])
        self.assertEqual(H.identify("BLE", "", "4C", "0900", "")["company"], "Apple, Inc.")

    def test_advertisement_with_nothing_in_it(self):
        hv = H.identify("BLE", "", "", "020106", "")
        self.assertEqual((hv["id"], hv["layer"]), ("no-data", ""))
        self.assertEqual(H.identify("BLE", "", "", "0201060201060A00"[:6] + "020A00", "")["id"], "no-data")   # flags + power
        self.assertIsNone(H.identify("BLE", "", "", "", ""))                       # a Sit row: no advertisement at all
        self.assertNotEqual((H.identify("BLE", "A name", "", "020106", "") or {}).get("id"), "no-data")
        # still unidentified, and the Workbench keeps it on its "No data" tab
        row = {"kind": "BLE", "ident_id": "no-data", "ident_conf": "high", "category": "Unidentified"}
        self.assertEqual(H._wb_tab(row, True), "nodata")

    def test_a_local_rule_comes_first(self):
        with open(H.LOCAL_RULES, "w", encoding="utf-8") as fh:
            json.dump({"rules": [{"id": "local-test", "name": "My own thing", "layer": "Sensor", "confidence": "low",
                                  "match": {"kind": "BLE", "name_regex": "^Ruuvi "}}]}, fh)
        H.load_ids()
        self.assertEqual(H.identify("BLE", "Ruuvi ABCD", "", "", "")["id"], "local-test")


class Layers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "static", "common.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        cat = cls.js[cls.js.index("const CAT = ["):cls.js.index("];", cls.js.index("const CAT = ["))]
        cls.page_layers = re.findall(r"\[\s*'[^']*',\s*'([^']+)',\s*'#[0-9a-fA-F]{6}'", cat)

    def test_every_signature_class_has_a_layer(self):
        classes = set(H.SIG_CLASS.values()) | set(H.SIG_CLASS_OVERRIDE.values())
        self.assertFalse(classes - set(H.CLASS_CATEGORY),
                         "Fieldwatch has a new class: give it a layer in CLASS_CATEGORY and CLASS_PRIORITY")
        self.assertFalse(set(H.CLASS_CATEGORY) - set(H.CLASS_PRIORITY), "a class is missing from CLASS_PRIORITY")

    def test_every_layer_has_a_colour_on_the_pages(self):
        self.assertTrue(self.page_layers)
        used = (set(H.LAYERS) | set(H.CLASS_CATEGORY.values()) | set(H.SIG_LAYER.values()) | set(H.EXTRA_LAYERS)
                | {e["layer"] for e in H.FINDMY_IDENTS.values()} | {"Unidentified"})
        self.assertFalse(used - set(self.page_layers), "a layer of harava.py is missing from CAT in static/common.js")

    def test_phone_layers_are_the_same_on_the_pages(self):
        m = re.search(r"const PHONE_LAYERS = \[([^\]]*)\]", self.js)
        self.assertEqual(re.findall(r"'([^']+)'", m.group(1)), H.PHONE_LAYERS)

    def test_signature_layers_point_at_real_names(self):
        self.assertFalse(set(H.SIG_LAYER) - set(H.SIG_CLASS), "SIG_LAYER names a signature Fieldwatch does not have")
        self.assertFalse(set(H.SIG_CLASS_OVERRIDE) - set(H.SIG_CLASS))

    def test_signature_table_file(self):
        with open(os.path.join(ROOT, "signature_classes.json"), encoding="utf-8") as fh:
            t = json.load(fh)
        self.assertIsInstance(t["catalog"], int)
        self.assertRegex(t["app_version"], r"^\d+\.\d+\.\d+")
        self.assertGreater(len(t["classes"]), 250)
        self.assertEqual(H.SIG_META["catalog"], t["catalog"])

    def test_classify(self):
        for fleets, name, kind, want in (
                ("Apple Device", "", "BLE", ("PHONE", "Apple device")),
                ("Google", "", "BLE", ("PHONE", "Android / Google")),
                ("Microsoft Device", "", "BLE", ("PHONE", "Windows PC")),
                ("LG webOS TV", "", "BLE", ("HOME", "TV & media")),
                ("Ruuvi", "", "BLE", ("HOME", "Sensor")),
                ("Meta Quest", "", "BLE", ("GLASSES", "Glasses & VR")),
                ("Apple Device+Apple AirTags", "", "BLE", ("FINDER", "Tracker")),      # the watch list wins
                ("", "Test-Net", "WIFI", ("ISP", "Network")),
                ("", "", "BLE", ("", "Unidentified")),
                ("A signature of next year", "", "BLE", ("", "Unidentified"))):
            self.assertEqual(H.classify(fleets, name, kind), want, fleets)


# ── storing ─────────────────────────────────────────────────────────────────────────────────────
class Store(TempStore):
    def test_import_twice_then_a_longer_export(self):
        first = H.import_bytes("log1.csv", log_csv(sample_rows()), None, 0, None)
        self.assertEqual((first["status"], first["rows"], first["new_rows"], first["verdict"]), ("imported", 4, 4, "new"))
        again = H.import_bytes("copy.csv", log_csv(sample_rows()), None, 0, None)
        self.assertEqual((again["status"], again["verdict"]), ("skipped", "duplicate"))
        longer = H.import_bytes("log2.csv", log_csv(sample_rows(5)), None, 0, None)
        self.assertEqual((longer["rows"], longer["new_rows"], longer["known_rows"], longer["verdict"]), (9, 5, 4, "extends"))
        with H.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM hears").fetchone()[0], 9)
        self.assertEqual(len(self.devices()), 9)

    def test_deleting_an_import_keeps_what_another_one_has(self):
        H.import_bytes("log1.csv", log_csv(sample_rows()), None, 0, None)
        H.import_bytes("log2.csv", log_csv(sample_rows(5)), None, 0, None)
        with H.db() as con:
            first, second = [r[0] for r in con.execute("SELECT id FROM imports ORDER BY id")]
        H.delete_import(second)
        self.assertEqual(len(self.devices()), 4)
        H.delete_import(first)
        self.assertEqual(self.devices(), [])

    def test_the_same_row_twice_in_one_file_is_one_hear(self):
        rows = sample_rows()
        r = H.import_bytes("log.csv", log_csv(rows + rows), None, 0, None)
        self.assertEqual(r["rows"], 4)

    def test_devices_get_their_layers(self):
        H.import_bytes("log.csv", log_csv(sample_rows()), None, 0, None)
        self.assertEqual(self.device("02:00:00:00:00:01")["category"], "Network")
        self.assertEqual(self.device("02:00:00:00:00:02")["category"], "Apple device")
        ruuvi = self.device("02:00:00:00:00:03")
        self.assertEqual((ruuvi["category"], ruuvi["ident_id"]), ("Sensor", "name-ruuvi"))
        findmy = self.device("02:00:00:00:00:04")        # Fieldwatch says AirTag; the message says Apple device
        self.assertEqual((findmy["category"], findmy["ident_id"], findmy["reclass"]),
                         ("Apple device", "findmy-apple-device", "Tracker"))

    def test_a_real_tag_stays_a_tracker(self):
        H.import_bytes("log.csv", log_csv([log_row(T0, "BLE", "02:00:00:00:00:09", "", -80, "Apple AirTags", "4C",
                                                   "121910" + KEY22 + "0100")]), None, 0, None)
        tag = self.device("02:00:00:00:00:09")
        self.assertEqual((tag["category"], tag["ident_id"], tag["tracker_mode"]), ("Tracker", "findmy-airtag", "separated"))

    SIT_HEAD = "kind,mac,name,first_seen,last_seen,hits,rssi_max,rssi_min,signatures,lat,lon\n"

    def test_a_radio_only_in_a_sit(self):
        """A Sit export has no advertisement: Harava says so, and does not call a Find My radio a tracker."""
        sit = self.SIT_HEAD + "".join(f"BLE,{mac},{name},2026-11-09T08:00:00.000Z,2026-11-09T08:00:30.000Z,3,-70,-90,{sig},60.0,25.0\n"
                                      for mac, name, sig in (("4A:00:00:00:00:61", "", ""),
                                                             ("4A:00:00:00:00:62", "", "Apple AirTags"),
                                                             ("4A:00:00:00:00:63", "AirTag", "Apple AirTags"),
                                                             ("4A:00:00:00:00:64", "Ruuvi ABCD", ""),
                                                             ("4A:00:00:00:00:65", "", "Apple Device")))
        H.import_bytes("fieldwatch-sit-test.csv", sit.encode(), None, 0, None)
        d = {x["mac"][-2:]: x for x in self.devices()}
        self.assertEqual((d["61"]["ident_id"], d["61"]["category"]), ("sit-only", "Unidentified"))
        self.assertEqual((d["62"]["ident_id"], d["62"]["category"], d["62"]["reclass"], d["62"]["ident_conf"]),
                         ("findmy-unknown", "Apple device", "Tracker", "low"))
        self.assertEqual(d["63"]["category"], "Tracker")                     # it says itself that it is a tag
        self.assertEqual((d["64"]["ident_id"], d["64"]["category"]), ("name-ruuvi", "Sensor"))   # a name is enough
        self.assertEqual((d["65"]["ident_id"], d["65"]["category"]), ("sit-only", "Apple device"))
        self.assertEqual(H._wb_tab(d["61"], False), "nodata")
        self.assertIsNone(H._wb_tab(d["65"], False))
        # the same radio in the log too: the advertisement decides
        H.import_bytes("log.csv", log_csv([log_row(T0, "BLE", "4A:00:00:00:00:61", "", -70, "", "", "020106")]), None, 0, None)
        self.assertEqual(self.device("4A:00:00:00:00:61")["ident_id"], "no-data")

    def test_hole_in_the_log(self):
        """The Sit kept hearing new radios for 20 minutes; the log has rows only for the last five."""
        sit = [{"src": "sit", "collector_id": 1, "kind": "BLE", "ts": T0 + i * 20_000, "last_ts": T0 + i * 20_000 + 3000} for i in range(60)]
        log = [{"src": "log", "collector_id": 1, "kind": "BLE", "ts": T0 + 15 * MIN + i * 10_000} for i in range(30)]
        holes = H.log_holes(sit + log)
        self.assertEqual(len(holes), 1)
        self.assertEqual((holes[0]["from"], holes[0]["collector"]), (T0, 1))
        self.assertAlmostEqual((holes[0]["to"] - holes[0]["from"]) / MIN, 15, delta=0.5)
        self.assertEqual(holes[0]["sit"], 44)                                # radios first heard inside the hole
        whole = [{"src": "log", "collector_id": 1, "kind": "BLE", "ts": T0 + i * 20_000} for i in range(60)]
        self.assertEqual(H.log_holes(sit + whole), [])
        self.assertEqual(H.log_holes(sit[:5] + log), [])                     # a few radios in a quiet place: no hole
        self.assertEqual(H.log_holes(log), [])

    def test_both_raw_forms_end_up_the_same(self):
        """The same radios logged by Fieldwatch 1.1.42 and by 1.1.43 must look the same."""
        old = [log_row(T0, "BLE", "02:00:00:00:00:03", "Ruuvi ABCD", -65, "Ruuvi", "499", RUUVI),
               log_row(T0, "BLE", "02:00:00:00:00:04", "", -80, "Apple AirTags", "4C", "12020002"),
               log_row(T0, "BLE", "02:00:00:00:00:02", "", -70, "Apple Device", "4C", "1005011C0A0B0C")]
        new = [log_row(T0, "BLE", "02:00:00:00:00:03", "Ruuvi ABCD", -65, "Ruuvi", "499", RUUVI_ADVERT),
               log_row(T0, "BLE", "02:00:00:00:00:04", "", -80, "Apple AirTags", "4C", "07FF4C0012020002"),
               log_row(T0, "BLE", "02:00:00:00:00:02", "", -70, "Apple Device", "4C", "02011A0AFF4C001005011C0A0B0C")]
        seen = []
        for rows in (old, new):
            H.import_bytes("log.csv", log_csv(rows), None, 0, None)
            seen.append(sorted((d["mac"], d["category"], d["ident_id"], d.get("reclass")) for d in self.devices()))
            with H.db() as con:
                H.delete_import(con.execute("SELECT id FROM imports").fetchone()[0])
        self.assertEqual(seen[0], seen[1])

    def test_zip_of_exports(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("exports/log1.csv", log_csv(sample_rows()))
            z.writestr("exports/log2.csv", log_csv(sample_rows(5)))
            z.writestr("exports/readme.txt", "not an export")
        res = H.import_upload("exports.zip", buf.getvalue(), 0, None)
        self.assertEqual([r["status"] for r in res], ["imported", "imported"])
        self.assertEqual(len(self.devices()), 9)
        self.assertEqual(H.import_upload("bad.zip", b"not a zip", 0, None)[0]["status"], "error")

    def test_zip_limits(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(H.ZIP_MAX_FILES + 1):
                z.writestr(f"log{i}.csv", "x")
        res = H.import_upload("many.zip", buf.getvalue(), 0, None)
        self.assertEqual((len(res), res[0]["status"]), (1, "error"))
        self.assertIn("too big", res[0]["reason"])
        # an export inside a zip may not unpack to more than one upload may be
        H.MAX_UPLOAD = 2000
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("small.csv", log_csv(sample_rows()))
            z.writestr("bomb.csv", LOG_HEADER + "\n" + "0" * 5_000_000)
        res = {r["file"]: r for r in H.import_upload("bomb.zip", buf.getvalue(), 0, None)}
        self.assertEqual(res["small.csv"]["status"], "imported")
        self.assertEqual(res["bomb.csv"]["status"], "error")
        self.assertIn("limit", res["bomb.csv"]["reason"])

    def test_bluetooth_pause(self):
        hears = [{"src": "log", "collector_id": 1, "kind": "WIFI", "ts": T0 + i * 30_000} for i in range(41)]   # 20 min
        hears += [{"src": "log", "collector_id": 1, "kind": "BLE", "ts": T0 + i * 30_000} for i in range(10)]   # first 5 min
        hears += [{"src": "log", "collector_id": 1, "kind": "BLE", "ts": T0 + 15 * MIN + i * 30_000} for i in range(11)]
        gaps, logged = H.bluetooth_gaps(hears)
        self.assertEqual(len(gaps), 1)
        g = gaps[0]
        self.assertAlmostEqual((g["to"] - g["from"]) / MIN, 10, delta=1)
        self.assertGreaterEqual(g["from"], T0 + 4 * MIN)
        self.assertLessEqual(g["to"], T0 + 15 * MIN)
        self.assertAlmostEqual(logged[1] / MIN, 20, delta=1)
        only_ble = [h for h in hears if h["kind"] == "BLE"]
        self.assertEqual(H.bluetooth_gaps(only_ble)[0], [])        # no Wi-Fi rows: nothing can be told


# ── one device, many addresses ──────────────────────────────────────────────────────────────────
def fcf1(data20):
    return "020102" + "1716F1FC" + data20                     # Google service data FCF1, 20 bytes


def fef3(code, rest18):
    return "1E16F3FE" + "4A1723" + code.encode().hex().upper() + "1132" + rest18


class Linking(TempStore):
    A1, A2, A3 = "4A:00:00:00:00:01", "5B:00:00:00:00:02", "6C:00:00:00:00:03"       # resolvable private
    S1, S2 = "C4:00:00:00:00:01", "D5:00:00:00:00:02"                                # static
    UUID = "A1B2C3D4-0000-4000-8000-00805F9B34FB"

    def load(self, rows):
        H.import_bytes("log.csv", log_csv(rows), None, 0, None)
        return {d["mac"]: d for d in self.devices()}

    def test_address_kinds(self):
        self.assertTrue(H.rotating_address("4A:00:00:00:00:01"))
        self.assertTrue(H.rotating_address("7F:FF:00:00:00:01"))
        self.assertFalse(H.rotating_address("C4:00:00:00:00:01"))      # static
        self.assertFalse(H.rotating_address("00:1B:10:00:00:01"))      # a maker's own (public) address
        self.assertFalse(H.rotating_address(""))

    def test_same_google_advertisement_is_one_device(self):
        one, other = "11" * 20, "22" * 20
        d = self.load([log_row(T0, "BLE", self.A1, raw=fcf1(one)),
                       log_row(T0 + 9 * MIN, "BLE", self.A2, raw=fcf1(one)),
                       log_row(T0 + 9 * MIN, "BLE", self.A3, raw=fcf1(other))])
        self.assertEqual(d[self.A1]["ident_id"], "google-fcf1")
        self.assertEqual((d[self.A1]["group_size"], d[self.A2]["group_size"], d[self.A3]["group_size"]), (2, 2, 1))
        self.assertEqual(d[self.A1]["group"], d[self.A2]["group"])
        self.assertEqual(d[self.A1]["category"], "Android / Google")

    def test_google_day_code_links_through_a_changed_advertisement(self):
        d = self.load([log_row(T0, "BLE", self.A1, raw=fef3("Q0BB", "11" * 18)),
                       log_row(T0 + 9 * MIN, "BLE", self.A2, raw=fef3("Q0BB", "22" * 18)),
                       log_row(T0 + 9 * MIN, "BLE", self.A3, raw=fef3("ZZ99", "22" * 18))])
        self.assertEqual(d[self.A1]["ident_id"], "google-fef3-day")
        self.assertEqual(d[self.A1]["group"], d[self.A2]["group"])
        self.assertTrue(d[self.A1]["group"].startswith("google-day-code:"))
        self.assertEqual((d[self.A1]["group_size"], d[self.A3]["group_size"]), (2, 1))

    def test_other_fef3_forms_link_only_when_identical(self):
        odd = "1E16F3FE" + "000102" + "AA" * 24                       # not the 4A 17 23 form
        odd2 = "1E16F3FE" + "000102" + "BB" * 24
        d = self.load([log_row(T0, "BLE", self.A1, raw=odd), log_row(T0 + MIN, "BLE", self.A2, raw=odd),
                       log_row(T0 + MIN, "BLE", self.A3, raw=odd2)])
        self.assertEqual(d[self.A1]["ident_id"], "google-fef3")
        self.assertEqual((d[self.A1]["group_size"], d[self.A3]["group_size"]), (2, 1))

    def test_same_name_joins_changing_addresses_only(self):
        """Earbuds change address and keep their name: one device. Twenty rental scooters that are
        all named the same have twenty fixed addresses: twenty devices."""
        rows = [log_row(T0 + i * MIN, "BLE", m, "Buds of a test", uuids=self.UUID) for i, m in enumerate((self.A1, self.A2))]
        rows += [log_row(T0 + i * MIN, "BLE", m, "Scooter1", uuids=self.UUID.replace("A1B2", "B2C3")) for i, m in enumerate((self.S1, self.S2))]
        d = self.load(rows)
        self.assertEqual((d[self.A1]["group_size"], d[self.A2]["group_size"]), (2, 2))
        self.assertEqual((d[self.S1]["group_size"], d[self.S2]["group_size"]), (1, 1))
        self.assertEqual(d[self.S1]["group"], "")


# ── travelled with the phone ────────────────────────────────────────────────────────────────────
class Travel(TempStore):
    """25 minutes of a made-up day: 10 min at A, 5 min of driving 3.3 km north, 10 min at B."""
    X, Y, Z = "4A:00:00:00:00:0A", "4A:00:00:00:00:0B", "4A:00:00:00:00:0C"
    FA, FB = "02:00:00:00:0A:01", "02:00:00:00:0B:01"

    @staticmethod
    def where(sec):
        frac = min(max((sec - 600) / 300, 0), 1)
        return f"{60.0 + 0.03 * frac:.6f}", "25.000000"

    def day(self):
        rows = []
        for sec in range(0, 1501, 30):
            lat, lon = self.where(sec)
            rows.append(log_row(T0 + sec * 1000, "BLE", self.X, "", -50, lat=lat, lon=lon))      # always there
            if sec <= 600:
                rows.append(log_row(T0 + sec * 1000, "WIFI", self.FA, "Net-A", -60, lat=lat, lon=lon))
            if sec >= 900:
                rows.append(log_row(T0 + sec * 1000, "WIFI", self.FB, "Net-B", -60, lat=lat, lon=lon))
            if 660 <= sec <= 840:                                                              # 3 min, 2 km
                rows.append(log_row(T0 + sec * 1000, "BLE", self.Y, "", -90, lat=lat, lon=lon))
        for sec in (720, 775):                                                                # 55 s, 600 m
            lat, lon = self.where(sec)
            rows.append(log_row(T0 + sec * 1000, "BLE", self.Z, "", -95, lat=lat, lon=lon))
        return rows

    def test_stops_of_a_track(self):
        pts = []
        for sec in range(0, 2101, 10):                       # A, drive, B, and back to A at a jump
            lat, lon = self.where(sec) if sec <= 1500 else ("60.000100", "25.000100")
            pts.append((T0 + sec * 1000, float(lat), float(lon)))
        stops = H.find_stops(pts)
        self.assertEqual([s["place"] for s in stops], [1, 2, 1])
        self.assertAlmostEqual((stops[0]["to"] - stops[0]["from"]) / MIN, 10, delta=1.5)
        self.assertAlmostEqual(stops[1]["lat"], 60.03, places=3)
        self.assertEqual(H.find_stops([]), [])
        self.assertEqual(H.find_stops(pts[60:90]), [])        # five minutes of driving: no stop

    def test_levels(self):
        H.import_bytes("log.csv", log_csv(self.day()), None, 0, None)
        d = {x["mac"]: x for x in self.devices()}
        x = d[self.X]
        self.assertEqual((x["travel"], x["travel_stops"], x["travelled"]), ("stops", 2, True))
        self.assertAlmostEqual(x["travel_km"], 3.3, delta=0.1)
        self.assertEqual(len(x["travel_at"]), 2)
        y = d[self.Y]
        self.assertEqual((y["travel"], y["travel_stops"]), ("road", 0))
        self.assertAlmostEqual(y["travel_km"], 2.0, delta=0.1)
        self.assertEqual(y["travel_min"], 3)
        z = d[self.Z]                       # 600 m in under a minute: the old rule marked this
        self.assertGreaterEqual(z["spread_m"], 500)
        self.assertEqual((z["travel"], z["travelled"]), ("", False))
        for fixed in (self.FA, self.FB):
            self.assertEqual(d[fixed]["travel"], "")

    def test_a_linked_device_is_judged_as_one(self):
        """At stop A it used one address, at stop B another: together they were at both."""
        a1, a2, adv = "4A:00:00:00:00:21", "5B:00:00:00:00:22", fcf1("33" * 20)
        rows = self.day()
        la, lo = self.where(300)
        lb, lob = self.where(1200)
        rows += [log_row(T0 + 300 * 1000, "BLE", a1, raw=adv, lat=la, lon=lo),
                 log_row(T0 + 1200 * 1000, "BLE", a2, raw=adv, lat=lb, lon=lob)]
        H.import_bytes("log.csv", log_csv(rows), None, 0, None)
        d = {x["mac"]: x for x in self.devices()}
        self.assertEqual((d[a1]["group_size"], d[a1]["travel"], d[a2]["travel"]), (2, "stops", "stops"))
        self.assertEqual(d[a1]["spread_m"], 0)                          # the address alone did not move
        self.assertGreater(d[a1]["group_spread_m"], 3000)               # the device did

    def test_sit_rows_alone_give_no_travel(self):
        sit = ("kind,mac,name,first_seen,last_seen,hits,rssi_max,rssi_min,signatures,lat,lon\n"
               "BLE,4A:00:00:00:00:31,,2026-11-09T08:00:00.000Z,2026-11-09T09:00:00.000Z,40,-50,-90,,60.0,25.0\n")
        H.import_bytes("fieldwatch-sit-test.csv", sit.encode(), None, 0, None)
        d = self.devices()[0]
        self.assertEqual((d["travel"], d["travelled"], d["travel_at"]), ("", False, []))

    SIT_HEAD = "kind,mac,name,first_seen,last_seen,hits,rssi_max,rssi_min,signatures,lat,lon\n"

    @staticmethod
    def iso(sec):
        import datetime
        return datetime.datetime.fromtimestamp((T0 + sec * 1000) / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def test_sit_times_are_placed_on_the_phones_track(self):
        """A Sit row has one position but two times. Where the phone was at those times tells the rest."""
        both, road, home = "4A:00:00:00:00:41", "4A:00:00:00:00:42", "4A:00:00:00:00:43"
        sit = self.SIT_HEAD + "".join(
            f"BLE,{mac},,{self.iso(a)},{self.iso(b)},{hits},-60,-90,,60.0,25.0\n"
            for mac, a, b, hits in ((both, 120, 1300, 200),        # first heard at A, last heard at B
                                    (road, 660, 840, 40),          # only on the way: 3 min, 2 km
                                    (home, 60, 500, 90)))          # only at A
        H.import_bytes("log.csv", log_csv(self.day()), None, 0, None)
        H.import_bytes("fieldwatch-sit-test.csv", sit.encode(), None, 0, None)
        d = {x["mac"]: x for x in self.devices()}
        self.assertEqual((d[both]["travel"], d[both]["travel_stops"]), ("stops", 2))
        self.assertEqual(d[road]["travel"], "road")
        self.assertAlmostEqual(d[road]["travel_km"], 2.0, delta=0.1)
        self.assertEqual(d[home]["travel"], "")

    def test_the_sit_fills_a_hole_in_the_track(self):
        """Fieldwatch can lose a part of its log. A Sit row of a radio heard only for a moment tells where
        the phone was then; such rows make the track where the log has none, and only there."""
        passed = "".join(f"WIFI,02:00:00:00:0C:{i:02X},,{self.iso(sec)},{self.iso(sec + 2)},1,-80,-80,,"
                         f"{self.where(sec)[0]},{self.where(sec)[1]}\n" for i, sec in enumerate(range(0, 900, 20)))
        both, road, home = "4A:00:00:00:00:51", "4A:00:00:00:00:52", "4A:00:00:00:00:53"
        sit = self.SIT_HEAD + passed + "".join(
            f"BLE,{mac},,{self.iso(a)},{self.iso(b)},{hits},-60,-90,,60.0,25.0\n"
            for mac, a, b, hits in ((both, 120, 1300, 200),        # first heard at A, last heard at B
                                    (road, 300, 840, 40),          # at A, and last heard 2.7 km up the road
                                    (home, 60, 500, 90)))          # only at A
        late = [r for r in self.day() if int(r.split(",")[0]) >= T0 + 900_000]     # the log has only the stay at B
        H.import_bytes("log.csv", log_csv(late), None, 0, None)
        H.import_bytes("fieldwatch-sit-test.csv", sit.encode(), None, 0, None)
        with H.db() as con:
            hears = H.query_hears(con, None, None, ids=H.all_active_ids(con))
        ph = list(H.phone_stops(hears).values())[0]
        self.assertEqual([s["place"] for s in ph["stops"]], [1, 2])
        self.assertEqual(ph["from_sit"], 42)                        # not the three within a minute of the log
        d = {x["mac"]: x for x in H.build_devices(hears)}
        self.assertEqual((d[both]["travel"], d[both]["travel_stops"]), ("stops", 2))
        self.assertEqual(d[road]["travel"], "road")                 # one far sighting is enough for a Sit row
        self.assertEqual(d[home]["travel"], "")
        # with the whole log the track is the log's own
        H.import_bytes("log2.csv", log_csv(self.day()), None, 0, None)
        with H.db() as con:
            hears = H.query_hears(con, None, None, ids=H.all_active_ids(con))
        self.assertEqual(list(H.phone_stops(hears).values())[0]["from_sit"], 0)

    def walk(self):
        """Six minutes on foot, 330 m north, without a stop."""
        w, weak, fixed = "4A:00:00:00:00:51", "4A:00:00:00:00:52", "C4:00:00:00:00:53"
        rows = []
        for sec in range(0, 361, 20):
            lat, lon = f"{60.0 + 0.003 * sec / 360:.6f}", "25.000000"
            rows.append(log_row(T0 + sec * 1000, "WIFI", self.FA, "Net-A", -60 - sec // 6, lat=lat, lon=lon))
            rows.append(log_row(T0 + sec * 1000, "BLE", w, "", -70, lat=lat, lon=lon))        # beside the phone
            rows.append(log_row(T0 + sec * 1000, "BLE", weak, "", -92, lat=lat, lon=lon))     # far behind, or a strong beacon
            if sec <= 80:
                rows.append(log_row(T0 + sec * 1000, "BLE", fixed, "", -75, lat=lat, lon=lon))
        return rows, w, weak, fixed

    def test_beside_you_on_a_short_walk(self):
        rows, w, weak, fixed = self.walk()
        H.import_bytes("log.csv", log_csv(rows), None, 0, None)
        d = {x["mac"]: x for x in self.devices()}
        self.assertEqual(d[w]["travel"], "road")
        self.assertAlmostEqual(d[w]["travel_km"], 0.3, delta=0.05)
        self.assertEqual(d[w]["travel_min"], 6)
        self.assertEqual(d[weak]["travel"], "")            # heard all the way, but never strongly
        self.assertEqual(d[fixed]["travel"], "")           # strong, but only where it stands
        self.assertEqual(d[self.FA]["travel"], "")         # Wi-Fi carries far: not judged this way

    def test_companion(self):
        H.import_bytes("log.csv", log_csv(self.day()), None, 0, None)
        d = {x["mac"]: x for x in self.devices()}
        self.assertTrue(d[self.X]["companion"])            # at both stops, and at -50 dBm
        self.assertFalse(d[self.Y]["companion"])           # same road, at -90 dBm
        self.assertFalse(d[self.FA]["companion"])
        rows, w, _weak, _fixed = self.walk()
        H.import_bytes("walk.csv", log_csv([r.replace(",-70,", ",-50,") for r in rows]), None, 0, None)
        self.assertFalse({x["mac"]: x for x in self.devices()}[w]["companion"])    # close, but only for six minutes

    def test_always_with_me_is_a_kind_of_my_device(self):
        c = H.app.test_client()
        post = lambda body: c.post("/api/own", json=body, headers={"Origin": "http://localhost"})       # noqa: E731
        self.assertEqual(post({"match": "companion", "kind": "BLE", "value": "android / google", "label": "My watch"}).status_code, 200)
        self.assertEqual(post({"match": "companion", "value": "any", "label": "Anything"}).status_code, 200)
        self.assertEqual(post({"match": "companion", "value": "No such layer", "label": "x"}).status_code, 400)
        own = {o["label"]: o for o in c.get("/api/own").get_json()["own"]}
        self.assertEqual(own["My watch"]["value"], "Android / Google")          # stored as the layer is written

    def test_api_and_export(self):
        H.import_bytes("log.csv", log_csv(self.day()), None, 0, None)
        c = H.app.test_client()
        j = c.get("/api/devices").get_json()
        self.assertEqual([s["place"] for s in j["stops"]], [1, 2])
        import csv
        out = {r["mac"]: r for r in csv.DictReader(io.StringIO(c.get("/api/export.csv").get_data(as_text=True)))}
        self.assertEqual((out[self.X]["travel"], out[self.X]["travel_stops"]), ("stops", "2"))
        self.assertEqual(out[self.Y]["travel"], "road")
        self.assertEqual(out[self.Z]["travel"], "")


# ── the web side ────────────────────────────────────────────────────────────────────────────────
class Web(TempStore):
    ORIGIN = {"Origin": "http://localhost"}

    def setUp(self):
        super().setUp()
        self.c = H.app.test_client()

    def upload(self, name, data, **kw):
        return self.c.post("/api/import", data={"files": (io.BytesIO(data), name)},
                           content_type="multipart/form-data", headers=kw.get("headers", self.ORIGIN))

    def test_pages_open(self):
        self.assertEqual(self.c.get("/healthz").status_code, 200)
        for url in ("/", "/app", "/workbench", "/maintenance"):
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertIn(f"maxUploadMb: {H.CFG['max_upload_mb']}", self.c.get("/").get_data(as_text=True))
        self.assertIn(f"maxUploadMb: {H.CFG['max_upload_mb']}", self.c.get("/app").get_data(as_text=True))

    def test_watch_folder_on_another_drive(self):
        # on Windows os.path.commonpath() raises ValueError for paths on two drives
        with mock.patch("harava.os.path.commonpath", side_effect=ValueError("Paths don't have the same drive")):
            self.assertEqual(self.c.get("/").status_code, 200)

    def test_upload_and_read_back(self):
        r = self.upload("log.csv", log_csv(sample_rows()))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["results"][0]["status"], "imported")
        devs = self.c.get("/api/devices").get_json()["devices"]
        self.assertEqual(len(devs), 4)
        for url in ("/api/summary", "/api/timeline", "/api/imports", "/api/ids", "/api/path"):
            self.assertEqual(self.c.get(url).status_code, 200, url)

    def test_upload_over_the_limit_is_refused_with_a_message(self):
        H.app.config["MAX_CONTENT_LENGTH"] = 2000
        r = self.upload("log.csv", log_csv(sample_rows(100)))
        self.assertEqual(r.status_code, 413)
        self.assertIn("too large", r.get_json()["error"])
        self.assertEqual(self.c.get("/api/devices").get_json()["devices"], [])

    def test_upload_limit_follows_the_setting(self):
        self.assertEqual(H.MAX_UPLOAD, H.CFG["max_upload_mb"] * 1024 * 1024)
        self.assertGreaterEqual(H.app.config["MAX_CONTENT_LENGTH"], H.MAX_UPLOAD)
        self.assertLessEqual(H.app.config["MAX_CONTENT_LENGTH"], H.MAX_UPLOAD + 2 * 1024 * 1024)

    def test_a_request_from_another_site_is_refused(self):
        r = self.upload("log.csv", log_csv(sample_rows()), headers={"Origin": "https://example.org"})
        self.assertEqual(r.status_code, 403)

    def test_hostile_file_name_and_radio_name_stay_text(self):
        name = "a');window.x=1;('.csv"
        rows = [log_row(T0, "BLE", "02:00:00:00:00:02", "<img src=x onerror=alert(1)>")]
        self.assertEqual(self.upload(name, log_csv(rows)).status_code, 200)
        self.assertEqual(self.c.get("/api/imports").get_json()["imports"][0]["filename"], name)   # data, as it was
        page = self.c.get("/").get_data(as_text=True)
        self.assertNotIn("window.x=1", page)
        self.assertNotIn("onerror=alert", page)

    def test_csv_export_does_not_carry_formulas(self):
        rows = [log_row(T0, "BLE", "02:00:00:00:00:02", "=HYPERLINK(\"http://example.org\")"),
                log_row(T0, "BLE", "02:00:00:00:00:03", "@SUM(A1)"),
                log_row(T0, "BLE", "02:00:00:00:00:04", "Plain name")]
        self.upload("log.csv", log_csv(rows))
        import csv
        out = list(csv.DictReader(io.StringIO(self.c.get("/api/export.csv").get_data(as_text=True))))
        names = {r["mac"][-2:]: r["name"] for r in out}
        self.assertTrue(names["02"].startswith("'="))
        self.assertTrue(names["03"].startswith("'@"))
        self.assertEqual(names["04"], "Plain name")
        for r in out:
            self.assertEqual(r["rssi_max"], "-70")                   # numbers stay numbers

    def test_with_users_everything_needs_a_sign_in(self):
        con = sqlite3.connect(H.USERS_DB)
        con.execute("INSERT INTO users(username, role, password_hash, created, updated) VALUES (?,?,?,?,?)",
                    ("viewer", "view", generate_password_hash("a long test password"), "2026-11-10", "2026-11-10"))
        con.commit()
        con.close()
        self.assertEqual(self.c.get("/api/devices").status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.upload("log.csv", log_csv(sample_rows())).status_code, 401)
        self.assertEqual(self.c.get("/healthz").status_code, 200)
        r = self.c.post("/login", data={"username": "viewer", "password": "a long test password"}, headers=self.ORIGIN)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/api/devices").status_code, 200)
        self.assertEqual(self.upload("log.csv", log_csv(sample_rows())).status_code, 403)    # View only may not import
        self.assertEqual(self.c.get("/maintenance").status_code, 403)

    def test_next_after_sign_in_stays_on_this_site(self):
        con = sqlite3.connect(H.USERS_DB)
        con.execute("INSERT INTO users(username, role, password_hash, created, updated) VALUES (?,?,?,?,?)",
                    ("viewer", "view", generate_password_hash("a long test password"), "2026-11-10", "2026-11-10"))
        con.commit()
        con.close()
        r = self.c.post("/login?next=//example.org/x", data={"username": "viewer", "password": "a long test password"},
                        headers=self.ORIGIN)
        self.assertEqual(r.headers["Location"], "/")


class Pages(unittest.TestCase):
    """Things the page scripts must keep doing."""

    def test_no_text_inside_quotes_in_an_inline_handler(self):
        # onclick="f('${esc(x)}')" is not safe: the browser turns &#39; back into a quote before it
        # runs the handler. Use jsArg(x) from common.js.
        bad = re.compile(r"""on[a-z]+=\\?"[^"]*'\$\{""")
        for fn in glob.glob(os.path.join(ROOT, "static", "*.js")) + glob.glob(os.path.join(ROOT, "templates", "*.html")):
            with open(fn, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    self.assertIsNone(bad.search(line), f"{os.path.basename(fn)}:{n}")

    def test_export_guard_is_in_the_page_too(self):
        with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("uploadTooBig(", js)
        self.assertRegex(js, r"/\^\[=\+\\-@\\t\\r\]/")


# ── real exports in data/, when there are any ───────────────────────────────────────────────────
def _local_exports():
    folder = os.environ.get("HARAVA_TEST_EXPORTS") or os.path.join(ROOT, "data")
    files = []
    for ext in ("*.csv", "*.jsonl", "*.json"):
        files += glob.glob(os.path.join(folder, ext))
    return sorted(files, key=os.path.getmtime)[-12:]             # the newest are the ones that matter


@unittest.skipUnless(_local_exports(), "no Fieldwatch exports in data/ (they are never in git)")
class LocalExports(unittest.TestCase):
    """Fieldwatch's own signatures tell what some rows are; Harava must be able to read those rows.
    When Fieldwatch changes the log format, these shares fall, as they would have on 9 Nov 2026."""
    SHARE, ENOUGH = 0.95, 20

    @classmethod
    def setUpClass(cls):
        cls.logs = []
        for fn in _local_exports():
            with open(fn, "rb") as fh:
                data = fh.read()
            ftype, hears, _, _ = H._parse(os.path.basename(fn), data)
            cls.logs.append((os.path.basename(fn), ftype, data, hears))

    def share(self, what, good, rows, name):
        if len(rows) < self.ENOUGH:
            return
        ok = sum(1 for h in rows if good(h))
        self.assertGreaterEqual(ok / len(rows), self.SHARE,
                                f"{name}: {what}: only {ok} of {len(rows)} rows. Has Fieldwatch changed its log format?")

    def test_every_file_is_read(self):
        for name, ftype, data, hears in self.logs:
            with self.subTest(file=name):
                self.assertTrue(hears, "no rows read")
                lines = sum(1 for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()) - 1
                self.assertGreaterEqual(len(hears), 0.95 * lines, "many rows were skipped")

    def test_log_columns_are_the_known_ones(self):
        for name, ftype, data, hears in self.logs:
            head = data[:400].decode("utf-8", "replace").lstrip("\ufeff").splitlines()[0]
            if ftype == "csv" and head.lower().startswith("timestamp"):
                with self.subTest(file=name):
                    self.assertEqual([c.strip().lower() for c in head.split(",")], H.CSV_HEADER,
                                     "Fieldwatch's log has new or renamed columns")

    def test_signature_names_are_in_the_table(self):
        for name, ftype, data, hears in self.logs:
            names = {f for h in hears for f in h["fleets"].split("+") if f}
            with self.subTest(file=name):
                self.assertFalse(sorted(names - set(H.SIG_CLASS)),
                                 "Fieldwatch is newer than Harava's table: run tools/update_fieldwatch_signatures.py")

    def test_raw_field_can_be_read(self):
        for name, ftype, data, hears in self.logs:
            ble = [h for h in hears if h["kind"] == "BLE" and h["src"] == "log" and h["raw"]]
            feats = lambda h: H.adv_features(h["mfg"], h["raw"], h["uuids"])          # noqa: E731
            with self.subTest(file=name):
                self.share("raw without manufacturer data is a whole advertisement",
                           lambda h: H.parse_ad(h["raw"].upper()), [h for h in ble if not h["mfg"]], name)
                self.share("manufacturer data found in raw",
                           lambda h: feats(h)["mfg_data"], [h for h in ble if h["mfg"]], name)
                self.share("rows with the Ruuvi signature give a temperature",
                           lambda h: H.ruuvi_decode(H.mfg_payload(h["mfg"], h["raw"])),
                           [h for h in ble if "Ruuvi" in h["fleets"].split("+")], name)
                self.share("rows with the Apple AirTags signature carry a Find My message",
                           lambda h: H.findmy_info(feats(h)),
                           [h for h in ble if "Apple AirTags" in h["fleets"].split("+")], name)


if __name__ == "__main__":
    unittest.main()
