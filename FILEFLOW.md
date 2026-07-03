# File Flow Diagram

## Execution Order & Data Dependencies

```
INPUT FILES                 SCRIPTS                    OUTPUT FILES
================================================================================

                        ┌─────────────────────┐
                        │    main.py          │
                        │  (Orchestrator)     │
                        └──────────┬──────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
                    ▼              ▼              ▼
            [ITERATION LOOP]  (5 max iterations)
                    │              │              │
    ┌───────────────┼──────────────┼──────────────┴──────────┐
    │               │              │                         │
    ▼               ▼              ▼                         ▼
 input.json   llm1_generate.py   llm1_room_planner.py   room_allocator_v4_spatial.py
                   │                      │                    │
                   └──────────────────────┬────────────────────┘
                          ▼              ▼              ▼
                   generated_layout.json  room_plan.json  allocated_rooms.json
                          │              │              │
                          └──────────────┬──────────────┘
                                         ▼
                                   z3_checker.py
                                         │
                                    violations.json
                                         │
                         ┌───────────────┴───────────────┐
                         │                               │
                    [VALID?]                        [VIOLATIONS]
                         │                               │
                         │YES                            │NO
                         ▼                               ▼
                    generate_dxf.py              llm2_feedback.py
                         │                               │
                         ▼                               ▼
                 generated_layout.dxf          [Next Iteration]
                         │
                    [EXIT]
```

---

## Detailed File Flow

### **PHASE 1: INPUT SETUP**
```
input.json (Configuration)
│
└─→ Loaded by:
    ├─ llm1_generate.py (constraints, site boundary)
    ├─ llm1_room_planner.py (room requirements)
    ├─ room_allocator_v4_spatial.py (allocation config)
    ├─ z3_checker.py (constraint definitions)
    ├─ llm2_feedback.py (context for feedback)
    └─ generate_dxf.py (site/tree data)
```

---

### **PHASE 2: BUILDING GENERATION (LLM1)**
```
llm1_generate.py
├─ INPUT:  input.json (site boundary, constraints, optional feedback)
├─ CALL:   Gemini API
├─ OUTPUT: generated_layout.json
└─ LOGS:   llm1_tokens.json (API usage)

generated_layout.json
├─ Content: Building footprint polygon
└─ Used by:
    ├─ llm1_room_planner.py
    ├─ room_allocator_v4_spatial.py
    ├─ z3_checker.py
    ├─ llm2_feedback.py
    └─ generate_dxf.py
```

---

### **PHASE 3: ROOM PLANNING (LLM1)**
```
llm1_room_planner.py
├─ INPUT:  input.json + generated_layout.json
├─ CALL:   Gemini API
├─ OUTPUT: room_plan.json
└─ LOGS:   llm1_tokens.json (cumulative)

room_plan.json
├─ Content: Room specs (names, sizes, locations, adjacency)
└─ Used by:
    ├─ room_allocator_v4_spatial.py (room placement)
    └─ generate_dxf.py (room labels)
```

---

### **PHASE 4: SPATIAL ALLOCATION**
```
room_allocator_v4_spatial.py
├─ INPUT:  input.json + generated_layout.json + room_plan.json
├─ PROCESS: Zone-based room placement
├─ OUTPUT: allocated_rooms.json
└─ FLOW:
    ├─ Parse room locations → Zone groups
    ├─ Calculate room sizes → Area ratios
    ├─ Place rooms in zones → Geometric layout
    ├─ Validate no overlaps → Boundary check
    └─ Export coordinates → Polygon array

allocated_rooms.json
├─ Content: Rooms with exact coordinates (polygon vertices)
└─ Used by:
    ├─ z3_checker.py (building polygon only)
    ├─ llm2_feedback.py (building dimensions)
    └─ generate_dxf.py (room visualization)
```

---

### **PHASE 5: CONSTRAINT VALIDATION (Z3)**
```
z3_checker.py
├─ INPUT:  input.json + generated_layout.json
├─ VALIDATES:
│  ├─ Setback constraints (front, rear, left, right)
│  ├─ Max footprint area
│  ├─ Tree protection zone
│  └─ Site boundary containment
├─ OUTPUT: violations.json
└─ DECISION:
    ├─ EMPTY violations.json → ✓ VALID (proceed to DXF)
    └─ WITH violations → ✗ INVALID (proceed to feedback)

violations.json
├─ Empty:     [] → Continue to DXF generation
└─ With data: [...] → Continue to LLM2 feedback
```

---

### **PHASE 6A: VALID LAYOUT → DXF EXPORT**
```
generate_dxf.py (if violations.json is EMPTY)
├─ INPUT:  input.json + generated_layout.json + allocated_rooms.json
├─ PROCESS:
│  ├─ Create DXF document
│  ├─ Draw layers (SITE, BUILDING, TREE, ROOMS, LABELS)
│  ├─ Add polygons (site, building, rooms)
│  ├─ Add symbols (tree, exclusion zone)
│  └─ Add text (room labels)
├─ OUTPUT: generated_layout.dxf
└─ STATUS: ✓ EXIT - LAYOUT COMPLETE
```

---

### **PHASE 6B: INVALID LAYOUT → LLM FEEDBACK**
```
llm2_feedback.py (if violations.json has VIOLATIONS)
├─ INPUT:  input.json + generated_layout.json + violations.json
├─ CALL:   Groq API (fast, cost-effective)
├─ PROCESS:
│  ├─ Extract violation details
│  ├─ Calculate building metrics
│  ├─ Generate context message
│  └─ Send to LLM2 for analysis
├─ OUTPUT: Feedback text (console + back to LLM1)
└─ ACTION: → NEXT ITERATION

[Feedback is used in next iteration's llm1_generate.py]
```

---

### **PHASE 7: ITERATION TRACKING**
```
iteration_log.json
├─ Updated after each iteration by: main.py
├─ STRUCTURE:
│  {
│    "iteration": 1,
│    "status": "VALID" | "VIOLATIONS",
│    "violations": [...],
│    "timestamp": "2026-06-06 19:44:07"
│  }
├─ PURPOSE: Track which iterations succeeded/failed
└─ OUTPUT: Final log shows full history
```

---

## File Dependency Matrix

```
FILE                    READS FROM                              WRITES TO
════════════════════════════════════════════════════════════════════════════
input.json              (Source)                                (Read-only)
                                                                
llm1_generate.py        input.json                              generated_layout.json
                                                                llm1_tokens.json
                                                                
generated_layout.json   ← llm1_generate.py                      llm1_room_planner.py
                                                                room_allocator_v4_spatial.py
                                                                z3_checker.py
                                                                llm2_feedback.py
                                                                generate_dxf.py
                                                                
llm1_room_planner.py    input.json                              room_plan.json
                        generated_layout.json                   llm1_tokens.json
                                                                
room_plan.json          ← llm1_room_planner.py                  room_allocator_v4_spatial.py
                                                                generate_dxf.py
                                                                
room_allocator_v4_      input.json                              allocated_rooms.json
spatial.py              generated_layout.json
                        room_plan.json
                                                                
allocated_rooms.json    ← room_allocator_v4_spatial.py          z3_checker.py
                                                                llm2_feedback.py
                                                                generate_dxf.py
                                                                
z3_checker.py           input.json                              violations.json
                        generated_layout.json
                                                                
violations.json         ← z3_checker.py                         llm2_feedback.py
                                                                iteration_log.json
                                                                [Decision point]
                                                                
llm2_feedback.py        input.json                              [Feedback to LLM1]
                        generated_layout.json                   iteration_log.json
                        violations.json
                                                                
generate_dxf.py         input.json                              generated_layout.dxf
                        generated_layout.json
                        allocated_rooms.json
                                                                
iteration_log.json      ← main.py                               Final iteration history
                                                                
llm1_tokens.json        ← llm1_generate.py                      Cost tracking
                        ← llm1_room_planner.py
```

---

## Data Flow by Phase

### **ITERATION CYCLE** (Repeats 1-5 times)

```
Iteration N
│
├─ [LLM1 Generate]
│  ├─ Read:  input.json (+ feedback from iteration N-1)
│  ├─ Call:  Gemini API
│  └─ Write: generated_layout.json, llm1_tokens.json
│
├─ [LLM1 Room Plan]
│  ├─ Read:  input.json, generated_layout.json
│  ├─ Call:  Gemini API
│  └─ Write: room_plan.json, llm1_tokens.json (append)
│
├─ [Room Allocator]
│  ├─ Read:  input.json, generated_layout.json, room_plan.json
│  ├─ Logic: Zone-based placement, overlap checking
│  └─ Write: allocated_rooms.json
│
├─ [Z3 Validator]
│  ├─ Read:  input.json, generated_layout.json
│  ├─ Check: 4 constraint types
│  └─ Write: violations.json
│
├─ [Decision: Check violations.json]
│  │
│  ├─ IF EMPTY → valid_count++
│  │   └─ [Generate DXF] → Exit
│  │
│  └─ IF NOT EMPTY → feedback_count++
│      └─ [LLM2 Feedback]
│          ├─ Read:  input.json, generated_layout.json, violations.json
│          ├─ Call:  Groq API
│          └─ Loop to next iteration (pass feedback to LLM1)
│
└─ [Log Iteration]
   └─ Append to: iteration_log.json
```

---

## Quick Reference: Which File Does What

| File | Purpose | Read | Write | API |
|------|---------|------|-------|-----|
| **main.py** | Orchestrator | subprocess | iteration_log | None |
| **llm1_generate.py** | Building layout | input.json | generated_layout.json | Gemini |
| **llm1_room_planner.py** | Room specs | input.json, generated_layout.json | room_plan.json | Gemini |
| **room_allocator_v4_spatial.py** | Room placement | all above | allocated_rooms.json | None |
| **z3_checker.py** | Constraint check | input.json, generated_layout.json | violations.json | None |
| **llm2_feedback.py** | Violation analysis | input.json, generated_layout.json, violations.json | console/next iter | Groq |
| **generate_dxf.py** | CAD export | input.json, generated_layout.json, allocated_rooms.json | generated_layout.dxf | None |

---

## Exit Points

```
main.py LOOP
│
└─ Iteration N
   │
   ├─ z3_checker.py produces violations.json
   │
   └─ CHECK: Is violations.json EMPTY?
      │
      ├─ YES → generate_dxf.py → generate_layout.dxf → ✓ EXIT SUCCESS
      │
      ├─ NO (violations exist) AND iteration < 5
      │    → llm2_feedback.py → provide feedback → LOOP NEXT ITERATION
      │
      └─ NO (violations exist) AND iteration == 5
           → Print failure message → ⚠ EXIT WITH VIOLATIONS
```

---

**Version**: 1.0  
**Last Updated**: 2026-06-06
