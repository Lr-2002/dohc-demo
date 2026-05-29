from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_generator() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "generate_lr193_rerun.py"
    spec = importlib.util.spec_from_file_location("generate_lr193_rerun", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
