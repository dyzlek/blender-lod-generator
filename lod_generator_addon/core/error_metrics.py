"""
Geometric error metrics for error-budget and screen-size driven reduction.
"""

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from ..utils.math_helpers import calculate_bounding_sphere


def _bvh_and_points(obj: bpy.types.Object, max_samples: int):
    """BVH of the mesh surface plus an evenly strided subset of its vertices."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bvh = BVHTree.FromBMesh(bm)
    stride = max(1, len(bm.verts) // max_samples)
    points = [v.co.copy() for i, v in enumerate(bm.verts) if i % stride == 0]
    bm.free()
    return bvh, points


def measure_deviation(lod_obj: bpy.types.Object, lod0_obj: bpy.types.Object, max_samples: int = 20000) -> float:
    """
    Approximate symmetric Hausdorff distance between a LOD and LOD0, in world units:
    max(LOD vertices -> LOD0 surface, sampled LOD0 vertices -> LOD surface).
    Both meshes share the same local space (LODs are copies of LOD0).
    """
    if not lod_obj or not lod0_obj or not lod_obj.data.vertices or not lod0_obj.data.vertices:
        return 0.0

    bvh_lod, points_lod = _bvh_and_points(lod_obj, max_samples)
    bvh_ref, points_ref = _bvh_and_points(lod0_obj, max_samples)

    worst = 0.0
    for points, bvh in ((points_lod, bvh_ref), (points_ref, bvh_lod)):
        for co in points:
            hit = bvh.find_nearest(co)
            if hit[3] is not None and hit[3] > worst:
                worst = hit[3]

    scale = max(abs(s) for s in lod0_obj.matrix_world.to_scale())
    return worst * scale


def screen_size_to_error(diameter: float, screen_size: float, pixel_error: float, screen_height_px: int) -> float:
    """
    Largest geometric error invisible at a given screen size: when the object covers
    `screen_size` of the screen height, one pixel spans diameter / (screen_size * height).
    """
    pixels_covered = max(1e-6, screen_size * screen_height_px)
    return diameter * pixel_error / pixels_covered


def level_error_budget(settings, level: int, lod0_obj: bpy.types.Object, screen_thresholds: list[float]) -> float:
    """Maximum allowed deviation (world units) of a LOD level for the current reduction mode."""
    if settings.reduction_mode == 'SCREEN':
        _, radius = calculate_bounding_sphere(lod0_obj)
        scale = max(abs(s) for s in lod0_obj.matrix_world.to_scale())
        screen = screen_thresholds[level] if level < len(screen_thresholds) else screen_thresholds[-1] * 0.5
        return screen_size_to_error(2.0 * radius * scale, screen, settings.pixel_error, settings.screen_height_px)
    return settings.max_error * (settings.error_growth ** (level - 1))
