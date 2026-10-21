"""The GP-200 effect catalog: every effect, its module(s) and its parameters.

Data comes from gp200_catalog.json, generated from Valeton's algorithm.xml by
tools/extract_catalog.py. A parameter's "id" is its index among the 15 floats
of an effect block in the .prst file.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .prst import MODULES

CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gp200_catalog.json")

# Note values a tempo-synced Rate/Time knob selects; the stored value is the index.
SYNC_NOTES = ["1/1", "1/2", "1/2D", "1/2T", "1/4", "1/4D", "1/4T", "1/8", "1/8D", "1/8T", "1/16"]

# The effect each module holds in a fresh patch on the pedal (firmware 1.8).
MODULE_DEFAULTS = {
    "PRE": "COMP", "WAH": "V-Wah", "DST": "Green OD", "AMP": "Tweedy", "NR": "Gate 1",
    "CAB": "SUP ZEP", "EQ": "Guitar EQ 1", "MOD": "G-Chorus", "DLY": "BBD Delay S",
    "RVB": "Room", "VOL": "Volume",
}

# Values the pedal writes into parameter positions algorithm.xml doesn't list.
# Every CAB block carries four of them (older firmware exposed them as mic
# settings); a fresh patch on firmware 1.8 stores 1, 30, 15, 60.
HIDDEN_DEFAULTS = {"CAB": {0: 1.0, 2: 30.0, 3: 15.0, 4: 60.0}}

# Common names people write for a parameter, tried only when the effect has
# no parameter by the written name but does have the target.
PARAM_ALIASES = {
    "mid": "middle", "mids": "middle", "midrange": "middle",
    "drive": "gain", "level": "volume", "vol": "volume", "output": "volume",
    "predelay": "pre delay", "fb": "feedback", "speed": "rate",
    "lowcut": "low cut", "highcut": "high cut", "hicut": "high cut",
    "treb": "treble", "pres": "presence", "thresh": "threshold",
}

ON_WORDS = {"on", "true", "yes", "1"}
OFF_WORDS = {"off", "false", "no", "0", "none"}


def norm(text: str) -> str:
    """Case-, space- and punctuation-insensitive key: 'Mess4 LD-3' -> 'mess4ld3'."""
    return re.sub(r"[^a-z0-9+#%/]", "", str(text).lower())


class CatalogError(ValueError):
    pass


@dataclass
class Param:
    name: str
    kind: str  # knob | slider | switch | combo
    id: int
    default: float
    min: float
    max: float
    step: float = 1
    unit: str = ""
    min_is_off: bool = False
    value_type: int = 0
    bind: Optional[int] = None  # for a synced knob: the id of its Sync switch
    options: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def is_choice(self) -> bool:
        return self.kind in ("switch", "combo")

    @property
    def is_sync_knob(self) -> bool:
        return not self.is_choice and self.value_type == 1 and self.bind is not None

    @property
    def off_value(self) -> Optional[float]:
        """For Hz filters whose end position means OFF (CAB Low/High Cut)."""
        if not self.min_is_off:
            return None
        return self.max if self.value_type == 2 else self.min

    def option_name(self, value: float) -> Optional[str]:
        for name, v in self.options:
            if abs(v - value) < 1e-6:
                return name
        return None

    def describe_range(self) -> str:
        if self.is_choice:
            return " | ".join(n for n, _ in self.options)
        unit = f" {self.unit}" if self.unit else ""
        text = f"{_fmt(self.min)}..{_fmt(self.max)}{unit}"
        if self.off_value is not None:
            text += f" (OFF = {_fmt(self.off_value)})"
        if self.is_sync_knob:
            text += f", or a note value {SYNC_NOTES[0]}..{SYNC_NOTES[-1]} when Sync is ON"
        return text


@dataclass
class Effect:
    code: int
    name: str
    modules: List[str]
    params: List[Param]
    cab_code: Optional[int] = None

    def param_by_id(self, pid: int) -> Optional[Param]:
        for p in self.params:
            if p.id == pid:
                return p
        return None

    def find_param(self, name: str) -> Param:
        key = norm(name)
        for p in self.params:
            if norm(p.name) == key:
                return p
        alias = PARAM_ALIASES.get(key)
        if alias:
            for p in self.params:
                if norm(p.name) == norm(alias):
                    return p
        names = [p.name for p in self.params]
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise CatalogError(
            f"'{self.name}' has no parameter '{name}'.{hint} "
            f"Valid parameters: {', '.join(names) or '(none)'}"
        )

    def hidden_ids(self) -> List[int]:
        """Positions the pedal stores but the editor doesn't show (CAB mic settings)."""
        return sorted(HIDDEN_DEFAULTS.get(self.modules[0], {}))

    def defaults(self) -> List[float]:
        values = [0.0] * 15
        for pos, v in HIDDEN_DEFAULTS.get(self.modules[0], {}).items():
            values[pos] = v
        for p in self.params:
            values[p.id] = float(p.default)
        return values


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


class Catalog:
    def __init__(self, data: dict):
        self.effects: List[Effect] = []
        for e in data["effects"]:
            params = [
                Param(
                    name=p["name"], kind=p["kind"], id=p["id"], default=p.get("default") or 0,
                    min=p.get("min", 0), max=p.get("max", 0), step=p.get("step", 1) or 1,
                    unit=p.get("unit", ""), min_is_off=p.get("min_is_off", False),
                    value_type=p.get("value_type", 0), bind=p.get("bind"),
                    options=[(o["name"], o["value"]) for o in p.get("options", [])],
                )
                for p in e["params"]
            ]
            self.effects.append(Effect(e["code"], e["name"], e["modules"], params, e.get("cab_code")))
        self.by_code: Dict[int, Effect] = {e.code: e for e in self.effects}

    @classmethod
    def load(cls, path: str = CATALOG_PATH) -> "Catalog":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def in_module(self, module: str) -> List[Effect]:
        return [e for e in self.effects if module in e.modules]

    def find(self, module: str, name: str) -> Effect:
        """Look up an effect by name inside a module (PRE, DST, AMP, ...)."""
        if module not in MODULES:
            raise CatalogError(f"unknown module '{module}'. Modules: {', '.join(MODULES)}")
        key = norm(name)
        for e in self.in_module(module):
            if norm(e.name) == key:
                return e
        elsewhere = [e for e in self.effects if norm(e.name) == key]
        if elsewhere:
            mods = ", ".join(sorted({m for e in elsewhere for m in e.modules}))
            raise CatalogError(f"'{name}' is not a {module} effect (it belongs to {mods})")
        names = [e.name for e in self.in_module(module)]
        close = difflib.get_close_matches(name, names, n=4, cutoff=0.45)
        if not close:  # also try a substring match: "Twin" -> "Dark Twin", "Silver Twin"
            close = [n for n in names if key and key in norm(n)][:4]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise CatalogError(f"unknown {module} effect '{name}'.{hint} "
                           f"Run `prst_info.py effects {module}` to list them.")

    def default_effect(self, module: str) -> Effect:
        return self.find(module, MODULE_DEFAULTS[module])


# ── value conversion ──────────────────────────────────────────────────────────

def encode_value(param: Param, value, sync_on: bool = False) -> float:
    """Turn a YAML value (number, ON/OFF, option name, note) into the stored float."""
    if isinstance(value, bool):
        value = "ON" if value else "OFF"

    if param.is_choice:
        if isinstance(value, (int, float)):
            for _, v in param.options:
                if abs(v - value) < 1e-6:
                    return float(v)
            raise CatalogError(f"{param.name}: {value} is not one of {param.describe_range()}")
        key = norm(value)
        for name, v in param.options:
            if norm(name) == key:
                return float(v)
        words = {norm(n): v for n, v in param.options}
        if key in ON_WORDS and len(param.options) == 2 and "on" in words:
            return float(words["on"])
        if key in OFF_WORDS and len(param.options) == 2 and "off" in words:
            return float(words["off"])
        raise CatalogError(f"{param.name}: '{value}' is not one of {param.describe_range()}")

    # knob / slider
    if isinstance(value, str):
        text = value.strip()
        if param.is_sync_knob and norm(text) in [norm(n) for n in SYNC_NOTES]:
            if not sync_on:
                raise CatalogError(f"{param.name}: note value '{text}' needs the Sync switch ON")
            return float([norm(n) for n in SYNC_NOTES].index(norm(text)))
        if norm(text) in OFF_WORDS and param.off_value is not None:
            return float(param.off_value)
        m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z%]*)", text)
        if not m:
            raise CatalogError(f"{param.name}: '{value}' is not a number (range {param.describe_range()})")
        value = float(m.group(1))
        if m.group(2).lower() == "khz":
            value *= 1000

    value = float(value)
    if param.is_sync_knob and sync_on:
        if value.is_integer() and 0 <= value < len(SYNC_NOTES):
            return value
        raise CatalogError(f"{param.name}: with Sync ON use a note value ({', '.join(SYNC_NOTES)})")
    if not (param.min - 1e-6 <= value <= param.max + 1e-6):
        raise CatalogError(f"{param.name}: {_fmt(value)} is out of range ({param.describe_range()})")
    return round(value, 4)


def fit_stored(param: Param, stored: float, sync_on: bool = False) -> float:
    """Bring a stored value the current firmware wouldn't accept back into range.

    Old firmware and hand-made files sometimes hold values the catalog rejects:
    a menu between two positions (an EXP sweep saved mid-way) or a note index on
    a knob that has since lost its Sync switch. Snap to the nearest valid value.
    """
    if param.is_choice:
        if param.option_name(stored) is not None or not param.options:
            return stored
        return float(min((abs(v - stored), v) for _, v in param.options)[1])
    if param.is_sync_knob and sync_on:
        return float(min(len(SYNC_NOTES) - 1, max(0, round(stored))))
    return float(min(param.max, max(param.min, stored)))


def decode_value(param: Param, stored: float, sync_on: bool = False):
    """Turn a stored float back into the friendliest YAML value."""
    if param.is_choice:
        name = param.option_name(stored)
        return name if name is not None else _num(stored)
    if param.is_sync_knob and sync_on and float(stored).is_integer() and 0 <= stored < len(SYNC_NOTES):
        return SYNC_NOTES[int(stored)]
    if param.off_value is not None and abs(stored - param.off_value) < 1e-6:
        return "OFF"
    return _num(stored)


def _num(v: float):
    v = round(float(v), 4)
    return int(v) if v.is_integer() else v
