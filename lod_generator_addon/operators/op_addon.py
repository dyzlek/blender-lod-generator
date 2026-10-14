"""
Add-on maintenance operators (reload after an update).
"""

import bpy
from ..utils.version import reload_addon


class LOD_OT_reload_addon(bpy.types.Operator):
    """Recharge l'addon depuis le disque (utile après l'installation d'une nouvelle version)"""
    bl_idname = "lod.reload_addon"
    bl_label = "Recharger l'addon"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # Deferred: this operator's own class is unregistered during the reload
        bpy.app.timers.register(reload_addon, first_interval=0.05)
        self.report({'INFO'}, "Rechargement de LOD Generator...")
        return {'FINISHED'}
