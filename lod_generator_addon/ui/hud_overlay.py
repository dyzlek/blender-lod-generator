"""
Live LOD preview overlay.
Draws the selected LOD level as a wireframe on top of the active object (LOD0) in the
3D View, without creating any scene object. The preview mesh is computed on a temporary
copy (removed right away) and recomputed, debounced, whenever a setting changes.
"""

import time
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.app.handlers import persistent

from ..core.decimator import generate_lod_mesh
from ..utils.math_helpers import calculate_ratios, get_triangulated_face_count

PREVIEW_TMP_NAME = "_LOD_Preview_Tmp"

# Polling interval of the change watcher and quiet time before a recompute (seconds)
POLL_INTERVAL = 0.1
DEBOUNCE_DELAY = 0.3

# Settings whose change requires recomputing the preview mesh
WATCHED_SETTINGS = (
    "preview_level", "lod_count", "reduction_preset", "generation_mode",
    "custom_lod1_ratio", "custom_lod2_ratio", "custom_lod3_ratio",
    "custom_lod4_ratio", "custom_lod5_ratio", "custom_lod6_ratio",
    "pre_triangulate", "use_delimit_uv", "uv_border_curvature", "split_weld_isolation",
    "preserve_uv_seams", "preserve_silhouette", "silhouette_angle",
    "preserve_open_boundaries", "preserve_material_borders", "preserve_skinning",
    "relax_weights_per_lod", "pre_planar_dissolve", "planar_angle_limit",
    "cull_small_parts", "cull_threshold",
)


class _PreviewState:
    """Module-level state shared by the timer and the draw callbacks."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.obj_name = ""          # object the preview is attached to
        self.coords = []            # preview vertices, in the object's local space
        self.edges = []
        self.batch = None           # GPU batch, built lazily in the draw callback
        self.level = 0
        self.triangles = 0
        self.source_triangles = 0
        self.built_signature = None
        self.pending_signature = None
        self.pending_since = 0.0
        self.error = ""


_state = _PreviewState()
_draw_handles = []


# --------------------------------------------------------------------------- geometry
def compute_preview_geometry(context, source_obj, settings, level: int) -> dict:
    """
    Runs the LOD pipeline on a temporary copy of source_obj and returns its wireframe
    ({"coords", "edges", "triangles", "source_triangles"}). The temporary object and
    mesh are always removed. Normal transfer and UV repair are skipped: they do not
    change the wireframe.
    """
    lod_count = max(settings.lod_count, level)
    custom_ratios = [
        settings.custom_lod1_ratio, settings.custom_lod2_ratio, settings.custom_lod3_ratio,
        settings.custom_lod4_ratio, settings.custom_lod5_ratio, settings.custom_lod6_ratio,
    ]
    ratio = calculate_ratios(settings.reduction_preset, lod_count, custom_ratios)[level - 1]
    source_triangles = get_triangulated_face_count(source_obj)

    tmp_obj, stats = generate_lod_mesh(
        context=context,
        source_obj=source_obj,
        lod0_obj=source_obj,
        lod_name=PREVIEW_TMP_NAME,
        target_ratio=ratio,
        lod_level=level,
        original_lod0_triangles=source_triangles,
        pre_triangulate=settings.pre_triangulate,
        use_delimit_uv=settings.use_delimit_uv,
        uv_border_curvature=settings.uv_border_curvature,
        bvh_repair_uv=False,
        split_weld_isolation=settings.split_weld_isolation,
        preserve_uv=settings.preserve_uv_seams,
        preserve_silhouette=settings.preserve_silhouette,
        silhouette_angle_rad=settings.silhouette_angle,
        preserve_boundary=settings.preserve_open_boundaries,
        preserve_material=settings.preserve_material_borders,
        preserve_skinning=settings.preserve_skinning,
        relax_weights=settings.relax_weights_per_lod,
        pre_planar_dissolve=settings.pre_planar_dissolve,
        planar_angle_limit_rad=settings.planar_angle_limit,
        cull_small_parts=settings.cull_small_parts,
        cull_threshold_pct=settings.cull_threshold,
        normal_strategy='NONE',
        keep_protection_mask=False,
    )

    mesh = tmp_obj.data
    try:
        geometry = {
            "coords": [v.co.copy() for v in mesh.vertices],
            "edges": [tuple(e.vertices) for e in mesh.edges],
            "triangles": stats["triangles"],
            "source_triangles": source_triangles,
        }
    finally:
        bpy.data.objects.remove(tmp_obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return geometry


def _signature(obj, settings):
    """Everything that changes the preview mesh; compared between timer ticks."""
    mesh = obj.data
    return (
        obj.name, mesh.name, len(mesh.vertices), len(mesh.polygons), tuple(obj.dimensions),
        tuple(getattr(settings, name) for name in WATCHED_SETTINGS),
    )


# --------------------------------------------------------------------------- watcher
def _view3d_override():
    """Window/area/region of a 3D View, so the pipeline runs with a full context from a timer."""
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                return {"window": window, "area": area, "region": region}
    return None


def _tag_redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _watch():
    """Timer: follows the active object and settings, recomputes once they are stable."""
    scene = bpy.context.scene
    settings = getattr(scene, "lod_generator_settings", None) if scene else None
    if settings is None or not settings.preview_enabled:
        return None

    obj = bpy.context.view_layer.objects.active
    if settings.is_generating:
        return POLL_INTERVAL
    if obj is None or obj.type != 'MESH' or (_state.obj_name and obj.name != _state.obj_name):
        # Active object changed: the preview belongs to the previous one, switch it off
        settings.preview_enabled = False
        return None
    if obj.mode != 'OBJECT':
        return POLL_INTERVAL

    _state.obj_name = obj.name
    signature = _signature(obj, settings)
    if signature == _state.built_signature:
        return POLL_INTERVAL

    now = time.perf_counter()
    if signature != _state.pending_signature:
        _state.pending_signature = signature
        _state.pending_since = now
        _tag_redraw()
        return POLL_INTERVAL
    if now - _state.pending_since < DEBOUNCE_DELAY:
        return POLL_INTERVAL

    override = _view3d_override()
    if override is None:
        return POLL_INTERVAL
    try:
        with bpy.context.temp_override(**override):
            geometry = compute_preview_geometry(bpy.context, obj, settings, settings.preview_level)
        _state.coords = geometry["coords"]
        _state.edges = geometry["edges"]
        _state.triangles = geometry["triangles"]
        _state.source_triangles = geometry["source_triangles"]
        _state.level = settings.preview_level
        _state.batch = None
        _state.error = ""
    except Exception as err:
        _state.error = str(err)
        print(f"[LOD Generator] Aperçu impossible : {err}")
    _state.built_signature = signature
    _state.pending_signature = None
    _tag_redraw()
    return POLL_INTERVAL


# --------------------------------------------------------------------------- drawing
def _preview_object():
    obj = bpy.data.objects.get(_state.obj_name) if _state.obj_name else None
    if obj is None or not obj.visible_get():
        return None
    return obj


def _draw_wireframe():
    obj = _preview_object()
    if obj is None or not _state.edges:
        return
    settings = bpy.context.scene.lod_generator_settings

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    if _state.batch is None:
        _state.batch = batch_for_shader(shader, 'LINES', {"pos": _state.coords}, indices=_state.edges)

    gpu.state.blend_set('ALPHA')
    gpu.matrix.push()
    gpu.matrix.multiply_matrix(obj.matrix_world)
    shader.bind()
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", 1.5)
    shader.uniform_float("color", tuple(settings.preview_color))
    _state.batch.draw(shader)
    gpu.matrix.pop()
    gpu.state.blend_set('NONE')


def _draw_legend():
    if _preview_object() is None:
        return
    if _state.error:
        text = f"Aperçu LOD : erreur ({_state.error})"
    elif _state.pending_signature is not None or not _state.edges:
        text = "Aperçu LOD : calcul..."
    else:
        gain = (1.0 - _state.triangles / max(1, _state.source_triangles)) * 100.0
        text = f"Aperçu LOD{_state.level} : {_state.triangles:,} tris (-{gain:.1f}%)"

    font_id = 0
    blf.size(font_id, 14)
    blf.color(font_id, 1.0, 1.0, 1.0, 0.9)
    blf.position(font_id, 20, 40, 0)
    blf.draw(font_id, text)


# --------------------------------------------------------------------------- lifecycle
def start_preview():
    """Installs the draw handlers and the change watcher (idempotent)."""
    _state.reset()
    if not _draw_handles:
        _draw_handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_wireframe, (), 'WINDOW', 'POST_VIEW'))
        _draw_handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_legend, (), 'WINDOW', 'POST_PIXEL'))
    if not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=0.0)


def stop_preview():
    """Removes draw handlers, watcher and cached geometry."""
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    for handle in _draw_handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _draw_handles.clear()
    _state.reset()
    _tag_redraw()


def get_preview_info() -> dict:
    """Summary for the panel."""
    return {
        "level": _state.level,
        "triangles": _state.triangles,
        "source_triangles": _state.source_triangles,
        "computing": _state.pending_signature is not None,
        "error": _state.error,
    }


@persistent
def _on_load_post(_dummy):
    # A file saved with the preview on has no watcher running: reset the flag
    stop_preview()
    for scene in bpy.data.scenes:
        settings = getattr(scene, "lod_generator_settings", None)
        if settings and settings.preview_enabled:
            settings["preview_enabled"] = False


def register_overlay():
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister_overlay():
    stop_preview()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
