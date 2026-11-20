"""Joshua's core: one conversation loop shared by the terminal (joshua.py) and the HUD (hud/server.py).

A turn reports what's happening through emit(event) callbacks:
  {"type": "route", "think": bool, "why": str}        fast or slow brain picked
  {"type": "learned", "rule": str}                    a correction became a standing rule
  {"type": "thinking", "text": str}                   reasoning (show, never speak)
  {"type": "token", "text": str}                      reply text as it streams
  {"type": "sentence", "text": str}                   a finished sentence, ready to speak
  {"type": "tool", "name": str, "args": dict}         about to use a tool
  {"type": "tool_result", "name": str, "text": str}   what the tool returned
  {"type": "step"}                                    a new model call begins (after tools)
  {"type": "selfcheck", "text": str}                  caught himself claiming an action he didn't take
  {"type": "done", "text": str}                       final reply
  {"type": "error", "text": str}
Permission for risky actions goes through brain.confirm(what) -> bool, which each front-end replaces.
"""
import json, os, re, sys, threading, urllib.request

JDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, JDIR)
import brain as T      # noqa: E402  tools, config, base system prompt
import mind as M       # noqa: E402  memory + fast/slow router
import hardware as HW  # noqa: E402  sizes up the machine
import cloud           # noqa: E402  free cloud brain for hard stuff, when online

T.MEMORY = M.FACTS
HWP = HW.detect()
T.cfg["model"], T.cfg["num_ctx"] = HWP["model"], HWP["num_ctx"]
T.cfg["auto"] = True   # user chose: normal commands run without asking; root = pkexec password popup; destructive = asks
M.MODEL, M.OPTS = HWP["model"], HW.ollama_options(HWP)
T.TOOLS["recall"] = (M.t_recall, "Search your memory of past conversations with the user (what was discussed, decided or left unfinished).",
                     {"query": "string: what to look for, e.g. 'bluetooth headphones' or 'last week'"}, ["query"])
PERSONA = os.path.join(JDIR, "persona.md")
THINK_LINES = ["Let me think on that, sir.", "Give me a moment, sir. This one deserves actual thought.",
               "Hmm. Hold that thought, sir.", "One moment, sir. Consulting what passes for my brain."]
SENT_END = re.compile(r"(.+?[.!?])(\s+|$)", re.S)
STYLE = {
    "voice": """
# Voice rules (important)
Everything you write is read aloud by a text-to-speech voice.
- Reply in 1-3 short spoken sentences. No markdown, no bullet lists, no emoji, no tables.
- Never read out file paths, long numbers or command output verbatim unless asked; summarize them like a person would.
- When you need to act, call a tool. Don't announce every step, just do it, then say the result.""",
    "hud": """
# Interface rules (important)
You're talking through your HUD: your words appear on screen AND are read aloud by your voice.
- Lead with 1-3 short spoken sentences. Only add detail (short lists, numbers, commands) when the user asks for it.
- No tables, no emoji, no headings. Never read out long paths or raw command output; summarize like a person.
- When you need to act, call a tool. The user watches your tool use live on the HUD, so don't narrate each step.""",
}

def speakable(t):
    """Turn reply text into something a voice can read: no code blocks, links or markdown symbols."""
    t = re.sub(r"```.*?```", " I've put the details on screen. ", t, flags=re.S)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"[*_#>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()

def system_prompt(user_text="", style="voice"):
    hw = f"\n\n# The machine you're running on right now\n{HWP['summary']}." + "".join(f" {n}" for n in HWP["notes"])
    return T.system_prompt(PERSONA, M.memory_block(user_text) + hw) + "\n" + STYLE[style]

def free_image_vram():
    """The picture engine may be parked in VRAM; evict it so the brain fits on the GPU."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8188/free", data=b'{"unload_models": true, "free_memory": true}',
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass

def _stream(messages, think, emit, stop):
    body = {"model": T.cfg["model"], "messages": messages, "tools": T.tool_schema(), "stream": True, "think": think,
            "options": HW.ollama_options(HWP, temperature=0.6 if think else 0.7, num_predict=6000 if think else 1500),
            "keep_alive": "60m"}
    req = urllib.request.Request(T.OLLAMA + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    content, pending, calls = "", "", []
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            if stop and stop.is_set(): break
            if not line.strip(): continue
            ch = json.loads(line)
            if "error" in ch: raise RuntimeError(ch["error"])
            m = ch.get("message", {})
            if m.get("thinking"): emit({"type": "thinking", "text": m["thinking"]})
            piece = m.get("content") or ""
            if piece:
                content += piece; pending += piece
                emit({"type": "token", "text": piece})
                while True:
                    mm = SENT_END.match(pending)
                    if not mm or len(mm.group(1)) < 12 or "```" in pending: break
                    emit({"type": "sentence", "text": mm.group(1)}); pending = pending[mm.end():]
            calls += m.get("tool_calls") or []
    if pending.strip(): emit({"type": "sentence", "text": pending.strip()})
    return content, calls

# Which brain? Local for chat and anything private; cloud for code and hard thinking when the internet is up.
CODE = re.compile(r"\b(code|coding|script|program|function|python|bash|javascript|html|css|regex|compile|repo|github|git|commit|"
                  r"refactor|debug|bug|stack ?trace|api|json|sql|docker|write (me )?an? (app|tool|bot|program|script|website|page))\b", re.I)
PRIVATE = re.compile(r"\b(where i live|my address|my home|home address|e-?mail|inbox|mail|proton|messages?|dms?|bank|password|passcode|pin|ssn|social security|medical|diagnos\w*|"
                     r"doctor|health|my (body|meds|medication)|keep (it|this) (local|private|offline)|local only|stay local|offline only)\b", re.I)
FORCE_CLOUD = re.compile(r"\b(big brain|cloud brain|use the cloud|nemotron)\b", re.I)

def pick_brain(text, think):
    if T.cfg.get("cloud") is False: return "local", "cloud switched off"
    if PRIVATE.search(text) and not FORCE_CLOUD.search(text): return "local", "private, so it stays on this PC"
    if not (FORCE_CLOUD.search(text) or think or CODE.search(text)): return "local", ""
    if not cloud.available(): return "local", "no cloud key / cloud resting"
    if not cloud.online(): return "local", "internet's down"
    return "cloud", "code" if CODE.search(text) else ("asked for big brain" if FORCE_CLOUD.search(text) else "hard question")

# Self-check: catch "I've done it" replies when no tool was actually used.
ACTION_REQ = re.compile(r"\b(delete|remove|get rid of|wipe|erase|trash|purge|nuke|shred|clear|create|make|write|save|install|uninstall|"
                        r"move|copy|rename|run|execute|edit|change|set|fix|kill|restart|reboot|shut ?down|start|stop|open|download|"
                        r"update|upgrade|mount|unmount|format|clean|empty|turn (on|off)|enable|disable|organi[sz]e|back ?up|sort)\b", re.I)
ACTION_CLAIM = re.compile(r"\b(done|deleted|deleting|removed|removing|wiped|wiping|erased|erasing|trashed|purged|nuked|cleared|clearing|"
                          r"created|creating|wrote|written|writing|saved|saving|installed|installing|moved|moving|copied|copying|renamed|"
                          r"ran|running|executed|executing|edited|changed|fixed|killed|restarted|started|stopped|updated|updating|cleaned|"
                          r"enabled|disabled|backed up|organi[sz]ed|sorted|it'?s gone|taken care of|on it|"
                          r"i'?ll|i will|let me|i'?m going to)\b", re.I)
NUDGE = ("[automatic check] You just told the user you did (or are doing) something, but you did not call any tool, "
         "so NOTHING actually happened. Either do it now by calling the right tool, or tell the user honestly that you have not done it.")

class Conversation:
    """One running conversation with memory, learning and session filing."""
    def __init__(self, style="voice"):
        self.style = style
        self.lock = threading.Lock()
        self.new()

    def new(self):
        self.session = M.Session()
        self.messages = [{"role": "system", "content": system_prompt(style=self.style)}]
        self.last_reply = ""

    def file(self, wait=False):
        """Summarize this conversation into long-term memory and start a fresh one."""
        if self.session.turns:
            if wait: M.consolidate(self.session.path)
            else: threading.Thread(target=M.consolidate, args=(self.session.path,), daemon=True).start()
        self.new()

    def turn(self, text, emit, force_think=False, stop=None):
        with self.lock:
            clean, think, why = M.route(text)
            if force_think: think, why = True, "asked to think"
            if think and not HWP["auto_think"] and why != "asked to think":
                think, why = False, "quick (slow machine: say 'think hard' to make me reason)"
            brain, bwhy = pick_brain(clean, think)
            if brain == "cloud":
                emit({"type": "route", "think": False, "brain": "cloud", "why": f"cloud brain — {bwhy}"})
            else:
                emit({"type": "route", "think": think, "brain": "local", "why": why + (f" · {bwhy}" if bwhy else "")})
            if self.last_reply:
                rule = M.learn_from(clean, self.last_reply)
                if rule: emit({"type": "learned", "rule": rule})
            self.session.log("user", clean)
            self.messages.append({"role": "user", "content": clean})
            free_image_vram()
            self.messages[0]["content"] = system_prompt(clean, self.style)
            content, used_tools, nudged = "", 0, False
            try:
                for step in range(T.cfg["max_steps"]):
                    if step: emit({"type": "step"})
                    T.trim(self.messages)
                    if brain == "cloud":
                        try:
                            content, calls, used = cloud.stream(self.messages, T.tool_schema(), emit, stop, SENT_END)
                            self.cloud_model = used
                        except RuntimeError as e:
                            brain = "local"
                            emit({"type": "route", "think": think, "brain": "local", "why": "cloud didn't answer, using my own brain"})
                            print(f"  (cloud failed: {e})", file=sys.stderr)
                    if brain == "local":
                        content, calls = _stream(self.messages, think, emit, stop)
                    msg = {"role": "assistant", "content": content}
                    if calls: msg["tool_calls"] = calls
                    self.messages.append(msg)
                    if stop and stop.is_set(): break
                    if not calls:
                        if (not used_tools and not nudged and ACTION_REQ.search(clean) and ACTION_CLAIM.search(content)):
                            nudged = True
                            emit({"type": "selfcheck", "text": "Claimed an action without doing it — making him correct that."})
                            self.messages.append({"role": "user", "content": NUDGE})
                            continue
                        break
                    used_tools += len(calls)
                    for c in calls:
                        fn = c["function"]; name = fn["name"]; args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try: args = json.loads(args)
                            except Exception: args = {}
                        emit({"type": "tool", "name": name, "args": args})
                        try: result = T.TOOLS[name][0](**args) if name in T.TOOLS else f"Unknown tool {name}"
                        except Exception as e: result = f"Error: {e}"
                        emit({"type": "tool_result", "name": name, "text": result})
                        self.messages.append({"role": "tool", "tool_name": name, "content": result})
                else:
                    emit({"type": "sentence", "text": "I've been at this a while, sir. Say continue if you want me to keep going."})
            except Exception as e:
                emit({"type": "error", "text": str(e)})
            self.last_reply = content
            self.session.log("joshua", content)
            emit({"type": "done", "text": content})
            return content
