# app.py
import os
import time
import requests
from flask import Flask, request, jsonify, session

# ── Flask setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)

# Secret key for sessions (set FLASK_SECRET in Render → Environment)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me")

# Make session cookies work inside Canvas' iframe
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True  # must be HTTPS on Render
)

# ── OpenAI config ─────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ASSISTANT_ID = os.environ.get("ASSISTANT_ID")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY env var")
if not ASSISTANT_ID:
    # We don't raise here so the service can start and show a helpful error in UI
    print("⚠️  WARNING: ASSISTANT_ID not set. Set it in Render → Environment.")

OPENAI_HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
    "OpenAI-Beta": "assistants=v2",                      # required for Assistants v2
    **({"OpenAI-Project": os.environ["OPENAI_PROJECT"]}  # (optional) if your key is project-scoped
       if os.environ.get("OPENAI_PROJECT") else {}),
    **({"OpenAI-Organization": os.environ["OPENAI_ORG"]} # (optional) if your org requires it
       if os.environ.get("OPENAI_ORG") else {}),
}


# ── Security headers so Canvas can iframe your app ────────────────────────────
@app.after_request
def add_headers(resp):
    # Allow iframing by Canvas
    resp.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://*.instructure.com https://*.instructuremedia.com;"
    )
    # If any proxy sets a blocking X-Frame-Options, override it
    resp.headers["X-Frame-Options"] = "ALLOWALL"
    return resp

# ── Helpers ───────────────────────────────────────────────────────────────────
def ensure_thread():
    """
    Create or reuse a per-user thread stored in the Flask session.
    Each browser/user gets their own conversation thread.
    """
    if "thread_id" not in session:
        r = requests.post(
            "https://api.openai.com/v1/threads",
            headers=OPENAI_HEADERS,
            timeout=30
        )
        r.raise_for_status()
        session["thread_id"] = r.json()["id"]
    return session["thread_id"]

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    # Simple health endpoint for Render
    return {"ok": True}, 200

@app.route("/api/chat", methods=["POST"])
def chat_api():
    if not ASSISTANT_ID:
        return jsonify({"error": "Missing ASSISTANT_ID env var in Render"}), 500

    data = request.get_json(silent=True) or {}
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify({"error": "message required"}), 400

    try:
        thread_id = ensure_thread()

        # (1) Add user message to thread
        r1 = requests.post(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers=OPENAI_HEADERS,
            json={"role": "user", "content": msg},
            timeout=30
        )
        if r1.status_code >= 400:
            return jsonify({"error": r1.text}), 502

        # (2) Create a run

        r2 = requests.post(
        f"https://api.openai.com/v1/threads/{thread_id}/runs",
        headers=OPENAI_HEADERS,
        json={
            "assistant_id": ASSISTANT_ID,
            "response_format": {"type": "text"}   # ✅ force plain text
        },timeout=30
        )

        if r2.status_code >= 400:
            return jsonify({"error": r2.text}), 502
        run_id = r2.json()["id"]

        # (3) Poll until the run completes
        #    (You can switch to server-sent events for streaming later.)
        while True:
            rr = requests.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers=OPENAI_HEADERS,
                timeout=30
            )
            j = rr.json()
            status = j.get("status")
            if status in ("completed", "failed", "cancelled", "expired"):
                break
            time.sleep(0.7)

        if status != "completed":
            return jsonify({"error": f"run status: {status}"}), 502

        # (4) Fetch the latest assistant message and extract text
        msgs = requests.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers=OPENAI_HEADERS,
            params={"limit": 5, "order": "desc"},
            timeout=30
        ).json()["data"]

        text_out = ""
        for m in msgs:
            if m.get("role") == "assistant":
                for part in m.get("content", []):
                    if part.get("type") == "text":
                        text_out += part["text"]["value"]
                break

        return jsonify({"text": text_out or "[no text content]"}), 200

    except requests.HTTPError as e:
        return jsonify({"error": f"HTTPError: {e}", "details": getattr(e, 'response', None).text if hasattr(e, 'response') else ""}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    # Minimal UI for Canvas iframe or direct use
    return """
<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Course Chatbot</title>
<style>
  :root { --pad: 12px; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 var(--pad); }
  h2 { margin: 0 0 12px 0; }
  #log { border: 1px solid #e2e2e2; padding: var(--pad); height: 460px; overflow: auto; border-radius: 10px; background: #fafafa; }
  .u { color: #333; margin: 4px 0; }
  .b { color: #0b3d2e; margin: 4px 0; white-space: pre-wrap; }
  form { display: flex; gap: 8px; margin-top: 12px; }
  input { flex: 1; padding: 12px; border: 1px solid #ccc; border-radius: 10px; }
  button { padding: 12px 16px; border: 0; border-radius: 10px; cursor: pointer; background: #0ea5e9; color: white; }
  .small { color: #666; font-size: 12px; margin-top: 8px }
</style>
<h2>Ask questions about the course here:</h2>
<div id="log" aria-live="polite"></div>
<form id="f">
  <input id="m" autocomplete="off" placeholder="Type your question…" />
  <button type="submit">Send</button>
</form>
<div class="small">Chats may be logged for course improvement.</div>
<script>
  const log = document.getElementById('log');
  const f = document.getElementById('f');
  const m = document.getElementById('m');

  function addLine(cls, prefix, text){
    const d = document.createElement('div');
    d.className = cls;
    d.textContent = prefix + text;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
  }

  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = m.value.trim();
    if (!text) return;
    addLine('u', 'You: ', text);
    m.value = '';
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ message: text })
      });
      const data = await r.json();
      addLine('b', 'Bot: ', data.text || data.error || '[no response]');
    } catch (err) {
      addLine('b', 'Bot: ', 'Network error: ' + err);
    }
  });
</script>
"""

# ── Entrypoint for local dev ───────────────────────────────────────────────────
if __name__ == "__main__":
    # Local: python app.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
