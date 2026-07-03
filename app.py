import io
import json
import os
import queue
import shutil
import subprocess
import threading

from flask import Flask, Response, jsonify, render_template, request, send_file

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON   = os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")

SCRIPTS = {
    "llm1_generate":       "llm1_generate.py",
    "llm1_room_planner":   "llm1_room_planner.py",
    "apply_feedback_llm":  "apply_feedback_llm.py",
    "room_allocator":      "room_allocator_v4_spatial.py",
    "generate_dxf":        "generate_dxf.py",
    "z3_checker":          "z3_checker.py",
    "llm2_feedback":       "llm2_feedback.py",
}

VIOLATIONS_FILE     = os.path.join(BASE_DIR, "violations.json")
FEEDBACK_FILE       = os.path.join(BASE_DIR, "feedback.txt")
HUMAN_FEEDBACK_FILE = os.path.join(BASE_DIR, "human_feedback.txt")
DXF_FILE            = os.path.join(BASE_DIR, "generated_layout.dxf")
INPUT_FILE          = os.path.join(BASE_DIR, "input.json")

INNER_VIOLATION_TYPES = {"room_adjacency", "corridor_connectivity",
                         "room_overlap", "room_outside_building"}
MAX_OUTER_ITERATIONS      = 5
MAX_INNER_ITERATIONS      = 5
MAX_PHASE3_FIX_ITERATIONS = 5

# ============================================================
# GLOBAL PIPELINE STATE
# ============================================================

pipeline_queue: queue.Queue = queue.Queue()
feedback_event = threading.Event()
pipeline_lock  = threading.Lock()

pipeline_state = {
    "running":          False,
    "phase":            "idle",   # idle | phase1 | phase2 | phase3 | done | error
    "waiting_feedback": False,
}

# ============================================================
# PIPELINE HELPERS
# ============================================================

def _push(msg: str):
    pipeline_queue.put(msg)

def _push_sentinel(token: str):
    pipeline_queue.put(f"__SENTINEL__{token}__")

def run_script(name: str) -> bool:
    script = os.path.join(BASE_DIR, SCRIPTS[name])
    proc = subprocess.Popen(
        [PYTHON, script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=BASE_DIR,
    )
    for line in proc.stdout:
        _push(line.rstrip())
    proc.wait()
    return proc.returncode == 0

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

def _remove_if_exists(*paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)

# ============================================================
# PIPELINE THREAD
# ============================================================

def pipeline_thread():
    try:
        # ── cleanup stale artifacts ──────────────────────────
        _remove_if_exists(
            os.path.join(BASE_DIR, "allocated_rooms.json"),
            os.path.join(BASE_DIR, "room_plan.json"),
            HUMAN_FEEDBACK_FILE,
        )

        # ── PHASE 1 ─────────────────────────────────────────
        pipeline_state["phase"] = "phase1"
        _push("=== PHASE 1: OUTER LAYOUT ===")
        _push_sentinel("PHASE1_START")

        outer_valid = False
        for outer_iter in range(1, MAX_OUTER_ITERATIONS + 1):
            _push(f"\n--- Outer Iteration {outer_iter}/{MAX_OUTER_ITERATIONS} ---")
            if not run_script("llm1_generate"):
                raise RuntimeError("llm1_generate failed")
            if not run_script("generate_dxf"):
                raise RuntimeError("generate_dxf failed")
            if not run_script("z3_checker"):
                raise RuntimeError("z3_checker failed")

            all_v   = load_violations()
            outer_v = [v for v in all_v if is_outer(v)]

            if not outer_v:
                _push("[OK] Outer layout valid.")
                outer_valid = True
                break

            _push(f"[FAIL] {len(outer_v)} outer violation(s).")
            for v in outer_v:
                _push(f"       {v.get('constraint_id','?')}: {v.get('message','')}")

            save_violations(outer_v)
            if not run_script("llm2_feedback"):
                raise RuntimeError("llm2_feedback failed")

        if not outer_valid:
            raise RuntimeError("Phase 1 failed: max outer iterations reached")

        _push_sentinel("PHASE1_DONE")

        # ── PHASE 2 ─────────────────────────────────────────
        pipeline_state["phase"] = "phase2"
        _push("\n=== PHASE 2: INNER LAYOUT ===")
        _push_sentinel("PHASE2_START")
        _remove_if_exists(FEEDBACK_FILE)

        inner_iter = 0
        while True:
            inner_iter += 1
            _push(f"\n--- Inner Iteration {inner_iter} ---")
            if not run_script("llm1_room_planner"):
                raise RuntimeError("llm1_room_planner failed")
            if not run_script("room_allocator"):
                raise RuntimeError("room_allocator failed")
            if not run_script("generate_dxf"):
                raise RuntimeError("generate_dxf failed")
            if not run_script("z3_checker"):
                raise RuntimeError("z3_checker failed")

            all_v   = load_violations()
            inner_v = [v for v in all_v if is_inner(v)]

            if not inner_v:
                _push("[OK] Inner layout valid.")
                break

            _push(f"[FAIL] {len(inner_v)} inner violation(s).")
            for v in inner_v:
                _push(f"       {v.get('constraint_id','?')}: {v.get('message','')}")

            save_violations(inner_v)
            if not run_script("llm2_feedback"):
                raise RuntimeError("llm2_feedback failed")

        _push_sentinel("PHASE2_DONE")
        _push_sentinel("UPDATED")  # trigger preview refresh

        # ── PHASE 3 ─────────────────────────────────────────
        pipeline_state["phase"] = "phase3"
        pipeline_state["waiting_feedback"] = True
        _push("\n=== PHASE 3: HUMAN FEEDBACK ===")
        _push("Layout valid. Enter design feedback in the form below.")
        _push_sentinel("PHASE3_READY")

        while True:
            feedback_event.wait()
            feedback_event.clear()

            human_fb = ""
            if os.path.exists(HUMAN_FEEDBACK_FILE):
                with open(HUMAN_FEEDBACK_FILE, "r", encoding="utf-8") as f:
                    human_fb = f.read().strip()

            if not human_fb:
                _push("[OK] No more feedback — final layout saved.")
                break

            _push(f"\n[Feedback] {human_fb}")
            _remove_if_exists(FEEDBACK_FILE)

            # Save backup of last valid layout before attempting changes
            _rooms_file  = os.path.join(BASE_DIR, "allocated_rooms.json")
            _backup_file = os.path.join(BASE_DIR, "allocated_rooms_backup.json")
            if os.path.exists(_rooms_file):
                shutil.copy2(_rooms_file, _backup_file)

            run_script("apply_feedback_llm")
            run_script("room_allocator")
            run_script("generate_dxf")
            run_script("z3_checker")

            all_v   = load_violations()
            inner_v = [v for v in all_v if is_inner(v)]

            if not inner_v:
                _push("[OK] Layout valid after adjustment.")
                _push_sentinel("UPDATED")
                continue

            _push(f"[WARN] {len(inner_v)} violation(s) — auto-fixing (max {MAX_PHASE3_FIX_ITERATIONS} attempts)...")
            _remove_if_exists(HUMAN_FEEDBACK_FILE)

            fix_iter = 0
            fixed    = False
            while fix_iter < MAX_PHASE3_FIX_ITERATIONS:
                fix_iter += 1
                save_violations(inner_v)
                run_script("llm2_feedback")
                run_script("llm1_room_planner")
                run_script("room_allocator")
                run_script("generate_dxf")
                run_script("z3_checker")

                all_v   = load_violations()
                inner_v = [v for v in all_v if is_inner(v)]

                if not inner_v:
                    _push(f"[OK] Auto-fixed in {fix_iter} iteration(s).")
                    fixed = True
                    break

            if not fixed:
                _push(f"[WARN] Could not resolve {len(inner_v)} violation(s) after {MAX_PHASE3_FIX_ITERATIONS} attempts.")
                _push("[WARN] Restoring last valid layout. Try rephrasing your feedback.")
                if os.path.exists(_backup_file):
                    shutil.copy2(_backup_file, _rooms_file)
                    run_script("generate_dxf")
                _push_sentinel("UPDATED")
                pipeline_state["waiting_feedback"] = True
                _push_sentinel("PHASE3_READY")
                continue

            _push_sentinel("UPDATED")

        pipeline_state["phase"] = "done"
        _push_sentinel("DONE")

    except Exception as exc:
        _push(f"\n[ERROR] {exc}")
        pipeline_state["phase"] = "error"
        _push_sentinel("ERROR")
    finally:
        pipeline_state["running"]          = False
        pipeline_state["waiting_feedback"] = False

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start_pipeline():
    with pipeline_lock:
        if pipeline_state["running"]:
            return jsonify({"error": "Pipeline already running"}), 409
        pipeline_state["running"]          = True
        pipeline_state["phase"]            = "starting"
        pipeline_state["waiting_feedback"] = False

    # drain any leftover messages from a prior run
    while not pipeline_queue.empty():
        try:
            pipeline_queue.get_nowait()
        except queue.Empty:
            break

    t = threading.Thread(target=pipeline_thread, daemon=True)
    t.start()
    return jsonify({"status": "started"})

@app.route("/api/stream")
def stream():
    def generate():
        while True:
            try:
                msg = pipeline_queue.get(timeout=30)
            except queue.Empty:
                yield "data: __HEARTBEAT__\n\n"
                continue

            if msg.startswith("__SENTINEL__"):
                token = msg.replace("__SENTINEL__", "").replace("__", "")
                yield f"event: sentinel\ndata: {token}\n\n"
                if token in ("DONE", "ERROR"):
                    break
            else:
                # escape newlines for SSE
                safe = msg.replace("\n", "↵")
                yield f"data: {safe}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    if not pipeline_state["waiting_feedback"]:
        return jsonify({"error": "Not waiting for feedback"}), 400
    data = request.get_json(silent=True) or {}
    fb   = data.get("feedback", "").strip()
    with open(HUMAN_FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write(fb)
    feedback_event.set()
    return jsonify({"status": "ok", "feedback": fb})

@app.route("/api/preview")
def preview():
    layout_path = os.path.join(BASE_DIR, "generated_layout.json")
    if not os.path.exists(layout_path):
        return jsonify({"error": "No layout yet"}), 404
    try:
        import math as _math
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.patheffects as pe
        from matplotlib.patches import Polygon as MplPoly, Circle, Arc as MplArc
        from shapely.geometry import Polygon as SPoly, LineString as SLine, Point as SPoint

        BG = "#0d1117"
        fig, ax = plt.subplots(figsize=(13, 10))
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)

        # ── Input data ───────────────────────────────────────
        with open(INPUT_FILE) as f:
            inp = json.load(f)
        site_pts = inp["site"]["boundary"]["points"]
        tree     = inp["protected_objects"][0]

        # ── Site boundary ────────────────────────────────────
        ax.add_patch(MplPoly(site_pts, closed=True, fill=True,
                             facecolor="#12152a", edgecolor="#555577",
                             linewidth=1.5, linestyle="--", zorder=1))
        for i in range(len(site_pts)):
            p1, p2 = site_pts[i], site_pts[(i+1) % len(site_pts)]
            L = ((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2) ** 0.5
            if L < 1: continue
            ax.text((p1[0]+p2[0])/2, (p1[1]+p2[1])/2, f"{L:.0f} ft",
                    color="#555577", fontsize=6, ha="center", va="center", zorder=2)

        # ── Building outline ─────────────────────────────────
        with open(layout_path) as f:
            layout = json.load(f)
        bpts = layout["building_outline"]["points"]
        if bpts:
            ax.add_patch(MplPoly(bpts, closed=True, fill=True,
                                 facecolor="#141a28", edgecolor="#00ee55",
                                 linewidth=3, zorder=3))
            for i in range(len(bpts)):
                p1, p2 = bpts[i], bpts[(i+1) % len(bpts)]
                L = ((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2) ** 0.5
                if L < 1: continue
                ax.text((p1[0]+p2[0])/2, (p1[1]+p2[1])/2, f"{L:.0f} ft",
                        color="#33aa55", fontsize=6.5, ha="center", va="center",
                        zorder=4,
                        path_effects=[pe.withStroke(linewidth=2, foreground=BG)])

        # ── Rooms ────────────────────────────────────────────
        ROOM_FILLS = ["#182030","#182820","#281820","#201828",
                      "#182828","#281820","#202818","#1e1828",
                      "#18202e","#2e2018","#182e20"]
        rooms_path = os.path.join(BASE_DIR, "allocated_rooms.json")
        if os.path.exists(rooms_path):
            with open(rooms_path) as f:
                rdata = json.load(f)
            for idx, room in enumerate(rdata.get("rooms", [])):
                poly = room["polygon"]
                ax.add_patch(MplPoly(poly, closed=True, fill=True,
                                     facecolor=ROOM_FILLS[idx % len(ROOM_FILLS)],
                                     edgecolor="#ee22ee",
                                     linewidth=2.5, zorder=5))
                # Wall dimension labels
                for i in range(len(poly)):
                    p1, p2 = poly[i], poly[(i+1) % len(poly)]
                    L = ((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2) ** 0.5
                    if L < 3: continue
                    ax.text((p1[0]+p2[0])/2, (p1[1]+p2[1])/2, f"{L:.1f}",
                            color="#bb5500", fontsize=5.5,
                            ha="center", va="center", zorder=6,
                            path_effects=[pe.withStroke(linewidth=2, foreground=BG)])
                # Room name label — always inside polygon
                try:
                    rp = SPoly(poly).representative_point()
                    cx, cy = rp.x, rp.y
                except Exception:
                    cx = sum(p[0] for p in poly) / len(poly)
                    cy = sum(p[1] for p in poly) / len(poly)
                ax.text(cx, cy, room["name"],
                        color="#FF7700", fontsize=10, fontweight="bold",
                        ha="center", va="center", zorder=7,
                        path_effects=[pe.withStroke(linewidth=4, foreground="#000000")])

        # ── Doors & Windows ──────────────────────────────────
        if os.path.exists(rooms_path):
            DOOR_COLOR = "#ffdd00"
            WIN_COLOR  = "#4488ff"
            DOOR_W     = 3.0
            _EPS       = 1e-6

            room_lookup = {r["name"]: r["polygon"] for r in rdata.get("rooms", [])}
            room_bounds = {n: SPoly(p).boundary for n, p in room_lookup.items()}
            bld_boundary = SPoly(bpts).boundary
            seen_doors = set()

            for room in rdata.get("rooms", []):
                rname = room["name"]
                poly  = room["polygon"]

                # Interior doors — one per unique adjacent pair
                for adj in room.get("adjacent_to", []):
                    pair = tuple(sorted([rname, adj]))
                    if pair in seen_doors or adj not in room_lookup:
                        continue
                    seen_doors.add(pair)
                    try:
                        shared = SPoly(poly).boundary.intersection(SPoly(room_lookup[adj]).boundary)
                        if shared.is_empty or shared.length < 0.5:
                            continue
                        if hasattr(shared, "geoms"):
                            segs = [g for g in shared.geoms if hasattr(g, "length") and g.length > 0]
                            if not segs: continue
                            shared = max(segs, key=lambda g: g.length)
                        mid = shared.interpolate(0.5, normalized=True)
                        mx, my = mid.x, mid.y
                        b = shared.bounds
                        is_h = (b[2]-b[0]) >= (b[3]-b[1])
                        hw = min(DOOR_W / 2, shared.length / 2 * 0.85)
                        if hw < 0.4: continue
                        dw = hw * 2
                        if is_h:
                            hy = (b[1]+b[3]) / 2
                            xh = mx - hw
                            ax.plot([xh, xh], [hy, hy - dw], color=DOOR_COLOR, linewidth=2, zorder=9)
                            ax.add_patch(MplArc((xh, hy), dw*2, dw*2, angle=0,
                                               theta1=270, theta2=360,
                                               color=DOOR_COLOR, linewidth=1.5, zorder=9))
                        else:
                            vx = (b[0]+b[2]) / 2
                            yh = my - hw
                            ax.plot([vx, vx + dw], [yh, yh], color=DOOR_COLOR, linewidth=2, zorder=9)
                            ax.add_patch(MplArc((vx, yh), dw*2, dw*2, angle=0,
                                               theta1=0, theta2=90,
                                               color=DOOR_COLOR, linewidth=1.5, zorder=9))
                    except Exception:
                        pass

                # Exterior entrance door
                if rname == "Entrance":
                    try:
                        xs_e = [p[0] for p in poly]; ys_e = [p[1] for p in poly]
                        ecx = sum(xs_e) / len(xs_e)
                        eymin = min(ys_e)
                        xh = ecx - DOOR_W / 2
                        ax.plot([xh, xh + DOOR_W], [eymin, eymin],
                                color=DOOR_COLOR, linewidth=2.5, zorder=9)
                        ax.add_patch(MplArc((xh, eymin), DOOR_W*2, DOOR_W*2, angle=0,
                                           theta1=270, theta2=360,
                                           color=DOOR_COLOR, linewidth=1.5, zorder=9))
                    except Exception:
                        pass

                # Windows on exterior walls only
                if not room.get("windows_required", False):
                    continue
                for i in range(len(poly)):
                    p1, p2 = poly[i], poly[(i+1) % len(poly)]
                    x1, y1 = p1; x2, y2 = p2
                    wall_len = _math.dist(p1, p2)
                    if wall_len < _EPS: continue
                    mx_w = (x1+x2)/2; my_w = (y1+y2)/2
                    # (a) not shared with another room
                    wall_line = SLine([p1, p2])
                    is_ext = all(
                        wall_line.intersection(room_bounds[on]).length <= 0.3
                        for on in room_bounds if on != rname
                    )
                    if not is_ext: continue
                    # (b) midpoint within 2 ft of building boundary
                    if bld_boundary.distance(SPoint(mx_w, my_w)) > 2.0: continue
                    if wall_len < 4: continue
                    win_sz = min(4, wall_len * 0.6)
                    if abs(y2-y1) <= _EPS:  # horizontal wall
                        ax.plot([mx_w - win_sz/2, mx_w + win_sz/2], [y1, y1],
                                color=WIN_COLOR, linewidth=3, solid_capstyle="round", zorder=9)
                    elif abs(x2-x1) <= _EPS:  # vertical wall
                        ax.plot([x1, x1], [my_w - win_sz/2, my_w + win_sz/2],
                                color=WIN_COLOR, linewidth=3, solid_capstyle="round", zorder=9)

        # ── Tree ─────────────────────────────────────────────
        tc = tree["geometry"]["center"]
        tr = tree["geometry"]["radius"]
        te = tree["protection_rules"]["total_exclusion_radius"]
        ax.add_patch(Circle(tc, te, facecolor="#0a200a", edgecolor="#33cc33",
                            linewidth=1.5, alpha=0.6, zorder=4))
        ax.add_patch(Circle(tc, tr, facecolor="#1a5c1a", edgecolor="#44ff44",
                            linewidth=1.5, zorder=5))
        ax.text(tc[0], tc[1]+te+1.5, "Protected Tree", color="#44ff44",
                fontsize=7, fontweight="bold", ha="center", va="bottom", zorder=6)

        # ── Legend ───────────────────────────────────────────
        legend_handles = [
            mpatches.Patch(facecolor="#555577", label="Site Boundary"),
            mpatches.Patch(facecolor="#00ee55", label="Building Outline"),
            mpatches.Patch(facecolor="#44ff44", label="Protected Tree"),
            mpatches.Patch(facecolor="#ee22ee", label="Room Walls"),
            mpatches.Patch(facecolor="#FF7700", label="Room Labels"),
            mpatches.Patch(facecolor="#ffdd00", label="Doors"),
            mpatches.Patch(facecolor="#4488ff", label="Windows"),
        ]
        leg = ax.legend(handles=legend_handles,
                        loc="upper left", bbox_to_anchor=(1.01, 1.0),
                        borderaxespad=0,
                        fontsize=8, framealpha=0.9,
                        facecolor="#0f1117", edgecolor="#2d3148",
                        labelcolor="white", title="Legend", title_fontsize=8)
        leg.get_title().set_color("#a5b4fc")

        # ── Axis ─────────────────────────────────────────────
        xs = [p[0] for p in site_pts]
        ys = [p[1] for p in site_pts]
        pad = 5
        ax.set_xlim(min(xs)-pad, max(xs)+pad)
        ax.set_ylim(min(ys)-pad, max(ys)+pad)
        ax.set_aspect("equal")
        ax.tick_params(colors="#475569", labelsize=6)
        for spine in ax.spines.values():
            spine.set_edgecolor("#2d3148")
        ax.grid(True, color="#1a1d2e", linewidth=0.5, zorder=0)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    except Exception as exc:
        import traceback
        return jsonify({"error": str(exc), "trace": traceback.format_exc()}), 500

@app.route("/api/preview/site")
def preview_site():
    """Render site boundary + protected tree from input.json — no pipeline needed."""
    if not os.path.exists(INPUT_FILE):
        return jsonify({"error": "input.json not found"}), 404
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import Polygon as MplPolygon, Circle

        with open(INPUT_FILE, "r") as f:
            data = json.load(f)

        site_pts   = data["site"]["boundary"]["points"]
        tree       = data["protected_objects"][0]
        t_center   = tree["geometry"]["center"]
        t_trunk_r  = tree["geometry"]["radius"]
        t_excl_r   = tree["protection_rules"]["total_exclusion_radius"]

        all_x = [p[0] for p in site_pts]
        all_y = [p[1] for p in site_pts]
        margin = 5
        x_min, x_max = min(all_x) - margin, max(all_x) + margin
        y_min, y_max = min(all_y) - margin, max(all_y) + margin

        fig, ax = plt.subplots(figsize=(10, 9))
        fig.patch.set_facecolor("#0f1117")
        ax.set_facecolor("#0f1117")

        # Site boundary
        site_patch = MplPolygon(
            site_pts, closed=True,
            fill=True, facecolor="#1a1d2e", edgecolor="#818cf8",
            linewidth=2, linestyle="--", zorder=1
        )
        ax.add_patch(site_patch)

        # Dimension labels on each site edge
        for i in range(len(site_pts)):
            p1 = site_pts[i]
            p2 = site_pts[(i + 1) % len(site_pts)]
            length = ((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2) ** 0.5
            mx, my = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
            ax.text(mx, my, f"{length:.0f} ft", color="#94a3b8",
                    fontsize=7, ha="center", va="center", zorder=4,
                    bbox=dict(facecolor="#0f1117", edgecolor="none", pad=1))

        # Tree exclusion zone
        ax.add_patch(Circle(t_center, t_excl_r,
                            facecolor="#14532d", edgecolor="#4ade80",
                            linewidth=1.5, alpha=0.35, zorder=2))
        # Tree trunk
        ax.add_patch(Circle(t_center, t_trunk_r,
                            facecolor="#16a34a", edgecolor="#4ade80",
                            linewidth=1.5, zorder=3))
        ax.text(t_center[0], t_center[1] + t_excl_r + 1.5,
                "Protected Tree", color="#4ade80", fontsize=7,
                ha="center", va="bottom", zorder=4)

        # Corner coordinates
        for p in site_pts:
            ax.text(p[0], p[1], f"({p[0]},{p[1]})", color="#475569",
                    fontsize=6, ha="center", va="bottom", zorder=4)

        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)
        ax.set_aspect("equal")
        ax.tick_params(colors="#475569", labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor("#2d3148")
        ax.set_title("Site Plot — Input Layout", color="#a5b4fc",
                     fontsize=10, pad=10)
        ax.grid(True, color="#1e2235", linewidth=0.5, zorder=0)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor="#0f1117")
        plt.close(fig)
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/constraints")
def constraints():
    if not os.path.exists(INPUT_FILE):
        return jsonify({"error": "input.json not found"}), 404
    with open(INPUT_FILE, "r") as f:
        return jsonify(json.load(f))

@app.route("/api/status")
def status():
    return jsonify(dict(pipeline_state))

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    app.run(debug=False, threaded=True, port=5000)
