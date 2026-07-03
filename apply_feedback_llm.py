import json, os, sys, time
from google import genai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found")

client = genai.Client(api_key=api_key)

# Load EXISTING valid room plan — the LLM edits it, not replaces it
with open("room_plan.json", "r", encoding="utf-8") as f:
    existing_plan = json.load(f)

human_fb = ""
if os.path.exists("human_feedback.txt"):
    with open("human_feedback.txt", "r", encoding="utf-8") as f:
        human_fb = f.read().strip()

if not human_fb:
    print("[apply_feedback_llm] No feedback — room_plan.json unchanged.")
    sys.exit(0)

prompt = f"""You are editing an existing residential room plan JSON.
Apply the user's feedback with MINIMUM changes.

STRICT RULES:
1. Return the COMPLETE JSON with ALL rooms — do not drop any room.
2. Only modify rooms that are EXPLICITLY MENTIONED in the feedback.
3. Rooms NOT mentioned must be returned with VALUES IDENTICAL to the current plan.
4. Size changes: set size_category to "large" (grow/bigger/larger/expand/enlarge) or "small" (shrink/smaller/reduce/compact).
5. Location changes: update preferred_location using values: north, south, east, west, north-west, north-east, south-west, south-east, central.
6. Swaps: exchange the preferred_location (and optionally size_category) of the two rooms.
7. Do NOT change adjacency, windows_required, doors_required, privacy, or type unless explicitly asked.
8. Return ONLY valid JSON — no markdown fences, no explanation text.

CURRENT ROOM PLAN (your base — preserve everything not mentioned):
{json.dumps(existing_plan, indent=2)}

USER FEEDBACK:
{human_fb}

Return the updated room plan JSON:"""


def call_gemini(prompt, max_retries=3, initial_wait=2):
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        except Exception as e:
            msg = str(e)
            retryable = any(k in msg.lower() for k in ("deadline", "timeout", "connection", "unavailable", "500", "503"))
            if attempt < max_retries - 1 and retryable:
                wait = initial_wait * (2 ** attempt)
                print(f"[apply_feedback_llm] API error (attempt {attempt+1}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


try:
    response = call_gemini(prompt)
except Exception as e:
    print(f"[apply_feedback_llm] ERROR: {type(e).__name__}: {e}")
    sys.exit(1)

output = response.text.strip()
if output.startswith("```"):
    output = output.split("```")[1]
    if output.startswith("json"):
        output = output[4:]
    output = output.strip()

try:
    updated_plan = json.loads(output)
except json.JSONDecodeError as e:
    print(f"[apply_feedback_llm] ERROR: LLM returned invalid JSON: {e}")
    print(f"Raw (first 500 chars): {output[:500]}")
    sys.exit(1)

with open("room_plan.json", "w", encoding="utf-8") as f:
    json.dump(updated_plan, f, indent=2)

print("[apply_feedback_llm] room_plan.json updated via LLM.")

# Show what changed
old_rooms = {r["name"]: r for r in (existing_plan["rooms"] if isinstance(existing_plan, dict) else existing_plan)}
new_rooms = updated_plan["rooms"] if isinstance(updated_plan, dict) else updated_plan
any_change = False
for r in new_rooms:
    old = old_rooms.get(r["name"], {})
    diffs = [f"{k}: {old.get(k)!r} -> {r[k]!r}" for k in ("size_category", "preferred_location") if old.get(k) != r.get(k)]
    if diffs:
        print(f"  {r['name']}: {', '.join(diffs)}")
        any_change = True
if not any_change:
    print("[apply_feedback_llm] No fields changed (LLM preserved existing plan).")
