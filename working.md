# Architectural Layout Pipeline — Working Documentation

## Problem Statement

Given a site with legal constraints (setbacks, max area, protected trees), automatically generate
a valid residential building footprint and interior room layout, then allow the architect to
iteratively refine the design through natural-language feedback.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web server | Flask (Python) |
| Real-time logs | Server-Sent Events (SSE) |
| LLM | Google Gemini 2.5 Flash |
| Geometry | Shapely |
| Constraint checking | Z3 SMT solver |
| DXF output | ezdxf |
| Preview rendering | Matplotlib |

---

## Input File — `input.json`

Single source of truth for the pipeline. Contains:

```
site.boundary.points          → polygon defining the site perimeter
protected_objects[]           → tree with exclusion radius
constraints[]                 → C1 front setback (7ft), C2 rear setback (7ft),
                                C3 left setback (5ft), C4 right setback (5ft),
                                C5 max footprint (4650 sq ft),
                                C6 inside site, C7 avoid tree
allocation_config             → size_category_area_ratios (small=0.12, medium=0.20, large=0.30)
                                margin (1ft wall buffer), corridor_width_ratio, etc.
```

The coordinate system: origin at bottom-left (southwest), X increases east, Y increases north.
South = low Y (entry side), North = high Y (private/bedroom side).

---

## File Flow Diagram

```
.env ──────────────── GEMINI_API_KEY loaded by all LLM scripts
input.json ─────────── site boundary, constraints, allocation config
    │
    ├─── PHASE 1: OUTER BOUNDARY ──────────────────────────────────────
    │
    │   ┌─ loop (max 5 iterations) ───────────────────────────────┐
    │   │                                                          │
    │   │  llm1_generate.py                                        │
    │   │      reads:  input.json, feedback.txt (violation hints)  │
    │   │      calls:  Gemini API                                   │
    │   │      writes: generated_layout.json                        │
    │   │                         ↓                                │
    │   │  generate_dxf.py                                         │
    │   │      reads:  input.json, generated_layout.json           │
    │   │      writes: generated_layout.dxf                        │
    │   │                         ↓                                │
    │   │  z3_checker.py                                           │
    │   │      reads:  input.json, generated_layout.json           │
    │   │      writes: violations.json                             │
    │   │                         ↓                                │
    │   │  [outer violations?] ── yes ──→ llm2_feedback.py         │
    │   │       ↓ no                          writes: feedback.txt  │
    │   │     done                                 └──── repeat ──┘│
    │   └──────────────────────────────────────────────────────────┘
    │
    ├─── PHASE 2: INNER ROOM LAYOUT ───────────────────────────────────
    │
    │   ┌─ loop (until valid) ────────────────────────────────────┐
    │   │                                                          │
    │   │  llm1_room_planner.py                                    │
    │   │      reads:  input.json, generated_layout.json           │
    │   │              feedback.txt (violation hints, if any)       │
    │   │      calls:  Gemini API                                   │
    │   │      writes: room_plan.json   ← logical room graph        │
    │   │                         ↓                                │
    │   │  room_allocator_v4_spatial.py                            │
    │   │      reads:  input.json, generated_layout.json           │
    │   │              room_plan.json, human_feedback.txt (if any) │
    │   │      writes: allocated_rooms.json  ← room polygons        │
    │   │                         ↓                                │
    │   │  generate_dxf.py                                         │
    │   │      reads:  input.json, generated_layout.json           │
    │   │              allocated_rooms.json                         │
    │   │      writes: generated_layout.dxf  ← rooms/doors/windows │
    │   │                         ↓                                │
    │   │  z3_checker.py                                           │
    │   │      reads:  input.json, generated_layout.json           │
    │   │              allocated_rooms.json                         │
    │   │      writes: violations.json                             │
    │   │                         ↓                                │
    │   │  [inner violations?] ── yes ──→ llm2_feedback.py         │
    │   │       ↓ no                          writes: feedback.txt  │
    │   │     done                                 └──── repeat ──┘│
    │   └──────────────────────────────────────────────────────────┘
    │            ↓  preview refreshed in browser (UPDATED sentinel)
    │
    └─── PHASE 3: HUMAN FEEDBACK ──────────────────────────────────────

        [browser shows feedback form — PHASE3_READY sentinel]

        user types feedback → web UI writes human_feedback.txt
                                         ↓
        app.py saves backup:  allocated_rooms.json
                           →  allocated_rooms_backup.json
                                         ↓
        apply_feedback_llm.py
            reads:  room_plan.json (existing valid plan as base)
                    human_feedback.txt
            calls:  Gemini API  (surgical edit — only touched rooms change)
            writes: room_plan.json  (updated size_category / preferred_location)
                                         ↓
        room_allocator_v4_spatial.py
            reads:  input.json, generated_layout.json
                    room_plan.json (updated), human_feedback.txt
            writes: allocated_rooms.json
                                         ↓
        generate_dxf.py  →  generated_layout.dxf
                                         ↓
        z3_checker.py    →  violations.json
                                         ↓
        [inner violations?]
            no  →  preview refreshed, feedback form re-enabled  ✓
                                         ↓
            yes → ┌─ auto-fix loop (max 5 iterations) ─────────┐
                  │  llm2_feedback.py  →  feedback.txt          │
                  │  llm1_room_planner.py  →  room_plan.json    │
                  │  room_allocator  →  generate_dxf  →  z3     │
                  │  [still violations?] ── no → done ✓         │
                  └──────────────────── yes → repeat ───────────┘
                                         ↓
                  [5 attempts exhausted — still violations]
                  restore allocated_rooms_backup.json
                  regenerate DXF from backup
                  re-enable feedback form → user tries again
```

---

## Generated / Intermediate Files

| File | Created by | Purpose |
|---|---|---|
| `generated_layout.json` | `llm1_generate.py` | Building footprint polygon |
| `room_plan.json` | `llm1_room_planner.py` | Room adjacency graph (no geometry) |
| `allocated_rooms.json` | `room_allocator_v4_spatial.py` | Final room polygons |
| `allocated_rooms_backup.json` | `app.py` (Phase 3) | Backup before feedback applied |
| `generated_layout.dxf` | `generate_dxf.py` | Full DXF output |
| `violations.json` | `z3_checker.py` | List of constraint violations |
| `feedback.txt` | `llm2_feedback.py` | Violation descriptions for LLM |
| `human_feedback.txt` | Web UI | User's design preferences |

---

## Phase 1 — Outer Boundary Generation

**Goal**: Produce the largest valid building footprint that fits within all site constraints.

**Script**: `llm1_generate.py`

**Approach**:
1. Parses `input.json` to extract site boundary coordinates, setbacks, tree zone.
2. Computes the theoretically maximized building polygon from the site geometry and setbacks.
3. Sends a prompt to Gemini with:
   - The maximized target polygon as a reference
   - All constraint values interpolated into the prompt
   - Instruction to make minimal changes only if a previous violation was found
4. Saves the output polygon to `generated_layout.json`.

**Constraint check** (`z3_checker.py`):
- Outer violations: front/rear/side setbacks, max area, tree exclusion, site boundary
- If violations found: `llm2_feedback.py` generates a human-readable description → saved to `feedback.txt` → `llm1_generate.py` reruns with feedback
- Loop capped at `MAX_OUTER_ITERATIONS = 5`

---

## Phase 2 — Inner Room Layout

**Goal**: Fill the building footprint with a realistic residential room layout.

### Step A — Room Planning (`llm1_room_planner.py`)

Generates a **logical room graph** (no coordinates) describing:
- Room names, types, privacy levels
- `size_category`: small / medium / large
- `preferred_location`: south, north, central, north-west, etc.
- Adjacency requirements (which rooms must share a wall)
- Window and door requirements

This is purely semantic — no geometry yet.

### Step B — Spatial Allocation (`room_allocator_v4_spatial.py`)

Converts the logical room graph into actual polygons.

**Zone system**:

```
Building height divided into 4 priority groups (south → north):

  Priority 1 (south):         Entry zone — Living Room, Entrance, Dining Area
  Priority 2 (central-south): Transition — Bedroom 3, Dining Area
  Priority 3 (central):       Circulation — Corridor, Kitchen
  Priority 4 (north):         Private — Bedroom 1, Bedroom 2
```

Each priority group is further divided horizontally into west / center / east sub-zones:
`south-west`, `south`, `south-east`, `central-south-west`, `central-south-east`, etc.

**Dynamic zone heights** (key design decision):
Zone vertical extents are computed proportionally to the `size_category` demands of rooms in
each priority group. A priority group with a "large" room gets more building height than one
with only "small" rooms. This makes size_category changes visually significant.

```
group_demand[priority] = sum of SIZE_RATIOS[size_category] for all rooms in that priority
zone_height[priority]  = (group_demand[priority] / total_demand) * build_height
```

**Allocation engine** (per zone, vertical stacking):
1. Intersect zone bounding box with the margin-shrunk feasible area
2. Stack rooms vertically, each getting height proportional to its size ratio
3. `feasible` polygon shrinks as rooms are carved out

**Post-allocation passes**:
- Living Room east expansion (absorbs unallocated south-band gap)
- Corridor carved as central vertical spine
- Ensuite bathrooms carved inside each bedroom corner
- Gap fill: remaining building area distributed to adjacent rooms
- Adjacency bridging: if required rooms don't share a wall, a bridge rectangle is inserted

### Step C — DXF Generation (`generate_dxf.py`)

Draws:
- Site boundary (grey)
- Building outline (green)
- Tree exclusion zone (red)
- Room polygons (magenta)
- Windows on exterior walls only (blue lines, on walls adjacent to building outline)
- Doors on shared walls between adjacent rooms (yellow arc + panel symbol)
- Dimension labels for all walls

### Step D — Constraint Check (`z3_checker.py`)

Inner violation types checked: `room_adjacency`, `corridor_connectivity`, `room_overlap`, `room_outside_building`.
If violations → `llm2_feedback.py` generates text → full re-plan loop.

---

## Phase 3 — Human Feedback

**Goal**: Let the architect iteratively adjust the layout through natural-language instructions.

**UI flow**:
1. Web UI shows `PHASE3_READY` → enables feedback text box.
2. User types feedback (e.g. "make Bedroom 3 bigger and Living Room smaller").
3. UI writes `human_feedback.txt` and POSTs to `/api/feedback`.
4. `feedback_event` is set → pipeline thread wakes up.

**Feedback application** (`apply_feedback_llm.py`):
- Loads the EXISTING `room_plan.json` as a base (not regenerating from scratch).
- Sends a short, focused prompt to Gemini: "apply only these changes, return everything else identical".
- LLM can handle: size changes, location changes, room swaps.
- Only `size_category` and `preferred_location` change for mentioned rooms — all others are preserved.

**Why not re-run `llm1_room_planner.py`?**
The full room planner has a 300-line blank-slate architectural brief. It regenerates everything
including rooms not mentioned in feedback, causing random zone changes → violations cascade.
The focused `apply_feedback_llm.py` prevents this.

**Auto-fix** (if feedback still causes violations):
- `llm2_feedback.py` → `llm1_room_planner.py` (full re-plan with violation context)
- Capped at `MAX_PHASE3_FIX_ITERATIONS = 5`
- If still unresolved: restores `allocated_rooms_backup.json`, re-enables feedback form

**Feedback keywords understood**:

| Intent | Keywords |
|---|---|
| Make room bigger | bigger, larger, increase, expand, enlarge, grow, more |
| Make room smaller | smaller, shrink, reduce, decrease, less, compact |
| Move room | north, south, east, west, north-west, north-east, south-west, south-east, central |
| Swap rooms | swap [Room A] and [Room B] |

Room names must match exactly: `Living Room`, `Bedroom 1`, `Bedroom 2`, `Bedroom 3`, `Kitchen`, `Dining Area`, `Corridor`, `Entrance`.

---

## Web Server (`app.py`)

**Endpoints**:

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves `index.html` |
| `/api/start` | POST | Starts pipeline thread |
| `/api/feedback` | POST | Receives human feedback, sets event |
| `/api/stream` | GET | SSE stream of pipeline log lines |
| `/api/preview` | GET | Renders full layout as PNG (matplotlib) |
| `/api/preview/site` | GET | Renders site boundary only (no rooms) |
| `/api/download/dxf` | GET | Serves `generated_layout.dxf` |

**SSE sentinels** (special tokens in the SSE stream that drive UI state):

| Sentinel | UI action |
|---|---|
| `PHASE1_START` | Update status label |
| `PHASE1_DONE` | Update status label |
| `PHASE2_START` | Update status label |
| `PHASE2_DONE` | Update status label |
| `UPDATED` | Reload preview image |
| `PHASE3_READY` | Enable feedback form |
| `PIPELINE_DONE` | Mark pipeline complete |
| `PIPELINE_ERROR` | Show error state |

---

## Room Allocator — Key Design Decisions

### `feasible = building_poly.buffer(-MARGIN)`
Single source of truth for the 1ft wall buffer. Applied once at the start; every room
is clipped against `feasible`, so double-margin bugs are impossible.

### Position-bounded keyword windows (allocator's own feedback parser)
When multiple rooms are mentioned in one feedback string, each room's keyword window is
bounded by the midpoint to its neighbours. Prevents "bedroom 1 bigger and bedroom 3 smaller"
from leaking keywords across rooms.

### Gap fill priority
Gap fill prefers rooms in `rooms_to_grow` (adjacent pieces go to growing rooms first),
then falls back to nearest-room centroid to distribute remaining space fairly.

### Bathroom allocation
Bathrooms are ensuite — carved out of a bedroom corner after the bedroom is placed.
They are not separate rooms in `room_plan.json`.

---

## Constraint Violation Classification

```
Outer violations (Phase 1):         → fixed by adjusting building footprint
  front_setback, rear_setback,
  side_setback_left, side_setback_right,
  max_area, tree_zone, site_boundary

Inner violations (Phase 2/3):       → fixed by adjusting room layout
  room_adjacency                    rooms that must share a wall don't
  corridor_connectivity             corridor not connected to all rooms
  room_overlap                      two rooms occupy the same space
  room_outside_building             room polygon exceeds building outline
```

---

## Data Flow Summary (one sentence per file)

1. **`input.json`** — defines the site, constraints, and allocator config.
2. **`llm1_generate.py`** — asks Gemini to produce the building footprint polygon.
3. **`llm1_room_planner.py`** — asks Gemini to produce a room adjacency graph.
4. **`apply_feedback_llm.py`** — asks Gemini to surgically edit the room graph based on user feedback.
5. **`room_allocator_v4_spatial.py`** — converts the room graph into actual polygons using zone-based spatial allocation.
6. **`generate_dxf.py`** — draws site, building, rooms, doors, and windows into a DXF file.
7. **`z3_checker.py`** — checks all constraints and writes violations.
8. **`llm2_feedback.py`** — converts violation JSON into human-readable text for the LLM.
9. **`app.py`** — orchestrates the pipeline, streams logs via SSE, serves the web UI.
10. **`templates/index.html`** — web UI: start button, live log, preview image, feedback form.

---

## Files Reference

### Source Scripts

| File | Role |
|---|---|
| `app.py` | Flask server — orchestrates the full pipeline, streams logs via SSE, serves all API endpoints and the web UI. |
| `llm1_generate.py` | Calls Gemini to generate the outer building footprint polygon from site constraints and optional violation feedback. |
| `llm1_room_planner.py` | Calls Gemini to generate a logical room adjacency graph (no coordinates) for the interior of the building. |
| `apply_feedback_llm.py` | Calls Gemini with the existing `room_plan.json` as a base and surgically applies only the changes the user asked for in Phase 3 feedback. |
| `room_allocator_v4_spatial.py` | Zone-based spatial allocator — converts the logical room graph into actual polygon coordinates using priority zones, dynamic height assignment, gap fill, and adjacency bridging. |
| `generate_dxf.py` | Draws the site boundary, building outline, room polygons, interior doors, and exterior windows into a DXF file using ezdxf. |
| `z3_checker.py` | Checks all constraints (setbacks, area, tree zone, room adjacency, overlaps) using the Z3 SMT solver and writes `violations.json`. |
| `llm2_feedback.py` | Reads `violations.json` and calls Gemini to convert the raw violation data into human-readable corrective instructions saved to `feedback.txt`. |

### Web UI

| File | Role |
|---|---|
| `templates/index.html` | Single-page web app — renders the constraint panel, pipeline start button, live SSE log, layout preview image, and Phase 3 feedback form. |

### Configuration & Environment

| File | Role |
|---|---|
| `input.json` | Single source of truth for the site boundary polygon, legal constraints (setbacks, max area, tree), and allocator tuning parameters (size ratios, margin, corridor width). |
| `.env` | Stores the `GEMINI_API_KEY` — loaded by all LLM scripts at startup via `python-dotenv`. |
| `requirements.txt` | Lists all Python dependencies (Flask, Shapely, ezdxf, z3-solver, google-genai, etc.) for `pip install`. |

### Runtime Artifacts (generated during execution)

| File | Written by | Role |
|---|---|---|
| `generated_layout.json` | `llm1_generate.py` | Building footprint polygon vertices — the outer boundary used by all downstream scripts. |
| `room_plan.json` | `llm1_room_planner.py` / `apply_feedback_llm.py` | Logical room graph: name, type, size_category, preferred_location, adjacency list — no coordinates. |
| `allocated_rooms.json` | `room_allocator_v4_spatial.py` | Final room polygons with coordinates, adjacency, window/door flags — input to DXF and Z3. |
| `allocated_rooms_backup.json` | `app.py` (Phase 3) | Snapshot of the last valid layout saved before feedback is applied; restored if auto-fix fails. |
| `generated_layout.dxf` | `generate_dxf.py` | Final DXF file served for download and rendered as the preview image. |
| `violations.json` | `z3_checker.py` | Structured list of constraint violations with type, message, and severity — drives the fix loop. |
| `feedback.txt` | `llm2_feedback.py` | Human-readable violation descriptions fed back into LLM scripts during the auto-fix loop. |
| `human_feedback.txt` | Web UI (`/api/feedback`) | Raw natural-language design preferences submitted by the user in Phase 3; read by `apply_feedback_llm.py` and `room_allocator_v4_spatial.py`. |

### Documentation

| File | Role |
|---|---|
| `working.md` | This file — explains the pipeline architecture, file flow, phase-by-phase approach, and design decisions. |
