#!/usr/bin/env python3
"""Build gp200_catalog.json from Valeton's algorithm.xml.

algorithm.xml ships inside the official GP-200 editor for Windows
(<install dir>/Resource/GP-200/File/algorithm.xml). It lists every effect
("Alg") with its 32-bit code, its module, and each control's range, default,
unit and menu options.

Usage:
    python tools/extract_catalog.py [path/to/algorithm.xml] [-o gp200_catalog.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XML = ROOT / "data" / "source" / "algorithm.xml"
DEFAULT_OUT = ROOT / "gp200_catalog.json"

MODULES = ["PRE", "WAH", "DST", "AMP", "NR", "CAB", "EQ", "MOD", "DLY", "RVB", "VOL"]

# Effects that appear many times under one name with different codes.
NUMBERED_NAMES = {"SnapTone", "User IR"}

KIND_BY_TAG = {"Knob": "knob", "Slider": "slider", "Switch": "switch", "Combox": "combo"}


def _num(value: str | None) -> float | int | None:
    if value is None or value.strip() == "":
        return None
    # A few entries carry a unit inside the number (Detune: default = "-25 Cents").
    m = re.match(r"\s*([-+]?\d+(?:\.\d+)?)", value)
    if not m:
        raise ValueError(f"not a number: {value!r}")
    f = float(m.group(1))
    return int(f) if f.is_integer() else f


def parse_param(el: ET.Element) -> dict:
    a = {k.strip(): v.strip() for k, v in el.attrib.items()}
    kind = KIND_BY_TAG[el.tag]
    param: dict = {
        "name": a["Name"],
        "kind": kind,
        "idx": int(float(a["idx"])),
        "id": int(float(a["ID"])),
        "default": _num(a.get("default")),
    }
    if kind in ("knob", "slider"):
        param["min"] = _num(a.get("Dmin"))
        param["max"] = _num(a.get("Dmax"))
        param["step"] = _num(a.get("step")) or 1
        if a.get("Suffix"):
            param["unit"] = a["Suffix"]
        if a.get("MinISOff") == "1":
            param["min_is_off"] = True
        if a.get("valueType") is not None:
            param["value_type"] = int(a["valueType"])
    else:
        options = []
        for m in el.findall("Menu"):
            ma = {k.strip(): v.strip() for k, v in m.attrib.items()}
            opt = {"name": ma["Name"], "value": int(float(ma["ID"]))}
            if "TrueID" in ma:
                opt["true_id"] = int(float(ma["TrueID"]))
            options.append(opt)
        param["options"] = options
        param["min"] = min(o["value"] for o in options) if options else 0
        param["max"] = max(o["value"] for o in options) if options else 0
    if a.get("bind"):
        param["bind"] = int(a["bind"])
    if a.get("SubType"):
        param["subtype"] = a["SubType"]
    return param


def build_catalog(xml_path: Path) -> dict:
    root = ET.parse(xml_path).getroot()  # ElementTree drops XML comments
    by_code: dict[int, dict] = {}
    counters: dict[tuple[str, str], int] = {}

    for catalog in root.findall("Catalog"):
        module = catalog.get("Name").strip()
        if module not in MODULES:
            raise SystemExit(f"Unknown module {module!r} in {xml_path}")
        for alg in catalog.findall("Alg"):
            a = {k.strip(): v.strip() for k, v in alg.attrib.items()}
            code = int(a["Code"])
            name = a["Name"]
            if name in NUMBERED_NAMES:
                key = (module, name)
                counters[key] = counters.get(key, 0) + 1
                name = f"{name} {counters[key]}"

            if code in by_code:
                effect = by_code[code]
                if effect["name"] != name:
                    raise SystemExit(f"Code {code:#x} has two names: {effect['name']!r} / {name!r}")
                if module not in effect["modules"]:
                    effect["modules"].append(module)
                continue

            params = [parse_param(el) for el in alg if el.tag in KIND_BY_TAG]
            params.sort(key=lambda p: p["idx"])
            effect = {
                "code": code,
                "name": name,
                "modules": [module],
                "index": int(a.get("Index", "0")),
            }
            if a.get("CABCode"):
                effect["cab_code"] = int(a["CABCode"])
            effect["params"] = params
            by_code[code] = effect

    effects = sorted(by_code.values(), key=lambda e: (MODULES.index(e["modules"][0]), e["index"]))
    return {
        "format": 1,
        "source": "Valeton GP-200 editor algorithm.xml",
        "modules": MODULES,
        "effects": effects,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", nargs="?", type=Path, default=DEFAULT_XML)
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if not args.xml.exists():
        print(f"algorithm.xml not found: {args.xml}", file=sys.stderr)
        return 1
    catalog = build_catalog(args.xml)
    args.output.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n_params = sum(len(e["params"]) for e in catalog["effects"])
    print(f"Wrote {args.output} ({len(catalog['effects'])} effects, {n_params} params)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
