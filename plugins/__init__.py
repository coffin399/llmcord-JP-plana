import importlib
import inspect
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Protocol, Any

PLUGIN_PATH = Path(__file__).parent


class ToolBase(Protocol):
    name: str
    tool_spec: dict
    def run(self, *, arguments: dict, bot) -> str: ... 


def _discover_modules() -> List[ModuleType]:
    pkgname = __name__
    return [
        importlib.import_module(f"{pkgname}.{m.name}")
        for m in pkgutil.iter_modules([str(PLUGIN_PATH)])
        if not m.name.startswith("_")
    ]


def load_plugins(bot) -> Dict[str, ToolBase]:
    plugins = {}
    for module in _discover_modules():
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if hasattr(obj, "tool_spec") and hasattr(obj, "run"):
                inst = obj(bot)
                plugins[inst.name] = inst
    return plugins