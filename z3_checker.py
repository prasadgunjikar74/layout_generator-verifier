import json
import math
import os

from shapely.geometry import Polygon, Point

# ============================================================
# LOAD FILES
# ============================================================

with open("input.json", "r") as f:
    input_data = json.load(f)

with open("generated_layout.json", "r") as f:
    generated_data = json.load(f)

# ============================================================
# EXTRACT DATA
# ============================================================

site_polygon_points = input_data["site"]["boundary"]["points"]

building_points = generated_data["building_outline"]["points"]

constraints = input_data["constraints"]

protected_tree = input_data["protected_objects"][0]

tree_center = (
    protected_tree["geometry"]["center"][0],
    protected_tree["geometry"]["center"][1]
)

tree_radius = (
    protected_tree["protection_rules"]["total_exclusion_radius"]
)

# ============================================================
# LOAD CONSTRAINT VALUES
# ============================================================

front_setback = 0
rear_setback = 0
left_setback = 0
right_setback = 0
max_allowed_area = 0

for constraint in constraints:

    if constraint["type"] == "front_setback":

        front_setback = constraint["value"]

    elif constraint["type"] == "rear_setback":

        rear_setback = constraint["value"]

    elif constraint["type"] == "side_setback_left":

        left_setback = constraint["value"]

    elif constraint["type"] == "side_setback_right":

        right_setback = constraint["value"]

    elif constraint["type"] == "max_building_footprint":

        max_allowed_area = constraint["value"]

# ============================================================
# SHAPELY POLYGONS
# ============================================================

site_poly = Polygon(site_polygon_points)

building_poly = Polygon(building_points)

inner_constraints = input_data.get("inner_constraints", [])

violations = []

# ============================================================
# POLYGON AREA
# ============================================================

def polygon_area(poly):

    area = 0

    n = len(poly)

    for i in range(n):

        x1, y1 = poly[i]

        x2, y2 = poly[(i + 1) % n]

        area += (x1 * y2) - (x2 * y1)

    return abs(area) / 2

# ============================================================
# EDGE INTERSECTS TREE CIRCLE
# ============================================================

def edge_intersects_circle(p1, p2, center, radius):

    x1, y1 = p1
    x2, y2 = p2

    cx, cy = center

    dx = x2 - x1
    dy = y2 - y1

    length_sq = dx * dx + dy * dy

    if length_sq == 0:
        dist = math.dist(p1, center)
        return dist <= radius

    t = (
        ((cx - x1) * dx + (cy - y1) * dy)
        / length_sq
    )

    t = max(0, min(1, t))

    nearest_x = x1 + t * dx
    nearest_y = y1 + t * dy

    distance = math.dist(
        (nearest_x, nearest_y),
        center
    )

    return distance <= radius

# ============================================================
# ROOM ADJACENCY HELPERS
# ============================================================

def rooms_are_adjacent(poly1, poly2, min_shared_length=0.01):
    """True if two room polygons share a wall (non-trivial shared boundary)."""
    try:
        shared = poly1.intersection(poly2)
        return shared.length > min_shared_length
    except Exception:
        return False

def bfs_reachable(room_polys_by_name, start_room):
    """BFS through shared-wall adjacency graph. Returns set of reachable room names."""
    visited = {start_room}
    queue = [start_room]
    while queue:
        current = queue.pop(0)
        current_poly = room_polys_by_name[current]
        for other_name, other_poly in room_polys_by_name.items():
            if other_name not in visited:
                if rooms_are_adjacent(current_poly, other_poly):
                    visited.add(other_name)
                    queue.append(other_name)
    return visited

# ============================================================
# SITE BOUNDARY HELPERS FOR SETBACK CHECKS
# ============================================================
# Compute actual site boundary Y (or X) at a given X (or Y)
# position by intersecting with site exterior edges.
# This correctly handles irregular site polygons.

site_exterior_coords = list(site_poly.exterior.coords)

def site_y_range_at_x(px):
    """Return (south_y, north_y) of the site at x=px, or (None, None) if outside site."""
    ys = []
    n = len(site_exterior_coords) - 1
    for i in range(n):
        x1, y1 = site_exterior_coords[i]
        x2, y2 = site_exterior_coords[i + 1]
        x_lo, x_hi = min(x1, x2), max(x1, x2)
        if x_lo <= px <= x_hi:
            if x1 == x2:
                ys.extend([y1, y2])
            else:
                t = (px - x1) / (x2 - x1)
                ys.append(y1 + t * (y2 - y1))
    if not ys:
        return None, None
    return min(ys), max(ys)

def site_x_range_at_y(py):
    """Return (west_x, east_x) of the site at y=py, or (None, None) if outside site."""
    xs = []
    n = len(site_exterior_coords) - 1
    for i in range(n):
        x1, y1 = site_exterior_coords[i]
        x2, y2 = site_exterior_coords[i + 1]
        y_lo, y_hi = min(y1, y2), max(y1, y2)
        if y_lo <= py <= y_hi:
            if y1 == y2:
                xs.extend([x1, x2])
            else:
                t = (py - y1) / (y2 - y1)
                xs.append(x1 + t * (x2 - x1))
    if not xs:
        return None, None
    return min(xs), max(xs)

# ============================================================
# INVALID POLYGON CHECK
# ============================================================

if not building_poly.is_valid:

    violations.append({
        "constraint_id": "C0",
        "type": "invalid_polygon",
        "message":
            "Building polygon is self-intersecting or invalid"
    })

# ============================================================
# BUILDING AREA CHECK
# ============================================================

building_area = polygon_area(building_points)

print(f"\nBuilding Area = {building_area}\n")

if building_area > max_allowed_area:

    violations.append({
        "constraint_id": "C5",
        "type": "max_building_footprint",
        "message":
            f"Building area {building_area:.2f} exceeds "
            f"maximum allowed {max_allowed_area:.2f}"
    })

# ============================================================
# SITE BOUNDARY CHECK (C6)
# ============================================================

if not site_poly.covers(building_poly):

    violations.append({
        "constraint_id": "C6",
        "type": "outside_site_boundary",
        "message":
            "Building polygon extends outside site boundary"
    })

# ============================================================
# SETBACK CHECKS
# ============================================================
# For each building vertex, look up the ACTUAL site boundary
# at that vertex's x- or y-position. This correctly handles
# irregular site polygons where the boundary is not uniform.

front_setback_violations = 0
rear_setback_violations = 0
left_setback_violations = 0
right_setback_violations = 0

for point in building_points:
    px, py = point

    south_y, north_y = site_y_range_at_x(px)
    west_x, east_x = site_x_range_at_y(py)

    # FRONT SETBACK (south)
    if south_y is not None and py < south_y + front_setback:
        front_setback_violations += 1

    # REAR SETBACK (north)
    if north_y is not None and py > north_y - rear_setback:
        rear_setback_violations += 1

    # LEFT SETBACK (west)
    if west_x is not None and px < west_x + left_setback:
        left_setback_violations += 1

    # RIGHT SETBACK (east)
    if east_x is not None and px > east_x - right_setback:
        right_setback_violations += 1

if front_setback_violations > 0:
    violations.append({
        "constraint_id": "C1",
        "type": "front_setback",
        "message":
            f"Building violates front (south) setback of {front_setback}ft"
    })

if rear_setback_violations > 0:
    violations.append({
        "constraint_id": "C2",
        "type": "rear_setback",
        "message":
            f"Building violates rear (north) setback of {rear_setback}ft"
    })

if left_setback_violations > 0:
    violations.append({
        "constraint_id": "C3",
        "type": "left_setback",
        "message":
            f"Building violates left (west) setback of {left_setback}ft"
    })

if right_setback_violations > 0:
    violations.append({
        "constraint_id": "C4",
        "type": "right_setback",
        "message":
            f"Building violates right (east) setback of {right_setback}ft"
    })

# ============================================================
# TREE CHECK
# ============================================================
# Check both edge intersection AND tree containment inside building.

tree_point = Point(tree_center)
tree_circle = tree_point.buffer(tree_radius)

if building_poly.intersects(tree_circle):

    # Check which edges intersect for a precise message
    edge_violations = []
    for i in range(len(building_points)):
        p1 = building_points[i]
        p2 = building_points[(i + 1) % len(building_points)]
        if edge_intersects_circle(p1, p2, tree_center, tree_radius):
            edge_violations.append(f"{p1}->{p2}")

    if edge_violations:
        message = f"Building edge(s) intersect tree exclusion zone: {', '.join(edge_violations)}"
    else:
        message = "Building encloses tree exclusion zone"

    violations.append({
        "constraint_id": "C7",
        "type": "tree_exclusion_zone",
        "message": message
    })

# ============================================================
# INNER LAYOUT CHECK - ROOMS INSIDE BUILDING
# ============================================================

if os.path.exists("allocated_rooms.json"):
    try:
        with open("allocated_rooms.json", "r") as f:
            allocated_data = json.load(f)

        allocated_rooms = allocated_data.get("rooms", [])

        print(f"\n--- Inner Layout Validation ---")
        print(f"Checking {len(allocated_rooms)} rooms...\n")

        for room in allocated_rooms:
            room_name = room["name"]
            room_polygon_coords = room["polygon"]

            try:
                room_poly = Polygon(room_polygon_coords)

                if not building_poly.covers(room_poly):
                    violations.append({
                        "constraint_id": "INNER_C1",
                        "type": "room_outside_building",
                        "message": f"Room '{room_name}' extends outside building boundary"
                    })
                    print(f"[FAIL] {room_name}: OUTSIDE building boundary")

                elif not room_poly.is_valid:
                    violations.append({
                        "constraint_id": "INNER_C2",
                        "type": "invalid_room_polygon",
                        "message": f"Room '{room_name}' has invalid polygon geometry"
                    })
                    print(f"[FAIL] {room_name}: INVALID polygon geometry")

                else:
                    print(f"[OK]   {room_name}: Valid (inside building)")

            except Exception as e:
                violations.append({
                    "constraint_id": "INNER_C3",
                    "type": "room_validation_error",
                    "message": f"Error validating room '{room_name}': {str(e)}"
                })
                print(f"[ERR]  {room_name}: Validation error - {str(e)}")

        print(f"\nChecking room overlaps...\n")

        for i, room1 in enumerate(allocated_rooms):
            room1_poly = Polygon(room1["polygon"])

            for j, room2 in enumerate(allocated_rooms):
                if i >= j:
                    continue

                room2_poly = Polygon(room2["polygon"])

                if room1_poly.intersects(room2_poly) and not room1_poly.touches(room2_poly):
                    violations.append({
                        "constraint_id": "INNER_C4",
                        "type": "room_overlap",
                        "message": f"Room '{room1['name']}' overlaps with '{room2['name']}'"
                    })
                    print(f"[FAIL] OVERLAP: '{room1['name']}' overlaps '{room2['name']}'")

        if len(allocated_rooms) > 0:
            total_room_area = sum(
                polygon_area(room["polygon"])
                for room in allocated_rooms
            )
            building_coverage = (total_room_area / building_area) * 100 if building_area > 0 else 0
            print(f"\nBuilding coverage: {building_coverage:.1f}% ({total_room_area:.1f}/{building_area:.1f} sq units)")

        # --------------------------------------------------------
        # BUILD ROOM POLYGON LOOKUP FOR CONSTRAINT CHECKS
        # --------------------------------------------------------

        room_polys_by_name = {}
        for room in allocated_rooms:
            try:
                room_polys_by_name[room["name"]] = Polygon(room["polygon"])
            except Exception:
                pass

        # --------------------------------------------------------
        # ADJACENCY CONSTRAINTS (from input.json inner_constraints)
        # --------------------------------------------------------

        print(f"\n--- Adjacency Constraint Checks ---\n")

        for ic in inner_constraints:

            if ic["type"] != "room_adjacency":
                continue

            cid = ic["id"]
            a = ic["room_a"]
            b = ic["room_b"]

            poly_a = room_polys_by_name.get(a)
            poly_b = room_polys_by_name.get(b)

            if poly_a is None:
                violations.append({
                    "constraint_id": cid,
                    "type": "room_adjacency",
                    "message": f"Room '{a}' not found in allocated rooms"
                })
                print(f"[ERR]  {cid}: Room '{a}' not in layout")
                continue

            if poly_b is None:
                violations.append({
                    "constraint_id": cid,
                    "type": "room_adjacency",
                    "message": f"Room '{b}' not found in allocated rooms"
                })
                print(f"[ERR]  {cid}: Room '{b}' not in layout")
                continue

            if rooms_are_adjacent(poly_a, poly_b):
                print(f"[OK]   {cid}: '{a}' <-> '{b}' are adjacent")
            else:
                violations.append({
                    "constraint_id": cid,
                    "type": "room_adjacency",
                    "message": f"'{a}' and '{b}' must be adjacent but share no wall"
                })
                print(f"[FAIL] {cid}: '{a}' and '{b}' are NOT adjacent")

        # --------------------------------------------------------
        # CORRIDOR CONNECTIVITY (from input.json inner_constraints)
        # --------------------------------------------------------

        for ic in inner_constraints:

            if ic["type"] != "corridor_connectivity":
                continue

            cid = ic["id"]
            corridor_name = ic["corridor_room"]
            entry_name = ic["entry_room"]
            private_rooms = ic.get("private_rooms", [])

            print(f"\n--- Corridor Connectivity Check ---\n")

            corridor_poly = room_polys_by_name.get(corridor_name)

            if corridor_poly is None:
                violations.append({
                    "constraint_id": cid,
                    "type": "corridor_connectivity",
                    "message": f"Corridor room '{corridor_name}' not found in layout"
                })
                print(f"[ERR]  Corridor '{corridor_name}' not in layout")

            else:
                for room_name in private_rooms:
                    room_poly = room_polys_by_name.get(room_name)
                    if room_poly is None:
                        violations.append({
                            "constraint_id": cid,
                            "type": "corridor_connectivity",
                            "message": f"Private room '{room_name}' not found in layout"
                        })
                        print(f"[ERR]  '{room_name}' not in layout")
                    elif rooms_are_adjacent(corridor_poly, room_poly):
                        print(f"[OK]   '{room_name}' is directly accessible from '{corridor_name}'")
                    else:
                        violations.append({
                            "constraint_id": cid,
                            "type": "corridor_connectivity",
                            "message": f"'{room_name}' is not directly accessible from '{corridor_name}'"
                        })
                        print(f"[FAIL] '{room_name}' is NOT directly accessible from '{corridor_name}'")

            # BFS full connectivity from entry room
            print(f"\n--- Full Connectivity from '{entry_name}' ---\n")

            if entry_name not in room_polys_by_name:
                violations.append({
                    "constraint_id": cid,
                    "type": "corridor_connectivity",
                    "message": f"Entry room '{entry_name}' not found in layout"
                })
                print(f"[ERR]  Entry room '{entry_name}' not in layout")
            else:
                reachable = bfs_reachable(room_polys_by_name, entry_name)
                all_names = set(room_polys_by_name.keys())
                unreachable = all_names - reachable
                for room_name in sorted(unreachable):
                    violations.append({
                        "constraint_id": cid,
                        "type": "room_unreachable",
                        "message": f"'{room_name}' is not reachable from '{entry_name}'"
                    })
                    print(f"[FAIL] '{room_name}' is NOT reachable from '{entry_name}'")
                if not unreachable:
                    print(f"[OK]   All rooms are reachable from '{entry_name}'")

    except Exception as e:
        violations.append({
            "constraint_id": "INNER_C0",
            "type": "inner_layout_error",
            "message": f"Inner layout validation failed: {str(e)}"
        })
        print(f"\nWarning: Inner layout validation failed - {str(e)}")

# ============================================================
# REMOVE DUPLICATES
# ============================================================

unique_violations = []

seen = set()

for violation in violations:

    key = (
        violation["constraint_id"],
        violation["message"]
    )

    if key not in seen:

        seen.add(key)

        unique_violations.append(violation)

violations = unique_violations

# ============================================================
# FINAL RESULT
# ============================================================

if len(violations) == 0:

    print("LAYOUT VALID")

    if os.path.exists("feedback.txt"):

        os.remove("feedback.txt")

        print("Old feedback.txt removed")

    with open("violations.json", "w") as f:

        json.dump([], f)

    exit()

else:

    print("LAYOUT INVALID")

    with open("violations.json", "w") as f:

        json.dump(
            violations,
            f,
            indent=2
        )

    print("\nViolations saved to violations.json\n")
