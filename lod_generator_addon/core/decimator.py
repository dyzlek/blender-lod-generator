"""
Decimator Pipeline Module for Procedural LOD Generator.
Executes the multi-stage geometry simplification pipeline:
0. Clean Pre-Triangulation (Beauty + Keep Normals)
1. Small mesh islands culling (BFS size filtering)
2. Coplanar face pre-dissolve (Planar decimation for hard-surface)
3. QEM edge collapse with delimit={'UV', 'MATERIAL', 'SEAM'} and protection mask
4. Optional physical isolation (Split -> Decimate -> Weld)
5. Post-decimation UV repair via BVHTree and barycentric projection
6. Normal projection / restoration via Data Transfer
"""

import math
import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform
from .analyzer import find_connected_islands, clean_mesh_topology
from .weight_mapper import (
    apply_protection_mask_to_object,
    PROTECTION_VG_NAME,
    mark_uv_discontinuities_as_seams
)
from ..utils.math_helpers import (
    clamp,
    get_triangulated_face_count,
    calculate_coords_bounding_sphere,
    calculate_bounding_sphere,
    calculate_uv_signed_area,
    calculate_triangle_3d_area,
)


def cull_small_islands_from_object(obj: bpy.types.Object, threshold_pct: float) -> int:
    """
    Identifies and removes detached sub-elements (rivets, bolts, tiny handles)
    whose relative bounding radius is smaller than threshold_pct % of the overall mesh.
    Returns the number of culled islands.
    """
    if not obj or obj.type != 'MESH' or threshold_pct <= 0.0:
        return 0

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    if len(bm.verts) == 0:
        bm.free()
        return 0

    coords = [v.co for v in bm.verts]
    _, global_radius = calculate_coords_bounding_sphere(coords)
    if global_radius <= 1e-5:
        bm.free()
        return 0

    islands = find_connected_islands(bm)
    if len(islands) <= 1:
        bm.free()
        return 0

    culled_count = 0
    verts_to_delete = []

    ratio_threshold = threshold_pct / 100.0

    for island in islands:
        island_coords = [v.co for v in island]
        _, island_radius = calculate_coords_bounding_sphere(island_coords)
        relative_size = island_radius / global_radius

        if relative_size < ratio_threshold:
            culled_count += 1
            verts_to_delete.extend(island)

    if verts_to_delete:
        bmesh.ops.delete(bm, geom=verts_to_delete, context='VERTS')
        bm.to_mesh(obj.data)
        obj.data.update()

    bm.free()
    return culled_count


def repair_corrupted_uvs_via_bvh(target_obj: bpy.types.Object, lod0_obj: bpy.types.Object) -> int:
    """
    Scans the decimated mesh for UV foldovers or degenerated UV faces (e.g. barcode artifacts
    where UV area is near zero while 3D area is non-zero).
    Uses a BVHTree constructed on a pre-triangulated copy of LOD0 to find corresponding surface coordinates
    and re-projects clean barycentric UVs sampled from the interior of the face.
    Returns the number of repaired faces.
    """
    if not target_obj or not lod0_obj or not target_obj.data.uv_layers:
        return 0

    bm0 = bmesh.new()
    bm0.from_mesh(lod0_obj.data)
    # Triangulate bm0 to guarantee that every source face f0 has exactly 3 vertices
    bmesh.ops.triangulate(bm0, faces=bm0.faces)
    bm0.faces.ensure_lookup_table()
    bm0.verts.ensure_lookup_table()

    uv_layer0 = bm0.loops.layers.uv.active
    if not uv_layer0:
        bm0.free()
        return 0

    bvh0 = BVHTree.FromBMesh(bm0)

    bm_target = bmesh.new()
    bm_target.from_mesh(target_obj.data)
    bm_target.faces.ensure_lookup_table()
    bm_target.verts.ensure_lookup_table()

    uv_layer_target = bm_target.loops.layers.uv.active
    if not uv_layer_target:
        bm0.free()
        bm_target.free()
        return 0

    repaired_count = 0

    for f in bm_target.faces:
        if len(f.verts) != 3:
            continue

        v0, v1, v2 = f.verts[0].co, f.verts[1].co, f.verts[2].co
        lp0, lp1, lp2 = f.loops[0], f.loops[1], f.loops[2]
        uv0, uv1, uv2 = lp0[uv_layer_target].uv, lp1[uv_layer_target].uv, lp2[uv_layer_target].uv

        area_uv = abs(calculate_uv_signed_area(uv0, uv1, uv2))
        area_3d = calculate_triangle_3d_area(v0, v1, v2)

        # 2D Aspect Ratio: a triangle is a barcode artifact only if area_uv is nearly zero or
        # flattened beyond 99.99% relative to its maximum UV edge length
        max_uv_edge = max((uv1 - uv0).length, (uv2 - uv1).length, (uv0 - uv2).length)
        is_barcode = (max_uv_edge > 1e-4 and (2.0 * area_uv / (max_uv_edge ** 2)) < 1e-4)
        is_zero_uv = (area_uv < 1e-10 and area_3d > 1e-5)

        if is_barcode or is_zero_uv:
            # Locate source face at the geometric center of the 3D triangle to eliminate seam ambiguity
            face_center = (v0 + v1 + v2) / 3.0
            _, _, center_face_idx, _ = bvh0.find_nearest(face_center)

            if center_face_idx is not None and center_face_idx < len(bm0.faces):
                f0 = bm0.faces[center_face_idx]
                pA, pB, pC = f0.verts[0].co, f0.verts[1].co, f0.verts[2].co
                uvA = Vector((f0.loops[0][uv_layer0].uv.x, f0.loops[0][uv_layer0].uv.y, 0.0))
                uvB = Vector((f0.loops[1][uv_layer0].uv.x, f0.loops[1][uv_layer0].uv.y, 0.0))
                uvC = Vector((f0.loops[2][uv_layer0].uv.x, f0.loops[2][uv_layer0].uv.y, 0.0))

                for loop in f.loops:
                    # Inward sampling: slight pull (5%) towards face center prevents jumping over seams
                    sample_pt = loop.vert.co.lerp(face_center, 0.05)
                    loc, _, _, _ = bvh0.find_nearest(sample_pt)
                    if loc is not None:
                        uv_repaired = barycentric_transform(loc, pA, pB, pC, uvA, uvB, uvC)
                        loop[uv_layer_target].uv = uv_repaired.xy

                repaired_count += 1

    if repaired_count > 0:
        bm_target.to_mesh(target_obj.data)
        target_obj.data.update()

    bm0.free()
    bm_target.free()
    return repaired_count


def apply_modifier_safely(context, obj: bpy.types.Object, mod_name: str) -> bool:
    """Safely applies a modifier on an object in the current scene view layer."""
    if not obj or mod_name not in obj.modifiers:
        return False

    prev_active = context.view_layer.objects.active
    context.view_layer.objects.active = obj
    obj.select_set(True)

    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    success = True
    try:
        bpy.ops.object.modifier_apply(modifier=mod_name)
    except Exception as err:
        print(f"[LOD Generator] Failed to apply modifier '{mod_name}' on '{obj.name}': {err}")
        success = False

    context.view_layer.objects.active = prev_active
    return success


def generate_lod_mesh(
    context,
    source_obj: bpy.types.Object,
    lod0_obj: bpy.types.Object,
    lod_name: str,
    target_ratio: float,
    lod_level: int,
    original_lod0_triangles: int,
    pre_triangulate: bool = True,
    use_delimit_uv: bool = True,
    uv_border_curvature: bool = True,
    bvh_repair_uv: bool = True,
    split_weld_isolation: bool = False,
    preserve_uv: float = 0.85,
    preserve_silhouette: float = 0.75,
    silhouette_angle_rad: float = math.radians(35.0),
    preserve_boundary: float = 1.0,
    preserve_material: float = 0.90,
    preserve_skinning: float = 0.70,
    relax_weights: bool = True,
    pre_planar_dissolve: bool = False,
    planar_angle_limit_rad: float = math.radians(0.5),
    cull_small_parts: bool = True,
    cull_threshold_pct: float = 2.5,
    normal_strategy: str = 'TRANSFER',
    keep_protection_mask: bool = False
) -> tuple[bpy.types.Object, dict]:
    """
    Executes the complete generation of a single LOD level from a source mesh.
    Returns the newly created LOD object and a dictionary of performance metrics.
    """
    # 1. Clean duplication
    new_mesh = source_obj.data.copy()
    new_mesh.name = f"{lod_name}_Mesh"
    new_obj = source_obj.copy()
    new_obj.data = new_mesh
    new_obj.name = lod_name

    # Clear existing unapplied modifiers from duplicate
    new_obj.modifiers.clear()

    # Link to active collection temporarily
    context.collection.objects.link(new_obj)

    # 2. Pass 0 — Clean Pre-Triangulation (Beauty + Keep Normals)
    # Eliminates non-planar quads and concave N-Gons to prevent barcode UV artifacts
    if pre_triangulate:
        mod_tri = new_obj.modifiers.new(name="LOD_Pre_Triangulate", type='TRIANGULATE')
        mod_tri.quad_method = 'BEAUTY'
        mod_tri.ngon_method = 'CLIP'
        mod_tri.keep_custom_normals = True
        apply_modifier_safely(context, new_obj, mod_tri.name)

    # 3. Geometry Culling for small detached components on distant LODs
    culled_islands = 0
    if cull_small_parts and lod_level >= 2:
        effective_cull_pct = cull_threshold_pct
        if lod_level >= 3:
            effective_cull_pct = max(effective_cull_pct, 5.0)
        culled_islands = cull_small_islands_from_object(new_obj, effective_cull_pct)

    # 4. Pass 1 — Coplanar Face Dissolve (Planar Pre-Decimate)
    # Only applied if enabled and below strict angle (<= 0.5 deg)
    if pre_planar_dissolve:
        mod_planar = new_obj.modifiers.new(name="LOD_Planar_Dissolve", type='DECIMATE')
        mod_planar.decimate_type = 'DISSOLVE'
        mod_planar.angle_limit = planar_angle_limit_rad
        mod_planar.delimit = {'UV', 'MATERIAL', 'SEAM', 'SHARP'}
        apply_modifier_safely(context, new_obj, mod_planar.name)

    # Pre-mark UV discontinuities as seams so that both physical isolation and Delimit detect all UV cuts
    if use_delimit_uv and new_obj.data.uv_layers:
        bm_seam = bmesh.new()
        bm_seam.from_mesh(new_obj.data)
        bm_seam.edges.ensure_lookup_table()
        marked = mark_uv_discontinuities_as_seams(bm_seam)
        if marked > 0:
            bm_seam.to_mesh(new_obj.data)
            new_obj.data.update()
        bm_seam.free()

    # 5. Optional Split-Decimate-Weld Physical Isolation
    if split_weld_isolation:
        bm_split = bmesh.new()
        bm_split.from_mesh(new_obj.data)
        bm_split.edges.ensure_lookup_table()
        seam_edges = [e for e in bm_split.edges if e.seam]
        if seam_edges:
            bmesh.ops.split_edges(bm_split, edges=seam_edges)
            bm_split.to_mesh(new_obj.data)
            new_obj.data.update()
        bm_split.free()

    # 6. Generate Procedural Protection Weight Mask
    apply_protection_mask_to_object(
        obj=new_obj,
        preserve_uv=preserve_uv,
        preserve_silhouette=preserve_silhouette,
        silhouette_angle_rad=silhouette_angle_rad,
        preserve_boundary=preserve_boundary,
        preserve_material=preserve_material,
        preserve_skinning=preserve_skinning,
        uv_border_curvature=uv_border_curvature,
        lod_level=lod_level,
        relax_weights=relax_weights
    )

    # 7. Pass 2 — QEM Guided Collapse Decimation with Delimit Barrier
    target_triangles = max(8, int(original_lod0_triangles * target_ratio))
    current_triangles = get_triangulated_face_count(new_obj)

    if current_triangles > target_triangles:
        eff_collapse_ratio = clamp(target_triangles / max(1, current_triangles), 0.002, 1.0)
        mod_qem = new_obj.modifiers.new(name="LOD_QEM_Collapse", type='DECIMATE')
        mod_qem.decimate_type = 'COLLAPSE'
        mod_qem.ratio = eff_collapse_ratio
        mod_qem.use_collapse_triangulate = True
        mod_qem.vertex_group = PROTECTION_VG_NAME
        mod_qem.invert_vertex_group = True
        mod_qem.vertex_group_factor = 1.0

        # Native C++ boundary barrier: keep 'UV' on ALL LOD levels to prevent bridges between UV islands
        if use_delimit_uv:
            mod_qem.delimit = {'UV', 'MATERIAL', 'SEAM'}

        apply_modifier_safely(context, new_obj, mod_qem.name)

    # 8. Re-weld after Split if isolation was active
    if split_weld_isolation:
        bm_weld = bmesh.new()
        bm_weld.from_mesh(new_obj.data)
        bm_weld.verts.ensure_lookup_table()
        bmesh.ops.remove_doubles(bm_weld, verts=bm_weld.verts, dist=0.00001)
        bm_weld.to_mesh(new_obj.data)
        new_obj.data.update()
        bm_weld.free()

    # 9. Pass 3 — Post-Decimation UV Repair via BVH Tree (Rule D)
    repaired_uvs = 0
    if bvh_repair_uv and lod0_obj:
        repaired_uvs = repair_corrupted_uvs_via_bvh(new_obj, lod0_obj)

    # 10. Pass 4 — Shading Restoration (Custom Normal Transfer)
    if normal_strategy == 'TRANSFER' and lod0_obj:
        mod_norm = new_obj.modifiers.new(name="LOD_Normal_Transfer", type='DATA_TRANSFER')
        mod_norm.object = lod0_obj
        mod_norm.use_loop_data = True
        mod_norm.data_types_loops = {'CUSTOM_NORMAL'}
        mod_norm.loop_mapping = 'POLYINTERP_LNORPROJ'
        apply_modifier_safely(context, new_obj, mod_norm.name)

    elif normal_strategy == 'WEIGHTED':
        mod_wn = new_obj.modifiers.new(name="LOD_Weighted_Normal", type='WEIGHTED_NORMAL')
        mod_wn.keep_sharp = True
        apply_modifier_safely(context, new_obj, mod_wn.name)

    # 11. Clean up temporary vertex group
    if not keep_protection_mask and PROTECTION_VG_NAME in new_obj.vertex_groups:
        vg = new_obj.vertex_groups.get(PROTECTION_VG_NAME)
        new_obj.vertex_groups.remove(vg)

    # Validate mesh data to ensure clean export without dangling indices
    new_obj.data.validate()
    new_obj.data.update()

    # Compute final metrics
    final_triangles = get_triangulated_face_count(new_obj)
    reduction_pct = (1.0 - (final_triangles / max(1, original_lod0_triangles))) * 100.0

    stats = {
        "lod_level": lod_level,
        "lod_name": lod_name,
        "triangles": final_triangles,
        "target_triangles": target_triangles,
        "reduction_pct": round(reduction_pct, 1),
        "culled_islands": culled_islands,
        "repaired_uvs": repaired_uvs
    }

    return new_obj, stats
