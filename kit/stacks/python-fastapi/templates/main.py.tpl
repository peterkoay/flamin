"""Application entry point. Created once by flamin generate; this file is open and yours to edit."""
import importlib
import pkgutil
from pathlib import Path

from fastapi import FastAPI

app = FastAPI()

_modules = Path(__file__).parent / "modules"
for _mod in sorted(p.name for p in _modules.iterdir() if (p / "api").is_dir()) if _modules.is_dir() else []:
    for _info in pkgutil.iter_modules([str(_modules / _mod / "api")]):
        _m = importlib.import_module(f"modules.{_mod}.api.{_info.name}")
        if hasattr(_m, "router"):
            app.include_router(_m.router)
