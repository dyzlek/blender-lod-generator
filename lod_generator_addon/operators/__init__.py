"""
Operators package for LOD Generator Add-on.
"""

import bpy
from .op_generate import LOD_OT_generate, LOD_OT_cancel_generation, LOD_OT_visualize_mask, LOD_OT_clear_lods
from .op_preview import LOD_OT_toggle_exploded_view, LOD_OT_camera_distance_preview
from .op_export import LOD_OT_export
from .op_addon import LOD_OT_reload_addon

classes = (
    LOD_OT_generate,
    LOD_OT_cancel_generation,
    LOD_OT_visualize_mask,
    LOD_OT_clear_lods,
    LOD_OT_toggle_exploded_view,
    LOD_OT_camera_distance_preview,
    LOD_OT_export,
    LOD_OT_reload_addon,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
