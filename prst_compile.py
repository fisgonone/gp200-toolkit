#!/usr/bin/env python3
"""Compile a YAML preset into a Valeton GP-200 .prst file.

    python prst_compile.py preset.yaml -o preset.prst
    python prst_compile.py preset.yaml --check        # validate only

Exit code 0 on success, 1 when the YAML has problems (all listed on stderr).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # embedded Python ignores the script dir

from gp200 import SpecError, compile_yaml  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compile a YAML preset to a GP-200 .prst file.")
    ap.add_argument("input", help="YAML preset file ('-' reads stdin)")
    ap.add_argument("-o", "--output", help="output .prst path (default: input name with .prst)")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = ap.parse_args(argv)

    try:
        if args.input == "-":
            text = sys.stdin.read()
        else:
            with open(args.input, encoding="utf-8-sig") as f:
                text = f.read()
    except OSError as e:
        print(f"Cannot read {args.input}: {e.strerror}", file=sys.stderr)
        return 1

    try:
        data, preset, warnings = compile_yaml(text)
    except SpecError as e:
        n = len(e.errors)
        print(f"Found {n} problem{'s' if n != 1 else ''} in the preset:", file=sys.stderr)
        for line in e.errors:
            print(f"  - {line}", file=sys.stderr)
        return 1

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if args.check:
        print(f"OK: '{preset.name}' is valid")
        return 0

    out = args.output or (os.path.splitext(args.input)[0] + ".prst" if args.input != "-" else "preset.prst")
    with open(out, "wb") as f:
        f.write(data)
    print(f"Compiled '{preset.name}' -> {os.path.basename(out)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
