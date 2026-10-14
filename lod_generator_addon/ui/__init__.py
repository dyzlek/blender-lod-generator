"""
UI package for Procedural LOD Generator Add-on.
"""

import bpy
from .panels import VIEW3D_PT_lod_generator
from . import hud_overlay

classes = (
    VIEW3D_PT_lod_generator,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    hud_overlay.register_overlay()


def unregister():
    hud_overlay.unregister_overlay()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
