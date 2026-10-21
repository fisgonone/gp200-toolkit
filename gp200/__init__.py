"""GP-200 toolkit: compile YAML presets to Valeton GP-200 .prst files and back."""
from __future__ import annotations

from typing import List, Optional, Tuple

from .catalog import Catalog, CatalogError
from .prst import Preset, PrstError, read_prst, write_prst
from .spec import SpecError, build_preset, load_yaml_text, preset_to_yaml

__version__ = "1.0.0"

_catalog: Optional[Catalog] = None


def catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog.load()
    return _catalog


def compile_yaml(text: str) -> Tuple[bytes, Preset, List[str]]:
    """YAML text -> (.prst bytes, preset, warnings). Raises SpecError listing every problem."""
    preset, warnings = build_preset(load_yaml_text(text), catalog())
    return write_prst(preset), preset, warnings


def decompile_prst(data: bytes, comments: bool = True) -> str:
    """.prst bytes -> YAML text."""
    return preset_to_yaml(read_prst(data), catalog(), comments=comments)


__all__ = ["Catalog", "CatalogError", "Preset", "PrstError", "SpecError", "catalog",
           "compile_yaml", "decompile_prst", "read_prst", "write_prst", "__version__"]
