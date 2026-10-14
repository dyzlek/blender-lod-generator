"""
Maps the scene settings onto the keyword arguments of the LOD pipeline
(shared by the generate operator, the live preview and the error-budget search).
"""


def pipeline_kwargs(settings) -> dict:
    """Pipeline options from LODGeneratorSettings (everything except source, name, ratio and level)."""
    return {
        "pre_triangulate": settings.pre_triangulate,
        "use_delimit_uv": settings.use_delimit_uv,
        "uv_border_curvature": settings.uv_border_curvature,
        "bvh_repair_uv": settings.bvh_repair_uv,
        "split_weld_isolation": settings.split_weld_isolation,
        "preserve_uv": settings.preserve_uv_seams,
        "preserve_silhouette": settings.preserve_silhouette,
        "silhouette_angle_rad": settings.silhouette_angle,
        "preserve_boundary": settings.preserve_open_boundaries,
        "preserve_material": settings.preserve_material_borders,
        "preserve_skinning": settings.preserve_skinning,
        "relax_weights": settings.relax_weights_per_lod,
        "pre_planar_dissolve": settings.pre_planar_dissolve,
        "planar_angle_limit_rad": settings.planar_angle_limit,
        "cull_small_parts": settings.cull_small_parts,
        "cull_threshold_pct": settings.cull_threshold,
        "normal_strategy": settings.normal_strategy,
        "keep_protection_mask": settings.keep_protection_mask,
        "remesh_mode": settings.remesh_mode,
        "remesh_voxel_pct": settings.remesh_voxel_pct,
        "remesh_quadriflow_ratio": settings.remesh_quadriflow_ratio,
        "remesh_from_level": settings.remesh_from_level,
    }
