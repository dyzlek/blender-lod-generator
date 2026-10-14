import math
import bmesh
import bpy
from mathutils import Vector
from ..utils.math_helpers import clamp, calculate_uv_angle_deviation


PROTECTION_VG_NAME = "_LOD_Protection_Mask"


def mark_uv_discontinuities_as_seams(bm: bmesh.types.BMesh, epsilon: float = 1e-4) -> int:
    """
    Scans all edges in BMesh and explicitly marks e.seam = True
    whenever UV loop coordinates diverge between two adjacent faces.
    Also welds sawtooth UV coordinates at smooth geometric poles (>= 8 edges)
    so Blender's C++ delimit={'UV'} does not lock sphere/cylinder poles into spikes.
    """
    uv_layer = bm.loops.layers.uv.active
    if not uv_layer:
        return 0

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    # 1. Unify sawtooth UVs at smooth poles (e.g. UV Sphere North/South poles)
    # On a standard UV sphere, 32 triangles meet at 1 pole vertex with 32 different U values,
    # which causes Blender's C++ delimit={'UV'} to lock all 32 radial edges and create a spike.
    for v in bm.verts:
        if len(v.link_edges) >= 8:
            is_boundary_or_sharp = any((len(e.link_faces) < 2 or not e.smooth) for e in v.link_edges)
            if not is_boundary_or_sharp and len(v.link_loops) > 0:
                avg_uv = Vector((0.0, 0.0))
                for lp in v.link_loops:
                    avg_uv += lp[uv_layer].uv
                avg_uv /= len(v.link_loops)
                for lp in v.link_loops:
                    lp[uv_layer].uv = avg_uv.copy()

    # 2. Mark true UV island boundaries as seams
    marked_count = 0
    for e in bm.edges:
        if e.seam:
            continue
        if len(e.link_faces) == 2:
            f0, f1 = e.link_faces[0], e.link_faces[1]
            vA, vB = e.verts[0], e.verts[1]

            uv0_A = uv0_B = uv1_A = uv1_B = None
            for lp in f0.loops:
                if lp.vert == vA:
                    uv0_A = lp[uv_layer].uv
                elif lp.vert == vB:
                    uv0_B = lp[uv_layer].uv
            for lp in f1.loops:
                if lp.vert == vA:
                    uv1_A = lp[uv_layer].uv
                elif lp.vert == vB:
                    uv1_B = lp[uv_layer].uv

            if uv0_A and uv1_A and uv0_B and uv1_B:
                if (uv0_A - uv1_A).length > epsilon and (uv0_B - uv1_B).length > epsilon:
                    e.seam = True
                    marked_count += 1
    return marked_count


def compute_vertex_protection_weights(
    bm: bmesh.types.BMesh,
    obj: bpy.types.Object | None = None,
    preserve_uv: float = 0.85,
    preserve_silhouette: float = 0.75,
    silhouette_angle_rad: float = math.radians(35.0),
    preserve_boundary: float = 1.0,
    preserve_material: float = 0.90,
    preserve_skinning: float = 0.70,
    uv_border_curvature: bool = True,
    lod_level: int = 1,
    relax_weights: bool = True
) -> list[float]:
    """
    Computes a protection weight in [0.0, 1.0] for each vertex in the BMesh.
    1.0 = Highly protected / preserved from collapse.
    0.0 = Flat / smooth surface, collapses uniformly.
    """
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # Automatically identify and mark all physical UV cuts as seams & fix pole UVs
    mark_uv_discontinuities_as_seams(bm)

    vert_count = len(bm.verts)
    weights = [0.0] * vert_count

    uv_layer = bm.loops.layers.uv.active

    # Pre-cache skinning data if armature weights exist on source object
    skinning_scores = None
    if obj and preserve_skinning > 0.0 and len(obj.vertex_groups) > 0:
        skinning_scores = compute_skinning_joint_scores(obj)

    eff_preserve_uv = preserve_uv
    eff_preserve_mat = preserve_material
    eff_preserve_sil = preserve_silhouette
    eff_sil_angle = silhouette_angle_rad

    if lod_level >= 3 and relax_weights:
        eff_preserve_uv = max(0.25, preserve_uv * 0.4)
        eff_preserve_mat = max(0.4, preserve_material * 0.5)
        eff_sil_angle = max(silhouette_angle_rad, math.radians(50.0))

    for idx, v in enumerate(bm.verts):
        w_boundary = 0.0
        w_uv = 0.0
        w_uv_curve = 0.0
        w_mat = 0.0
        w_curve = 0.0
        w_skin = 0.0

        # --- 0. Smooth Pole Check ---
        is_smooth_pole = False
        if len(v.link_edges) >= 8:
            has_sharp_or_boundary = any((len(e.link_faces) < 2 or not e.smooth) for e in v.link_edges)
            if not has_sharp_or_boundary:
                is_smooth_pole = True

        # --- 1. Open Boundary Check ---
        for e in v.link_edges:
            if len(e.link_faces) == 1:
                w_boundary = max(w_boundary, preserve_boundary)
                break

        # --- 2. 3D Dihedral Curvature & Silhouette Check ---
        max_dihedral = 0.0
        is_sharp_edge = False
        for e in v.link_edges:
            if not e.smooth:
                is_sharp_edge = True
            if len(e.link_faces) == 2:
                n1 = e.link_faces[0].normal
                n2 = e.link_faces[1].normal
                dot_val = clamp(n1.dot(n2), -1.0, 1.0)
                dihedral = math.acos(dot_val)
                if dihedral > max_dihedral:
                    max_dihedral = dihedral

        if eff_preserve_sil > 0.0:
            if is_sharp_edge:
                w_curve = eff_preserve_sil
            elif max_dihedral >= eff_sil_angle:
                ratio = min(1.0, max_dihedral / (math.pi * 0.5))
                w_curve = ratio * eff_preserve_sil

        # --- 3. UV Seam Check ---
        # CRITICAL FIX: Blender's C++ delimit={'UV', 'SEAM'} already forbids collapsing ACROSS seams.
        # In the Vertex Group, if a seam sits on a smooth surface (like a sphere meridian or smooth cylinder),
        # giving it a high vertex weight freezes vertex density ALONG the seam while the interior shrinks,
        # causing triangles next to the seam to fold over!
        # Therefore, we scale w_uv by the 3D curvature so smooth seams simplify at the same pace as the surface.
        if eff_preserve_uv > 0.0 and not is_smooth_pole:
            seam_edges_on_vert = [e for e in v.link_edges if e.seam]
            if seam_edges_on_vert:
                # If the seam is on a structural 3D corner (> 20 deg) or is a UV junction (>= 3 seam edges)
                if len(seam_edges_on_vert) >= 3:
                    w_uv = min(0.75, eff_preserve_uv)
                elif max_dihedral >= math.radians(20.0):
                    curve_factor = min(1.0, max_dihedral / math.radians(60.0))
                    w_uv = eff_preserve_uv * curve_factor * 0.65

                # 2D UV border turn protection on structural edges
                if uv_border_curvature and uv_layer and len(seam_edges_on_vert) == 2 and max_dihedral >= math.radians(25.0):
                    e1, e2 = seam_edges_on_vert[0], seam_edges_on_vert[1]
                    if len(e1.link_faces) >= 1 and len(e2.link_faces) >= 1:
                        f_ref = e1.link_faces[0]
                        v1 = e1.other_vert(v)
                        v2 = e2.other_vert(v)
                        uv_v = uv_1 = uv_2 = None
                        for lp in f_ref.loops:
                            if lp.vert == v:
                                uv_v = lp[uv_layer].uv
                            elif lp.vert == v1:
                                uv_1 = lp[uv_layer].uv
                            elif lp.vert == v2:
                                uv_2 = lp[uv_layer].uv
                        if uv_v and uv_1 and uv_2:
                            d1 = uv_1 - uv_v
                            d2 = uv_2 - uv_v
                            if d1.length > 1e-6 and d2.length > 1e-6:
                                dev = calculate_uv_angle_deviation(
                                    Vector((d1.x, d1.y, 0.0)),
                                    Vector((d2.x, d2.y, 0.0))
                                )
                                if dev > math.radians(20.0):
                                    w_uv_curve = 0.70

        # --- 4. Material Boundary Check ---
        if eff_preserve_mat > 0.0 and len(v.link_faces) > 1 and max_dihedral >= math.radians(15.0):
            mat_indices = {f.material_index for f in v.link_faces}
            if len(mat_indices) > 1:
                w_mat = eff_preserve_mat * 0.65

        # --- 5. Skinning / Bone Joint Check ---
        if skinning_scores and idx < len(skinning_scores):
            w_skin = skinning_scores[idx] * preserve_skinning

        if is_smooth_pole:
            combined_weight = 0.0
        else:
            combined_weight = max(w_boundary, w_uv, w_uv_curve, w_mat, w_curve, w_skin)
            combined_weight = clamp(combined_weight, 0.0, 1.0)

        if lod_level == 2 and relax_weights:
            combined_weight = math.pow(combined_weight, 2)

        weights[idx] = combined_weight

    return weights


def compute_skinning_joint_scores(obj: bpy.types.Object) -> list[float]:
    """
    Evaluates vertex bone weight sharing to protect articulation loops (elbows, knees, shoulders).
    """
    mesh = obj.data
    scores = [0.0] * len(mesh.vertices)

    for i, v in enumerate(mesh.vertices):
        influences = [g.weight for g in v.groups if g.weight > 0.1]
        if len(influences) >= 2:
            sum_w = sum(influences)
            if sum_w > 0:
                normalized = [w / sum_w for w in influences]
                score = 1.0 - max(normalized)
                scores[i] = min(1.0, score * 2.0)
    return scores


def apply_protection_mask_to_object(
    obj: bpy.types.Object,
    preserve_uv: float = 0.85,
    preserve_silhouette: float = 0.75,
    silhouette_angle_rad: float = math.radians(35.0),
    preserve_boundary: float = 1.0,
    preserve_material: float = 0.90,
    preserve_skinning: float = 0.70,
    uv_border_curvature: bool = True,
    lod_level: int = 1,
    relax_weights: bool = True
) -> bpy.types.VertexGroup:
    """
    Computes and writes the protection mask directly into the vertex group '_LOD_Protection_Mask'.
    """
    if not obj or obj.type != 'MESH':
        return None

    vg = obj.vertex_groups.get(PROTECTION_VG_NAME)
    if not vg:
        vg = obj.vertex_groups.new(name=PROTECTION_VG_NAME)

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    weights = compute_vertex_protection_weights(
        bm=bm,
        obj=obj,
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
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    for idx, w in enumerate(weights):
        vg.add([idx], w, 'REPLACE')

    return vg
