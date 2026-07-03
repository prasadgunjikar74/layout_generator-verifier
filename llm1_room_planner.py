
import json
import os
import sys
import time
from google import genai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# LOAD ENV VARIABLES
# ============================================================

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env file")

# ============================================================
# CONFIGURE GEMINI CLIENT
# ============================================================

client = genai.Client(api_key=api_key)
model = "gemini-2.5-flash"

# ============================================================
# LOAD INPUT DATA
# ============================================================

with open("input.json", "r") as f:

    input_data = json.load(f)

# ============================================================
# LOAD BUILDING FOOTPRINT
# ============================================================

with open("generated_layout.json", "r") as f:

    generated_layout = json.load(f)

# ============================================================
# LOAD FEEDBACK
# ============================================================

# Human design preferences (Phase 3 refinement) — design intent, honored as goals
human_feedback_text = ""
if os.path.exists("human_feedback.txt"):
    with open("human_feedback.txt", "r", encoding="utf-8") as f:
        human_feedback_text = f.read().strip()

# Violation feedback (from LLM2) — constraint violations, must be fixed
feedback_text = ""
if os.path.exists("feedback.txt"):
    with open("feedback.txt", "r", encoding="utf-8") as f:
        feedback_text = f.read().strip()

building_outline = (
    generated_layout["building_outline"]["points"]
)

# ============================================================
# READ CONSTRAINTS FROM input.json (no hardcoding)
# ============================================================

_c = {c["id"]: c["value"] for c in input_data.get("constraints", []) if "id" in c and "value" in c}
front_setback = _c.get("C1", 7)
rear_setback  = _c.get("C2", 7)
left_setback  = _c.get("C3", 5)
right_setback = _c.get("C4", 5)
max_footprint = _c.get("C5", 4650)

# ============================================================
# COMPACT INPUT - ENHANCED WITH FOOTPRINT INFO
# ============================================================

compact_input = {

    "building_outline": building_outline,
    "building_outline_info": f"Non-rectangular polygon with {len(building_outline)} vertices",

    "site_boundary": input_data["site"]["boundary"]["points"],
    "site_info": "Irregular polygon - building must stay within this boundary",

    "constraints": {
        "front_setback_ft": front_setback,
        "rear_setback_ft": rear_setback,
        "left_setback_ft": left_setback,
        "right_setback_ft": right_setback,
        "max_footprint_sqft": max_footprint,
        "coordinate_system": {
            "origin": "bottom-left (southwest)",
            "x_increases": "east (right)",
            "y_increases": "north (up)",
            "south_entry": "low Y values (bottom)",
            "north_private": "high Y values (top)"
        }
    },

    "requirements": {

        "bedrooms": 2,
        "bathrooms": 2,

        "include": [
            "Living Room",
            "Kitchen",
            "Dining Area",
            "Corridor"
        ]
    }
}

# ============================================================
# PROMPT
# ============================================================

prompt = f"""
You are an expert architectural room-planning agent specializing in realistic residential layouts.

CRITICAL CONSTRAINT AWARENESS:
The building footprint is NOT a simple rectangle. It's an irregular polygon that must respect:
- Site boundary (building must stay completely inside)
- Front setback: {front_setback} ft from south edge
- Rear setback: {rear_setback} ft from north edge
- Left setback: {left_setback} ft from west edge
- Right setback: {right_setback} ft from east edge
- Max area: {max_footprint} sq ft

Your room placement graph should be AWARE of these constraints and position rooms accordingly.

COORDINATE SYSTEM:
- Origin: bottom-left corner (southwest)
- X-axis: increases going EAST (right)
- Y-axis: increases going NORTH (up)
- SOUTH (entry): low Y values, bottom of footprint - ENTRY POINT
- NORTH (private): high Y values, top of footprint - BEDROOM ZONE
- EAST: high X values, right
- WEST: low X values, left

BUILDING FOOTPRINT CHARACTERISTICS:
- The boundary is IRREGULAR/NON-RECTANGULAR polygon with {len(building_outline)} vertices
- Rooms must logically fit inside without exceeding boundary
- You don't design the exact layout geometry, but your room plan should be CONSTRAINT-AWARE
- The actual building outline is provided for reference (see Input section)

{f"""HUMAN DESIGN PREFERENCES (highest priority — apply these as design goals):
{human_feedback_text}

To apply size changes, update the room's "size_category" field:
  "increase / bigger / larger"  → set size_category to "large"  (30% of building area)
  "decrease / smaller / compact" → set size_category to "small"  (12% of building area)
  "medium / normal"              → set size_category to "medium" (20% of building area)
Also adjust "preferred_location" if the user asks to move a room.

""" if human_feedback_text else ""}{f"""VIOLATION FEEDBACK (hard constraint violations — must fix):
{feedback_text}

""" if feedback_text else ""}TASK:
Generate a realistic interior room planning graph that RESPECTS the constraint zones and building boundary.

You are NOT generating walls, geometry, or exact coordinates.

You ARE ONLY generating:
- room names and types
- adjacency relationships (which rooms connect)
- circulation flow (how people move through space)
- privacy hierarchy (public → semi-private → private)
- preferred placement zones (AWARE of setbacks and boundary shape)
- relative room sizing (small/medium/large)
- window and door requirements

CRITICAL ARCHITECTURAL RULES:

🚫 MUST NOT DO (READ THIS CAREFULLY):
- DO NOT create rooms that span the ENTIRE WIDTH of the building
- DO NOT stack rooms vertically like a cake (Living Room full width, then Dining full width, then Corridor full width)
- This is UNREALISTIC and BORING
- Real houses have rooms arranged side-by-side and in varied configurations

✅ MUST DO (REALISTIC LAYOUT):
- Create rooms with VARIED WIDTHS (some narrow, some wide)
- Arrange rooms horizontally alongside each other (not just stacked)
- Example: Bedroom 1 on LEFT (30% width), Bedroom 2 on RIGHT (50% width), Corridor on FAR RIGHT (20% width spine)
- Example: Kitchen and Dining Area in different horizontal positions (not stacked vertically full width)
- Example: Living Room might occupy LEFT HALF, with Dining/Kitchen on RIGHT HALF at different heights
- Multiple rooms should share the same vertical level (Y range)
- This creates an ORGANIC, REALISTIC house like the reference image shown

1. ENTRY & PUBLIC SPACES:
   - Main entrance at SOUTH (bottom of building, entry point)
   - Living Room MUST be immediately inside entry (south zone, but NOT necessarily full width)
   - Living Room can occupy left/center portion (30-50% of width)
   - Allow space for other public areas (Dining, Kitchen) at same level but different X position
   - Entrance → Living Room → rest of house (natural flow)
   - These rooms are closest to Y=minimum (south end of boundary)

2. CIRCULATION & CONNECTIVITY:
   - Central corridor connects private areas (bedrooms, bathrooms)
   - Corridor should branch off from public spaces (living/dining)
   - Corridor is backbone connecting bedroom zones
   - All bedrooms access via corridor, not directly from living spaces
   - Corridor can be narrow spine (15-25% width) running vertically

3. BEDROOM & BATHROOM PLACEMENT:
   - Bedrooms towards NORTH (high Y values, private side, away from entry)
   - Bathrooms INTEGRATED INTO BEDROOMS (ensuite concept - shown as part of bedroom footprint, not separate)
   - Bathroom 1 is INSIDE Bedroom 1 space (ensuite)
   - Bathroom 2 is INSIDE Bedroom 2 space (ensuite)
   - Bedroom 1 and Bedroom 2 should be SIDE-BY-SIDE (different X positions, same Y range)
   - Bedrooms should be roughly equal distance from entry
   - Account for REAR SETBACK ({rear_setback}ft) - bedrooms should not reach it
   - Each bedroom includes attached ensuite bathroom within its footprint
   - Bedroom 1 occupies LEFT section, Bedroom 2 occupies RIGHT/CENTER section at SAME HEIGHT LEVEL

4. KITCHEN & DINING:
   - Kitchen connected to Dining Area (food service)
   - Dining Area connected to Living Room (entertaining)
   - Kitchen NOT in public entry zone
   - Can be west or central position, avoiding excessive setback constraints
   - Kitchen connects to corridor for circulation
   - Kitchen away from rear setback area

5. BEDROOM 3 ROOM:
   - Add a Bedroom 3 room to utilize remaining floor space
   - Can be west or east position, south of bedrooms
   - Adjacent to corridor for access
   - Medium size category
   - Semi-private (not high privacy like bedrooms)
   - Windows required

6. SIZE HIERARCHY:
   - Large: Living Room (primary public space)
   - Medium: Bedrooms (2, includes ensuite bathrooms), Dining Area, Kitchen, Bedroom 3
   - Small: Entrance, Corridor
   - Bathrooms: Integrated within bedrooms (not separate entries)

7. WINDOW & DOOR REQUIREMENTS:
   - Exterior rooms (living, dining, bedrooms with ensuite): windows required (positioned near boundary edges)
   - Interior rooms (corridor): minimal/no windows
   - Bedroom 3: windows required
   - All doors on room boundaries where adjacencies connect

8. REALISTIC ADJACENCY CONSTRAINTS:
   - Each bedroom includes integrated ensuite (no separate bathroom room)
   - Bedroom 3 adjacent to Corridor
   - Dining adjacent to both Kitchen and Living
   - Corridor is central hub
   - No dead-end rooms

8. RESPECTING THE IRREGULAR BOUNDARY:
   - The building outline has multiple vertices and is non-rectangular
   - Room zones should respect the actual boundary shape
   - South zone rooms should use lower Y values
   - North zone rooms should use higher Y values but stay within boundary
   - Don't assume rectangular layout - be aware of diagonal/irregular edges

REAL-WORLD EXAMPLE LAYOUT:
Entry (S) → Living Room → Corridor branches:
  - Left/West branch: Bedroom 1 (with ensuite bathroom inside)
  - Right/East branch: Bedroom 2 (with ensuite bathroom inside)
Dining/Kitchen: Central area, connected to Living and Corridor
Bedroom 3: Fills remaining space, accessible from Corridor

Building Footprint (for awareness):
Vertices: {building_outline}
Note: This is IRREGULAR - adapt room placement accordingly

Site Constraints:
{json.dumps(compact_input['constraints'], indent=2)}

Return ONLY valid JSON with NO markdown formatting.

Output MUST include all required fields:

{{
  "rooms": [
    {{
      "name": "Entrance",
      "type": "circulation",
      "size_category": "small",
      "privacy": "public",
      "preferred_location": "south-east",
      "adjacent_to": ["Living Room"],
      "windows_required": false,
      "doors_required": true
    }},
    {{
      "name": "Living Room",
      "type": "living",
      "size_category": "large",
      "privacy": "public",
      "preferred_location": "south",
      "adjacent_to": ["Entrance", "Dining Area", "Corridor"],
      "windows_required": true,
      "doors_required": true
    }},
    {{
      "name": "Corridor",
      "type": "circulation",
      "size_category": "small",
      "privacy": "semi-private",
      "preferred_location": "central",
      "adjacent_to": ["Living Room", "Kitchen", "Bedroom 1", "Bedroom 2", "Bedroom 3"],
      "windows_required": false,
      "doors_required": true
    }},
    {{
      "name": "Bedroom 1",
      "type": "bedroom",
      "size_category": "medium",
      "privacy": "private",
      "preferred_location": "north-west",
      "interior_features": ["Ensuite Bathroom"],
      "adjacent_to": ["Corridor"],
      "windows_required": true,
      "doors_required": true
    }},
    {{
      "name": "Bedroom 2",
      "type": "bedroom",
      "size_category": "medium",
      "privacy": "private",
      "preferred_location": "north-east",
      "interior_features": ["Ensuite Bathroom"],
      "adjacent_to": ["Corridor"],
      "windows_required": true,
      "doors_required": true
    }},
    {{
      "name": "Bedroom 3",
      "type": "bedroom",
      "size_category": "medium",
      "privacy": "private",
      "preferred_location": "central-south-west",
      "adjacent_to": ["Corridor"],
      "windows_required": true,
      "doors_required": true
    }},
    {{
      "name": "Dining Area",
      "type": "dining",
      "size_category": "medium",
      "privacy": "public",
      "preferred_location": "central-south-east",
      "adjacent_to": ["Living Room", "Kitchen"],
      "windows_required": true,
      "doors_required": true
    }},
    {{
      "name": "Kitchen",
      "type": "kitchen",
      "size_category": "medium",
      "privacy": "public",
      "preferred_location": "central-east",
      "adjacent_to": ["Dining Area", "Corridor"],
      "windows_required": true,
      "doors_required": true
    }}
  ]
}}

Building Constraints Awareness:
- South entry zone (Y <= approx 20% of height): Entrance, Living Room - near bottom boundary
- Central zone (Y ~20-70% height): Corridor, Dining, Kitchen, Bedroom 3 - middle of building
- North private zone (Y >= 70% height): Bedrooms with integrated ensuites - upper area but before rear setback
- Respect irregular boundary: don't assume rectangular layout

Key Changes:
- Bathrooms are now INTEGRATED within bedroom spaces (shown as ensuite features, not separate rooms)
- Bedroom 3 room added to utilize remaining floor space
- More compact and realistic modern residential layout

Input:
{json.dumps(compact_input)}
"""

# ============================================================
# GENERATE RESPONSE WITH RETRY
# ============================================================

def call_gemini_with_retry(prompt, model="gemini-2.5-flash", max_retries=3, initial_wait=2):
    """
    Call Gemini API with exponential backoff retry logic.
    Handles transient network errors and rate limiting.
    """
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
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
                raise

try:
    response = call_gemini_with_retry(prompt)
except Exception as e:
    print("\n" + "="*60)
    print("ERROR: Gemini API Call Failed After Retries (Room Planner)")
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

try:
    output_text = response.text.strip()
except Exception as e:
    print(f"\nERROR: Could not extract response text: {e}")
    print(f"Response object type: {type(response)}")
    exit(1)

# ============================================================
# CLEAN MARKDOWN
# ============================================================

if output_text.startswith("```json"):

    output_text = (
        output_text
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

# ============================================================
# PARSE JSON
# ============================================================

try:
    room_plan = json.loads(output_text)
except json.JSONDecodeError as e:
    print("\n" + "="*60)
    print("ERROR: Room planning response is not valid JSON")
    print("="*60)
    print(f"JSON Parse Error: {e}")
    print(f"\nRaw response (first 1000 chars):\n{output_text[:1000]}")
    exit(1)

# ============================================================
# SAVE OUTPUT
# ============================================================

try:
    with open("room_plan.json", "w", encoding="utf-8") as f:
        json.dump(room_plan, f, indent=2)
except Exception as e:
    print(f"\nERROR: Could not save room_plan.json: {e}")
    exit(1)

# ============================================================
# PRINT OUTPUT
# ============================================================

print("\n[OK] Room planning graph generated successfully.")
print("[OK] Saved to room_plan.json")

