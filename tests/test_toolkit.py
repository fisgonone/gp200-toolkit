"""Tests for the GP-200 toolkit. Run: python -m unittest discover -s tests

Set GP200_SAMPLES to a folder of real .prst files to also round-trip those.
"""
import glob
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gp200  # noqa: E402
from gp200.catalog import SYNC_NOTES, CatalogError, encode_value  # noqa: E402
from gp200.prst import (MODULES, USER_SIZE, PrstError, checksum, parse_slot_name, read_prst,  # noqa: E402
                        slot_name, write_prst)
from gp200.spec import SpecError  # noqa: E402

CAT = gp200.catalog()


def compile_text(text: str):
    data, preset, warnings = gp200.compile_yaml(text)
    return data, preset, warnings


def block(preset, module):
    return preset.blocks[MODULES.index(module)]


def value(preset, module, param_name):
    b = block(preset, module)
    return b.params[CAT.by_code[b.code].find_param(param_name).id]


class CatalogTests(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(len(CAT.effects), 305)
        for m in MODULES:
            self.assertTrue(CAT.in_module(m), m)
            CAT.default_effect(m)

    def test_param_ids_fit_a_block(self):
        for e in CAT.effects:
            ids = [p.id for p in e.params]
            self.assertEqual(len(ids), len(set(ids)), e.name)
            self.assertTrue(all(0 <= i < 15 for i in ids), e.name)

    def test_sync_knobs_point_at_a_switch(self):
        for e in CAT.effects:
            for p in e.params:
                if p.is_sync_knob:
                    sw = e.param_by_id(p.bind)
                    self.assertIsNotNone(sw, f"{e.name}.{p.name}")
                    self.assertTrue(sw.is_choice, f"{e.name}.{p.name}")

    def test_lookup_is_forgiving(self):
        self.assertEqual(CAT.find("AMP", "uk800").name, "UK 800")
        self.assertEqual(CAT.find("AMP", "MESS4 LD 3").name, "Mess4 LD 3")
        self.assertEqual(CAT.find("CAB", "dark twin").modules, ["CAB"])
        self.assertEqual(CAT.find("AMP", "dark twin").modules, ["AMP"])

    def test_lookup_suggests(self):
        with self.assertRaises(CatalogError) as ctx:
            CAT.find("DLY", "Digital Delay")
        self.assertIn("Digital Delay S", str(ctx.exception))
        with self.assertRaises(CatalogError) as ctx:
            CAT.find("AMP", "Green OD")
        self.assertIn("DST", str(ctx.exception))

    def test_amp_cab_codes_exist(self):
        for e in CAT.effects:
            if e.cab_code:
                self.assertIn(e.cab_code, CAT.by_code, e.name)


class ValueTests(unittest.TestCase):
    def setUp(self):
        self.pure = CAT.find("DLY", "Pure")
        self.cab = CAT.find("CAB", "UK GRN 2")

    def test_switch_words(self):
        sync = self.pure.find_param("Sync")
        self.assertEqual(encode_value(sync, True), 1.0)
        self.assertEqual(encode_value(sync, "off"), 0.0)
        with self.assertRaises(CatalogError):
            encode_value(sync, "maybe")

    def test_sync_notes(self):
        time = self.pure.find_param("Time")
        self.assertEqual(encode_value(time, "1/8D", sync_on=True), SYNC_NOTES.index("1/8D"))
        with self.assertRaises(CatalogError):
            encode_value(time, "1/8D", sync_on=False)
        self.assertEqual(encode_value(time, 450), 450)
        with self.assertRaises(CatalogError):
            encode_value(time, 5000)

    def test_cab_filters_off(self):
        self.assertEqual(encode_value(self.cab.find_param("Low Cut"), "OFF"), 19)
        self.assertEqual(encode_value(self.cab.find_param("High Cut"), False), 20001)
        self.assertEqual(encode_value(self.cab.find_param("High Cut"), "8kHz"), 8000)


class BinaryTests(unittest.TestCase):
    def test_layout_and_checksum(self):
        data, _, _ = compile_text("name: Test\n")
        self.assertEqual(len(data), USER_SIZE)
        self.assertEqual(data[:4], b"TSRP")
        self.assertEqual(data[0x10:0x14], b"2-PG")
        self.assertEqual(struct.unpack(">H", data[-2:])[0], checksum(data[:-2]))
        self.assertEqual(data[0x44:0x48], b"Test")

    def test_round_trip_bytes(self):
        for path in glob.glob(os.path.join(ROOT, "examples", "*.yaml")):
            with open(path, encoding="utf-8") as f:
                data, _, _ = compile_text(f.read())
            self.assertEqual(write_prst(read_prst(data)), data, path)

    def test_rejects_bad_files(self):
        data, _, _ = compile_text("name: Test\n")
        with self.assertRaises(PrstError):
            read_prst(data[:-10])
        broken = bytearray(data)
        broken[0x100] ^= 0xFF
        with self.assertRaises(PrstError):
            read_prst(bytes(broken))
        with self.assertRaises(PrstError):
            read_prst(b"XXXX" + data[4:])

    def test_slot_names(self):
        self.assertEqual(slot_name(0), "01-A")
        self.assertEqual(slot_name(255), "64-D")
        self.assertEqual(parse_slot_name("05-b"), 17)
        self.assertEqual(parse_slot_name("5B"), 17)
        with self.assertRaises(ValueError):
            parse_slot_name("65-A")


class SpecTests(unittest.TestCase):
    def test_init_patch(self):
        _, p, _ = compile_text("name: Init\n")
        for i, m in enumerate(MODULES):
            self.assertEqual(p.blocks[i].enabled, m == "VOL", m)
        self.assertEqual(CAT.by_code[block(p, "AMP").code].name, "Tweedy")

    def test_full_preset(self):
        text = """
name: Full
author: tester
slot: 10-C
tempo: 96
volume: 70
pan: L10
chain: [NR, PRE, WAH, DST, AMP, CAB, EQ, MOD, DLY, RVB, VOL]
fx_loop: {send: after DST, return: after AMP, mode: series}
slots:
  AMP: {effect: UK 800, Gain: 70, Middle: 40}
  CAB: {effect: auto}
  DLY: {effect: Pure, Sync: ON, Time: 1/4D}
  MOD: {effect: G-Chorus, enabled: false}
exp:
  exp1_a: [{module: AMP, param: Gain, min: 10, max: 90}]
ctrl:
  CTRL2: [DLY, FX_LOOP]
"""
        _, p, w = compile_text(text)
        self.assertEqual(w, [])
        self.assertEqual((p.author, p.tempo, p.volume, p.pan), ("tester", 96, 70, -10))
        self.assertEqual(slot_name(p.patch_slot), "10-C")
        self.assertEqual(p.chain[0], MODULES.index("NR"))
        self.assertEqual((p.fx_send, p.fx_return, p.fx_mode), (4, 5, 1))
        self.assertEqual(value(p, "AMP", "Gain"), 70)
        self.assertEqual(CAT.by_code[block(p, "CAB").code].name, "UK GRN 2")
        self.assertEqual(value(p, "DLY", "Time"), SYNC_NOTES.index("1/4D"))
        self.assertFalse(block(p, "MOD").enabled)
        self.assertEqual(p.exp[0].block, MODULES.index("AMP"))
        self.assertEqual((p.exp[0].min, p.exp[0].max), (10, 90))
        self.assertEqual(p.ctrl[1].mask, (1 << MODULES.index("DLY")) | (1 << 11))

    def test_errors_are_collected(self):
        text = """
name: Broken
chain: [PRE]
slots:
  AMP: {effect: Clean Combo}
  DLY: {effect: Pure, Time: 1/8}
  RVB: {effect: Hall, Mix: 150, Wetness: 3}
  FOO: {effect: x}
"""
        with self.assertRaises(SpecError) as ctx:
            compile_text(text)
        msgs = "\n".join(ctx.exception.errors)
        for needle in ("chain", "slots.AMP", "slots.DLY.Time", "slots.RVB.Mix", "slots.RVB.Wetness", "FOO"):
            self.assertIn(needle, msgs)

    def test_aliases_warn(self):
        _, p, w = compile_text("name: A\nslots:\n  AMP: {effect: UK 800, Mid: 30}\n")
        self.assertEqual(value(p, "AMP", "Middle"), 30)
        self.assertTrue(any("Middle" in x for x in w))

    def test_long_name_is_cut(self):
        _, p, w = compile_text("name: This name is far too long\n")
        self.assertEqual(p.name, "This name is far")
        self.assertTrue(w)

    def test_yaml_syntax_error(self):
        with self.assertRaises(SpecError) as ctx:
            compile_text("name: [unclosed\n")
        self.assertIn("line", ctx.exception.errors[0])

    def test_decompile_compile_is_stable(self):
        for path in glob.glob(os.path.join(ROOT, "examples", "*.yaml")):
            with open(path, encoding="utf-8") as f:
                data, p1, _ = compile_text(f.read())
            yaml_text = gp200.decompile_prst(data)
            data2, p2, warnings = compile_text(yaml_text)
            self.assertEqual(warnings, [], path)
            p2.timestamp = read_prst(data).timestamp  # same clock value as the first file
            self.assertEqual(write_prst(p2), data, path)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)

    def test_compile_and_decompile(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "lead.prst")
            r = self.run_cli("prst_compile.py", "examples/lead.yaml", "-o", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.path.getsize(out), USER_SIZE)
            r = self.run_cli("prst_decompile.py", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Solo100 LD", r.stdout)

    def test_compile_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.yaml")
            with open(bad, "w") as f:
                f.write("name: X\nslots:\n  AMP: {effect: Nope}\n")
            r = self.run_cli("prst_compile.py", bad, "-o", os.path.join(tmp, "x.prst"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("slots.AMP", r.stderr)


@unittest.skipUnless(os.environ.get("GP200_SAMPLES"), "set GP200_SAMPLES to a folder of real .prst files")
class SampleTests(unittest.TestCase):
    def test_real_files(self):
        files = [f for f in glob.glob(os.path.join(os.environ["GP200_SAMPLES"], "**", "*.prst"), recursive=True)
                 if os.path.getsize(f) in (1176, 1224)]
        self.assertTrue(files)
        for f in files:
            with open(f, "rb") as fh:
                data = fh.read()
            p = read_prst(data)
            if len(data) == USER_SIZE and struct.unpack_from("<I", data, 0x20)[0] == 0x28:
                self.assertEqual(write_prst(p), data, f)
            yaml_text = gp200.decompile_prst(data)
            compile_text(yaml_text)  # must not raise


if __name__ == "__main__":
    unittest.main()
