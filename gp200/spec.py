"""Convert between the human-readable YAML preset format and Preset objects.

YAML format (every key except `name` and `slots` is optional):

    name: Lead Shimmer          # up to 16 ASCII characters
    author: me                  # up to 16
    note: free text             # up to 40
    slot: 05-B                  # patch slot the file targets (01-A .. 64-D)
    tempo: 120                  # BPM
    volume: 50                  # patch volume 0..100
    pan: 0                      # -50..50, or L20 / C / R15
    chain: [PRE, WAH, DST, AMP, NR, CAB, EQ, MOD, DLY, RVB, VOL]
    fx_loop: {send: after AMP, return: after AMP, mode: parallel}
    slots:
      AMP:
        effect: UK 800
        enabled: true           # default: true for every slot listed here
        Gain: 65
        Middle: 55
      CAB:
        effect: auto            # the cabinet that matches the AMP (Auto CAB Match)
      DLY:
        effect: Pure
        Sync: ON
        Time: 1/8D              # note values when Sync is ON
    exp:
      exp1_a: [{module: VOL, param: Volume, min: 0, max: 100}]
      exp1_b: [{module: WAH, param: Position}]
      exp2: []
    ctrl:
      CTRL1: [DST, DLY]         # modules (or FX_LOOP) the footswitch toggles

Modules not listed under `slots` stay as in the pedal's init patch: its default
effect, switched off (VOL stays on).
Parameters not listed keep the effect's default value.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import yaml

from .catalog import Catalog, CatalogError, Effect, Param, decode_value, encode_value, fit_stored, norm
from .prst import (BLOCK_COUNT, FX_LOOP_BIT, MODULES, Block, CtrlAssign, ExpAssign, Preset,
                   default_exp, parse_slot_name, slot_name)

EXP_PAGES = {"exp1_a": 0, "exp1_b": 1, "exp2": 2}
TOP_KEYS = {"name", "author", "note", "slot", "tempo", "bpm", "volume", "pan", "style", "chain",
            "fx_loop", "slots", "exp", "ctrl", "advanced", "version"}
SLOT_META_KEYS = {"effect", "enabled", "params", "hidden_params"}


class SpecError(ValueError):
    """Raised with every problem found in a YAML preset."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


# ── YAML -> Preset ────────────────────────────────────────────────────────────

class _Builder:
    def __init__(self, catalog: Catalog):
        self.cat = catalog
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}" if where else msg)

    def text(self, doc: dict, key: str, limit: int, default: str = "") -> str:
        value = doc.get(key, default)
        if value is None:
            return ""
        value = str(value)
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            self.err(key, f"'{value}' has non-ASCII characters; the pedal only shows A-Z, a-z, 0-9 and symbols")
            return ""
        if len(value) > limit:
            self.warnings.append(f"{key}: '{value}' is longer than {limit} characters; cut to '{value[:limit]}'")
            value = value[:limit]
        return value

    def integer(self, doc: dict, key: str, lo: int, hi: int, default: int) -> int:
        value = doc.get(key, default)
        try:
            number = float(value)
            if not number.is_integer():
                raise ValueError
            number = int(number)
        except (TypeError, ValueError):
            self.err(key, f"'{value}' must be a whole number between {lo} and {hi}")
            return default
        if not lo <= number <= hi:
            self.err(key, f"{number} is out of range ({lo}..{hi})")
            return default
        return number

    def module_name(self, where: str, value) -> Optional[str]:
        key = str(value).strip().upper()
        if key in MODULES:
            return key
        self.err(where, f"'{value}' is not a module. Modules: {', '.join(MODULES)}")
        return None

    # -- sections -------------------------------------------------------------

    def build(self, doc: dict) -> Preset:
        if not isinstance(doc, dict):
            raise SpecError(["the document must be a YAML mapping (key: value)"])
        for key in doc:
            if key not in TOP_KEYS:
                self.warnings.append(f"unknown key '{key}' ignored")

        p = Preset()
        p.name = self.text(doc, "name", 16, "Untitled") or "Untitled"
        p.author = self.text(doc, "author", 16)
        p.note = self.text(doc, "note", 40)
        if doc.get("slot") is not None:
            try:
                p.patch_slot = parse_slot_name(doc["slot"])
            except ValueError as e:
                self.err("slot", str(e))
        p.tempo = self.integer(doc, "tempo" if "tempo" in doc else "bpm", 40, 250, 120)
        p.volume = self.integer(doc, "volume", 0, 100, 50)
        p.pan = self.pan(doc.get("pan", 0))
        p.style = self.integer(doc, "style", 0, 0xFFFF, 0)

        self.chain(doc.get("chain"), p)
        self.slots(doc.get("slots"), p)
        self.fx_loop(doc.get("fx_loop"), p)
        self.exp(doc.get("exp"), p)
        self.ctrl(doc.get("ctrl"), p)
        self.advanced(doc.get("advanced"), p)
        if self.errors:
            raise SpecError(self.errors)
        return p

    def pan(self, value) -> int:
        text = str(value).strip().upper()
        try:
            if text in ("C", "CENTER", "0"):
                number = 0
            elif text[:1] in ("L", "R") and text[1:].isdigit():
                number = int(text[1:]) * (-1 if text[0] == "L" else 1)
            else:
                number = int(float(value))
        except (TypeError, ValueError):
            self.err("pan", f"'{value}' must be -50..50, C, L1..L50 or R1..R50")
            return 0
        if not -50 <= number <= 50:
            self.err("pan", f"{value} is out of range (L50..R50)")
            return 0
        return number

    def chain(self, value, p: Preset) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            self.err("chain", "must be a list of the 11 modules in playing order")
            return
        mods = [self.module_name("chain", v) for v in value]
        if None in mods:
            return
        if sorted(mods) != sorted(MODULES):
            missing = [m for m in MODULES if m not in mods]
            dup = sorted({m for m in mods if mods.count(m) > 1})
            detail = "; ".join(x for x in (f"missing {', '.join(missing)}" if missing else "",
                                           f"repeated {', '.join(dup)}" if dup else "") if x)
            self.err("chain", f"must list each of the 11 modules exactly once ({detail})")
            return
        p.chain = [MODULES.index(m) for m in mods]

    def slots(self, value, p: Preset) -> None:
        listed: Dict[str, dict] = {}
        if value is not None and not isinstance(value, dict):
            self.err("slots", "must map module names (PRE, AMP, ...) to their settings")
            value = {}
        for raw_key, body in (value or {}).items():
            module = self.module_name(f"slots.{raw_key}", raw_key)
            if module is None:
                continue
            if module in listed:
                self.err(f"slots.{raw_key}", f"{module} is listed twice")
                continue
            if body is None:
                body = {}
            if isinstance(body, str):
                body = {"effect": body}
            if not isinstance(body, dict):
                self.err(f"slots.{module}", "must be a mapping with at least `effect:`")
                continue
            listed[module] = body

        # AMP first so `CAB: {effect: auto}` can follow it.
        order = sorted(range(BLOCK_COUNT), key=lambda i: (MODULES[i] != "AMP", i))
        for i in order:
            module = MODULES[i]
            if module in listed:
                p.blocks[i] = self.slot(module, listed[module], p) or self.default_block(i)
            else:
                p.blocks[i] = self.default_block(i)

    def default_block(self, i: int) -> Block:
        """A module the YAML doesn't mention: as in the pedal's init patch (only VOL is on)."""
        effect = self.cat.default_effect(MODULES[i])
        return Block(i, effect.code, MODULES[i] == "VOL", effect.defaults())

    def slot(self, module: str, body: dict, p: Preset) -> Optional[Block]:
        where = f"slots.{module}"
        i = MODULES.index(module)
        name = body.get("effect")
        if name is None:
            self.err(where, "missing `effect:` (the effect name, e.g. effect: UK 800)")
            return None
        if module == "CAB" and norm(name) == "auto":
            amp = self.cat.by_code.get(p.blocks[MODULES.index("AMP")].code)
            if not amp or not amp.cab_code or amp.cab_code not in self.cat.by_code:
                self.err(where, "effect: auto needs an AMP that has a matching cabinet")
                return None
            effect = self.cat.by_code[amp.cab_code]
        else:
            try:
                effect = self.cat.find(module, str(name))
            except CatalogError as e:
                self.err(where, str(e))
                return None

        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = norm(enabled) in ("on", "true", "yes", "1")

        given: Dict[str, object] = {k: v for k, v in body.items() if k not in SLOT_META_KEYS}
        if isinstance(body.get("params"), dict):
            given.update(body["params"])

        resolved: List[Tuple[Param, object, str]] = []
        for key, val in given.items():
            try:
                param = effect.find_param(str(key))
                if norm(param.name) != norm(key):
                    self.warnings.append(f"{where}.{key}: read as '{param.name}' ({effect.name} has no '{key}')")
                resolved.append((param, val, str(key)))
            except CatalogError as e:
                self.err(f"{where}.{key}", str(e))

        values = effect.defaults()
        # Choices first: a synced knob's meaning depends on its Sync switch.
        for param, val, key in sorted(resolved, key=lambda r: not r[0].is_choice):
            sync_on = False
            if param.is_sync_knob:
                sync_on = values[param.bind] >= 0.5
            try:
                values[param.id] = encode_value(param, val, sync_on)
            except CatalogError as e:
                self.err(f"{where}.{key}", str(e))
        hidden = body.get("hidden_params")
        if hidden is not None:
            if not isinstance(hidden, dict):
                self.err(f"{where}.hidden_params", "must map positions to numbers, e.g. {0: 1, 2: 30}")
            else:
                for pos, val in hidden.items():
                    try:
                        pos, val = int(pos), float(val)
                    except (TypeError, ValueError):
                        self.err(f"{where}.hidden_params", f"{pos}: {val} must be a number")
                        continue
                    if pos not in effect.hidden_ids():
                        self.err(f"{where}.hidden_params", f"position {pos} is not a hidden parameter of {effect.name}")
                        continue
                    values[pos] = val
        # A synced knob left at its ms/Hz default must become a note index.
        for param in effect.params:
            if param.is_sync_knob and values[param.bind] >= 0.5 and not (
                    values[param.id].is_integer() and 0 <= values[param.id] <= 10):
                values[param.id] = 4.0  # 1/4, the pedal's default division
        return Block(i, effect.code, bool(enabled), values)

    def position(self, where: str, value, p: Preset, default: int) -> int:
        if value is None:
            return default
        text = str(value).strip()
        if text.lower().startswith("after"):
            module = self.module_name(where, text[5:].strip())
            if module is None:
                return default
            return p.chain.index(MODULES.index(module)) + 1
        try:
            number = int(text)
        except ValueError:
            self.err(where, f"'{value}' must be 1..11 or 'after <MODULE>'")
            return default
        if not 1 <= number <= 11:
            self.err(where, f"{number} is out of range (1..11)")
            return default
        return number

    def fx_loop(self, value, p: Preset) -> None:
        if value is None:
            return
        if not isinstance(value, dict):
            self.err("fx_loop", "must be a mapping: {send: ..., return: ..., mode: parallel|series}")
            return
        p.fx_send = self.position("fx_loop.send", value.get("send"), p, p.fx_send)
        p.fx_return = self.position("fx_loop.return", value.get("return"), p, p.fx_return)
        if p.fx_return < p.fx_send:
            self.err("fx_loop", "return must not come before send (the pedal forbids it to avoid feedback)")
        mode = norm(value.get("mode", "parallel"))
        if mode in ("parallel", "par", "0"):
            p.fx_mode = 0
        elif mode in ("series", "serial", "ser", "1"):
            p.fx_mode = 1
        else:
            self.err("fx_loop.mode", f"'{value.get('mode')}' must be parallel or series")
        p.send_level = self.integer(value, "send_level", 0, 0xFFFF, p.send_level)
        p.return_level = self.integer(value, "return_level", 0, 0xFFFF, p.return_level)

    def exp(self, value, p: Preset) -> None:
        if value is None:
            return
        if not isinstance(value, dict):
            self.err("exp", "must be a mapping with exp1_a / exp1_b / exp2 lists")
            return
        by_slot = {(a.page, a.item): a for a in p.exp}
        for raw_key, targets in value.items():
            key = norm(raw_key).replace("_", "")
            page = {"exp1a": 0, "exp1": 0, "a": 0, "exp1b": 1, "b": 1, "exp2": 2}.get(key)
            if page is None:
                self.err(f"exp.{raw_key}", "use exp1_a, exp1_b or exp2")
                continue
            targets = targets or []
            if isinstance(targets, dict):
                targets = [targets]
            if not isinstance(targets, list) or len(targets) > 3:
                self.err(f"exp.{raw_key}", "must be a list of up to 3 targets")
                continue
            for item in range(3):
                by_slot[(page, item)] = ExpAssign(page, item)
            for item, t in enumerate(targets):
                a = self.exp_target(f"exp.{raw_key}[{item}]", t, p, page, item)
                if a:
                    by_slot[(page, item)] = a
        p.exp = [by_slot[(pg, it)] for pg in range(3) for it in range(3)]

    def exp_target(self, where: str, t, p: Preset, page: int, item: int) -> Optional[ExpAssign]:
        if not isinstance(t, dict):
            self.err(where, "must be a mapping like {module: VOL, param: Volume, min: 0, max: 100}")
            return None
        if "target" in t:  # raw special target (e.g. patch volume), from decompiled files
            try:
                return ExpAssign(page, item, int(t["target"]), int(t.get("param_index", 0)),
                                 float(t.get("min", 0)), float(t.get("max", 100)))
            except (TypeError, ValueError):
                self.err(where, "target / param_index / min / max must be numbers")
                return None
        module = self.module_name(f"{where}.module", t.get("module", ""))
        if module is None:
            return None
        effect = self.cat.by_code.get(p.blocks[MODULES.index(module)].code)
        try:
            param = effect.find_param(str(t.get("param", "")))
        except CatalogError as e:
            self.err(f"{where}.param", str(e))
            return None
        lo = t.get("min", param.min if not param.is_choice else param.min)
        hi = t.get("max", param.max if not param.is_choice else param.max)
        try:
            lo_v = encode_value(param, lo)
            hi_v = encode_value(param, hi)
        except CatalogError as e:
            self.err(where, str(e))
            return None
        return ExpAssign(page, item, MODULES.index(module), param.id, lo_v, hi_v)

    def ctrl(self, value, p: Preset) -> None:
        if value is None:
            return
        if not isinstance(value, dict):
            self.err("ctrl", "must map CTRL1..CTRL8 to lists of modules")
            return
        for raw_key, targets in value.items():
            digits = "".join(ch for ch in str(raw_key) if ch.isdigit())
            if not digits or not 1 <= int(digits) <= 8:
                self.err(f"ctrl.{raw_key}", "use CTRL1 .. CTRL8")
                continue
            idx = int(digits) - 1
            if isinstance(targets, str):
                targets = [targets]
            mask = 0
            for t in targets or []:
                if norm(t) in ("fxloop", "loop", "fx"):
                    mask |= 1 << FX_LOOP_BIT
                    continue
                module = self.module_name(f"ctrl.{raw_key}", t)
                if module:
                    mask |= 1 << MODULES.index(module)
            p.ctrl[idx].mask = mask

    def advanced(self, value, p: Preset) -> None:
        if not value:
            return
        knobs = value.get("knob_records")
        if knobs:
            try:
                p.knob_records = [bytes.fromhex(str(k)) for k in knobs][:3]
                assert all(len(k) == 4 for k in p.knob_records)
            except (ValueError, AssertionError):
                self.err("advanced.knob_records", "must be three 8-digit hex strings")


def build_preset(doc: dict, catalog: Catalog) -> Tuple[Preset, List[str]]:
    """Build a Preset from a parsed YAML document. Returns (preset, warnings)."""
    b = _Builder(catalog)
    preset = b.build(doc)
    return preset, b.warnings


def load_yaml_text(text: str) -> dict:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise SpecError([f"YAML syntax error{where}: {getattr(e, 'problem', None) or e}"])
    if doc is None:
        raise SpecError(["the YAML document is empty"])
    return doc


# ── Preset -> YAML ────────────────────────────────────────────────────────────

def _scalar(value) -> str:
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10_000).strip()
    if text.endswith("\n..."):
        text = text[:-4].strip()
    return text.replace("\n...", "")


def _key(text: str) -> str:
    return _scalar(str(text))


def preset_to_yaml(p: Preset, catalog: Catalog, comments: bool = True) -> str:
    out: List[str] = []
    w = out.append
    w(f"name: {_scalar(p.name)}")
    if p.author:
        w(f"author: {_scalar(p.author)}")
    if p.note:
        w(f"note: {_scalar(p.note)}")
    if p.patch_slot is not None:
        w(f"slot: {slot_name(p.patch_slot)}")
    w(f"tempo: {p.tempo}")
    w(f"volume: {p.volume}")
    if p.pan:
        w(f"pan: {'L' if p.pan < 0 else 'R'}{abs(p.pan)}")
    if p.style:
        w(f"style: {p.style}")
    if p.chain != list(range(BLOCK_COUNT)):
        w(f"chain: [{', '.join(MODULES[i] for i in p.chain)}]")

    loop = []
    if (p.fx_send, p.fx_return) != (4, 4) or p.fx_mode or p.send_level or p.return_level:
        def pos(n: int) -> str:
            return f"after {MODULES[p.chain[n - 1]]}" if 1 <= n <= BLOCK_COUNT else str(n)
        loop = [f"send: {pos(p.fx_send)}", f"return: {pos(p.fx_return)}",
                f"mode: {'series' if p.fx_mode else 'parallel'}"]
        if p.send_level:
            loop.append(f"send_level: {p.send_level}")
        if p.return_level:
            loop.append(f"return_level: {p.return_level}")
        w(f"fx_loop: {{{', '.join(loop)}}}")

    w("")
    w("slots:")
    for i in p.chain:
        b = p.blocks[i]
        effect = catalog.by_code.get(b.code)
        w(f"  {MODULES[i]}:")
        if effect is None:
            w(f"    effect: {b.code:#010x}   # unknown effect code")
            w(f"    enabled: {'true' if b.enabled else 'false'}")
            continue
        w(f"    effect: {_scalar(effect.name)}")
        w(f"    enabled: {'true' if b.enabled else 'false'}")
        for param in effect.params:
            sync_on = param.is_sync_knob and b.params[param.bind] >= 0.5
            stored = b.params[param.id]
            fitted = fit_stored(param, stored, sync_on)
            value = decode_value(param, fitted, sync_on)
            shown = value if value in ("ON", "OFF") else _scalar(value)  # bare ON/OFF reads as a YAML bool
            line = f"    {_key(param.name)}: {shown}"
            note = param.describe_range()
            if abs(fitted - stored) > 1e-6:
                note += f"  (the file had {round(stored, 4):g}, which this firmware does not accept)"
            if comments or abs(fitted - stored) > 1e-6:
                line = f"{line:<34}# {note}"
            w(line.rstrip())

        hidden = {pos: b.params[pos] for pos in effect.hidden_ids()}
        if any(abs(v - effect.defaults()[pos]) > 1e-6 for pos, v in hidden.items()):
            items = ", ".join(f"{pos}: {_scalar(round(v, 4))}" for pos, v in hidden.items())
            w(f"    hidden_params: {{{items}}}   # values the editor hides, kept as-is")

    exp_lines = _exp_yaml(p, catalog)
    if exp_lines:
        w("")
        out.extend(exp_lines)

    ctrl = [(c.index, c.mask) for c in p.ctrl if c.mask]
    if ctrl:
        w("")
        w("ctrl:")
        for idx, mask in ctrl:
            names = [MODULES[i] for i in range(BLOCK_COUNT) if mask & (1 << i)]
            if mask & (1 << FX_LOOP_BIT):
                names.append("FX_LOOP")
            w(f"  CTRL{idx + 1}: [{', '.join(names)}]")

    from .prst import DEFAULT_KNOB_RECORDS
    if list(p.knob_records) != DEFAULT_KNOB_RECORDS:
        w("")
        w("advanced:")
        w(f"  knob_records: [{', '.join(repr(k.hex()) for k in p.knob_records)}]   # quick-access knob data, kept as-is")
    return "\n".join(out) + "\n"


def _exp_yaml(p: Preset, catalog: Catalog) -> List[str]:
    defaults = {(a.page, a.item): a for a in default_exp()}
    current = {(a.page, a.item): a for a in p.exp}
    lines: List[str] = []
    for page_name, page in EXP_PAGES.items():
        page_assigns = [current.get((page, it)) or ExpAssign(page, it) for it in range(3)]
        if all(_same_exp(a, defaults[(page, a.item)]) for a in page_assigns):
            continue
        items = []
        for a in page_assigns:
            if a.block is None:
                continue
            if a.block < BLOCK_COUNT:
                effect = catalog.by_code.get(p.blocks[a.block].code)
                param = effect.param_by_id(a.param) if effect else None
                if param:
                    items.append(f"{{module: {MODULES[a.block]}, param: {_scalar(param.name)}, "
                                 f"min: {_scalar(decode_value(param, a.min))}, max: {_scalar(decode_value(param, a.max))}}}")
                    continue
            items.append(f"{{target: {a.block}, param_index: {a.param}, min: {_scalar(round(a.min, 4))}, "
                         f"max: {_scalar(round(a.max, 4))}}}")
        lines.append(f"  {page_name}: [{', '.join(items)}]")
    return ["exp:"] + lines if lines else []


def _same_exp(a: ExpAssign, b: ExpAssign) -> bool:
    if a.block is None and b.block is None:
        return True
    return (a.block, a.param, round(a.min, 4), round(a.max, 4)) == (b.block, b.param, round(b.min, 4), round(b.max, 4))
