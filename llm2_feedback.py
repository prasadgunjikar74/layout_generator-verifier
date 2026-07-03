import json
import os
import time
from shapely.geometry import Polygon
from groq import Groq
from dotenv import load_dotenv

# ================================================
# LOAD ENV VARIABLES
# ================================================

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise ValueError("GROQ_API_KEY not found")

# ================================================
# CREATE GROQ CLIENT
# ================================================

client = Groq(api_key=api_key)

# ================================================
# LOAD DATA
# ================================================

with open("input.json", "r") as f:
    input_data = json.load(f)

with open("generated_layout.json", "r") as f:
    layout_data = json.load(f)

with open("violations.json", "r") as f:
    violations_data = json.load(f)

# ================================================
# EXTRACT OUTER CONSTRAINT VALUES
# ================================================

building_points = layout_data["building_outline"]["points"]
site_boundary_points = input_data["site"]["boundary"]["points"]

constraints_map = {}
for constraint in input_data["constraints"]:
    constraints_map[constraint["type"]] = constraint["value"]

front_setback = constraints_map.get("front_setback", 0)
rear_setback  = constraints_map.get("rear_setback", 0)
left_setback  = constraints_map.get("side_setback_left", 0)
right_setback = constraints_map.get("side_setback_right", 0)
max_area      = constraints_map.get("max_building_footprint", 0)

# ================================================
# COMPUTE ACTUAL SETBACK LIMITS FROM SITE POLYGON
# ================================================
# Use site edge intersection (not bounding box) so limits are
# accurate for irregular polygons.

site_poly = Polygon(site_boundary_points)
site_ext  = list(site_poly.exterior.coords)

def site_y_range_at_x(px):
    ys = []
    for i in range(len(site_ext) - 1):
        x1, y1 = site_ext[i]; x2, y2 = site_ext[i + 1]
        xlo, xhi = min(x1, x2), max(x1, x2)
        if xlo <= px <= xhi:
            if x1 == x2:
                ys.extend([y1, y2])
            else:
                ys.append(y1 + (px - x1) / (x2 - x1) * (y2 - y1))
    return (min(ys), max(ys)) if ys else (None, None)

def site_x_range_at_y(py):
    xs = []
    for i in range(len(site_ext) - 1):
        x1, y1 = site_ext[i]; x2, y2 = site_ext[i + 1]
        ylo, yhi = min(y1, y2), max(y1, y2)
        if ylo <= py <= yhi:
            if y1 == y2:
                xs.extend([x1, x2])
            else:
                xs.append(x1 + (py - y1) / (y2 - y1) * (x2 - x1))
    return (min(xs), max(xs)) if xs else (None, None)

# Building bounds
building_min_x = min(p[0] for p in building_points)
building_max_x = max(p[0] for p in building_points)
building_min_y = min(p[1] for p in building_points)
building_max_y = max(p[1] for p in building_points)

# Per-vertex actual distances for the building corners
def polygon_area(points):
    area = 0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]; x2, y2 = points[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2

current_area = polygon_area(building_points)

# Compute the tightest setback violations per direction
# (actual site boundary at that vertex, not bounding box)
rear_limit_min  = None   # minimum allowed rear limit across all building top vertices
front_limit_max = None   # maximum allowed front limit
left_limit_max  = None
right_limit_min = None

for px, py in building_points:
    sy, ny = site_y_range_at_x(px)
    wx, ex = site_x_range_at_y(py)
    if ny is not None:
        rl = ny - rear_setback
        rear_limit_min = rl if rear_limit_min is None else min(rear_limit_min, rl)
    if sy is not None:
        fl = sy + front_setback
        front_limit_max = fl if front_limit_max is None else max(front_limit_max, fl)
    if wx is not None:
        ll = wx + left_setback
        left_limit_max = ll if left_limit_max is None else max(left_limit_max, ll)
    if ex is not None:
        rli = ex - right_setback
        right_limit_min = rli if right_limit_min is None else min(right_limit_min, rli)

# ================================================
# EXTRACT INNER CONSTRAINTS
# ================================================

inner_constraints = input_data.get("inner_constraints", [])

adjacency_pairs = [
    f"'{ic['room_a']}' must share a wall with '{ic['room_b']}'"
    for ic in inner_constraints
    if ic["type"] == "room_adjacency"
]

corridor_ic = next(
    (ic for ic in inner_constraints if ic["type"] == "corridor_connectivity"),
    None
)

# ================================================
# LOAD ALLOCATED ROOMS (for inner context)
# ================================================

allocated_rooms_summary = ""

if os.path.exists("allocated_rooms.json"):
    try:
        with open("allocated_rooms.json", "r") as f:
            allocated_data = json.load(f)

        room_lines = []
        for room in allocated_data.get("rooms", []):
            poly = room.get("polygon", [])
            if poly:
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                room_lines.append(
                    f"  - {room['name']}: X=[{min(xs):.1f},{max(xs):.1f}] "
                    f"Y=[{min(ys):.1f},{max(ys):.1f}]"
                )
        allocated_rooms_summary = "\n".join(room_lines)

    except Exception:
        allocated_rooms_summary = "  (could not load)"

# ================================================
# BUILD CONTEXT
# ================================================

context = f"""
SITE CONSTRAINTS (correct values from actual site geometry):
- Front setback (south): {front_setback} ft  =>  building min Y >= {front_limit_max:.1f}
- Rear setback (north):  {rear_setback} ft   =>  building max Y <= {rear_limit_min:.1f}
- Left setback (west):   {left_setback} ft   =>  building min X >= {left_limit_max:.1f}
- Right setback (east):  {right_setback} ft  =>  building max X <= {right_limit_min:.1f}
- Max building footprint: {max_area} sq ft

CURRENT OUTER LAYOUT:
- Building area: {current_area:.2f} sq ft
- Building bounds: X=[{building_min_x}, {building_max_x}]  Y=[{building_min_y}, {building_max_y}]
- Rear clearance (building top to north boundary): varies per X (NOT a single number — site is irregular)
- Front clearance: varies per X

CURRENT INNER LAYOUT (allocated room bounding boxes):
{allocated_rooms_summary if allocated_rooms_summary else "  (no rooms allocated yet)"}

REQUIRED ROOM ADJACENCIES (from inner_constraints):
{chr(10).join(f"  - {p}" for p in adjacency_pairs) if adjacency_pairs else "  (none)"}
{f"  - Bedrooms {corridor_ic['private_rooms']} must each directly touch '{corridor_ic['corridor_room']}'" if corridor_ic else ""}

VIOLATIONS DETECTED:
{json.dumps(violations_data, indent=2)}
"""

# ================================================
# PROMPT
# ================================================

prompt = f"""You are an expert architectural feedback generator. Analyse the violations below and produce SHORT, SPECIFIC, ACTIONABLE instructions so an LLM can fix the layout in the next iteration.

{context}

FEEDBACK RULES:
1. One violation per line.
2. For OUTER layout violations (setbacks, area, site boundary, tree zone):
   - Specify direction (north/south/east/west) and exact shift needed in feet.
   - Give the current value, the required limit, and the correction.
   - Reference exact coordinates where relevant.
3. For INNER layout violations (room_adjacency, corridor_connectivity, room_overlap, room_outside_building):
   - Name the two rooms involved.
   - Describe their current spatial relationship (where they are).
   - Give concrete repositioning advice (move Room X to be beside Room Y, adjust preferred_location to X).
4. Keep each line under 160 characters.
5. Use directions: north (Y+), south (Y-), east (X+), west (X-).
6. Be deterministic — same input always gives same output.

EXAMPLES:
- Rear setback: "C2: Building top Y=78, north boundary Y=84, need 7ft gap => max Y must be <=77. Contract top edge 1ft south."
- Left setback: "C3: Lower-section west vertex X=35 touches south-extension wall X=35, need 5ft gap => min X=40. Shift lower section 5ft east."
- Right setback: "C4: Lower-section east vertex X=75 touches south-extension wall X=75, need 5ft gap => max X=70. Shift lower section 5ft west."
- Adjacency: "IC3: Living Room (X=51.9-74, Y=15-32) and Corridor (X=28-52, Y=32-53) share only corner point (51.9,31.85). Reposition Corridor so it overlaps Y range with Living Room, or extend Living Room west to share a wall edge."
- Adjacency: "IC9: Corridor (X=28-52, Y=32-53) and Bedroom 3 (X=6-28, Y=25-39) share only a 2ft wall. Widen the shared boundary so Bedroom 3 has clear corridor access."

Generate feedback now — one line per violation:"""

# ================================================
# CALL GROQ WITH RETRY
# ================================================

def call_groq_with_retry(messages, model="llama-3.3-70b-versatile", max_retries=3, initial_wait=2):
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=600
            )
            return response
        except Exception as e:
            error_msg = str(e)
            is_retryable = any([
                "deadline" in error_msg.lower(),
                "timeout" in error_msg.lower(),
                "connection" in error_msg.lower(),
                "temporarily unavailable" in error_msg.lower(),
                "rate_limit" in error_msg.lower(),
                "500" in error_msg,
                "503" in error_msg,
            ])
            if attempt < max_retries - 1 and is_retryable:
                wait_time = initial_wait * (2 ** attempt)
                print(f"\nAPI Error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                print(f"  Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            raise

try:
    response = call_groq_with_retry([{"role": "user", "content": prompt}])
except Exception as e:
    print("\n" + "=" * 70)
    print("ERROR: Groq API Call Failed After Retries (LLM2 Feedback)")
    print("=" * 70)
    print(f"\nError Type: {type(e).__name__}")
    print(f"Error Message: {str(e)}")
    import traceback
    traceback.print_exc()
    exit(1)

# ================================================
# EXTRACT RESPONSE
# ================================================

feedback = response.choices[0].message.content

# ================================================
# TOKEN TRACKING
# ================================================

usage = response.usage
llm2_token_data = {
    "prompt_tokens": usage.prompt_tokens,
    "completion_tokens": usage.completion_tokens,
    "total_tokens": usage.total_tokens
}

with open("llm2_tokens.json", "w") as f:
    json.dump(llm2_token_data, f, indent=2)

# ================================================
# SAVE FEEDBACK
# ================================================

with open("feedback.txt", "w", encoding="utf-8") as f:
    f.write(feedback)

# ================================================
# DISPLAY OUTPUT
# ================================================

print("\nLLM2 FEEDBACK GENERATED")
print("=" * 70)
print("\nFEEDBACK:")
print("-" * 70)
print(feedback)
print("\n" + "=" * 70)
print(f"\nToken Usage: {usage.total_tokens} ({usage.prompt_tokens} prompt + {usage.completion_tokens} completion)")
