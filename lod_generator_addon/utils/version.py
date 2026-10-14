"""
Loaded-vs-installed version check.
Blender keeps imported Python modules in memory: installing a new zip over an enabled
add-on can leave the old code running. This module compares the version loaded in
memory with the one installed on disk and lists submodules whose file changed after
they were imported.
"""

import ast
import os
import sys
import time

from .. import bl_info

ADDON_PACKAGE = __name__.rsplit(".", 2)[0]
ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Version of the code imported together with this module, and when it was imported
LOADED_VERSION = tuple(bl_info["version"])
LOADED_AT = time.time()

# Panel redraws are frequent: the disk check is cached for this long (seconds)
CACHE_SECONDS = 2.0
_cache = {"time": 0.0, "state": None}


def format_version(version) -> str:
    return ".".join(str(part) for part in version) if version else "?"


def read_disk_version(addon_dir: str = ADDON_DIR):
    """Reads bl_info["version"] from the installed __init__.py without importing it."""
    try:
        with open(os.path.join(addon_dir, "__init__.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "bl_info" for t in node.targets):
            try:
                return tuple(ast.literal_eval(node.value)["version"])
            except (ValueError, KeyError):
                return None
    return None


def find_stale_modules(loaded_at: float = LOADED_AT) -> list[str]:
    """Add-on submodules whose source file was modified (or removed) after being imported."""
    stale = []
    for name, module in list(sys.modules.items()):
        if module is None or not (name == ADDON_PACKAGE or name.startswith(ADDON_PACKAGE + ".")):
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            if os.path.getmtime(path) > loaded_at + 1.0:
                stale.append(name)
        except OSError:
            stale.append(name)
    return sorted(stale)


def check_version_state(force: bool = False) -> dict:
    """
    {"loaded", "disk", "stale_modules", "mismatch"}; mismatch is True when the code
    running in memory is not the code installed on disk.
    """
    now = time.time()
    if not force and _cache["state"] is not None and now - _cache["time"] < CACHE_SECONDS:
        return _cache["state"]

    disk = read_disk_version()
    stale = find_stale_modules()
    state = {
        "loaded": LOADED_VERSION,
        "disk": disk,
        "stale_modules": stale,
        "mismatch": bool(stale) or (disk is not None and disk != LOADED_VERSION),
    }
    _cache["time"], _cache["state"] = now, state
    return state


def reload_addon():
    """
    Disables the add-on, drops every one of its modules from sys.modules and enables it
    again, so the code installed on disk is imported from scratch. Meant to run from a
    timer (never from inside one of the add-on's own operators, which get unregistered).
    """
    import addon_utils

    addon_utils.disable(ADDON_PACKAGE, default_set=False)
    for name in list(sys.modules):
        if name == ADDON_PACKAGE or name.startswith(ADDON_PACKAGE + "."):
            del sys.modules[name]
    addon_utils.enable(ADDON_PACKAGE, default_set=False)
    return None
