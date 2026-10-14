"""
Remesh pass before decimation, for "dirty" sources (scans, sculpts, kitbash).
Voxel remesh merges islands and closes holes; QuadriFlow rebuilds clean quads.
Both lose UVs and material indices, which are transferred back from the source.
"""

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from ..utils.math_helpers import calculate_bounding_sphere


def _apply_modifier(obj: bpy.types.Object, mod_name: str):
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_apply(modifier=mod_name)


def _voxel_remesh(obj: bpy.types.Object, voxel_size: float):
    mod = obj.modifiers.new(name="LOD_Remesh", type='REMESH')
    mod.mode = 'VOXEL'
    mod.voxel_size = voxel_size
    mod.adaptivity = 0.0
    _apply_modifier(obj, mod.name)


def _quadriflow_remesh(obj: bpy.types.Object, target_faces: int) -> bool:
    """QuadriFlow runs synchronously when called from a script. Returns False on failure."""
    faces_before = len(obj.data.polygons)
    try:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                       selected_editable_objects=[obj]):
            result = bpy.ops.object.quadriflow_remesh(
                target_faces=max(8, target_faces), use_mesh_symmetry=False, smooth_normals=False
            )
    except RuntimeError as err:
        print(f"[LOD Generator] QuadriFlow a échoué sur '{obj.name}' : {err}")
        return False
    return 'FINISHED' in result and len(obj.data.polygons) != faces_before


def _transfer_attributes(obj: bpy.types.Object, source_mesh: bpy.types.Mesh, context):
    """Restores UV layers (Data Transfer) and material indices (nearest source face)."""
    src_obj = bpy.data.objects.new("_LOD_Remesh_Source", source_mesh)
    src_obj.matrix_world = obj.matrix_world.copy()
    context.collection.objects.link(src_obj)
    try:
        # UVs: destination layers must exist for the NAME mapping
        if source_mesh.uv_layers:
            for uv in source_mesh.uv_layers:
                if uv.name not in obj.data.uv_layers:
                    obj.data.uv_layers.new(name=uv.name)
            mod = obj.modifiers.new(name="LOD_Remesh_UV", type='DATA_TRANSFER')
            mod.object = src_obj
            mod.use_object_transform = True
            mod.use_loop_data = True
            mod.data_types_loops = {'UV'}
            mod.loop_mapping = 'POLYINTERP_NEAREST'
            mod.layers_uv_select_src = 'ALL'
            mod.layers_uv_select_dst = 'NAME'
            context.view_layer.update()
            _apply_modifier(obj, mod.name)

        # Material indices and smooth flag from the nearest source face
        bm_src = bmesh.new()
        bm_src.from_mesh(source_mesh)
        bm_src.faces.ensure_lookup_table()
        bvh = BVHTree.FromBMesh(bm_src)
        src_materials = [p.material_index for p in source_mesh.polygons]
        src_smooth = [p.use_smooth for p in source_mesh.polygons]
        for poly in obj.data.polygons:
            _, _, index, _ = bvh.find_nearest(poly.center)
            if index is not None:
                poly.material_index = src_materials[index]
                poly.use_smooth = src_smooth[index]
        bm_src.free()
    finally:
        bpy.data.objects.remove(src_obj, do_unlink=True)


def remesh_object(context, obj: bpy.types.Object, mode: str, voxel_pct: float = 1.0,
                  quadriflow_ratio: float = 0.5) -> str:
    """
    Remeshes obj in place. mode: 'VOXEL' or 'QUADRIFLOW' (falls back to voxel when it
    fails, e.g. on non-manifold input). voxel_pct is the voxel size in % of the bounding
    radius; quadriflow_ratio the target face count relative to the source.
    Returns the mode actually used.
    """
    if mode not in {'VOXEL', 'QUADRIFLOW'} or not obj.data.polygons:
        return 'NONE'

    source_mesh = obj.data.copy()
    try:
        used = mode
        if mode == 'QUADRIFLOW':
            target = int(len(obj.data.polygons) * quadriflow_ratio)
            if not _quadriflow_remesh(obj, target):
                used = 'VOXEL'
        if used == 'VOXEL':
            _, radius = calculate_bounding_sphere(obj)
            _voxel_remesh(obj, max(radius * voxel_pct / 100.0, 1e-4))
        _transfer_attributes(obj, source_mesh, context)
    finally:
        bpy.data.meshes.remove(source_mesh)
    return used
