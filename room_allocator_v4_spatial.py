"""
SPATIAL ROOM ALLOCATOR v4 - Zone-Based Layout

Uses room_plan.json preferred_location hints to create realistic layouts.
Rooms with same location preference are placed side-by-side.
No hardcoded zones - everything derives from input configuration.
"""

import json
import os
import sys
from shapely.geometry import Polygon, box
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# LOAD DATA
# ============================================================

print("\n" + "="*70)
print("ROOM ALLOCATOR v4 - SPATIAL ZONE-BASED")
print("="*70)

with open("input.json", "r") as f:
    config_data = json.load(f)

with open("generated_layout.json", "r") as f:
    layout_data = json.load(f)

with open("room_plan.json", "r") as f:
    room_plan = json.load(f)

# Extract configuration
ALLOC_CFG = config_data["allocation_config"]
MARGIN = ALLOC_CFG["margin"]
SIZE_RATIOS = ALLOC_CFG["size_category_area_ratios"]
ROOM_TYPE_DEPTHS = ALLOC_CFG["room_type_max_depth"]
CORRIDOR_RATIO = ALLOC_CFG["corridor_width_ratio"]
MIN_DEPTH_RATIO = ALLOC_CFG["min_room_depth_ratio"]

# Load building data
building_points = layout_data["building_outline"]["points"]
building_poly = Polygon(building_points)
building_area = building_poly.area
rooms_spec = room_plan["rooms"] if isinstance(room_plan, dict) else room_plan

# ============================================================
# HUMAN FEEDBACK — parse which rooms to grow / shrink
# ============================================================

_GROW_KW   = {"increase", "bigger", "larger", "expand", "enlarge", "grow", "more"}
_SHRINK_KW = {"decrease", "smaller", "shrink", "reduce", "less", "compact"}

_human_fb = ""
if os.path.exists("human_feedback.txt"):
    with open("human_feedback.txt", "r", encoding="utf-8") as _hf:
        _human_fb = _hf.read().lower()

rooms_to_grow   = set()
rooms_to_shrink = set()

if _human_fb:
    # Step 1: find positions of every mentioned room name (sort by position).
    # We then bound each room's keyword window by the midpoint to its neighbours
    # so "bedroom 1 bigger and bedroom 3 smaller" never leaks "bigger" into
    # bedroom 3's window even though they sit only ~20 chars apart.
    _room_positions = []
    for _r in rooms_spec:
        _rname_lower = _r["name"].lower()
        _idx = _human_fb.find(_rname_lower)
        if _idx >= 0:
            _room_positions.append((_idx, _idx + len(_rname_lower), _r))
    _room_positions.sort(key=lambda x: x[0])

    for _i, (_start, _end, _r) in enumerate(_room_positions):
        _prev_end   = _room_positions[_i - 1][1] if _i > 0 else 0
        _next_start = _room_positions[_i + 1][0] if _i < len(_room_positions) - 1 else len(_human_fb)
        # Window: from midpoint with previous room to midpoint with next room.
        # At the edges fall back to ±60 chars so single-room feedback still works.
        _win_start = (_prev_end + _start) // 2 if _i > 0 else max(0, _start - 60)
        _win_end   = (_end + _next_start) // 2 if _i < len(_room_positions) - 1 else min(len(_human_fb), _end + 60)
        _window = _human_fb[_win_start:_win_end]
        _wwords = set(_window.split())
        if _wwords & _GROW_KW:
            rooms_to_grow.add(_r["name"])
            _r["size_category"] = "large"
        elif _wwords & _SHRINK_KW:
            rooms_to_shrink.add(_r["name"])
            _r["size_category"] = "small"

if rooms_to_grow:
    print(f"\n[Human Feedback] Rooms to GROW  : {sorted(rooms_to_grow)}")
if rooms_to_shrink:
    print(f"[Human Feedback] Rooms to SHRINK: {sorted(rooms_to_shrink)}")

# Calculate derived parameters
bounds_x = [p[0] for p in building_points]
bounds_y = [p[1] for p in building_points]
build_width = max(bounds_x) - min(bounds_x)
build_height = max(bounds_y) - min(bounds_y)
min_x, max_x = min(bounds_x), max(bounds_x)
min_y, max_y = min(bounds_y), max(bounds_y)

MIN_ROOM_DEPTH = max(6.0, build_height * MIN_DEPTH_RATIO)
CORRIDOR_WIDTH = max(2.5, build_width * CORRIDOR_RATIO)

print(f"\nBuilding: {build_width:.1f}W x {build_height:.1f}H = {building_area:.0f} sq units")
print(f"Bounds: X[{min_x:.1f}, {max_x:.1f}], Y[{min_y:.1f}, {max_y:.1f}]")
print(f"Config: MIN_DEPTH={MIN_ROOM_DEPTH:.1f}, CORRIDOR_W={CORRIDOR_WIDTH:.1f}")

# ============================================================
# DYNAMIC ZONE HEIGHT — proportional to size_category demand
# Makes size_category changes visually significant even when
# a room is the sole occupant of its zone.
# ============================================================
_ZONE_PRIORITY_MAP = {
    "south": 1, "south-west": 1, "south-east": 1, "south-entry": 1,
    "central-south": 2, "central-south-west": 2, "central-south-east": 2,
    "central": 3, "central-west": 3, "central-east": 3,
    "north": 4, "north-west": 4, "north-east": 4,
}

_group_demand = defaultdict(float)
for _r in rooms_spec:
    _pref = _r.get("preferred_location", "south").split("(")[0].strip()
    _pri  = _ZONE_PRIORITY_MAP.get(_pref, 1)
    _size = _r.get("size_category", "medium")
    _group_demand[_pri] += SIZE_RATIOS.get(_size, 0.20)

_total_demand = sum(_group_demand.values()) or 1.0
_cursor_y = min_y
_zone_y = {}
for _pri in sorted(_group_demand.keys()):
    _h = (_group_demand[_pri] / _total_demand) * build_height
    _zone_y[_pri] = (round(_cursor_y, 2), round(_cursor_y + _h, 2))
    _cursor_y += _h
for _pri in range(1, 5):          # fallback for any missing priority
    if _pri not in _zone_y:
        _zone_y[_pri] = (min_y, max_y)

print("\n[Dynamic Zones] Priority -> y_range (size_category demand):")
for _pri in sorted(_zone_y):
    print(f"  Priority {_pri}: y={_zone_y[_pri][0]:.1f}->{_zone_y[_pri][1]:.1f}  demand={_group_demand.get(_pri,0):.2f}")

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_room_by_name(name):
    return next((r for r in rooms_spec if r["name"] == name), None)

def get_bounds(poly):
    if poly.is_empty:
        return None
    # Handle GeometryCollection by extracting largest geometry
    if hasattr(poly, 'exterior'):
        coords = list(poly.exterior.coords)
    elif hasattr(poly, 'geoms') and poly.geom_type == 'GeometryCollection':
        # Get bounds from all geometries
        all_bounds = []
        for geom in poly.geoms:
            if hasattr(geom, 'bounds'):
                all_bounds.append(geom.bounds)
        if not all_bounds:
            return None
        xs = [b[0] for b in all_bounds] + [b[2] for b in all_bounds]
        ys = [b[1] for b in all_bounds] + [b[3] for b in all_bounds]
        return {
            "min_x": min(xs), "max_x": max(xs),
            "min_y": min(ys), "max_y": max(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }
    else:
        bounds = poly.bounds
        return {
            "min_x": bounds[0], "max_x": bounds[2],
            "min_y": bounds[1], "max_y": bounds[3],
            "width": bounds[2] - bounds[0],
            "height": bounds[3] - bounds[1],
        }
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return {
        "min_x": min(xs), "max_x": max(xs),
        "min_y": min(ys), "max_y": max(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }

def rect(x1, y1, x2, y2):
    if x1 >= x2 or y1 >= y2:
        return None
    return box(x1, y1, x2, y2)

def to_coords(poly):
    b = get_bounds(poly)
    return [b["min_x"], b["min_y"], b["max_x"], b["max_y"]]

def to_array(poly):
    if hasattr(poly, 'exterior'):
        return [list(c) for c in poly.exterior.coords[:-1]]
    elif hasattr(poly, 'geoms') and poly.geom_type == 'GeometryCollection':
        # Get largest geometry and convert to array
        largest_geom = max(poly.geoms, key=lambda g: g.area if hasattr(g, 'area') else 0)
        if hasattr(largest_geom, 'exterior'):
            return [list(c) for c in largest_geom.exterior.coords[:-1]]
        else:
            bounds = largest_geom.bounds
            return [[bounds[0], bounds[1]], [bounds[2], bounds[1]], [bounds[2], bounds[3]], [bounds[0], bounds[3]]]
    else:
        bounds = poly.bounds
        return [[bounds[0], bounds[1]], [bounds[2], bounds[1]], [bounds[2], bounds[3]], [bounds[0], bounds[3]]]

def get_shared_edge_length(p1, p2):
    """Length of shared boundary segment between two polygons."""
    try:
        shared = p1.boundary.intersection(p2.boundary)
        return shared.length if not shared.is_empty else 0.0
    except Exception:
        return 0.0

def bridge_gap(name_a, name_b, allocated, building_poly):
    """
    Bridge the gap between two rooms that need to be adjacent.
    Determines the direction of the gap, builds a candidate rectangle,
    clips it to available space (building minus rooms with interior overlap),
    then attaches the clipped bridge to the smaller room.
    Returns True if a valid bridge was created.

    Key rule: only subtract rooms that have INTERIOR overlap with the bridge.
    Rooms that merely touch the bridge boundary (collinear edges) are skipped
    to avoid GEOS topology errors from exact boundary alignment.
    """
    poly_a = allocated.get(name_a)
    poly_b = allocated.get(name_b)
    if poly_a is None or poly_b is None:
        return False

    try:
        ba = poly_a.bounds   # (minx, miny, maxx, maxy)
        bb = poly_b.bounds
    except Exception:
        return False

    bridge_cand = None
    if ba[2] <= bb[0] + 0.1:                          # A is left of B
        y_lo, y_hi = max(ba[1], bb[1]), min(ba[3], bb[3])
        if y_hi > y_lo + 0.5:
            bridge_cand = box(ba[2], y_lo, bb[0], y_hi)
    elif bb[2] <= ba[0] + 0.1:                        # B is left of A
        y_lo, y_hi = max(ba[1], bb[1]), min(ba[3], bb[3])
        if y_hi > y_lo + 0.5:
            bridge_cand = box(bb[2], y_lo, ba[0], y_hi)
    elif ba[3] <= bb[1] + 0.1:                        # A is below B
        x_lo, x_hi = max(ba[0], bb[0]), min(ba[2], bb[2])
        if x_hi > x_lo + 0.5:
            bridge_cand = box(x_lo, ba[3], x_hi, bb[1])
    elif bb[3] <= ba[1] + 0.1:                        # B is below A
        x_lo, x_hi = max(ba[0], bb[0]), min(ba[2], bb[2])
        if x_hi > x_lo + 0.5:
            bridge_cand = box(x_lo, bb[3], x_hi, ba[1])

    if bridge_cand is None or bridge_cand.is_empty:
        return False

    try:
        bridge = bridge_cand.intersection(building_poly)

        for other_name, other_poly in list(allocated.items()):
            if other_name in (name_a, name_b) or bridge.is_empty:
                continue
            try:
                # Only subtract rooms with actual interior overlap.
                # Rooms that only touch the bridge boundary (collinear/tangent edges)
                # are skipped — subtracting them causes GEOS topology errors.
                if bridge.intersects(other_poly) and not bridge.touches(other_poly):
                    bridge = bridge.difference(other_poly)
            except Exception:
                continue

        if bridge.is_empty or bridge.area < 0.1:
            return False

        # Verify the clipped bridge still connects both rooms
        if not poly_a.union(bridge).buffer(0.01).intersects(poly_b):
            return False

        # Attach to the smaller room and normalise the resulting geometry
        if poly_a.area <= poly_b.area:
            allocated[name_a] = poly_a.union(bridge).buffer(0)
        else:
            allocated[name_b] = poly_b.union(bridge).buffer(0)
        return True

    except Exception:
        return False

def get_largest_rect_in_region(poly, y_min, y_max):
    """Get largest rectangle in vertical region"""
    b = get_bounds(poly)
    if not b:
        return None
    
    for depth in range(int(y_max - y_min), int(MIN_ROOM_DEPTH), -2):
        r = rect(b["min_x"], y_min, b["max_x"], y_min + depth)
        if r is None:
            continue
        clipped = r.intersection(poly)
        if not clipped.is_empty and clipped.area > MIN_ROOM_DEPTH ** 2:
            return clipped
    return None

# ============================================================
# PARSE ROOM LOCATIONS INTO ZONES
# ============================================================

location_groups = defaultdict(list)
for room in rooms_spec:
    pref_loc = room.get("preferred_location", "unknown")
    # Extract zone name (before any parenthesis or description)
    pref_loc_clean = pref_loc.split("(")[0].strip() if pref_loc else "unknown"
    location_groups[pref_loc_clean].append(room)

print("\n" + "="*70)
print("LOCATION ZONES")
print("="*70)
for loc, rooms_in_zone in sorted(location_groups.items()):
    room_names = [r["name"] for r in rooms_in_zone]
    print(f"{loc:20s}: {room_names}")

# ============================================================
# ZONE DEFINITIONS (derived from location preferences)
# Enhanced with LEFT-CENTER-RIGHT horizontal divisions
# ============================================================

zones = {
    # y_range values are computed dynamically from size_category demand (_zone_y).
    # x_range and priority are fixed architectural decisions.
    "south-west": {
        "x_range": (min_x, min_x + build_width * 0.33),
        "y_range": _zone_y[1],
        "priority": 1,
        "allocation": "vertical"
    },
    "south": {
        "x_range": (min_x + build_width * 0.33, min_x + build_width * 0.67),
        "y_range": _zone_y[1],
        "priority": 1,
        "allocation": "vertical"
    },
    "south-east": {
        "x_range": (min_x + build_width * 0.67, max_x),
        "y_range": _zone_y[1],
        "priority": 1,
        "allocation": "vertical"
    },
    "south-entry": {
        "x_range": (min_x + build_width * 0.8, max_x),
        "y_range": _zone_y[1],
        "priority": 1,
        "allocation": "none"
    },
    "central-south-west": {
        "x_range": (min_x, min_x + build_width * 0.33),
        "y_range": _zone_y[2],
        "priority": 2,
        "allocation": "vertical"
    },
    "central-south": {
        "x_range": (min_x + build_width * 0.33, min_x + build_width * 0.67),
        "y_range": _zone_y[2],
        "priority": 2,
        "allocation": "vertical"
    },
    "central-south-east": {
        "x_range": (min_x + build_width * 0.67, max_x),
        "y_range": _zone_y[2],
        "priority": 2,
        "allocation": "vertical"
    },
    "central-west": {
        "x_range": (min_x, min_x + build_width * 0.33),
        "y_range": _zone_y[3],
        "priority": 3,
        "allocation": "vertical"
    },
    "central": {
        "x_range": (min_x + build_width * 0.33, min_x + build_width * 0.67),
        "y_range": _zone_y[3],
        "priority": 3,
        "allocation": "vertical"
    },
    "central-east": {
        "x_range": (min_x + build_width * 0.67, max_x),
        "y_range": _zone_y[3],
        "priority": 3,
        "allocation": "vertical"
    },
    "north-west": {
        "x_range": (min_x, min_x + build_width * 0.5),
        "y_range": _zone_y[4],
        "priority": 4,
        "allocation": "vertical"
    },
    "north-east": {
        "x_range": (min_x + build_width * 0.5, max_x),
        "y_range": _zone_y[4],
        "priority": 4,
        "allocation": "vertical"
    },
}

# ============================================================
# ALLOCATION ENGINE - ZONE-BASED
# ============================================================

print(f"\n" + "="*70)
print("ZONE-BASED ALLOCATION")
print("="*70)

allocated = {}
# Shrink feasible by MARGIN from the start so every placement path (zones,
# special-handling, gap-fill) respects the 1ft wall buffer without needing
# per-block min_x+MARGIN guards.
feasible = building_poly.buffer(-MARGIN)

# Process zones by priority
sorted_zones = sorted(zones.items(), key=lambda x: x[1]["priority"])

for zone_name, zone_def in sorted_zones:
    print(f"\n--- ZONE: {zone_name} (priority={zone_def['priority']}) ---")
    
    rooms_in_zone = location_groups.get(zone_name, [])
    if not rooms_in_zone:
        print(f"  No rooms for this zone")
        continue
    
    if zone_def["allocation"] == "none":
        # Entrance - small, fixed size
        for room in rooms_in_zone:
            if room["name"] in allocated or room["name"] == "Entrance":
                spec = room
                b = get_bounds(feasible)
                w = build_width * 0.15
                h = build_height * 0.1
                room_poly = rect(
                    max(zone_def.get("x_range", (min_x, max_x))[1] - w, min_x),
                    zone_def.get("y_range", (min_y, max_y))[0],
                    min(zone_def.get("x_range", (min_x, max_x))[1], max_x),
                    min(zone_def.get("y_range", (min_y, max_y))[0] + h, max_y)
                )
                if room_poly and feasible.intersects(room_poly):
                    room_poly = room_poly.intersection(feasible)
                    if not room_poly.is_empty and room_poly.area > MIN_ROOM_DEPTH ** 2:
                        allocated[room["name"]] = room_poly
                        feasible = feasible.difference(room_poly)
                        print(f"  {room['name']}: OK (entrance)")
    
    elif zone_def["allocation"] == "horizontal":
        # Allocate rooms side-by-side (left to right) - NOT full width
        y_min, y_max = zone_def["y_range"]
        
        # Get the feasible region in this y-range
        zone_region = rect(min_x, y_min, max_x, y_max)
        if zone_region:
            zone_feasible = zone_region.intersection(feasible)
            if zone_feasible.is_empty:
                print(f"  No feasible space in Y-range")
                continue
            
            z_bounds = get_bounds(zone_feasible)
            total_width = z_bounds["width"]
            zone_height = z_bounds["height"]
            
            # Divide width proportionally among rooms in zone
            num_rooms = len(rooms_in_zone)
            col_width = total_width / num_rooms
            
            for idx, room in enumerate(rooms_in_zone):
                if room["name"] in allocated:
                    print(f"  {room['name']}: Already allocated")
                    continue
                
                col_x_start = z_bounds["min_x"] + (idx * col_width)
                col_x_end = z_bounds["min_x"] + ((idx + 1) * col_width)
                
                # Try to fit room in this column - DON'T make it full height
                # Make rooms proportional to their size category
                spec = get_room_by_name(room["name"])
                room_type = room.get("type", "general")
                size_cat = room.get("size_category", "medium")
                
                # Calculate room depth based on size
                target_area = building_area * SIZE_RATIOS.get(size_cat, 0.15)
                target_depth = target_area / col_width if col_width > 0 else zone_height / 2
                target_depth = max(MIN_ROOM_DEPTH, min(target_depth, zone_height * 0.8))  # Don't exceed 80% of zone
                
                room_poly = rect(col_x_start, z_bounds["min_y"], col_x_end, z_bounds["min_y"] + target_depth)
                if room_poly:
                    room_poly = room_poly.intersection(zone_feasible)
                    if not room_poly.is_empty and room_poly.area > MIN_ROOM_DEPTH ** 2:
                        allocated[room["name"]] = room_poly
                        print(f"  {room['name']}: OK (col {idx+1}/{num_rooms}, depth={target_depth:.1f})")
                    else:
                        print(f"  {room['name']}: Space too small ({room_poly.area if not room_poly.is_empty else 'empty'})")
            
            # Update feasible with allocated rooms from this zone
            for room in rooms_in_zone:
                if room["name"] in allocated:
                    feasible = feasible.difference(allocated[room["name"]])
    
    elif zone_def["allocation"] == "vertical":
        # Allocate rooms stacked vertically in the zone
        x_range = zone_def.get("x_range", (min_x, max_x))
        y_range = zone_def.get("y_range", (min_y, max_y))
        
        zone_region = rect(x_range[0], y_range[0], x_range[1], y_range[1])
        if zone_region:
            zone_feasible = zone_region.intersection(feasible)
            if zone_feasible.is_empty:
                print(f"  No feasible space in zone")
                continue
            
            z_bounds = get_bounds(zone_feasible)
            remaining_height = z_bounds["height"]
            current_y = z_bounds["min_y"]
            
            # Pre-calculate proportional depths so every room in the zone gets guaranteed space.
            # Without this, the first room can consume the entire zone height and kick out later rooms.
            rooms_to_place = [r for r in rooms_in_zone
                              if r["name"] not in allocated and get_room_by_name(r["name"])]

            # Pass 1 — compute boosted ratios first, then sum for total_ratio.
            # Computing total_ratio from pre-boost values would dampen the growth effect.
            _boosted_ratios = {}
            for _r in rooms_to_place:
                _spec     = get_room_by_name(_r["name"])
                _size_cat = _spec.get("size_category", "medium") if _spec else "medium"
                _base     = SIZE_RATIOS.get(_size_cat, 0.15)
                if _r["name"] in rooms_to_grow:
                    _boosted_ratios[_r["name"]] = min(_base * 1.6, 0.48)
                elif _r["name"] in rooms_to_shrink:
                    _boosted_ratios[_r["name"]] = max(_base * 0.6, 0.08)
                else:
                    _boosted_ratios[_r["name"]] = _base
            total_ratio = sum(_boosted_ratios.values()) or 1.0

            for idx, room in enumerate(rooms_to_place):
                ratio     = _boosted_ratios[room["name"]]
                rooms_after = len(rooms_to_place) - idx - 1
                proportional = (ratio / total_ratio) * z_bounds["height"]
                max_allowed = remaining_height - rooms_after * MIN_ROOM_DEPTH
                target_depth = max(MIN_ROOM_DEPTH, min(proportional, max_allowed))
                # Entrance must not consume the whole zone — cap by config ratio
                if room["name"] == "Entrance" and "entrance_depth_ratio" in ALLOC_CFG:
                    entrance_cap = build_height * ALLOC_CFG["entrance_depth_ratio"]
                    target_depth = max(MIN_ROOM_DEPTH, min(target_depth, entrance_cap))

                room_poly = rect(z_bounds["min_x"], current_y, z_bounds["max_x"], current_y + target_depth)
                if room_poly:
                    room_poly = room_poly.intersection(zone_feasible)
                    if not room_poly.is_empty and room_poly.area > MIN_ROOM_DEPTH ** 2:
                        allocated[room["name"]] = room_poly
                        remaining_height -= target_depth
                        current_y += target_depth
                        print(f"  {room['name']}: OK (depth={target_depth:.1f})")
                        if remaining_height < MIN_ROOM_DEPTH:
                            break
                    else:
                        print(f"  {room['name']}: Space too small")
            
            # Update feasible
            for room in rooms_in_zone:
                if room["name"] in allocated:
                    feasible = feasible.difference(allocated[room["name"]])

# ============================================================
# EXTEND LIVING ROOM: absorb east-side south-band gap
# Fills unallocated space between Entrance top and Dining Area
# bottom, forming an L-shaped Living Room that shares a full
# horizontal wall with Entrance (instead of just a corner edge).
# ============================================================

print(f"\n--- EXTEND: Living Room east expansion ---")
if "Living Room" in allocated and "Living Room" not in rooms_to_shrink:
    lr_poly = allocated["Living Room"]
    lr_b = get_bounds(lr_poly)
    if lr_b:
        east_gap = rect(lr_b["max_x"], lr_b["min_y"], max_x, lr_b["max_y"])
        if east_gap:
            gap_space = east_gap.intersection(feasible)  # feasible is already margin-shrunk
            if not gap_space.is_empty:
                gap_space = gap_space.buffer(0)
            if not gap_space.is_empty and gap_space.area > 1.0:
                allocated["Living Room"] = lr_poly.union(gap_space).buffer(0)
                feasible = feasible.difference(gap_space)
                print(f"  Living Room expanded east by {gap_space.area:.1f} sq ft")
            else:
                print(f"  No east gap to absorb")
    else:
        print(f"  Living Room not allocated, skipping")

# ============================================================
# SPECIAL HANDLING: REMAINING ROOMS IN LEFTOVER SPACE
# ============================================================

print(f"\n--- SPECIAL: Allocate remaining rooms in leftover space ---")

# Try to fit Kitchen and Bedroom 3 in the remaining west space (left side)
remaining_rooms = [r for r in rooms_spec if r["name"] not in allocated]
for room in remaining_rooms:
    if room["name"] == "Entrance":
        continue  # Skip entrance for now
    
    # Try to find space in the leftover area (west/left side)
    b = get_bounds(feasible)
    if b and b["width"] > MIN_ROOM_DEPTH * 2:
        # Allocate in remaining feasible space
        room_poly = rect(b["min_x"], b["min_y"], min(b["min_x"] + b["width"] * 0.5, b["max_x"]), b["max_y"])
        if room_poly:
            room_poly = room_poly.intersection(feasible)
            if not room_poly.is_empty and room_poly.area > MIN_ROOM_DEPTH ** 2:
                allocated[room["name"]] = room_poly
                feasible = feasible.difference(room_poly)
                print(f"  {room['name']}: OK (remaining space)")
            else:
                print(f"  {room['name']}: Space too small ({room_poly.area if not room_poly.is_empty else 'empty'})")

# ============================================================
# SPECIAL HANDLING: CORRIDOR (central circulation)
# ============================================================

corridor_room = get_room_by_name("Corridor")
if corridor_room and "Corridor" not in allocated:
    print(f"\n--- SPECIAL: Corridor (central spine) ---")
    
    b = get_bounds(feasible)
    if b and b["width"] > CORRIDOR_WIDTH * 2:
        # Try standard width centered
        cx_min = b["min_x"] + (b["width"] - CORRIDOR_WIDTH) / 2
        cx_max = cx_min + CORRIDOR_WIDTH
        room_poly = rect(cx_min, b["min_y"], cx_max, b["max_y"])
        
        if room_poly and feasible.intersects(room_poly):
            room_poly = room_poly.intersection(feasible)
            if not room_poly.is_empty and room_poly.area > MIN_ROOM_DEPTH ** 2:
                allocated["Corridor"] = room_poly
                feasible = feasible.difference(room_poly)
                print(f"  Corridor: OK (central spine)")

# ============================================================
# BATHROOM ALLOCATION (ensuite inside each bedroom)
# ============================================================

print(f"\n--- SPECIAL: Ensuite bathrooms inside bedrooms ---")

BATHROOM_W = 8.0
BATHROOM_D = 6.0

bathroom_specs = []

for bedroom_room in [r for r in rooms_spec if r.get("type") == "bedroom"]:
    bedroom_name = bedroom_room["name"]
    if bedroom_name not in allocated:
        continue

    bedroom_poly = allocated[bedroom_name]
    b = get_bounds(bedroom_poly)
    if not b:
        continue

    bw = min(BATHROOM_W, b["width"] * 0.38)
    bd = min(BATHROOM_D, b["height"] * 0.35)

    # Try corners from most-private (furthest from corridor) to least
    corners = [
        (b["max_x"] - bw, b["max_y"] - bd, b["max_x"], b["max_y"]),   # top-right
        (b["min_x"],      b["max_y"] - bd, b["min_x"] + bw, b["max_y"]),  # top-left
        (b["max_x"] - bw, b["min_y"],      b["max_x"], b["min_y"] + bd),  # bottom-right
        (b["min_x"],      b["min_y"],      b["min_x"] + bw, b["min_y"] + bd),  # bottom-left
    ]

    bath_poly = None
    for x1, y1, x2, y2 in corners:
        candidate = rect(x1, y1, x2, y2)
        if candidate is None:
            continue
        clipped = candidate.intersection(bedroom_poly)
        if not clipped.is_empty and clipped.area >= bw * bd * 0.65:
            bath_poly = clipped
            break

    if bath_poly:
        bath_name = f"Bathroom ({bedroom_name})"
        allocated[bath_name] = bath_poly
        # Carve bathroom out of bedroom so polygons don't overlap
        allocated[bedroom_name] = bedroom_poly.difference(bath_poly)
        bathroom_specs.append({
            "name": bath_name,
            "type": "bathroom",
            "adjacent_to": [bedroom_name],
            "windows_required": False,
            "doors_required": True,
            "size_category": "small",
            "privacy": "private",
            "preferred_location": bedroom_room.get("preferred_location", "unknown")
        })
        print(f"  {bath_name}: OK (area={bath_poly.area:.1f})")
    else:
        print(f"  Bathroom for {bedroom_name}: Could not fit")

# ============================================================
# ADJACENCY ENFORCEMENT
# ============================================================

print(f"\n--- ADJACENCY ENFORCEMENT ---")

all_adj_specs = list(rooms_spec) + bathroom_specs
seen_pairs = set()
adj_fail_count = 0

for room in all_adj_specs:
    room_name = room["name"]
    if room_name not in allocated:
        continue
    for adj_name in room.get("adjacent_to", []):
        pair = tuple(sorted([room_name, adj_name]))
        if pair in seen_pairs or adj_name not in allocated:
            continue
        seen_pairs.add(pair)

        edge_len = get_shared_edge_length(allocated[room_name], allocated[adj_name])
        if edge_len >= 0.5:
            print(f"  OK     : {room_name} <-> {adj_name} ({edge_len:.1f}ft shared)")
            continue

        success = bridge_gap(room_name, adj_name, allocated, building_poly)
        if success:
            new_edge = get_shared_edge_length(allocated[room_name], allocated[adj_name])
            print(f"  BRIDGED: {room_name} <-> {adj_name} ({new_edge:.1f}ft after bridge)")
        else:
            adj_fail_count += 1
            print(f"  WARN   : {room_name} <-> {adj_name} - no viable bridge (gap too complex)")

if adj_fail_count:
    print(f"\n  {adj_fail_count} adjacency pair(s) could not be enforced - layout redesign needed")

# ============================================================
# GAP FILL — absorb all remaining building space into rooms
# Runs after bathrooms + adjacency so every carved boundary
# is already finalised before we redistribute leftover area.
# ============================================================

print(f"\n--- GAP FILL: distributing remaining space to adjacent rooms ---")

# Recompute union of all allocated rooms
_union_all = Polygon()
for _poly in allocated.values():
    try:
        _union_all = _union_all.union(_poly)
    except Exception:
        pass

_fill_target = building_poly.buffer(-MARGIN)
_remaining = _fill_target.difference(_union_all.buffer(0)).buffer(0)

if _remaining.is_empty or _remaining.area < 0.5:
    print("  No significant gaps — interior fully covered (margin preserved).")
else:
    print(f"  Gap area to fill: {_remaining.area:.1f} sq ft")
    _pieces = (list(_remaining.geoms)
               if _remaining.geom_type in ("MultiPolygon", "GeometryCollection")
               else [_remaining])

    for _piece in _pieces:
        if _piece.area < 0.1:
            continue

        _best_room = None

        # 1. Prefer rooms the user explicitly wants to grow IF they are
        #    directly adjacent (distance ≈ 0) to this piece
        if rooms_to_grow:
            _grow_adjacent = [
                n for n in rooms_to_grow
                if n in allocated and allocated[n].distance(_piece) < 0.5
            ]
            if _grow_adjacent:
                _best_room = min(
                    _grow_adjacent,
                    key=lambda n: allocated[n].centroid.distance(_piece.centroid)
                )

        # 2. Nearest-room by centroid distance (Voronoi-like, fair distribution).
        #    Prevents one room from absorbing all disconnected leftover pieces.
        if _best_room is None:
            try:
                _piece_centroid = _piece.centroid
                _best_room = min(
                    allocated.keys(),
                    key=lambda n: allocated[n].centroid.distance(_piece_centroid)
                )
            except Exception:
                pass

        if _best_room:
            try:
                allocated[_best_room] = allocated[_best_room].union(_piece).buffer(0)
                print(f"  +{_piece.area:.1f} sq ft → {_best_room}")
            except Exception as _e:
                print(f"  Could not absorb piece ({_piece.area:.1f} sq ft): {_e}")

# ============================================================
# BUILD OUTPUT
# ============================================================

print(f"\n" + "="*70)
print("ALLOCATION SUMMARY")
print("="*70)

output_rooms = []
for room in rooms_spec:
    room_name = room["name"]
    if room_name not in allocated:
        print(f"✗ {room_name}: NOT ALLOCATED")
        continue
    
    room_poly = allocated[room_name]
    output_rooms.append({
        "name": room_name,
        "type": room.get("type", "general"),
        "polygon": to_array(room_poly),
        "adjacent_to": room.get("adjacent_to", []),
        "windows_required": room.get("windows_required", False),
        "doors_required": room.get("doors_required", False),
        "size_category": room.get("size_category", "medium"),
        "privacy": room.get("privacy", "general"),
        "preferred_location": room.get("preferred_location", "unknown")
    })
    print(f"✓ {room_name:20s} {to_coords(room_poly)}")

for room in bathroom_specs:
    room_name = room["name"]
    if room_name not in allocated:
        print(f"✗ {room_name}: NOT ALLOCATED")
        continue
    room_poly = allocated[room_name]
    output_rooms.append({
        "name": room_name,
        "type": room["type"],
        "polygon": to_array(room_poly),
        "adjacent_to": room["adjacent_to"],
        "windows_required": room["windows_required"],
        "doors_required": room["doors_required"],
        "size_category": room["size_category"],
        "privacy": room["privacy"],
        "preferred_location": room["preferred_location"]
    })
    print(f"✓ {room_name:20s} {to_coords(room_poly)}")

# Validation
print(f"\n" + "="*70)
print("VALIDATION")
print("="*70)

issues = 0
for room in output_rooms:
    room_poly = Polygon(room["polygon"])
    if not building_poly.covers(room_poly):
        print(f"✗ {room['name']}: Outside boundary")
        issues += 1

for i, r1 in enumerate(output_rooms):
    for r2 in output_rooms[i+1:]:
        p1 = Polygon(r1["polygon"])
        p2 = Polygon(r2["polygon"])
        if p1.intersects(p2) and not p1.touches(p2):
            print(f"✗ {r1['name']} overlaps {r2['name']}")
            issues += 1

# Final adjacency report against the saved polygons (authoritative check)
print(f"\n--- FINAL ADJACENCY REPORT (from saved polygons) ---")
out_polys = {r["name"]: Polygon(r["polygon"]) for r in output_rooms}
out_adj   = {r["name"]: r["adjacent_to"] for r in output_rooms}
seen_final = set()
adj_ok = adj_fail = 0
for name, adjs in out_adj.items():
    for adj in adjs:
        pair = tuple(sorted([name, adj]))
        if pair in seen_final or adj not in out_polys:
            continue
        seen_final.add(pair)
        edge = get_shared_edge_length(out_polys[name], out_polys[adj])
        if edge >= 0.5:
            adj_ok += 1
            print(f"  OK  : {name} <-> {adj} ({edge:.1f}ft shared edge)")
        else:
            adj_fail += 1
            issues += 1
            print(f"  FAIL: {name} <-> {adj} (no shared edge — layout redesign needed)")
print(f"\n  Adjacency: {adj_ok} satisfied, {adj_fail} unsatisfied")

total_area = sum(Polygon(r["polygon"]).area for r in output_rooms)
coverage = (total_area / building_area) * 100

print(f"\nCoverage: {coverage:.1f}% ({total_area:.0f}/{building_area:.0f} sq units)")
print(f"Rooms: {len(output_rooms)}, Geometry issues: {issues - adj_fail}, Adjacency failures: {adj_fail}")

# Save
output_data = {
    "rooms": output_rooms,
    "metadata": {
        "building_area": building_area,
        "total_room_area": total_area,
        "coverage_percent": coverage,
        "rooms_count": len(output_rooms),
        "violations": issues,
        "allocation_method": "spatial_zone_based"
    }
}

with open("allocated_rooms.json", "w") as f:
    json.dump(output_data, f, indent=2)

print(f"\n✓ Saved to allocated_rooms.json")
print(f"✓ Using spatial zone-based allocation from room_plan.json")
