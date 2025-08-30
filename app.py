import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# If you add Flask sessions later, these flags make cookies work inside Canvas' iframe:
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True
)

@app.after_request
def add_headers(resp):
    # Allow Canvas to iframe your site
    resp.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://*.instructure.com https://*.instructuremedia.com;"
    )
    # If anything upstream injects a blocking X-Frame-Options, override it:
    resp.headers["X-Frame-Options"] = "ALLOWALL"
    return resp

@app.route("/api/chat", methods=["POST"])
def chat_api():
    data = request.get_json(silent=True) or {}
    msg = data.get("message")
    if not msg:
        return jsonify({"error": "message required"}), 400

    # Call OpenAI server-side. If you built an Assistant, see the Assistants example below.
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-5.1-mini",   # swap to your model
            "input": msg
        },
        timeout=60
    )
    j = r.json()
    text = (
        j.get("output_text")
        or (((j.get("output") or [{}])[0].get("content") or [{}])[0].get("text") or {}).get("value")
        or str(j)
    )
    return jsonify({"text": text})

@app.route("/")
def index():
    # Minimal UI
    return """
<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Course Chatbot</title>
<style>
body { font-family: system-ui, sans-serif; max-width: 800px; margin: 24px auto; }
#log { border: 1px solid #ddd; padding: 12px; height: 420px; overflow: auto; border-radius: 8px; }
form { display: flex; gap: 8px; margin-top: 12px; }
input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
button { padding: 10px 14px; border: 0; border-radius: 8px; cursor: pointer; }
.small { color:#666; font-size:12px; margin-top:8px }
</style>
<h2>Ask the Course Assistant</h2>
<div id="log"></div>
<form id="f">
  <input id="m" autocomplete="off" placeholder="Type your question…" />
  <button>Send</button>
</form>
<div class="small">Chats may be logged for course improvement.</div>
<script>
const log = document.getElementById('log');
const f = document.getElementById('f');
const m = document.getElementById('m');
function addLine(p, t){ const d=document.createElement('div'); d.textContent=p+t; log.appendChild(d); log.scrollTop=log.scrollHeight; }
f.addEventListener('submit', async (e)=>{
  e.preventDefault();
  if(!m.value.trim()) return;
  const user=m.value; addLine('You: ', user); m.value='';
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:user})});
  const data=await r.json();
  addLine('Bot: ', data.text || data.error || 'No response');
});
</script>
"""

if __name__ == "__main__":
    # Local dev: python app.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
