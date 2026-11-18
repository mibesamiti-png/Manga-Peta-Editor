"""Small, Qt-independent geometry helpers for manga panel polygons.

Points may be Qt QPointF instances or (x, y) pairs.  No project data or
runtime GUI objects are read here, so synthetic geometry tests can run alone.
"""
import math


def xy(point):
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x()), float(point.y())
    return float(point[0]), float(point[1])


def edge_midpoint(points, edge_index):
    a = xy(points[edge_index % len(points)])
    b = xy(points[(edge_index + 1) % len(points)])
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


def _cross(a, b, c):
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(a, b, c, epsilon):
    """Is c on the inclusive segment a-b?"""
    return (
        min(a[0], b[0]) - epsilon <= c[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= c[1] <= max(a[1], b[1]) + epsilon
    )


def _segments_touch_or_cross(a, b, c, d, epsilon):
    ab_c = _cross(a, b, c)
    ab_d = _cross(a, b, d)
    cd_a = _cross(c, d, a)
    cd_b = _cross(c, d, b)
    if ((ab_c > epsilon and ab_d < -epsilon or ab_c < -epsilon and ab_d > epsilon)
            and (cd_a > epsilon and cd_b < -epsilon or cd_a < -epsilon and cd_b > epsilon)):
        return True
    for cross, start, end, pt in (
        (ab_c, a, b, c), (ab_d, a, b, d),
        (cd_a, c, d, a), (cd_b, c, d, b),
    ):
        if abs(cross) <= epsilon and _on_segment(start, end, pt, epsilon):
            return True
    return False


def valid_polygon(points, min_edge_length=12.0, min_area_twice=576.0):
    """Validate a simple polygon (triangles, concave shapes, and collinear inserts).

    Adjacent collinear *forward* edges are legal because pressing E inserts a
    vertex at an edge midpoint without changing the outline.  Backtracking or
    overlapping edges, self-crossings, zero-length edges and tiny shapes are
    rejected.  The result does not depend on clockwise/counterclockwise order.
    """
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        return False
    try:
        pts = [xy(p) for p in points]
    except (TypeError, ValueError, IndexError, AttributeError):
        return False
    if any(not math.isfinite(v) for pt in pts for v in pt):
        return False
    n = len(pts)
    area2 = 0.0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < min_edge_length:
            return False
        area2 += a[0] * b[1] - b[0] * a[1]
    if abs(area2) < min_area_twice:
        return False

    epsilon = 1e-7
    # Reject consecutive edges that double back over one another.
    for i in range(n):
        prev, curr, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
        if abs(_cross(prev, curr, nxt)) <= epsilon:
            a = (prev[0] - curr[0], prev[1] - curr[1])
            b = (nxt[0] - curr[0], nxt[1] - curr[1])
            if a[0] * b[0] + a[1] * b[1] > 0:
                return False

    # Adjacent edges legitimately share an endpoint. Every other contact is
    # invalid (including tangencies and nonadjacent shared vertices).
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == (i + 1) % n or i == (j + 1) % n:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            if _segments_touch_or_cross(a, b, c, d, epsilon):
                return False
    return True
