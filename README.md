# gp200-toolkit

Compile human-readable YAML presets into Valeton GP-200 `.prst` files, and
decompile `.prst` files back to YAML. Pure Python 3.8+ plus PyYAML.

This is the compiler behind [gp200-preset-builder](https://github.com/fisgonone/gp200-preset-builder),
but it works on its own from the command line.

- **All 305 effects** of firmware 1.8, with names, ranges, defaults, units and
  menu options taken from Valeton's own editor data (`algorithm.xml`).
- **Byte-exact output.** A preset built from scratch is identical to what the
  pedal exports; every real `.prst` tested (100 factory presets + device
  exports) reads and writes back unchanged.
- **Reads old and new files**: 1224-byte presets (firmware 1.8) and the
  1176-byte factory presets of older firmware. Always writes 1224 bytes.
- **Covers the whole patch**: signal-chain order, module on/off, tempo, patch
  volume and pan, FX loop, EXP pedal assignments and CTRL footswitches.
- **Helpful errors**: every problem is reported at once, with its location and
  a suggestion (`unknown DLY effect 'Digital Delay'. Did you mean: Digital Delay S?`).

## Usage

```bash
pip install pyyaml

python prst_compile.py examples/lead.yaml -o lead.prst
python prst_compile.py my.yaml --check          # validate only
python prst_decompile.py lead.prst -o lead.yaml

python prst_info.py modules                     # modules and effect counts
python prst_info.py effects AMP                 # every amp
python prst_info.py effect "UK 800"             # parameters, ranges, defaults
python prst_info.py search twin                 # find by part of a name
python prst_info.py inspect lead.prst           # quick look at a preset
```

Load the `.prst` onto the pedal with Valeton's GP-200 editor (import patch).

## YAML format

```yaml
name: Lead Shimmer          # up to 16 ASCII characters
author: me                  # optional, up to 16
slot: 05-B                  # optional target slot 01-A .. 64-D
tempo: 120                  # BPM (40..250)
volume: 55                  # patch volume 0..100
pan: C                      # -50..50, or C / L20 / R15
chain: [PRE, WAH, DST, AMP, NR, CAB, EQ, MOD, DLY, RVB, VOL]   # optional reorder
fx_loop: {send: after AMP, return: after AMP, mode: parallel}  # or series

slots:
  DST:
    effect: Green OD
    Gain: 20
    Tone: 60
    Volume: 75
  AMP:
    effect: Solo100 LD
    Gain: 60
  CAB:
    effect: auto            # the cabinet matching the AMP (like Auto CAB Match)
  DLY:
    effect: Pure
    Sync: ON
    Time: 1/8D              # with Sync ON, Time/Rate take note values
    Mix: 22
  MOD:
    effect: G-Chorus
    enabled: false          # keep the effect stored but switched off

exp:                        # up to 3 targets per pedal mode
  exp1_a: [{module: VOL, param: Volume, min: 0, max: 100}]
  exp1_b: [{module: DLY, param: Mix, min: 10, max: 50}]
  exp2: []

ctrl:                       # what each CTRL footswitch toggles
  CTRL1: [DST]
  CTRL2: [DLY, FX_LOOP]
```

Rules:

- Modules you don't list stay as in the pedal's init patch: their default
  effect, switched off (VOL stays on).
- Parameters you don't list keep the effect's default.
- Names are matched ignoring case, spaces and dashes (`uk800`, `mess4 ld 3`).
  A few common parameter names are understood (`Mid` -> `Middle`,
  `Drive` -> `Gain`, `Level` -> `Volume`) and reported as warnings.
- Switches take `ON`/`OFF`; menus take the option name (`Mode: HP`).
- CAB `Low Cut` / `High Cut` accept `OFF` and values like `8kHz`.
- With `Sync: ON`, synced Time/Rate knobs take one of
  `1/1 1/2 1/2D 1/2T 1/4 1/4D 1/4T 1/8 1/8D 1/8T 1/16`.

Decompiled files list every module and every parameter, with each
parameter's range as a comment, so they double as a reference.

## Files

| Path | What it is |
|---|---|
| `prst_compile.py`, `prst_decompile.py`, `prst_info.py` | command-line tools |
| `gp200/prst.py` | the binary format: reading, writing, checksum |
| `gp200/catalog.py` | effect lookup and value conversion |
| `gp200/spec.py` | YAML <-> preset |
| `gp200_catalog.json` | effect catalog generated from `algorithm.xml` |
| `tools/extract_catalog.py` | regenerates the catalog |
| `examples/` | ready-to-compile presets |
| `tests/` | `python -m unittest discover -s tests` |

## The .prst format

Documented at the top of [`gp200/prst.py`](gp200/prst.py). In short: a
1224-byte little-endian file with the magic `TSRP`, patch settings and name,
11 effect blocks of 72 bytes (effect code + 15 float parameters), a table of
EXP/CTRL assignment records, and a big-endian 16-bit byte-sum checksum at the
end. A parameter's position inside its block is its `ID` attribute in
`algorithm.xml` (not `idx`; they differ for 102 effects, e.g. every cabinet).
With Sync on, the pedal stores a note index 0..10 in the Time/Rate knob.

## Updating the catalog for new firmware

Install (or unpack) the new Valeton GP-200 editor and run:

```bash
python tools/extract_catalog.py "C:/Program Files/Valeton/GP-200/Resource/GP-200/File/algorithm.xml"
python -m unittest discover -s tests
```

## Credits

The format work of these open-source projects was used as a reference and
cross-checked against real files: [gp200editor / Preset Forge](https://github.com/phash/gp200editor),
[gp200-studio](https://github.com/kabir0st/gp200-studio),
[PRSTDecoder](https://github.com/mikeliddle/PRSTDecoder). No code was copied
from them. Effect names and parameter data belong to Valeton; this project is
not affiliated with or endorsed by Valeton.

## License

MIT
