"""
UI package for Procedural LOD Generator Add-on.
"""

import bpy
from .panels import VIEW3D_PT_lod_generator

classes = (
    VIEW3D_PT_lod_generator,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
