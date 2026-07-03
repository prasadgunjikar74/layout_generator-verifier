import json
import math
import ezdxf
from shapely.geometry import Polygon as ShapelyPolygon, LineString, Point as ShapelyPoint

# ------------------------------------------------
# LOAD INPUT FILES
# ------------------------------------------------

with open("input.json", "r") as f:
    input_data = json.load(f)

with open("generated_layout.json", "r") as f:
    generated_data = json.load(f)

# ------------------------------------------------
# EXTRACT SITE DATA
# ------------------------------------------------

site_polygon = input_data["site"]["boundary"]["points"]

building_points = generated_data["building_outline"]["points"]

building_boundary = ShapelyPolygon(building_points).boundary

tree = input_data["protected_objects"][0]

tree_center = (
    tree["geometry"]["center"][0],
    tree["geometry"]["center"][1]
)

tree_radius = tree["protection_rules"]["total_exclusion_radius"]

tree_trunk_radius = tree["geometry"]["radius"]

# ------------------------------------------------
# CREATE DXF DOCUMENT
# ------------------------------------------------

doc = ezdxf.new()

msp = doc.modelspace()

# ------------------------------------------------
# CREATE LAYERS
# ------------------------------------------------

doc.layers.new(
    name="SITE",
    dxfattribs={"color": 8}  # gray
)

doc.layers.new(
    name="BUILDING",
    dxfattribs={"color": 3}  # green
)

doc.layers.new(
    name="TREE",
    dxfattribs={"color": 1}  # red
)

doc.layers.new(
    name="DIMENSIONS",
    dxfattribs={"color": 4}  # cyan
)

# ------------------------------------------------
# DRAW SITE POLYGON
# ------------------------------------------------
msp.add_lwpolyline(
            site_polygon,
            close=True,
            dxfattribs={
                "layer": "SITE"
            }
        )

for i in range(len(site_polygon) - 1):

    p1 = tuple(site_polygon[i])
    p2 = tuple(site_polygon[i + 1])
  
    # --------------------------------------------
    # SITE DIMENSIONS
    # --------------------------------------------

    length = math.dist(p1, p2)

    mx = (p1[0] + p2[0]) / 2
    my = (p1[1] + p2[1]) / 2

    msp.add_text(
        f"{length:.1f} ft",
        dxfattribs={
            "height": 1,
            "layer": "DIMENSIONS"
        }
    ).set_placement((mx, my))

for i in range(len(building_points) - 1):

    p1 = tuple(building_points[i])
    p2 = tuple(building_points[i + 1])

  

    # --------------------------------------------
    # BUILDING DIMENSIONS
    # --------------------------------------------

    length = math.dist(p1, p2)

    mx = (p1[0] + p2[0]) / 2
    my = (p1[1] + p2[1]) / 2

    msp.add_text(
        f"{length:.1f} ft",
        dxfattribs={
            "height": 1,
            "layer": "DIMENSIONS"
        }
    ).set_placement((mx, my))

# ------------------------------------------------
# DRAW TREE EXCLUSION ZONE
# ------------------------------------------------

msp.add_circle(
    tree_center,
    tree_radius,
    dxfattribs={
        "layer": "TREE"
    }
)

# ------------------------------------------------
# DRAW TREE TRUNK
# ------------------------------------------------

msp.add_circle(
    tree_center,
    tree_trunk_radius,
    dxfattribs={
        "layer": "TREE"
    }
)

# ------------------------------------------------
# OPTIONAL: LABEL TREE
# ------------------------------------------------

msp.add_text(
    "Protected Tree",
    dxfattribs={
        "height": 1,
        "layer": "TREE"
    }
).set_placement((
    tree_center[0] + 2,
    tree_center[1] + 2
))
# ------------------------------------------------
# LOAD ALLOCATED ROOMS (optional — skipped in Phase 1)
# ------------------------------------------------

import os as _os
allocated_rooms = []
if _os.path.exists("allocated_rooms.json"):
    with open("allocated_rooms.json", "r") as f:
        allocated_data = json.load(f)
    allocated_rooms = allocated_data["rooms"]

# ------------------------------------------------
# CREATE INTERIOR LAYERS (only when rooms exist)
# ------------------------------------------------

if allocated_rooms:
    doc.layers.new(name="ROOMS",   dxfattribs={"color": 6})
    doc.layers.new(name="DOORS",   dxfattribs={"color": 2})
    doc.layers.new(name="WINDOWS", dxfattribs={"color": 5})

# Pre-build room boundary objects for the exterior-wall check
room_boundaries_shapely = {
    room["name"]: ShapelyPolygon(room["polygon"]).boundary
    for room in allocated_rooms
}

# ------------------------------------------------
# DRAW ROOMS
# ------------------------------------------------

for room in allocated_rooms:

    room_name = room["name"]

    polygon = room["polygon"]

    # ------------------------------------------------
    # DRAW ROOM BOUNDARY
    # ------------------------------------------------

    msp.add_lwpolyline(

        polygon,

        close=True,

        dxfattribs={
            "layer": "ROOMS"
        }
    )

    # ------------------------------------------------
    # ROOM CENTER — use representative_point so the label always
    # lands inside the polygon, even for concave or L-shaped rooms.
    # ------------------------------------------------

    _rp = ShapelyPolygon(polygon).representative_point()
    cx, cy = _rp.x, _rp.y

    # ------------------------------------------------
    # ROOM LABEL
    # ------------------------------------------------

    msp.add_text(

        room_name,

        dxfattribs={
            "height": 1.8,
            "layer": "DIMENSIONS"
        }

    ).set_placement((cx, cy))

    # ------------------------------------------------
    # WINDOWS — exterior walls only (walls on building boundary)
    # ------------------------------------------------
    # WINDOWS — draw on exterior walls only
    # A wall is exterior when:
    #   (a) it is not shared with any other room (intersection < 0.3 ft)
    #   (b) its midpoint is within 2 ft of the building boundary
    #       (rooms have margin=1.0 so their outer walls are ~1 ft from the outline)
    # Condition (b) guards against corridors / inner walls that happen to
    # have no direct neighbour but are still deep inside the building.
    # ------------------------------------------------

    _EPS = 1e-6  # float tolerance for axis-aligned wall check

    for i in range(len(polygon)):

        p1 = polygon[i]
        p2 = polygon[(i + 1) % len(polygon)]

        x1, y1 = p1
        x2, y2 = p2

        # Skip zero-length edges
        if math.dist(p1, p2) < _EPS:
            continue

        midpoint_x = (x1 + x2) / 2
        midpoint_y = (y1 + y2) / 2

        # (a) not shared with another room
        wall_line = LineString([p1, p2])
        is_exterior = True
        for other in allocated_rooms:
            if other["name"] == room_name:
                continue
            if wall_line.intersection(room_boundaries_shapely[other["name"]]).length > 0.3:
                is_exterior = False
                break
        if not is_exterior:
            continue

        # (b) midpoint within 2 ft of building outline
        if building_boundary.distance(ShapelyPoint(midpoint_x, midpoint_y)) > 2.0:
            continue

        # HORIZONTAL WALL — use epsilon instead of exact equality
        if abs(y2 - y1) <= _EPS:

            wall_length = abs(x2 - x1)

            if wall_length > 4:

                window_size = min(4, wall_length * 0.6)

                wx1 = midpoint_x - window_size / 2
                wx2 = midpoint_x + window_size / 2

                msp.add_line(
                    (wx1, y1),
                    (wx2, y1),
                    dxfattribs={"layer": "WINDOWS"}
                )

        # VERTICAL WALL — use epsilon instead of exact equality
        elif abs(x2 - x1) <= _EPS:

            wall_length = abs(y2 - y1)

            if wall_length > 4:

                window_size = min(4, wall_length * 0.6)

                wy1 = midpoint_y - window_size / 2
                wy2 = midpoint_y + window_size / 2

                msp.add_line(
                    (x1, wy1),
                    (x1, wy2),
                    dxfattribs={"layer": "WINDOWS"}
                )

# ------------------------------------------------
# SHARED-EDGE DOOR GENERATION
# Each door is placed on the actual shared wall between adjacent rooms.
# Living Room <-> Entrance door is handled automatically via adjacency.
# ------------------------------------------------

room_lookup = {room["name"]: room["polygon"] for room in allocated_rooms}
DOOR_WIDTH = 3.0
seen_door_pairs = set()


def get_shared_edge_info(pts1, pts2):
    """Return midpoint, orientation, and length of shared wall, or None."""
    try:
        p1 = ShapelyPolygon(pts1)
        p2 = ShapelyPolygon(pts2)
        shared = p1.boundary.intersection(p2.boundary)
        if shared.is_empty or shared.length < 0.5:
            return None
        # If the result is a collection (e.g. L-shaped bathroom boundary),
        # pick the longest single segment so the door lands on one clean wall.
        if hasattr(shared, 'geoms'):
            lines = [g for g in shared.geoms if hasattr(g, 'length') and g.length > 0]
            if not lines:
                return None
            shared = max(lines, key=lambda g: g.length)
        mid = shared.interpolate(0.5, normalized=True)
        b = shared.bounds  # (minx, miny, maxx, maxy)
        is_horiz = (b[2] - b[0]) >= (b[3] - b[1])
        return {"mx": mid.x, "my": mid.y, "length": shared.length,
                "is_horiz": is_horiz, "bounds": b}
    except Exception:
        return None


def draw_door(msp, seg):
    """Draw a door panel + 90-degree swing arc on a shared wall segment."""
    if seg is None:
        return
    mx, my = seg["mx"], seg["my"]
    hw = min(DOOR_WIDTH / 2, seg["length"] / 2 * 0.85)
    if hw < 0.4:
        return  # shared edge too short for a door symbol
    dw = hw * 2

    if seg["is_horiz"]:
        # Horizontal shared wall — door opens southward
        hy = (seg["bounds"][1] + seg["bounds"][3]) / 2
        x_hinge = mx - hw
        # Door panel in open position (perpendicular, pointing south)
        msp.add_line((x_hinge, hy), (x_hinge, hy - dw),
                     dxfattribs={"layer": "DOORS"})
        # Arc sweeps CCW from 270° (south/open) to 360° (east/closed on wall)
        msp.add_arc(center=(x_hinge, hy), radius=dw,
                    start_angle=270, end_angle=360,
                    dxfattribs={"layer": "DOORS"})
    else:
        # Vertical shared wall — door opens eastward
        vx = (seg["bounds"][0] + seg["bounds"][2]) / 2
        y_hinge = my - hw
        # Door panel in open position (perpendicular, pointing east)
        msp.add_line((vx, y_hinge), (vx + dw, y_hinge),
                     dxfattribs={"layer": "DOORS"})
        # Arc sweeps CCW from 0° (east/open) to 90° (north/closed on wall)
        msp.add_arc(center=(vx, y_hinge), radius=dw,
                    start_angle=0, end_angle=90,
                    dxfattribs={"layer": "DOORS"})


# Interior doors — one per unique adjacent pair
for room in allocated_rooms:
    for adj_name in room.get("adjacent_to", []):
        pair = tuple(sorted([room["name"], adj_name]))
        if pair in seen_door_pairs or adj_name not in room_lookup:
            continue
        seen_door_pairs.add(pair)
        seg = get_shared_edge_info(room_lookup[room["name"]], room_lookup[adj_name])
        draw_door(msp, seg)

# Exterior door for Entrance — main entry from outside the building
entrance_pts = room_lookup.get("Entrance")
if entrance_pts:
    xs = [p[0] for p in entrance_pts]
    ys = [p[1] for p in entrance_pts]
    cx = sum(xs) / len(xs)
    ymin = min(ys)
    x_hinge = cx - DOOR_WIDTH / 2
    # Panel pointing south (exterior side)
    msp.add_line((x_hinge, ymin), (x_hinge + DOOR_WIDTH, ymin),
                 dxfattribs={"layer": "DOORS"})
    msp.add_arc(center=(x_hinge, ymin), radius=DOOR_WIDTH,
                start_angle=270, end_angle=360,
                dxfattribs={"layer": "DOORS"})

# ------------------------------------------------
# ROOM DIMENSIONS
# ------------------------------------------------

for room in allocated_rooms:

    polygon = room["polygon"]

    for i in range(len(polygon)):

        p1 = tuple(polygon[i])
        p2 = tuple(polygon[(i + 1) % len(polygon)])

        length = math.dist(p1, p2)

        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2

        msp.add_text(

            f"{length:.1f} ft",

            dxfattribs={
                "height": 0.7,
                "layer": "DIMENSIONS"
            }

        ).set_placement((mx, my))

# ------------------------------------------------
# DRAW BUILDING POLYGON (last — so green outline renders on top of room edges)
# ------------------------------------------------

msp.add_lwpolyline(
    building_points,
    close=True,
    dxfattribs={"layer": "BUILDING"}
)

# ------------------------------------------------
# SAVE DXF
# ------------------------------------------------

doc.saveas("generated_layout.dxf")

print("\nDXF generated successfully!\n")