"""
Procedural LOD Generator Pro for Game Engines (Unreal Engine / Unity / Godot / WebXR).
Implements topological auditing, vertex protection weighting, planar pre-dissolve,
BFS island culling, QEM edge collapse, custom normal projection, and LodGroup Empty hierarchy.
"""

bl_info = {
    "name": "Procedural LOD Generator Pro",
    "author": "Antigravity",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > LOD Generator",
    "description": "Générateur procédural avancé de LODs pour moteurs de jeux (Unreal, Unity, Godot) avec protection UV/silhouette et hiérarchie LodGroup.",
    "warning": "",
    "doc_url": "",
    "category": "Mesh",
}

import sys
import importlib

# Reload submodules if reloaded in Blender
module_names = [
    "properties",
    "utils",
    "core",
    "operators",
    "ui"
]

current_module = __name__

for sub_name in module_names:
    full_name = f"{current_module}.{sub_name}"
    if full_name in sys.modules:
        importlib.reload(sys.modules[full_name])

from . import properties
from . import operators
from . import ui


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
