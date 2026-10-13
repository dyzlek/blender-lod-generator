"""
Math and Geometry Helpers for Procedural LOD Generator.
Handles bounding volume calculation, triangulated face counting,
reduction curves, and screen-space size projections.
"""

import math
from mathutils import Vector


def clamp(val: float, min_val: float, max_val: float) -> float:
    """Clamps a numeric value between min_val and max_val."""
    return max(min_val, min(val, max_val))


def calculate_uv_signed_area(uv0: Vector, uv1: Vector, uv2: Vector) -> float:
    """
    Computes the 2D signed area of a UV triangle:
    0.5 * ((u1 - u0)*(v2 - v0) - (v1 - v0)*(u2 - u0))
    A near-zero area indicates a degenerated / flattened UV triangle (barcode artifact).
    A flipped sign indicates an inverted UV foldover.
    """
    return 0.5 * ((uv1.x - uv0.x) * (uv2.y - uv0.y) - (uv1.y - uv0.y) * (uv2.x - uv0.x))


def calculate_triangle_3d_area(v0: Vector, v1: Vector, v2: Vector) -> float:
    """Computes the 3D area of a triangle formed by three vertices."""
    cross_prod = (v1 - v0).cross(v2 - v0)
    return 0.5 * cross_prod.length


def calculate_uv_angle_deviation(dir1: Vector, dir2: Vector) -> float:
    """
    Measures deviation (in radians) from a collinear straight line for two 2D UV border directions.
    If segments are straight, they point in opposite directions (angle = 180 deg, deviation = 0 deg).
    If the border turns, deviation > 0.
    """
    l1 = dir1.length
    l2 = dir2.length
    if l1 < 1e-7 or l2 < 1e-7:
        return 0.0
    dot = clamp(dir1.dot(dir2) / (l1 * l2), -1.0, 1.0)
    angle = math.acos(dot)
    # Deviation from opposite collinear vectors (pi radians)
    return abs(math.pi - angle)


def get_triangulated_face_count(obj) -> int:
    """
    Computes the exact number of triangles a mesh object would have once triangulated.
    For an n-gon with n vertices, triangle count is (n - 2).
    """
    if not obj or obj.type != 'MESH' or not obj.data:
        return 0
    return sum(max(1, len(poly.vertices) - 2) for poly in obj.data.polygons)


def calculate_bounding_box(obj):
    """
    Calculates the local bounding box min, max, and dimensions of an object.
    Returns (min_co, max_co, dimensions).
    """
    if not obj or not obj.bound_box:
        return Vector((0, 0, 0)), Vector((0, 0, 0)), Vector((0, 0, 0))

    coords = [Vector(pt) for pt in obj.bound_box]
    min_co = Vector((
        min(p.x for p in coords),
        min(p.y for p in coords),
        min(p.z for p in coords)
    ))
    max_co = Vector((
        max(p.x for p in coords),
        max(p.y for p in coords),
        max(p.z for p in coords)
    ))
    dimensions = max_co - min_co
    return min_co, max_co, dimensions


def calculate_bounding_sphere(obj):
    """
    Calculates bounding center and bounding radius for an object in local coordinates.
    Returns (center, radius).
    """
    min_co, max_co, dimensions = calculate_bounding_box(obj)
    center = (min_co + max_co) * 0.5
    radius = (dimensions * 0.5).length
    if radius <= 1e-6:
        radius = 0.5
    return center, radius


def calculate_coords_bounding_sphere(coords: list[Vector]):
    """
    Calculates bounding center and radius from a list of 3D coordinates.
    Returns (center, radius).
    """
    if not coords:
        return Vector((0, 0, 0)), 0.0

    min_x = min(p.x for p in coords)
    max_x = max(p.x for p in coords)
    min_y = min(p.y for p in coords)
    max_y = max(p.y for p in coords)
    min_z = min(p.z for p in coords)
    max_z = max(p.z for p in coords)

    center = Vector(((min_x + max_x) * 0.5, (min_y + max_y) * 0.5, (min_z + max_z) * 0.5))
    radius = max((p - center).length for p in coords)
    return center, radius


def calculate_ratios(preset: str, count: int, custom_ratios: list[float] | None = None) -> list[float]:
    """
    Computes polygon reduction ratios for k in {1, ..., count}.
    - EXPONENTIAL: (0.5)^k -> 50%, 25%, 12.5%, ...
    - LINEAR: 1.0 - k / (count + 1)
    - CINEMATIC: (0.7)^k -> 70%, 49%, 34.3%, ...
    - CUSTOM: Uses user-defined ratios.
    """
    ratios = []
    for k in range(1, count + 1):
        if preset == 'EXPONENTIAL':
            r = math.pow(0.5, k)
        elif preset == 'LINEAR':
            r = 1.0 - (k / (count + 1))
        elif preset == 'CINEMATIC':
            r = math.pow(0.7, k)
        elif preset == 'CUSTOM' and custom_ratios and len(custom_ratios) >= k:
            r = custom_ratios[k - 1]
        else:
            r = math.pow(0.5, k)
        
        # Clamp to reasonable bounds
        ratios.append(clamp(r, 0.005, 0.99))
    return ratios


def calculate_screen_size(radius: float, distance: float, fov_rad: float = math.radians(60.0)) -> float:
    """
    Calculates the screen space relative coverage:
    ScreenSize = (2 * R) / (distance * tan(FOV / 2))
    """
    if distance <= 1e-4:
        return 1.0
    denom = distance * math.tan(fov_rad * 0.5)
    if denom <= 1e-6:
        return 1.0
    screen_size = (2.0 * radius) / denom
    return clamp(screen_size, 0.0, 1.0)


def default_screen_thresholds(count: int) -> list[float]:
    """
    Generates standard game engine screen size transition thresholds.
    e.g. for count=3: LOD0 -> 0.5, LOD1 -> 0.25, LOD2 -> 0.12, LOD3 -> 0.04
    """
    thresholds = []
    base = 0.5
    for k in range(count):
        thresholds.append(round(base * math.pow(0.5, k), 3))
    return thresholds
