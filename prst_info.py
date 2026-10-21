#!/usr/bin/env python3
"""Browse the GP-200 effect catalog and inspect .prst files.

    python prst_info.py modules                 # the 11 modules and effect counts
    python prst_info.py effects AMP             # every AMP effect
    python prst_info.py effect "UK 800"         # parameters, ranges and defaults
    python prst_info.py inspect preset.prst     # summary of a preset file
    python prst_info.py search twin             # find effects by part of the name
    python prst_info.py catalog                 # the full catalog as JSON
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gp200 import PrstError, catalog, read_prst  # noqa: E402
from gp200.catalog import CATALOG_PATH, MODULE_DEFAULTS, norm  # noqa: E402
from gp200.prst import MODULES, slot_name  # noqa: E402


def cmd_modules(_args) -> int:
    cat = catalog()
    for m in MODULES:
        print(f"{m:4} {len(cat.in_module(m)):3} effects   (init: {MODULE_DEFAULTS[m]})")
    return 0


def cmd_effects(args) -> int:
    cat = catalog()
    mods = [args.module.upper()] if args.module else MODULES
    for m in mods:
        if m not in MODULES:
            print(f"Unknown module '{args.module}'. Modules: {', '.join(MODULES)}", file=sys.stderr)
            return 1
        print(f"[{m}]")
        for e in cat.in_module(m):
            print(f"  {e.name}")
    return 0


def cmd_effect(args) -> int:
    cat = catalog()
    matches = [e for e in cat.effects if norm(e.name) == norm(args.name)]
    if args.module:
        matches = [e for e in matches if args.module.upper() in e.modules]
    if not matches:
        found = [e.name for e in cat.effects if norm(args.name) in norm(e.name)]
        print(f"No effect named '{args.name}'." + (f" Similar: {', '.join(found[:8])}" if found else ""),
              file=sys.stderr)
        return 1
    for e in matches:
        print(f"{e.name}  [{', '.join(e.modules)}]  code {e.code:#010x}")
        if e.cab_code and e.cab_code in cat.by_code:
            print(f"  matching cabinet: {cat.by_code[e.cab_code].name}")
        for p in e.params:
            default = p.option_name(p.default) if p.is_choice else p.default
            print(f"  {p.name:<14} {p.describe_range():<40} default {default}")
    return 0


def cmd_search(args) -> int:
    key = norm(args.text)
    hits = [e for e in catalog().effects if key in norm(e.name)]
    for e in hits:
        print(f"{e.name:<18} {', '.join(e.modules)}")
    return 0 if hits else 1


def cmd_inspect(args) -> int:
    try:
        with open(args.file, "rb") as f:
            p = read_prst(f.read())
    except (OSError, PrstError) as e:
        print(f"{args.file}: {e}", file=sys.stderr)
        return 1
    cat = catalog()
    slot = slot_name(p.patch_slot) if p.patch_slot is not None else "-"
    print(f"'{p.name}' by {p.author or '-'}  slot {slot}  {p.tempo} BPM  volume {p.volume}  ({p.source_size} bytes)")
    for i in p.chain:
        b = p.blocks[i]
        e = cat.by_code.get(b.code)
        print(f"  {'ON ' if b.enabled else 'off'} {MODULES[i]:4} {e.name if e else hex(b.code)}")
    return 0


def cmd_catalog(_args) -> int:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(json.dumps(json.load(f), ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("modules").set_defaults(fn=cmd_modules)
    s = sub.add_parser("effects")
    s.add_argument("module", nargs="?")
    s.set_defaults(fn=cmd_effects)
    s = sub.add_parser("effect")
    s.add_argument("name")
    s.add_argument("--module")
    s.set_defaults(fn=cmd_effect)
    s = sub.add_parser("search")
    s.add_argument("text")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("inspect")
    s.add_argument("file")
    s.set_defaults(fn=cmd_inspect)
    sub.add_parser("catalog").set_defaults(fn=cmd_catalog)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
