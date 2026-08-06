"""
app.py

Roleplay Training Studio, a multi-scenario AI roleplay tutor. Each
scenario is a genuinely different sale, with its own persona, voice,
and coaching rubric, driven live by the Claude API rather than a
scripted branching tree. This exists to demonstrate, directly, what
a custom training simulation for a specific team and industry could
look like: swap the persona, the industry stays yours.

Voice is optional: if ELEVENLABS_API_KEY is set, the buyer's lines
are also spoken aloud. If not, the app runs text-only with zero
errors.

If ANTHROPIC_API_KEY is not set, each persona falls back to a fixed
sequence of realistic objections so the demo still works standalone,
it just is not dynamically reactive.

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


# ---------- Scenario library ----------

SCENARIOS = {
    "ld-tech": {
        "title": "Enterprise L&D Tech Sale",
        "persona_name": "Morgan Reyes",
        "persona_role": "VP of Learning & Development, Meridian Health Systems (~400 employees)",
        "hook": "Just absorbed a 20 percent training budget cut. Reports to a CFO who wants hard ROI on everything. Has been burned before by a vendor who overpromised and underdelivered.",
        "difficulty": "Intermediate",
        "skills": ["ROI framing", "budget objections", "executive presence"],
        "system_prompt": """You are Morgan Reyes, VP of Learning and Development at Meridian \
Health Systems, a 400-person healthcare software company. You are speaking with an outside \
AI learning consultant who is pitching their services to you right now.

Your situation: your training budget was just cut 20 percent this fiscal year. You report to \
a CFO who wants to see hard ROI on every line item. Two years ago you brought in a vendor who \
promised a full engagement platform and delivered a glorified quiz tool, so you are personally \
wary of new AI vendors overselling. Your L&D team is three people, all fully booked running \
mandatory compliance training.

You are professional but guarded. Your real concerns, in rough priority order: proving \
measurable ROI to your CFO, your team's bandwidth to adopt yet another tool, whether this is \
substantively different from the last vendor's empty promises, and IT and security review \
requirements.

Rules:
- Stay fully in character as Morgan. Never break character or acknowledge you are an AI.
- Raise ONE objection or concern per turn. Do not list multiple objections at once.
- If the consultant's response genuinely addresses your concern with specifics, ease up \
slightly and move to a new, related objection. If their response is vague or generic, push \
back harder on the same point, and reference your past bad experience if it fits naturally.
- Keep responses conversational and realistic: 2 to 4 sentences, not a monologue.
- Do not be cartoonishly hostile. You are a real, busy executive: direct, a little guarded, \
not rude.
- Do not use em dashes.""",
        "canned_objections": [
            "We already have an LMS. Why would we need this?",
            "This seems like a lot of money for something my team might not even use.",
            "My team's already stretched thin. Another tool means more setup, more training, more headaches.",
            "How do I know this actually moves the needle? I need to show ROI to my CFO.",
            "We tried something like this before and adoption died after a month.",
        ],
        "coach_focus": "quantifying ROI in terms a CFO would accept, addressing budget-cycle timing, and easing the team's bandwidth concerns without minimizing them",
        "fallback_transition": "Okay.",
        "voice_id": "gJU2icYQsdEmbGJ65Z8W",
    },
    "lms-it": {
        "title": "LMS Platform Migration",
        "persona_name": "Dana Okafor",
        "persona_role": "Director of IT, Fenwick Logistics (~1,200 employees)",
        "hook": "Two years ago, a promised HR platform migration ran six months over schedule and lost training records for 200 employees. Dana has been risk-averse about anything touching HR data ever since.",
        "difficulty": "Advanced",
        "skills": ["technical credibility", "security & compliance", "de-risking migration"],
        "system_prompt": """You are Dana Okafor, Director of IT at Fenwick Logistics, a 1,200 \
person national trucking and warehousing company. You are speaking with a vendor pitching a \
new learning platform right now.

Your situation: two years ago you led a migration to a new HR platform that a vendor promised \
would take six weeks. It took six months, and during the botched data migration you lost \
training completion records for 200 employees, records that mattered for a compliance audit. \
You have been personally risk-averse about anything touching HR or training data ever since. \
Your CISO requires SOC 2 Type II compliance from any vendor touching employee data, no \
exceptions. Your company runs Workday for HRIS and any new tool needs to integrate cleanly \
with it. Your last learning platform rollout had 40 percent adoption after six months, which \
you consider a failure you do not want to repeat. You are also mildly skeptical of AI features \
specifically, since it feels like every vendor slaps "AI-powered" on their product now \
regardless of whether it does anything meaningfully different.

You are technical, detail-oriented, and want specifics, not marketing language. Your real \
concerns, in rough priority order: data migration risk and a concrete rollback plan, SOC 2 \
and security compliance, clean integration with Workday, realistic adoption planning, and \
whether the AI claims are substantive or just a label.

Rules:
- Stay fully in character as Dana. Never break character or acknowledge you are an AI.
- Raise ONE technical or risk concern per turn. Do not list multiple at once.
- Push for specifics. If the consultant gives a vague or marketing-flavored answer, ask a \
sharper follow-up question rather than accepting it. If they give a genuinely specific, \
technical answer, ease up and move to the next concern.
- Keep responses conversational and realistic: 2 to 4 sentences, not a monologue.
- You are not rude, but you are not warm either. You have been burned once and it shows.
- Do not use em dashes.""",
        "canned_objections": [
            "Before we go further, walk me through your data migration process. What happens if it fails halfway through?",
            "Are you SOC 2 Type II certified? I need documentation, not a verbal assurance.",
            "We run Workday for HRIS. How does this actually integrate, not in theory, in practice?",
            "Our last platform rollout hit 40 percent adoption after six months. What's different this time?",
            "Everyone calls their product AI-powered now. What does that actually mean here, specifically?",
        ],
        "coach_focus": "technical specificity, fluency on security and compliance, concretely de-risking the migration, and not overselling the AI angle with vague claims",
        "fallback_transition": "Understood.",
        "voice_id": "KeU8nqWFDbaoi0QVUjD3",
    },
    "retail-floor": {
        "title": "Retail Showroom Floor",
        "persona_name": "Jordan Alvarez",
        "persona_role": "Shopper, furnishing a new apartment on a self-imposed budget",
        "hook": "Already visited two other stores today. A little overwhelmed, worried about buyer's remorse, and does not want to feel pressured.",
        "difficulty": "Beginner",
        "skills": ["building rapport", "creating urgency", "price objections"],
        "system_prompt": """You are Jordan Alvarez, a shopper in a furniture showroom. You just \
moved into a new apartment and are furnishing it on a budget you have not really written down \
anywhere, just a rough number in your head. You are speaking with a sales associate on the \
floor right now.

Your situation: you have already been to two other furniture stores today and are starting to \
feel a little decision-fatigued. You do not want to feel "sold to" or pressured. You are \
worried about buyer's remorse, you saw something similar somewhere else for less, and you are \
genuinely unsure whether you should just keep looking or commit today.

You are casual, a little non-committal, and speak in short, informal sentences, nothing like a \
corporate buyer. Your real concerns, in rough priority order: price versus what you saw \
elsewhere, whether you should just "keep looking" instead of deciding today, delivery \
timeline, and the return policy in case it does not fit your space.

Rules:
- Stay fully in character as Jordan. Never break character or acknowledge you are an AI.
- Raise ONE concern or hesitation per turn, casually, the way a real shopper talks, not a \
business objection format.
- If the associate builds real rapport and gives you a genuine, specific reason to act now, \
warm up and move toward a next concern rather than shutting down. If they push too hard or \
sound scripted, get more hesitant and non-committal.
- Keep responses short and conversational: 1 to 3 sentences, casual tone, contractions, the \
way people actually talk in a store.
- You are not rude, just a normal person who does not want to be pressured into a decision.
- Do not use em dashes.""",
        "canned_objections": [
            "I don't know, I'm just looking around for now.",
            "I saw something kind of like this at another store for less.",
            "I don't want to decide today, I feel like I should sleep on it.",
            "How long would delivery actually take? I need this before the end of the month.",
            "What if it doesn't fit my space right, can I return it?",
        ],
        "coach_focus": "building quick rapport, creating genuine urgency without pressure, and directly addressing the price comparison instead of dodging it",
        "fallback_transition": "Yeah, I hear you, but",
        "voice_id": "fI4LiKng8DlpjWJyDcsj",
    },
}

SCENARIO_ORDER = ["ld-tech", "lms-it", "retail-floor"]

COACH_PROMPT_TEMPLATE = """You are an expert sales coach reviewing a practice roleplay. \
Below is a transcript of a trainee practicing objection handling with a persona named {name}, \
{role}.

For this scenario, pay particular attention to: {coach_focus}.

Give feedback in exactly this structure, with each section on its own line:
STRENGTHS: two to three short points on what the trainee did well, separated by " | "
IMPROVE: one to two short points on what to sharpen, separated by " | "
READINESS: one sentence overall assessment

Be specific and reference what they actually said. Be constructive, not harsh. \
Do not use em dashes."""

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


def _extract_text(response):
    """Pulls the text out of a Claude API response. Sonnet-class models can
    return a ThinkingBlock ahead of the actual text block, so this walks
    the content list rather than assuming content[0] is text."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()
    return ""


# ---------- Storage ----------

def _init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            scenario_id TEXT,
            created_at TEXT
        )
    """)
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


def _create_session(session_id, scenario_id):
    conn = _init_db()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (session_id, scenario_id, created_at) VALUES (?, ?, ?)",
        (session_id, scenario_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _get_scenario_id(session_id):
    conn = _init_db()
    row = conn.execute(
        "SELECT scenario_id FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else "ld-tech"


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


# ---------- Persona ----------

def _persona_reply(session_id, scenario, history):
    if _client:
        api_messages = [{"role": m["role"], "content": m["content"]} for m in history]
        response = _client.messages.create(
            model="claude-sonnet-5",
            max_tokens=200,
            system=scenario["system_prompt"],
            messages=api_messages,
        )
        return _extract_text(response)

    objections = scenario["canned_objections"]
    idx = _trainee_turn_count(session_id) % len(objections)
    line = objections[idx]
    if idx == 0:
        return line
    transition = scenario.get("fallback_transition", "Okay.")
    return f"{transition} {line}"


def _voice_url(text, voice_id="gJU2icYQsdEmbGJ65Z8W"):
    """Returns an audio data URL for the persona's line, or None if voice
    is not configured. Never raises: any failure just skips audio."""
    if not (_elevenlabs_key and requests):
        return None
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
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

def _coaching_feedback(scenario, history):
    transcript_lines = []
    for m in history:
        speaker = "Trainee" if m["role"] == "user" else scenario["persona_name"]
        transcript_lines.append(f"{speaker}: {m['content']}")
    transcript = "\n".join(transcript_lines)

    prompt = COACH_PROMPT_TEMPLATE.format(
        name=scenario["persona_name"], role=scenario["persona_role"],
        coach_focus=scenario["coach_focus"],
    )

    if _client:
        response = _client.messages.create(
            model="claude-sonnet-5", max_tokens=300,
            messages=[{"role": "user", "content": f"{prompt}\n\nTranscript:\n{transcript}"}],
        )
        text = _extract_text(response)
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

def _scenario_cards_html():
    diff_class = {"Beginner": "diff-low", "Intermediate": "diff-medium", "Advanced": "diff-high"}
    cards = []
    for sid in SCENARIO_ORDER:
        s = SCENARIOS[sid]
        tags = "".join(f'<span class="skill-tag">{t}</span>' for t in s["skills"])
        title_attr = s["title"].replace("&", "&amp;").replace('"', "&quot;")
        cards.append(f"""
        <div class="scenario-card" data-scenario="{sid}">
          <div class="scenario-top">
            <span class="diff-badge {diff_class[s['difficulty']]}">{s['difficulty']}</span>
          </div>
          <h3 class="scenario-title">{s['title']}</h3>
          <div class="persona-line">{s['persona_name']} &middot; {s['persona_role']}</div>
          <div class="scenario-hook">{s['hook']}</div>
          <div class="skill-tags">{tags}</div>
          <button class="btn-cta scenario-start" data-scenario="{sid}" aria-label="Start the {title_attr} scenario">Start this scenario &#8599;</button>
        </div>
        """)
    return "\n".join(cards)


TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Roleplay Training Studio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Geist:wght@400;500;600&family=Geist+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  :root {
    --monolith: #0A0A0A; --surface: #141416; --ink-white: #FFFFFF;
    --muted: #9A9EA3; --hairline: rgba(255,255,255,0.12); --signal: #3AE73A;
    --text-scale: 1;
  }
  * { box-sizing: border-box; }
  body { background: var(--monolith); color: var(--ink-white); font-family: 'Geist', sans-serif; margin: 0; padding: 0 40px 40px; }
  .topbar { height: 3px; background: var(--signal); margin: 0 -40px 40px; }
  h1 { font-family: 'Archivo Black', sans-serif; font-size: 48px; letter-spacing: -1.5px; text-transform: uppercase; margin: 0 0 8px; line-height: 1.03; }
  .subtitle { font-family: 'Geist Mono', monospace; font-weight: 500; color: rgba(255,255,255,0.65); font-size: calc(15px * var(--text-scale)); letter-spacing: 1.2px; text-transform: uppercase; margin-bottom: 14px; }
  .tagline { font-size: calc(20px * var(--text-scale)); line-height: 1.6; color: rgba(255,255,255,0.9); max-width: 780px; margin-bottom: 28px; }
  .value-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 32px; }
  .value-card { background: var(--surface); border: 1px solid var(--hairline); border-radius: 10px; padding: 22px; }
  .value-card .v-label { font-family: 'Geist Mono', monospace; font-weight: 500; font-size: calc(14px * var(--text-scale)); letter-spacing: 1px; text-transform: uppercase; color: var(--signal); margin-bottom: 10px; }
  .value-card .v-body { font-size: calc(16px * var(--text-scale)); line-height: 1.6; color: rgba(255,255,255,0.88); }
  .card { background: var(--surface); border: 1px solid var(--hairline); border-radius: 10px; padding: 28px; margin-bottom: 24px; }
  .card h2 { font-family: 'Geist Mono', monospace; font-weight: 500; font-size: 16px; text-transform: uppercase; letter-spacing: 1.2px; color: rgba(255,255,255,0.65); margin: 0 0 8px; }
  .section-note { font-size: calc(16px * var(--text-scale)); line-height: 1.6; color: var(--muted); margin: 0 0 18px; max-width: 680px; }

  .scenario-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
  .scenario-card { background: #0D0D0E; border: 1px solid var(--hairline); border-radius: 10px; padding: 24px; display: flex; flex-direction: column; }
  .scenario-top { margin-bottom: 12px; }
  .diff-badge { font-family: 'Geist Mono', monospace; font-size: calc(13px * var(--text-scale)); letter-spacing: 1px; text-transform: uppercase; padding: 5px 12px; border-radius: 999px; border: 1px solid; }
  .diff-low { color: #9BE39B; border-color: rgba(155,227,155,0.4); }
  .diff-medium { color: #FAC775; border-color: rgba(250,199,117,0.4); }
  .diff-high { color: #F0997B; border-color: rgba(240,153,123,0.4); }
  .scenario-title { font-family: 'Archivo Black', sans-serif; font-size: 22px; text-transform: uppercase; letter-spacing: -0.3px; margin: 0 0 8px; }
  .persona-line { font-family: 'Geist Mono', monospace; font-size: calc(14px * var(--text-scale)); color: var(--signal); margin-bottom: 12px; letter-spacing: 0.3px; line-height: 1.4; }
  .scenario-hook { font-size: calc(15px * var(--text-scale)); line-height: 1.6; color: rgba(255,255,255,0.8); margin-bottom: 16px; flex-grow: 1; }
  .skill-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
  .skill-tag { font-family: 'Geist Mono', monospace; font-size: calc(13px * var(--text-scale)); letter-spacing: 0.5px; text-transform: uppercase; color: rgba(255,255,255,0.75); background: rgba(255,255,255,0.06); border: 1px solid var(--hairline); border-radius: 6px; padding: 5px 10px; }

  .btn-cta { background: var(--signal); color: #0D0D0D; font-family: 'Geist Mono', monospace; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; font-size: calc(15px * var(--text-scale)); padding: 16px 30px; border: none; border-radius: 10px; cursor: pointer; box-shadow: 0 6px 20px rgba(58,231,58,0.35); transition: all 150ms ease-out; width: 100%; }
  .btn-cta:hover { background: transparent; color: var(--signal); border: 1px solid var(--signal); box-shadow: none; }
  .btn-cta:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-ghost { background: transparent; color: rgba(255,255,255,0.75); font-family: 'Geist Mono', monospace; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; font-size: calc(14px * var(--text-scale)); padding: 12px 20px; border: 1px solid var(--hairline); border-radius: 10px; cursor: pointer; }
  .btn-ghost:hover { border-color: rgba(255,255,255,0.4); color: #fff; }

  .active-brief { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid var(--hairline); }
  .active-brief .ab-name { font-family: 'Archivo Black', sans-serif; font-size: 24px; text-transform: uppercase; margin: 0 0 4px; }
  .active-brief .ab-role { font-family: 'Geist Mono', monospace; font-size: calc(14px * var(--text-scale)); color: var(--signal); margin-bottom: 10px; }
  .active-brief .ab-hook { font-size: calc(15px * var(--text-scale)); color: rgba(255,255,255,0.78); max-width: 560px; line-height: 1.6; }

  #chat-log { display: flex; flex-direction: column; gap: 14px; margin-bottom: 20px; max-height: 480px; overflow-y: auto; padding-right: 4px; }
  .msg { max-width: 78%; padding: 16px 20px; border-radius: 10px; font-size: calc(17px * var(--text-scale)); line-height: 1.6; }
  .msg-persona { background: #1D1F20; border: 1px solid var(--hairline); align-self: flex-start; }
  .msg-persona .msg-label { color: var(--signal); }
  .msg-user { background: rgba(58,231,58,0.10); border: 1px solid rgba(58,231,58,0.25); align-self: flex-end; }
  .msg-user .msg-label { color: rgba(255,255,255,0.55); }
  .msg-label { font-family: 'Geist Mono', monospace; font-size: calc(13px * var(--text-scale)); letter-spacing: 1px; text-transform: uppercase; display: block; margin-bottom: 6px; }
  .input-row { display: flex; gap: 12px; }
  #trainee-input { flex: 1; background: #0D0D0E; border: 1px solid var(--hairline); border-radius: 10px; padding: 14px 16px; color: #fff; font-family: 'Geist', sans-serif; font-size: calc(17px * var(--text-scale)); line-height: 1.5; resize: none; }
  #trainee-input:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; border-color: var(--signal); }
  .controls-row { display: flex; gap: 12px; margin-top: 16px; }
  .hidden { display: none !important; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
  .feedback-block { margin-top: 8px; }
  .feedback-block h3 { font-family: 'Geist Mono', monospace; font-size: calc(15px * var(--text-scale)); text-transform: uppercase; letter-spacing: 1px; color: var(--signal); margin: 18px 0 8px; }
  .feedback-block ul { margin: 0; padding-left: 22px; }
  .feedback-block li { font-size: calc(17px * var(--text-scale)); line-height: 1.6; color: rgba(255,255,255,0.9); margin-bottom: 6px; }
  .feedback-block p { font-size: calc(17px * var(--text-scale)); line-height: 1.6; color: rgba(255,255,255,0.9); }
  .typing { color: var(--muted); font-family: 'Geist Mono', monospace; font-size: calc(15px * var(--text-scale)); }

  #send-btn { width: auto; padding: 14px 28px; }
  button:focus-visible, .scenario-card:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }

  .text-size-control { display: flex; align-items: center; gap: 10px; margin-bottom: 24px; }
  .tsc-label { font-family: 'Geist Mono', monospace; font-size: 13px; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
  .tsc-buttons { display: flex; border: 1px solid var(--hairline); border-radius: 8px; overflow: hidden; }
  .tsc-btn { background: var(--surface); color: rgba(255,255,255,0.85); font-family: 'Geist', sans-serif; font-weight: 500; border: none; border-right: 1px solid var(--hairline); padding: 8px 14px; cursor: pointer; line-height: 1; }
  .tsc-btn:last-child { border-right: none; }
  .tsc-btn:hover { background: #1D1F20; color: #fff; }
  .tsc-btn.small { font-size: 13px; }
  .tsc-btn.medium { font-size: 16px; }
  .tsc-btn.large { font-size: 19px; }
  .tsc-btn[aria-pressed="true"] { background: var(--signal); color: #0D0D0D; }
  #feedback-card:focus { outline: 2px solid var(--signal); outline-offset: 2px; }

  @media (max-width: 900px) {
    .scenario-grid { grid-template-columns: 1fr; }
    .value-strip { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
  <div class="topbar"></div>
  <h1>Roleplay Training Studio</h1>
  <div class="subtitle">AI Roleplay Tutor &middot; Multi-Industry Sales Practice</div>
  <div class="tagline">The same engine trains a SaaS rep, a solutions engineer, and a retail floor associate, because the skill underneath is always the same: staying sharp under real pushback. This is a preview of what a simulation built for your team, your product, your buyers could look like.</div>

  <div class="text-size-control" role="group" aria-label="Adjust text size">
    <span class="tsc-label">Text size</span>
    <div class="tsc-buttons">
      <button type="button" class="tsc-btn small" data-scale="1" aria-label="Small text" aria-pressed="true">A</button>
      <button type="button" class="tsc-btn medium" data-scale="1.2" aria-label="Medium text" aria-pressed="false">A</button>
      <button type="button" class="tsc-btn large" data-scale="1.4" aria-label="Large text" aria-pressed="false">A</button>
    </div>
  </div>

  <div class="value-strip">
    <div class="value-card">
      <div class="v-label">Built for your team</div>
      <div class="v-body">Swap the persona and the industry. This is the engine, not a one-off demo.</div>
    </div>
    <div class="value-card">
      <div class="v-label">Realistic, not scripted</div>
      <div class="v-body">Every persona is a live AI model reacting to what your rep actually says.</div>
    </div>
    <div class="value-card">
      <div class="v-label">Measurable practice</div>
      <div class="v-body">Structured coaching feedback after every run, tuned to what that scenario demands.</div>
    </div>
  </div>

  <div id="picker-card" class="card">
    <h2>Choose a scenario</h2>
    <div class="section-note">Three different sales, three different buyers. Pick one and see how the persona actually holds its ground.</div>
    <div class="scenario-grid">
      __SCENARIO_CARDS__
    </div>
  </div>

  <div id="chat-card" class="card hidden">
    <div class="active-brief">
      <div>
        <div class="ab-name" id="ab-name"></div>
        <div class="ab-role" id="ab-role"></div>
        <div class="ab-hook" id="ab-hook"></div>
      </div>
      <button id="change-scenario-btn" class="btn-ghost">Change scenario</button>
    </div>
    <div id="chat-log" role="log" aria-live="polite" aria-label="Roleplay conversation"></div>
    <div class="input-row">
      <label for="trainee-input" class="sr-only">Type your response to the buyer</label>
      <textarea id="trainee-input" rows="2" placeholder="Type your response..."></textarea>
      <button id="send-btn" class="btn-cta">Send</button>
    </div>
    <div class="controls-row">
      <button id="feedback-btn" class="btn-ghost">End &amp; get coaching feedback</button>
      <button id="restart-btn" class="btn-ghost">Restart this scenario</button>
    </div>
  </div>

  <div id="feedback-card" class="card hidden" tabindex="-1">
    <h2>Coaching feedback</h2>
    <div id="feedback-body" class="feedback-block"></div>
  </div>

<script>
const SCENARIOS = __SCENARIOS_JSON__;
let sessionId = null;
let currentScenario = null;

const TEXT_SCALE_KEY = 'rts-text-scale';
function applyTextScale(scale) {
  document.documentElement.style.setProperty('--text-scale', scale);
  document.querySelectorAll('.tsc-btn').forEach(btn => {
    const isActive = parseFloat(btn.dataset.scale) === scale;
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
  try { localStorage.setItem(TEXT_SCALE_KEY, String(scale)); } catch (e) {}
}
document.querySelectorAll('.tsc-btn').forEach(btn => {
  btn.addEventListener('click', () => applyTextScale(parseFloat(btn.dataset.scale)));
});
(function initTextScale() {
  let saved = 1;
  try {
    const stored = localStorage.getItem(TEXT_SCALE_KEY);
    if (stored) saved = parseFloat(stored);
  } catch (e) {}
  applyTextScale(saved);
})();

function showPicker() {
  document.getElementById('picker-card').classList.remove('hidden');
  document.getElementById('chat-card').classList.add('hidden');
  document.getElementById('feedback-card').classList.add('hidden');
  document.getElementById('chat-log').innerHTML = '';
}

function showChat(scenarioId) {
  currentScenario = scenarioId;
  const s = SCENARIOS[scenarioId];
  document.getElementById('ab-name').textContent = s.persona_name;
  document.getElementById('ab-role').textContent = s.persona_role;
  document.getElementById('ab-hook').textContent = s.hook;
  document.getElementById('picker-card').classList.add('hidden');
  document.getElementById('feedback-card').classList.add('hidden');
  document.getElementById('chat-card').classList.remove('hidden');
  document.getElementById('chat-log').innerHTML = '';
}

function addMessage(role, text) {
  const log = document.getElementById('chat-log');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'assistant' ? 'msg-persona' : 'msg-user');
  const label = document.createElement('span');
  label.className = 'msg-label';
  label.textContent = role === 'assistant' ? SCENARIOS[currentScenario].persona_name : 'You';
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
      el.textContent = SCENARIOS[currentScenario].persona_name + ' is typing...';
      document.getElementById('chat-log').appendChild(el);
    }
  } else if (el) {
    el.remove();
  }
}

async function startScenario(scenarioId) {
  sessionId = crypto.randomUUID();
  showChat(scenarioId);
  setTyping(true);
  const resp = await fetch('/api/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, scenario_id: scenarioId})
  });
  const data = await resp.json();
  setTyping(false);
  addMessage('assistant', data.reply);
  if (data.audio_url) { new Audio(data.audio_url).play().catch(()=>{}); }
}

document.querySelectorAll('.scenario-start').forEach(btn => {
  btn.addEventListener('click', () => startScenario(btn.dataset.scenario));
});

document.getElementById('change-scenario-btn').addEventListener('click', showPicker);

async function sendMessage() {
  const input = document.getElementById('trainee-input');
  const sendBtn = document.getElementById('send-btn');
  const text = input.value.trim();
  if (!text || !sessionId) return;
  addMessage('user', text);
  input.value = '';
  input.disabled = true;
  sendBtn.disabled = true;
  setTyping(true);
  const resp = await fetch('/api/respond', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, message: text})
  });
  const data = await resp.json();
  setTyping(false);
  addMessage('assistant', data.reply);
  input.disabled = false;
  sendBtn.disabled = false;
  input.focus();
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
  card.focus();
});

document.getElementById('restart-btn').addEventListener('click', () => {
  if (currentScenario) startScenario(currentScenario);
});
</script>
</body>
</html>
"""


def _render_template():
    scenarios_json = json.dumps({
        sid: {
            "persona_name": s["persona_name"],
            "persona_role": s["persona_role"],
            "hook": s["hook"],
        } for sid, s in SCENARIOS.items()
    })
    html = TEMPLATE.replace("__SCENARIO_CARDS__", _scenario_cards_html())
    html = html.replace("__SCENARIOS_JSON__", scenarios_json)
    return html


@app.route("/")
def index():
    return render_template_string(_render_template())


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(force=True)
    session_id = data.get("session_id") or str(uuid.uuid4())
    scenario_id = data.get("scenario_id", "ld-tech")
    if scenario_id not in SCENARIOS:
        scenario_id = "ld-tech"
    scenario = SCENARIOS[scenario_id]

    _create_session(session_id, scenario_id)

    if _client:
        opener_prompt = [{"role": "user", "content": "Begin the conversation. Open with your first objection or concern, in character, as if the other person just introduced themselves to you."}]
        response = _client.messages.create(
            model="claude-sonnet-5", max_tokens=200, system=scenario["system_prompt"], messages=opener_prompt,
        )
        reply = _extract_text(response)
    else:
        reply = scenario["canned_objections"][0]

    _save_message(session_id, "assistant", reply)
    return jsonify({"reply": reply, "audio_url": _voice_url(reply, scenario.get("voice_id"))})


@app.route("/api/respond", methods=["POST"])
def api_respond():
    data = request.get_json(force=True)
    session_id = data["session_id"]
    message = data["message"]
    scenario_id = _get_scenario_id(session_id)
    scenario = SCENARIOS.get(scenario_id, SCENARIOS["ld-tech"])

    _save_message(session_id, "user", message)
    history = _get_messages(session_id)
    reply = _persona_reply(session_id, scenario, history)
    _save_message(session_id, "assistant", reply)

    return jsonify({"reply": reply, "audio_url": _voice_url(reply, scenario.get("voice_id"))})


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = request.get_json(force=True)
    session_id = data["session_id"]
    scenario_id = _get_scenario_id(session_id)
    scenario = SCENARIOS.get(scenario_id, SCENARIOS["ld-tech"])
    history = _get_messages(session_id)
    return jsonify(_coaching_feedback(scenario, history))


if __name__ == "__main__":
    _init_db()
    print("Roleplay Training Studio running, open http://127.0.0.1:5000 in your browser")
    app.run(debug=True)
