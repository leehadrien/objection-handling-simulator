"""
app.py

Objection Handling Simulator, an AI roleplay tutor. The visitor
practices pitching an AI-powered training consultancy to a skeptical
VP of Learning & Development persona, driven live by the Claude API,
not a scripted branching tree. On request, a second Claude call
reviews the transcript and gives structured coaching feedback.

Voice is optional: if ELEVENLABS_API_KEY is set, the buyer's lines
are also spoken aloud. If not, the app runs text-only with zero
errors, same fallback philosophy as the Learning Analytics Agent.

If ANTHROPIC_API_KEY is not set, the buyer persona falls back to a
fixed sequence of realistic objections so the demo still works
standalone, it just is not dynamically reactive.

Local run:
    pip install -r requirements.txt
    python3 app.py
    open http://127.0.0.1:5000

Production (what the host runs):
    gunicorn app:app
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

DB_FILE = "sessions.db"

BUYER_NAME = "Morgan Reyes"
BUYER_TITLE = "VP of Learning & Development, healthcare software, ~400 employees"

SYSTEM_PROMPT = f"""You are {BUYER_NAME}, {BUYER_TITLE}. You are speaking with an \
outside AI learning consultant who is pitching their services to you right now.

You are professional but skeptical. You have been burned before by vendors who \
overpromised. Your real concerns: budget scrutiny from your CFO, your team's \
bandwidth to adopt yet another tool, IT and security review requirements, and \
proving measurable ROI.

Rules:
- Stay fully in character as {BUYER_NAME}. Never break character or acknowledge \
you are an AI.
- Raise ONE objection or concern per turn. Do not list multiple objections at once.
- If the consultant's response genuinely addresses your concern with specifics, \
ease up slightly and move to a new, related objection. If their response is vague \
or generic, push back harder on the same point.
- Keep responses conversational and realistic: 2 to 4 sentences, not a monologue.
- Do not be cartoonishly hostile. You are a real, busy executive: direct, a little \
guarded, not rude.
"""

COACH_PROMPT = """You are an expert sales coach reviewing a practice roleplay. \
Below is a transcript of a trainee practicing objection handling with a skeptical \
VP of L&D persona named Morgan.

Give feedback in exactly this structure, with each section on its own line:
STRENGTHS: two to three short points on what the trainee did well, separated by " | "
IMPROVE: one to two short points on what to sharpen, separated by " | "
READINESS: one sentence overall assessment

Be specific and reference what they actually said. Be constructive, not harsh. \
Do not use em dashes."""

CANNED_OBJECTIONS = [
    "We already have an LMS. Why would we need this?",
    "This seems like a lot of money for something my team might not even use.",
    "My team's already stretched thin. Another tool means more setup, more training, more headaches.",
    "How do I know this actually moves the needle? I need to show ROI to my CFO.",
    "We tried something like this before and adoption died after a month.",
]

try:
    import anthropic
    _client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
except ImportError:
    _client = None

try:
    import requests
    _elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
except ImportError:
    requests = None
    _elevenlabs_key = None


# ---------- Storage ----------

def _init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    return conn


def _save_message(session_id, role, content):
    conn = _init_db()
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _get_messages(session_id):
    conn = _init_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]


def _trainee_turn_count(session_id):
    conn = _init_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
        (session_id,),
    ).fetchone()[0]
    conn.close()
    return count


# ---------- Buyer persona ----------

def _buyer_reply(session_id, history):
    if _client:
        api_messages = [{"role": m["role"], "content": m["content"]} for m in history]
        response = _client.messages.create(
            model="claude-sonnet-5",
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=api_messages,
        )
        return response.content[0].text.strip()

    idx = _trainee_turn_count(session_id) % len(CANNED_OBJECTIONS)
    line = CANNED_OBJECTIONS[idx]
    if idx == 0:
        return line
    return f"Okay. {line}"


def _voice_url(text):
    """Returns an audio data URL for the buyer's line, or None if voice
    is not configured. Never raises: any failure just skips audio."""
    if not (_elevenlabs_key and requests):
        return None
    try:
        resp = requests.post(
            "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
            headers={"xi-api-key": _elevenlabs_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2"},
            timeout=15,
        )
        if resp.status_code == 200:
            import base64
            b64 = base64.b64encode(resp.content).decode("ascii")
            return f"data:audio/mpeg;base64,{b64}"
    except Exception:
        pass
    return None


# ---------- Coaching feedback ----------

def _coaching_feedback(history):
    transcript_lines = []
    for m in history:
        speaker = "Trainee" if m["role"] == "user" else BUYER_NAME
        transcript_lines.append(f"{speaker}: {m['content']}")
    transcript = "\n".join(transcript_lines)

    if _client:
        response = _client.messages.create(
            model="claude-sonnet-5",
            max_tokens=300,
            messages=[{"role": "user", "content": f"{COACH_PROMPT}\n\nTranscript:\n{transcript}"}],
        )
        text = response.content[0].text.strip()
        return _parse_feedback(text)

    return {
        "strengths": ["You stayed in the conversation and kept responding to pushback."],
        "improve": ["Live, personalized coaching needs an Anthropic API key configured on this deployment."],
        "readiness": "Connect an API key to get real feedback on this specific run.",
    }


def _parse_feedback(text):
    strengths, improve, readiness = [], [], ""
    for line in text.split("\n"):
        line = line.strip()
        if line.upper().startswith("STRENGTHS:"):
            strengths = [s.strip() for s in line.split(":", 1)[1].split("|") if s.strip()]
        elif line.upper().startswith("IMPROVE:"):
            improve = [s.strip() for s in line.split(":", 1)[1].split("|") if s.strip()]
        elif line.upper().startswith("READINESS:"):
            readiness = line.split(":", 1)[1].strip()
    if not strengths and not improve and not readiness:
        readiness = text[:300]
    return {"strengths": strengths, "improve": improve, "readiness": readiness}


# ---------- Routes ----------

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Objection Handling Simulator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Geist:wght@400;500;600&family=Geist+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  :root {
    --monolith: #0A0A0A; --surface: #141416; --ink-white: #FFFFFF;
    --muted: #9A9EA3; --hairline: rgba(255,255,255,0.12); --signal: #3AE73A;
  }
  * { box-sizing: border-box; }
  body { background: var(--monolith); color: var(--ink-white); font-family: 'Geist', sans-serif; margin: 0; padding: 0 40px 40px; }
  .topbar { height: 3px; background: var(--signal); margin: 0 -40px 40px; }
  h1 { font-family: 'Archivo Black', sans-serif; font-size: 48px; letter-spacing: -1.5px; text-transform: uppercase; margin: 0 0 8px; line-height: 1.03; }
  .subtitle { font-family: 'Geist Mono', monospace; font-weight: 500; color: rgba(255,255,255,0.6); font-size: 14px; letter-spacing: 1.2px; text-transform: uppercase; margin-bottom: 14px; }
  .tagline { font-size: 19px; line-height: 1.5; color: rgba(255,255,255,0.88); max-width: 760px; margin-bottom: 28px; }
  .value-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 32px; }
  .value-card { background: var(--surface); border: 1px solid var(--hairline); border-radius: 10px; padding: 22px; }
  .value-card .v-label { font-family: 'Geist Mono', monospace; font-weight: 500; font-size: 13px; letter-spacing: 1px; text-transform: uppercase; color: var(--signal); margin-bottom: 10px; }
  .value-card .v-body { font-size: 15px; line-height: 1.5; color: rgba(255,255,255,0.85); }
  .card { background: var(--surface); border: 1px solid var(--hairline); border-radius: 10px; padding: 28px; margin-bottom: 24px; }
  .card h2 { font-family: 'Geist Mono', monospace; font-weight: 500; font-size: 15px; text-transform: uppercase; letter-spacing: 1.2px; color: rgba(255,255,255,0.6); margin: 0 0 8px; }
  .section-note { font-size: 15px; line-height: 1.5; color: var(--muted); margin: 0 0 18px; max-width: 640px; }
  .btn-cta { background: var(--signal); color: #0D0D0D; font-family: 'Geist Mono', monospace; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; font-size: 14px; padding: 16px 30px; border: none; border-radius: 10px; cursor: pointer; box-shadow: 0 6px 20px rgba(58,231,58,0.35); transition: all 150ms ease-out; }
  .btn-cta:hover { background: transparent; color: var(--signal); border: 1px solid var(--signal); box-shadow: none; }
  .btn-cta:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-ghost { background: transparent; color: rgba(255,255,255,0.7); font-family: 'Geist Mono', monospace; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; font-size: 13px; padding: 12px 20px; border: 1px solid var(--hairline); border-radius: 10px; cursor: pointer; }
  .btn-ghost:hover { border-color: rgba(255,255,255,0.4); color: #fff; }
  #chat-log { display: flex; flex-direction: column; gap: 14px; margin-bottom: 20px; max-height: 480px; overflow-y: auto; padding-right: 4px; }
  .msg { max-width: 78%; padding: 14px 18px; border-radius: 10px; font-size: 15px; line-height: 1.5; }
  .msg-buyer { background: #1D1F20; border: 1px solid var(--hairline); align-self: flex-start; }
  .msg-buyer .msg-label { color: var(--signal); }
  .msg-user { background: rgba(58,231,58,0.10); border: 1px solid rgba(58,231,58,0.25); align-self: flex-end; }
  .msg-user .msg-label { color: rgba(255,255,255,0.55); }
  .msg-label { font-family: 'Geist Mono', monospace; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; display: block; margin-bottom: 6px; }
  .input-row { display: flex; gap: 12px; }
  #trainee-input { flex: 1; background: #0D0D0E; border: 1px solid var(--hairline); border-radius: 10px; padding: 14px 16px; color: #fff; font-family: 'Geist', sans-serif; font-size: 15px; resize: none; }
  #trainee-input:focus { outline: none; border-color: var(--signal); }
  .controls-row { display: flex; gap: 12px; margin-top: 16px; }
  .hidden { display: none !important; }
  .feedback-block { margin-top: 8px; }
  .feedback-block h3 { font-family: 'Geist Mono', monospace; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: var(--signal); margin: 18px 0 8px; }
  .feedback-block ul { margin: 0; padding-left: 20px; }
  .feedback-block li { font-size: 15px; line-height: 1.6; color: rgba(255,255,255,0.88); margin-bottom: 4px; }
  .feedback-block p { font-size: 15px; line-height: 1.6; color: rgba(255,255,255,0.88); }
  .typing { color: var(--muted); font-family: 'Geist Mono', monospace; font-size: 13px; }
</style>
</head>
<body>
  <div class="topbar"></div>
  <h1>Objection Handling Simulator</h1>
  <div class="subtitle">AI Roleplay Tutor · Sales Practice</div>
  <div class="tagline">Reps read scripts. This makes them handle real pushback, out loud, before it costs a deal.</div>

  <div class="value-strip">
    <div class="value-card">
      <div class="v-label">Realistic pushback</div>
      <div class="v-body">A live AI buyer persona, not a branching script with three right answers.</div>
    </div>
    <div class="value-card">
      <div class="v-label">Coaching, not grading</div>
      <div class="v-body">Specific feedback on what worked and what to sharpen, every single run.</div>
    </div>
    <div class="value-card">
      <div class="v-label">Practice that scales</div>
      <div class="v-body">Every rep gets unlimited reps, without booking a manager's calendar.</div>
    </div>
  </div>

  <div class="card">
    <h2>The scenario</h2>
    <div class="section-note">You are pitching an AI-powered training consultancy to Morgan Reyes, VP of Learning and Development at a mid-size healthcare software company. Morgan is skeptical, budget-conscious, and has been burned by vendors before. Type your pitch and handle whatever comes back.</div>
    <button id="start-btn" class="btn-cta">Start scenario &#8599;</button>
  </div>

  <div id="chat-card" class="card hidden">
    <h2>Live roleplay</h2>
    <div id="chat-log"></div>
    <div class="input-row">
      <textarea id="trainee-input" rows="2" placeholder="Type your response to Morgan..."></textarea>
      <button id="send-btn" class="btn-cta">Send</button>
    </div>
    <div class="controls-row">
      <button id="feedback-btn" class="btn-ghost">End &amp; get coaching feedback</button>
      <button id="restart-btn" class="btn-ghost">Restart</button>
    </div>
  </div>

  <div id="feedback-card" class="card hidden">
    <h2>Coaching feedback</h2>
    <div id="feedback-body" class="feedback-block"></div>
  </div>

<script>
let sessionId = null;

function addMessage(role, text) {
  const log = document.getElementById('chat-log');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'assistant' ? 'msg-buyer' : 'msg-user');
  const label = document.createElement('span');
  label.className = 'msg-label';
  label.textContent = role === 'assistant' ? 'Morgan Reyes' : 'You';
  const body = document.createElement('div');
  body.textContent = text;
  div.appendChild(label);
  div.appendChild(body);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function setTyping(on) {
  let el = document.getElementById('typing-indicator');
  if (on) {
    if (!el) {
      el = document.createElement('div');
      el.id = 'typing-indicator';
      el.className = 'typing';
      el.textContent = 'Morgan is typing...';
      document.getElementById('chat-log').appendChild(el);
    }
  } else if (el) {
    el.remove();
  }
}

document.getElementById('start-btn').addEventListener('click', async () => {
  sessionId = crypto.randomUUID();
  document.getElementById('start-btn').disabled = true;
  document.getElementById('chat-card').classList.remove('hidden');
  setTyping(true);
  const resp = await fetch('/api/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId})
  });
  const data = await resp.json();
  setTyping(false);
  addMessage('assistant', data.reply);
  if (data.audio_url) { new Audio(data.audio_url).play().catch(()=>{}); }
});

async function sendMessage() {
  const input = document.getElementById('trainee-input');
  const text = input.value.trim();
  if (!text || !sessionId) return;
  addMessage('user', text);
  input.value = '';
  setTyping(true);
  const resp = await fetch('/api/respond', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, message: text})
  });
  const data = await resp.json();
  setTyping(false);
  addMessage('assistant', data.reply);
  if (data.audio_url) { new Audio(data.audio_url).play().catch(()=>{}); }
}

document.getElementById('send-btn').addEventListener('click', sendMessage);
document.getElementById('trainee-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

document.getElementById('feedback-btn').addEventListener('click', async () => {
  if (!sessionId) return;
  const btn = document.getElementById('feedback-btn');
  btn.disabled = true;
  btn.textContent = 'Reviewing your run...';
  const resp = await fetch('/api/feedback', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId})
  });
  const data = await resp.json();
  btn.disabled = false;
  btn.textContent = 'End & get coaching feedback';

  const card = document.getElementById('feedback-card');
  const body = document.getElementById('feedback-body');
  body.innerHTML = '';

  if (data.strengths && data.strengths.length) {
    const h = document.createElement('h3'); h.textContent = 'Strengths';
    const ul = document.createElement('ul');
    data.strengths.forEach(s => { const li = document.createElement('li'); li.textContent = s; ul.appendChild(li); });
    body.appendChild(h); body.appendChild(ul);
  }
  if (data.improve && data.improve.length) {
    const h = document.createElement('h3'); h.textContent = 'Sharpen';
    const ul = document.createElement('ul');
    data.improve.forEach(s => { const li = document.createElement('li'); li.textContent = s; ul.appendChild(li); });
    body.appendChild(h); body.appendChild(ul);
  }
  if (data.readiness) {
    const h = document.createElement('h3'); h.textContent = 'Readiness';
    const p = document.createElement('p'); p.textContent = data.readiness;
    body.appendChild(h); body.appendChild(p);
  }
  card.classList.remove('hidden');
  card.scrollIntoView({behavior: 'smooth', block: 'start'});
});

document.getElementById('restart-btn').addEventListener('click', () => {
  window.location.reload();
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(force=True)
    session_id = data.get("session_id") or str(uuid.uuid4())

    if _client:
        opener_prompt = [{"role": "user", "content": "Begin the conversation. Open with your first objection, in character, as if the consultant just introduced themselves to you."}]
        response = _client.messages.create(
            model="claude-sonnet-5", max_tokens=200, system=SYSTEM_PROMPT, messages=opener_prompt,
        )
        reply = response.content[0].text.strip()
    else:
        reply = CANNED_OBJECTIONS[0]

    _save_message(session_id, "assistant", reply)
    return jsonify({"reply": reply, "audio_url": _voice_url(reply)})


@app.route("/api/respond", methods=["POST"])
def api_respond():
    data = request.get_json(force=True)
    session_id = data["session_id"]
    message = data["message"]

    _save_message(session_id, "user", message)
    history = _get_messages(session_id)
    reply = _buyer_reply(session_id, history)
    _save_message(session_id, "assistant", reply)

    return jsonify({"reply": reply, "audio_url": _voice_url(reply)})


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = request.get_json(force=True)
    session_id = data["session_id"]
    history = _get_messages(session_id)
    return jsonify(_coaching_feedback(history))


if __name__ == "__main__":
    _init_db()
    print("Objection Handling Simulator running, open http://127.0.0.1:5000 in your browser")
    app.run(debug=True)
