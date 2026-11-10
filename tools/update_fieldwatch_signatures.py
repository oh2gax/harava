#!/usr/bin/env python3
"""
update_fieldwatch_signatures.py — keep signature_classes.json in step with Fieldwatch.

Fieldwatch (https://github.com/OffGridPete/Fieldwatch, MIT) writes the names of the signatures
a radio matched into its log ("fleets"). Harava turns a signature name into a device layer with
signature_classes.json: signature name -> class (FINDER, PHONE, VEHICLE ...). Fieldwatch gets new
signatures and classes often, so this table has to follow.

This tool downloads Fieldwatch's stock catalog (dist/fieldwatch-signatures-v2.json) and its
latest version note (dist/app-update.json), and

  python tools/update_fieldwatch_signatures.py            rewrites signature_classes.json
  python tools/update_fieldwatch_signatures.py --check    only tells what is different
                                                          (exit code 1 when something is)

Names that Fieldwatch has removed stay in the table (old logs still carry them); they are listed
under "retired". A class that harava.py has no layer for (CLASS_CATEGORY) is pointed out: radios
of that class would show as Unidentified until a layer is added.

Run from the project folder. No extra packages are needed.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date

RAW = "https://raw.githubusercontent.com/OffGridPete/Fieldwatch/main/"
CATALOG = RAW + "dist/fieldwatch-signatures-v2.json"
APP = RAW + "dist/app-update.json"
CHANGELOG = "https://github.com/OffGridPete/Fieldwatch/blob/main/CHANGELOG.md"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "signature_classes.json")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "harava-tools"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def harava_classes():
    """The classes harava.py has a layer for (the keys of CLASS_CATEGORY), or None if not found."""
    try:
        with open(os.path.join(ROOT, "harava.py"), encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return None
    m = re.search(r"CLASS_CATEGORY\s*=\s*\{(.*?)\n\}", src, re.S)
    return set(re.findall(r'"([A-Z_]+)"\s*:', m.group(1))) if m else None


def main():
    check = "--check" in sys.argv[1:]
    try:
        with open(OUT, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = {"classes": {}}
    old_classes = old.get("classes", {})
    old_catalog = old.get("catalog")
    if old_catalog is None:                                    # files written before this tool
        m = re.search(r"catalogVersion (\d+)", old.get("source", ""))
        old_catalog = int(m.group(1)) if m else None

    print("Downloading the Fieldwatch catalog ...")
    cat = fetch(CATALOG)
    try:
        app = fetch(APP)
    except Exception as e:                                     # the version note is only for information
        print(f"  (could not read app-update.json: {e})")
        app = {}
    new = {f["name"]: f.get("kind", "") for f in cat.get("fleets", []) if f.get("name")}
    if len(new) < 50:
        sys.exit("The catalog looks wrong (fewer than 50 signatures); nothing was changed.")
    catalog, version = cat.get("catalogVersion"), app.get("versionName", "")

    added = sorted(n for n in new if n not in old_classes)
    changed = sorted(n for n in new if n in old_classes and old_classes[n] != new[n])
    retired = sorted(n for n in old_classes if n not in new)
    layers = harava_classes()
    no_layer = sorted({c for c in new.values() if c} - layers) if layers is not None else []

    print(f"Fieldwatch now: version {version or '?'}, catalog {catalog}, {len(new)} signatures")
    print(f"Harava's table: version {old.get('app_version') or '?'}, catalog {old_catalog if old_catalog is not None else '?'}, "
          f"{len(old_classes)} names")
    for title, names, show in (("New signatures", added, lambda n: f"{n} ({new[n]})"),
                               ("Class changed", changed, lambda n: f"{n}: {old_classes[n]} -> {new[n]}"),
                               ("No longer in Fieldwatch (kept for old logs)", retired, str)):
        if names:
            print(f"\n{title} ({len(names)}):")
            for n in names:
                print("  " + show(n))
    if no_layer:
        print("\nClasses that harava.py has no layer for (add them to CLASS_CATEGORY, CLASS_PRIORITY and CAT in static/common.js):")
        print("  " + ", ".join(no_layer))
    newer = (catalog != old_catalog) or (bool(version) and version != old.get("app_version"))
    if newer:
        print(f"\nFieldwatch has changed since the table was made. Read what else changed: {CHANGELOG}")
    different = bool(added or changed or no_layer or newer)
    if not different:
        print("\nNothing new: the table matches Fieldwatch.")
    if check:
        sys.exit(1 if different else 0)

    classes = dict(old_classes)
    classes.update(new)
    out = {
        "source": f"OffGridPete/Fieldwatch dist/fieldwatch-signatures-v2.json (MIT), catalogVersion {catalog}",
        "catalog": catalog,
        "app_version": version,
        "fetched": date.today().isoformat(),
        "retired": retired,
        "classes": dict(sorted(classes.items(), key=lambda kv: kv[0].lower())),
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, ensure_ascii=False, indent=0) + "\n")
    print(f"\nWrote {OUT}: {len(classes)} names ({len(retired)} retired).")


if __name__ == "__main__":
    main()
