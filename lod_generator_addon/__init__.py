"""
Procedural LOD Generator Pro for Game Engines (Unreal Engine / Unity / Godot / WebXR).
Implements topological auditing, vertex protection weighting, planar pre-dissolve,
BFS island culling, QEM edge collapse, custom normal projection, and LodGroup Empty hierarchy.
"""

bl_info = {
    "name": "Procedural LOD Generator Pro",
    "author": "Dylan",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > LOD Generator",
    "description": "Générateur procédural avancé de LODs pour moteurs de jeux (Unreal, Unity, Godot) avec protection UV/silhouette et hiérarchie LodGroup.",
    "warning": "",
    "doc_url": "",
    "category": "Mesh",
}

import sys

# When Blender re-executes this file (add-on updated, reloaded or re-enabled), drop every
# submodule from sys.modules so they are all imported fresh. Reloading only the
# subpackages would leave leaf modules such as core.decimator in their old version.
# The package attributes must go too: on reload this namespace still holds the old
# subpackages, and "from . import x" would reuse them instead of importing.
for _name in [m for m in sys.modules if m.startswith(__name__ + ".")]:
    del sys.modules[_name]
    if "." not in _name[len(__name__) + 1:]:
        globals().pop(_name[len(__name__) + 1:], None)

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
