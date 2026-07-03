import json
import os
import sys
import time

from google import genai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# ------------------------------------------------
# LOAD ENV VARIABLES
# ------------------------------------------------

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY not found")

# ------------------------------------------------
# CREATE GEMINI CLIENT
# ------------------------------------------------

client = genai.Client(api_key=api_key)

# ------------------------------------------------
# LOAD INPUT JSON
# ------------------------------------------------

with open("input.json", "r") as file:
    input_data = json.load(file)

# ------------------------------------------------
# EXTRACT CONSTRAINTS DYNAMICALLY FROM INPUT
# ------------------------------------------------

constraints_from_input = {}
for constraint in input_data["constraints"]:
    constraint_type = constraint["type"]
    if constraint_type == "front_setback":
        constraints_from_input["C1_front_setback"] = constraint["value"]
    elif constraint_type == "rear_setback":
        constraints_from_input["C2_rear_setback"] = constraint["value"]
    elif constraint_type == "side_setback_left":
        constraints_from_input["C3_left_setback"] = constraint["value"]
    elif constraint_type == "side_setback_right":
        constraints_from_input["C4_right_setback"] = constraint["value"]
    elif constraint_type == "max_building_footprint":
        constraints_from_input["C5_max_area"] = constraint["value"]

# ------------------------------------------------
# EXTRACT TREE ZONE DYNAMICALLY FROM INPUT
# ------------------------------------------------

tree_zone_from_input = {}
if input_data.get("protected_objects"):
    tree = input_data["protected_objects"][0]
    tree_center = tree["geometry"]["center"]
    tree_radius = tree["protection_rules"]["total_exclusion_radius"]
    tree_zone_from_input = {
        "C6_tree_exclusion": {
            "center": tree_center,
            "radius": tree_radius
        }
    }

# ------------------------------------------------
# COMPACT INPUT FOR TOKEN REDUCTION
# ------------------------------------------------

compact_input = {

    "site_boundary":
        input_data["site"]["boundary"]["points"][:-1],

    "constraints": constraints_from_input,

    "tree_zone": tree_zone_from_input
}
# ------------------------------------------------
# LOAD OPTIONAL FEEDBACK
# ------------------------------------------------

feedback_text = ""

if os.path.exists("feedback.txt"):
    with open("feedback.txt", "r", encoding="utf-8") as file:
        feedback_text = file.read()

# ------------------------------------------------
# CREATE PROMPT
# ------------------------------------------------

# ------------------------------------------------
# CALCULATE VALID BUILDING BOUNDS FROM SITE GEOMETRY
# ------------------------------------------------
# Site structure:
#   Main body      : x=0..80, y=8..84
#   South extension: x=35..75, y=0..8   (usable via front-setback from y=0)
#   North protrusion: x=75..80, y=84..88 (protected tree — avoid)
#
# Setback limits must be computed against the ACTUAL site boundary
# at each zone, NOT against the bounding-box extremes.

front_setback = constraints_from_input["C1_front_setback"]
rear_setback  = constraints_from_input["C2_rear_setback"]
left_setback  = constraints_from_input["C3_left_setback"]
right_setback = constraints_from_input["C4_right_setback"]
max_area      = constraints_from_input["C5_max_area"]

# Derive site structure from boundary polygon — no hardcoded values
site_boundary   = input_data["site"]["boundary"]["points"]
site_xs = sorted(set(p[0] for p in site_boundary))  # [0, 35, 75, 80]
site_ys = sorted(set(p[1] for p in site_boundary))  # [0, 8, 84, 88]

mb_west_x       = site_xs[0]   # 0  — main body west wall
ext_site_west_x = site_xs[1]   # 35 — south extension west wall
ext_site_east_x = site_xs[2]   # 75 — south extension east wall
mb_east_x       = site_xs[3]   # 80 — main body east wall
ext_site_south_y = site_ys[0]  # 0  — south extension floor
EXT_BASE_Y      = site_ys[1]   # 8  — south extension top / main body floor
mb_north_y      = site_ys[2]   # 84 — main body north (site_ys[3]=88 is protrusion)

# Setback-adjusted building limits
main_west  = mb_west_x        + left_setback    # 5
main_east  = mb_east_x        - right_setback   # 75
main_south = EXT_BASE_Y       + front_setback   # 15
main_north = mb_north_y       - rear_setback    # 77
ext_west   = ext_site_west_x  + left_setback    # 40
ext_east   = ext_site_east_x  - right_setback   # 70
ext_south  = ext_site_south_y + front_setback   # 7

# Maximized valid building: 10-vertex polygon (CW from top-left)
# For x=35..75 the site south boundary is y=0 (south extension), so
# front setback only requires y>=7, letting the building reach down to y=8
# for the full x=35..75 range and y=7 for the x=40..70 strip.
#   Upper block  x=5..75,  y=15..77  area = 70 * 62 = 4340 sq ft
#   Middle strip x=35..75, y=8..15   area = 40 *  7 =  280 sq ft
#   Ext strip    x=40..70, y=7..8    area = 30 *  1 =   30 sq ft
#   Total                                            = 4650 sq ft  (< max)
maximized_building = [
    [main_west,       main_north],  # [5,  77] top-left
    [main_east,       main_north],  # [75, 77] top-right
    [main_east,       EXT_BASE_Y],  # [75,  8] right wall base
    [ext_east,        EXT_BASE_Y],  # [70,  8] step left to ext
    [ext_east,        ext_south],   # [70,  7] bottom-right of ext
    [ext_west,        ext_south],   # [40,  7] bottom-left of ext
    [ext_west,        EXT_BASE_Y],  # [40,  8] ext left wall base
    [ext_site_west_x, EXT_BASE_Y],  # [35,  8] main body south wall
    [ext_site_west_x, main_south],  # [35, 15] step up (x<35 needs y>=15)
    [main_west,       main_south],  # [5,  15] bottom-left
]

tree_center = tree_zone_from_input.get('C6_tree_exclusion', {}).get('center', [0, 0])
tree_radius = tree_zone_from_input.get('C6_tree_exclusion', {}).get('radius', 0)

prompt = f"""
You are an expert architectural designer. Generate a MAXIMIZED valid residential building footprint.

GOAL: Produce the LARGEST possible building that fits within all constraints.
Target area: 4600 – 4650 sq ft.

HARD CONSTRAINTS (every vertex must satisfy ALL of these):
- Front setback (south): Y >= {main_south} for vertices where site south edge = y=8
                         Y >= {ext_south}  for vertices where site south edge = y=0 (south extension zone x={ext_west}..{ext_east})
- Rear setback  (north): Y <= {main_north}  (site north edge = y=84 for x=0..75)
- Left setback  (west) : X >= {main_west}   for main body vertices
                         X >= {ext_west}    for south extension vertices (y < {main_south})
- Right setback (east) : X <= {main_east}   for main body vertices
                         X <= {ext_east}    for south extension vertices (y < {main_south})
- Max area: {max_area} sq ft
- Tree exclusion zone: center {tree_center}, radius {tree_radius} ft — no building point or edge may touch this circle

COORDINATE SYSTEM:
- Origin: bottom-left (southwest) corner
- X increases EAST, Y increases NORTH
- South = low Y, North = high Y, West = low X, East = high X

MAXIMIZED TARGET BUILDING (use exactly these vertices if no feedback):
{json.dumps(maximized_building)}
Area ≈ 4650 sq ft  (max achievable on this site given setbacks)

FEEDBACK FROM PREVIOUS CHECK:
{feedback_text if feedback_text else "No violations — use the maximized target vertices above exactly."}

ADJUSTMENT RULES (apply only when feedback specifies a violation):
- Rear setback violation  : reduce max Y (move top edge south)
- Front setback violation : increase min Y (move bottom edge north)
- Left setback violation  : increase min X (move left edge east)
- Right setback violation : decrease max X (move right edge west)
- Tree zone violation     : pull affected edge away from tree center
- Make the MINIMUM change needed — keep all other edges at their maximized positions

CRITICAL INSTRUCTIONS:
1. Return ONLY valid JSON — no markdown, no explanation
2. All edges must be horizontal or vertical (no diagonals)
3. Polygon must be simple (non-self-intersecting)
4. Vertex order: clockwise starting from top-left
5. Exactly 10 vertices for the polygon shown above

OUTPUT FORMAT:
{{
  "building_outline": {{
    "type": "polygon",
    "points": [[x1,y1],[x2,y2],[x3,y3],[x4,y4],[x5,y5],[x6,y6],[x7,y7],[x8,y8]]
  }}
}}
"""

# ------------------------------------------------
# CALL GEMINI WITH RETRY
# ------------------------------------------------

def call_gemini_with_retry(prompt, model_name="gemini-2.5-flash", max_retries=3, initial_wait=2):
    """
    Call Gemini API with exponential backoff retry logic.
    Handles transient network errors and rate limiting.
    """
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return response
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            
            is_retryable = any([
                "deadline" in error_msg.lower(),
                "timeout" in error_msg.lower(),
                "connection" in error_msg.lower(),
                "temporarily unavailable" in error_msg.lower(),
                "500" in error_msg,
                "503" in error_msg,
            ])
            
            if attempt < max_retries - 1 and is_retryable:
                wait_time = initial_wait * (2 ** attempt)
                print(f"\n⚠ API Error (attempt {attempt + 1}/{max_retries}): {error_type}")
                print(f"  Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                # Final attempt or non-retryable error
                raise

try:
    response = call_gemini_with_retry(prompt)
except Exception as e:
    print("\n" + "="*60)
    print("ERROR: Gemini API Call Failed After Retries")
    print("="*60)
    print(f"\nError Type: {type(e).__name__}")
    print(f"Error Message: {str(e)}")
    print("\nPossible fixes:")
    print("1. Check GEMINI_API_KEY in .env file")
    print("2. Verify API key is valid and not expired")
    print("3. Check internet connection")
    print("4. Check API rate limits")
    print("5. Try again later (API might be temporarily unavailable)")
    print("\nFull traceback:")
    import traceback
    traceback.print_exc()
    exit(1)


# ----------------------------------------
# TOKEN USAGE TRACKING
# ----------------------------------------

try:
    usage = response.usage_metadata
    llm1_token_data = {
        "prompt_tokens": usage.prompt_token_count,
        "output_tokens": usage.candidates_token_count,
        "total_tokens": usage.total_token_count
    }

    with open("llm1_tokens.json", "w") as f:
        json.dump(llm1_token_data, f)

    prompt_tokens = usage.prompt_token_count
    output_tokens = usage.candidates_token_count
    total_tokens = usage.total_token_count

    print("\nLLM1 TOKEN USAGE")
    print("----------------------")
    print("Prompt Tokens :", prompt_tokens)
    print("Output Tokens :", output_tokens)
    print("Total Tokens  :", total_tokens)
except Exception as e:
    print(f"\nWarning: Could not parse token usage: {e}")

# ----------------------------------------
# EXTRACT RESPONSE TEXT
# ----------------------------------------

try:
    output_text = response.text.strip()
    print("\nGenerated Output:\n")
    print(output_text[:500])  # Print first 500 chars
except Exception as e:
    print(f"\nERROR: Could not extract response text: {e}")
    print(f"Response object type: {type(response)}")
    print(f"Response attributes: {dir(response)}")
    exit(1)

# ------------------------------------------------
# REMOVE MARKDOWN IF PRESENT
# ------------------------------------------------

if output_text.startswith("```json"):
    output_text = output_text.replace("```json", "")
    output_text = output_text.replace("```", "")
    output_text = output_text.strip()

# ------------------------------------------------
# CONVERT TO JSON
# ------------------------------------------------

try:
    generated_layout = json.loads(output_text)
except json.JSONDecodeError as e:
    print("\n" + "="*60)
    print("ERROR: Response is not valid JSON")
    print("="*60)
    print(f"JSON Parse Error: {e}")
    print(f"\nRaw response (first 1000 chars):\n{output_text[:1000]}")
    exit(1)

# ------------------------------------------------
# SAVE OUTPUT
# ------------------------------------------------

try:
    with open("generated_layout.json", "w", encoding="utf-8") as file:
        json.dump(generated_layout, file)
    print("\n[OK] generated_layout.json created successfully!")
except Exception as e:
    print(f"\nERROR: Could not save generated_layout.json: {e}")
    exit(1)