#!/usr/bin/env python3
"""
update_bt_numbers.py — refresh ids/bt_assigned_numbers.json from the Bluetooth SIG.

Downloads the official "Assigned Numbers" YAML files from the Bluetooth SIG's public
repository (https://bitbucket.org/bluetooth-SIG/public) and writes the parts Harava uses:

  companies   company identifier (the first two bytes of manufacturer data) -> company name
  uuids       16-bit service UUIDs: SIG services, member UUIDs (FCxx-FExx, owned by a company)
              and SDO UUIDs -> name, kind
  ad_types    advertising data types (Flags, Service Data, Mesh Beacon, ...) -> name

Run from the project folder:   python tools/update_bt_numbers.py
No extra packages are needed (the YAML files are simple enough to read line by line).
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date

BASE = "https://bitbucket.org/bluetooth-SIG/public/raw/main/assigned_numbers/"
FILES = {
    "companies": ("company_identifiers/company_identifiers.yaml", "value"),
    "service": ("uuids/service_uuids.yaml", "uuid"),
    "sdo": ("uuids/sdo_uuids.yaml", "uuid"),
    "member": ("uuids/member_uuids.yaml", "uuid"),
    "ad_types": ("core/ad_types.yaml", "value"),
}
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "ids", "bt_assigned_numbers.json")

_KEY = re.compile(r"^\s*-?\s*(value|uuid|name)\s*:\s*(.*?)\s*$")


def parse_entries(text, key):
    """Read '- <key>: 0x1234' / 'name: ...' pairs from one SIG YAML file."""
    out, num = {}, None
    for line in text.splitlines():
        m = _KEY.match(line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if k == key:
            try:
                num = int(v, 0)
            except ValueError:
                num = None
        elif k == "name" and num is not None:
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1].replace("''", "'")
            out[num] = v
            num = None
    return out


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Harava update_bt_numbers"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def main():
    data = {}
    for name, (path, key) in FILES.items():
        print(f"  downloading {path} …")
        data[name] = parse_entries(fetch(path), key)
    uuids = {}
    for kind in ("service", "sdo", "member"):
        for num, nm in data[kind].items():
            uuids[format(num, "04X")] = {"name": nm, "kind": kind}
    out = {
        "source": "Bluetooth SIG Assigned Numbers, https://bitbucket.org/bluetooth-SIG/public",
        "fetched": date.today().isoformat(),
        "companies": {format(k, "04X"): v for k, v in sorted(data["companies"].items())},
        "uuids": dict(sorted(uuids.items())),
        "ad_types": {format(k, "02X"): v for k, v in sorted(data["ad_types"].items())},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=0)
    print(f"  wrote {OUT}: {len(out['companies'])} companies, {len(out['uuids'])} UUIDs, "
          f"{len(out['ad_types'])} AD types")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {e}")
        sys.exit(1)
