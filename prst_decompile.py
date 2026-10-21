#!/usr/bin/env python3
"""Decompile a Valeton GP-200 .prst file into an editable YAML preset.

    python prst_decompile.py preset.prst -o preset.yaml
    python prst_decompile.py preset.prst               # print to stdout
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # embedded Python ignores the script dir

from gp200 import PrstError, decompile_prst  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Decompile a GP-200 .prst file to YAML.")
    ap.add_argument("input", help=".prst file")
    ap.add_argument("-o", "--output", help="output YAML path (default: stdout)")
    ap.add_argument("--no-comments", action="store_true", help="omit the parameter range comments")
    args = ap.parse_args(argv)

    try:
        with open(args.input, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"Cannot read {args.input}: {e.strerror}", file=sys.stderr)
        return 1

    try:
        text = decompile_prst(data, comments=not args.no_comments)
    except PrstError as e:
        print(f"{os.path.basename(args.input)}: {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
