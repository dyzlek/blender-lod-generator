import math
import bmesh
import bpy
from mathutils import Vector
from ..utils.math_helpers import clamp, calculate_uv_angle_deviation


PROTECTION_VG_NAME = "_LOD_Protection_Mask"


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
    0.0 = Flat / interior surface, easily sacrificable.
    """
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    vert_count = len(bm.verts)
    weights = [0.0] * vert_count

    uv_layer = bm.loops.layers.uv.active

    # Pre-cache skinning data if armature weights exist on source object
    skinning_scores = None
    if obj and preserve_skinning > 0.0 and len(obj.vertex_groups) > 0:
        skinning_scores = compute_skinning_joint_scores(obj)

    # Effective multipliers based on LOD level
    eff_preserve_uv = preserve_uv
    eff_preserve_mat = preserve_material
    eff_preserve_sil = preserve_silhouette
    eff_sil_angle = silhouette_angle_rad

    if lod_level >= 3 and relax_weights:
        # On aggressive LODs (LOD3+), release UV and material constraints to save polycount
        eff_preserve_uv = 0.0
        eff_preserve_mat = 0.0
        # Only preserve very sharp structural angles (> 60 degrees)
        eff_sil_angle = max(silhouette_angle_rad, math.radians(60.0))

    for idx, v in enumerate(bm.verts):
        w_boundary = 0.0
        w_uv = 0.0
        w_uv_curve = 0.0
        w_mat = 0.0
        w_curve = 0.0
        w_skin = 0.0

        # --- 1. Open Boundary Check ---
        for e in v.link_edges:
            if len(e.link_faces) == 1:
                w_boundary = max(w_boundary, preserve_boundary)
                break

        # --- 2. UV Seam Check & 2D UV Border Curvature Analysis ---
        if eff_preserve_uv > 0.0:
            is_uv_seam = False
            for e in v.link_edges:
                if e.seam:
                    is_uv_seam = True
                    break

            if not is_uv_seam and uv_layer and len(v.link_faces) > 1:
                # Detect UV discontinuity across adjacent faces
                uv_coords = []
                for loop in v.link_loops:
                    uv_coords.append(loop[uv_layer].uv)
                
                # If distinct UV loops differ by more than epsilon, vertex sits on a UV cut
                first_uv = uv_coords[0]
                for other_uv in uv_coords[1:]:
                    if (first_uv - other_uv).length > 1e-4:
                        is_uv_seam = True
                        break

            if is_uv_seam:
                w_uv = eff_preserve_uv

            # 2b. Protection of 2D UV border curvature (turns > 5 degrees)
            if uv_border_curvature and uv_layer and (is_uv_seam or w_boundary > 0.0):
                border_uv_dirs = []
                for e in v.link_edges:
                    is_b_edge = (len(e.link_faces) == 1) or e.seam
                    if is_b_edge:
                        other_v = e.other_vert(v)
                        for f in e.link_faces:
                            lp_curr = None
                            lp_other = None
                            for lp in f.loops:
                                if lp.vert == v: lp_curr = lp
                                elif lp.vert == other_v: lp_other = lp
                            if lp_curr and lp_other:
                                d = lp_other[uv_layer].uv - lp_curr[uv_layer].uv
                                if d.length > 1e-6:
                                    border_uv_dirs.append(d)

                if len(border_uv_dirs) >= 2:
                    dev = calculate_uv_angle_deviation(
                        Vector((border_uv_dirs[0].x, border_uv_dirs[0].y, 0.0)),
                        Vector((border_uv_dirs[1].x, border_uv_dirs[1].y, 0.0))
                    )
                    # If border curves > 5 degrees, lock vertex to prevent cutting outside texture
                    if dev > math.radians(5.0):
                        w_uv_curve = 1.0

        # --- 3. Material Boundary Check ---
        if eff_preserve_mat > 0.0 and len(v.link_faces) > 1:
            mat_indices = {f.material_index for f in v.link_faces}
            if len(mat_indices) > 1:
                w_mat = eff_preserve_mat

        # --- 4. Curvature & Sharp Silhouette Check ---
        if eff_preserve_sil > 0.0:
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

            if is_sharp_edge:
                w_curve = eff_preserve_sil
            elif max_dihedral >= eff_sil_angle:
                # Proportional curve importance up to 90 degrees
                ratio = min(1.0, max_dihedral / (math.pi * 0.5))
                w_curve = ratio * eff_preserve_sil

        # --- 5. Skinning / Bone Joint Check ---
        if skinning_scores and idx < len(skinning_scores):
            w_skin = skinning_scores[idx] * preserve_skinning

        # Combine all weighted criteria
        combined_weight = max(w_boundary, w_uv, w_uv_curve, w_mat, w_curve, w_skin)
        combined_weight = clamp(combined_weight, 0.0, 1.0)

        # Apply progressive relaxation for LOD2
        if lod_level == 2 and relax_weights:
            # W' = W^2 softens intermediate weights while preserving 1.0 boundaries (1.0^2 = 1.0)
            combined_weight = math.pow(combined_weight, 2)

        weights[idx] = combined_weight

    return weights


def compute_skinning_joint_scores(obj: bpy.types.Object) -> list[float]:
    """
    Evaluates vertex bone weight sharing to protect articulation loops (elbows, knees, shoulders).
    Vertices influenced by 2+ bones with balanced weights receive a higher protection score.
    """
    mesh = obj.data
    scores = [0.0] * len(mesh.vertices)

    for i, v in enumerate(mesh.vertices):
        influences = [g.weight for g in v.groups if g.weight > 0.1]
        if len(influences) >= 2:
            # High entropy / balance indicates a bending articulation
            sum_w = sum(influences)
            if sum_w > 0:
                normalized = [w / sum_w for w in influences]
                # Measure how evenly distributed the weights are (max when all equal)
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

    # Get or create vertex group
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

    bm.free()

    for idx, w in enumerate(weights):
        vg.add([idx], w, 'REPLACE')

    return vg
