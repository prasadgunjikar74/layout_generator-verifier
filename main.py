import os
import json
import subprocess
import time

# ============================================================
# FILES
# ============================================================

LLM1_SCRIPT             = "llm1_generate.py"
LLM1_ROOM_PLANNER_SCRIPT = "llm1_room_planner.py"
ROOM_ALLOCATOR_SCRIPT   = "room_allocator_v4_spatial.py"
DXF_SCRIPT              = "generate_dxf.py"
Z3_SCRIPT               = "z3_checker.py"
LLM2_SCRIPT             = "llm2_feedback.py"

VIOLATIONS_FILE      = "violations.json"
FEEDBACK_FILE        = "feedback.txt"
HUMAN_FEEDBACK_FILE  = "human_feedback.txt"
ITERATION_LOG        = "iteration_log.json"

INNER_VIOLATION_TYPES = {
    "room_adjacency",
    "corridor_connectivity",
    "room_overlap",
    "room_outside_building",
}

MAX_OUTER_ITERATIONS = 5
MAX_INNER_ITERATIONS = 5

# ============================================================
# HELPERS
# ============================================================

def load_violations():
    if not os.path.exists(VIOLATIONS_FILE):
        return []
    with open(VIOLATIONS_FILE, "r") as f:
        return json.load(f)

def save_violations(violations):
    with open(VIOLATIONS_FILE, "w") as f:
        json.dump(violations, f, indent=2)

def is_outer(v):
    return v.get("type") not in INNER_VIOLATION_TYPES

def is_inner(v):
    return v.get("type") in INNER_VIOLATION_TYPES

def run(script):
    subprocess.run(["venv/Scripts/python.exe", script], check=True)

def collect_llm1_tokens():
    global total_llm1_tokens
    if os.path.exists("llm1_tokens.json"):
        with open("llm1_tokens.json", "r") as f:
            data = json.load(f)
        t = data["total_tokens"]
        total_llm1_tokens += t
        print(f"  LLM1 tokens this step : {t}  |  total : {total_llm1_tokens}")

def collect_llm2_tokens():
    global total_llm2_tokens
    if os.path.exists("llm2_tokens.json"):
        with open("llm2_tokens.json", "r") as f:
            data = json.load(f)
        t = data["total_tokens"]
        total_llm2_tokens += t
        print(f"  LLM2 tokens this step : {t}  |  total : {total_llm2_tokens}")

# ============================================================
# STATE
# ============================================================

iteration_history = []
total_llm1_tokens = 0
total_llm2_tokens = 0

# ============================================================
# PHASE 1 — OUTER LAYOUT (loop until outer violations = 0)
# ============================================================

print("\n====================================================")
print("PHASE 1: OUTER LAYOUT VALIDATION")
print("====================================================")

# Remove inner-phase artifacts from any previous run so Phase 1
# only sees the outer boundary (no rooms, no room plan).
for _stale in ("allocated_rooms.json", "room_plan.json", HUMAN_FEEDBACK_FILE):
    if os.path.exists(_stale):
        os.remove(_stale)

outer_valid = False

for outer_iter in range(1, MAX_OUTER_ITERATIONS + 1):

    print(f"\n--- Outer Iteration {outer_iter} / {MAX_OUTER_ITERATIONS} ---")

    iter_data = {
        "phase": "outer",
        "iteration": outer_iter,
        "status": "",
        "violations": [],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    print("\n[1] LLM1 Layout Generation")
    run(LLM1_SCRIPT)
    collect_llm1_tokens()

    # DXF draws site+building+tree only (no rooms yet — allocated_rooms.json absent)
    print("\n[2] DXF Generation (outer only)")
    run(DXF_SCRIPT)

    # Z3 skips inner checks automatically when allocated_rooms.json is absent
    print("\n[3] Z3 Verification (outer only)")
    run(Z3_SCRIPT)

    all_violations   = load_violations()
    outer_violations = [v for v in all_violations if is_outer(v)]

    if not outer_violations:
        print("\n[OK] Outer layout valid — no outer violations.")
        iter_data["status"] = "OUTER_VALID"
        iteration_history.append(iter_data)
        outer_valid = True
        break

    print(f"\n[FAIL] {len(outer_violations)} outer violation(s) found.")
    for v in outer_violations:
        print(f"       {v.get('constraint_id','?')} : {v.get('message','')}")

    iter_data["status"]     = "OUTER_INVALID"
    iter_data["violations"] = outer_violations
    iteration_history.append(iter_data)

    # Feed only outer violations to LLM2
    save_violations(outer_violations)

    print("\n[4] LLM2 Feedback")
    run(LLM2_SCRIPT)
    collect_llm2_tokens()

if not outer_valid:
    print("\n====================================================")
    print("PHASE 1 FAILED: max outer iterations reached")
    print("====================================================")
    with open(ITERATION_LOG, "w") as f:
        json.dump(iteration_history, f, indent=2)
    grand = total_llm1_tokens + total_llm2_tokens
    print(f"\nLLM1: {total_llm1_tokens}  LLM2: {total_llm2_tokens}  Grand: {grand}")
    exit(1)

# ============================================================
# PHASE 2 — INNER LAYOUT (outer layout frozen, loop until inner = 0)
# ============================================================

print("\n====================================================")
print("PHASE 2: INNER LAYOUT VALIDATION (outer layout frozen)")
print("====================================================")

# Clear any leftover outer feedback so room planner starts fresh
if os.path.exists(FEEDBACK_FILE):
    os.remove(FEEDBACK_FILE)

inner_valid = False

inner_iter = 0
while True:
    inner_iter += 1
    print(f"\n--- Inner Iteration {inner_iter} ---")

    iter_data = {
        "phase": "inner",
        "iteration": inner_iter,
        "status": "",
        "violations": [],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Outer layout (generated_layout.json) is NOT regenerated
    print("\n[1] LLM1 Room Planning")
    run(LLM1_ROOM_PLANNER_SCRIPT)

    print("\n[2] Room Allocation")
    run(ROOM_ALLOCATOR_SCRIPT)

    print("\n[3] DXF Generation")
    run(DXF_SCRIPT)

    print("\n[4] Z3 Verification")
    run(Z3_SCRIPT)

    all_violations   = load_violations()
    inner_violations = [v for v in all_violations if is_inner(v)]

    if not inner_violations:
        print("\n[OK] Inner layout valid — no inner violations.")
        iter_data["status"] = "INNER_VALID"
        iteration_history.append(iter_data)
        inner_valid = True
        break

    print(f"\n[FAIL] {len(inner_violations)} inner violation(s) found.")
    for v in inner_violations:
        print(f"       {v.get('constraint_id','?')} : {v.get('message','')}")

    iter_data["status"]     = "INNER_INVALID"
    iter_data["violations"] = inner_violations
    iteration_history.append(iter_data)

    # Feed only inner violations to LLM2
    save_violations(inner_violations)

    print("\n[5] LLM2 Feedback")
    run(LLM2_SCRIPT)
    collect_llm2_tokens()

# ============================================================
# FINAL RESULT
# ============================================================

print("\n====================================================")
if inner_valid:
    print("FINAL RESULT: LAYOUT FULLY VALID (outer + inner)")
else:
    print("PHASE 2 FAILED: max inner iterations reached")
print("====================================================")

# ============================================================
# SAVE ITERATION HISTORY
# ============================================================

with open(ITERATION_LOG, "w") as f:
    json.dump(iteration_history, f, indent=2)

print(f"\nIteration log saved to: {ITERATION_LOG}")

# ============================================================
# TOKEN SUMMARY
# ============================================================

grand_total = total_llm1_tokens + total_llm2_tokens

print("\n====================================================")
print("TOKEN USAGE SUMMARY")
print("====================================================")
print(f"Total LLM1 Tokens : {total_llm1_tokens}")
print(f"Total LLM2 Tokens : {total_llm2_tokens}")
print(f"Grand Total Tokens: {grand_total}")

# ============================================================
# PHASE 3 — HUMAN FEEDBACK REFINEMENT (only if inner valid)
# ============================================================

if not inner_valid:
    exit(0)

print("\n====================================================")
print("PHASE 3: HUMAN FEEDBACK REFINEMENT")
print("====================================================")
print("The layout is valid. You can now refine room sizes.")
print("Type design feedback and press Enter to apply.")
print("Press Enter with no input to finish.\n")

while True:

    print("----------------------------------------------------")
    human_fb = input("Your feedback > ").strip()

    if not human_fb:
        print("\n[OK] No more feedback — final layout is saved.")
        break

    # Save human feedback for LLM1 room planner
    with open(HUMAN_FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write(human_fb)

    # Clear any leftover violation feedback
    if os.path.exists(FEEDBACK_FILE):
        os.remove(FEEDBACK_FILE)

    print("\n[1] Re-planning rooms with your feedback...")
    run(LLM1_ROOM_PLANNER_SCRIPT)

    print("\n[2] Allocating rooms...")
    run(ROOM_ALLOCATOR_SCRIPT)

    print("\n[3] Generating DXF...")
    run(DXF_SCRIPT)

    print("\n[4] Verifying layout...")
    run(Z3_SCRIPT)

    all_violations   = load_violations()
    inner_violations = [v for v in all_violations if is_inner(v)]

    if not inner_violations:
        print("\n[OK] Layout valid after adjustment.")
        print("[OK] Open generated_layout.dxf to review the changes.")
        continue

    # Violations appeared — auto-fix silently
    print(f"\n[WARN] {len(inner_violations)} violation(s) from the adjustment. Auto-fixing...")

    # Human feedback no longer relevant during auto-fix
    if os.path.exists(HUMAN_FEEDBACK_FILE):
        os.remove(HUMAN_FEEDBACK_FILE)

    fix_iter = 0
    while True:
        fix_iter += 1
        save_violations(inner_violations)

        run(LLM2_SCRIPT)
        collect_llm2_tokens()

        run(LLM1_ROOM_PLANNER_SCRIPT)
        run(ROOM_ALLOCATOR_SCRIPT)
        run(DXF_SCRIPT)
        run(Z3_SCRIPT)

        all_violations   = load_violations()
        inner_violations = [v for v in all_violations if is_inner(v)]

        if not inner_violations:
            print(f"\n[OK] Auto-fixed in {fix_iter} iteration(s). Layout is valid.")
            print("[OK] Open generated_layout.dxf to review the changes.")
            break
