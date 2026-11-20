"""Joshua's mind: fast/slow thinking router + long-term memory.

memory/
  facts.md         things about the user & this PC        (always in his head)
  rules.md         lessons from the user's corrections     (always in his head, followed strictly)
  episodes.jsonl   one summary per past conversation + embedding  (recalled when relevant)
  conversations/   raw transcripts, one file per session
"""
import json, math, os, re, threading, time, datetime, urllib.request

JDIR = os.path.dirname(os.path.abspath(__file__))
MEM = os.environ.get("JOSHUA_MEMORY_DIR") or os.path.join(JDIR, "memory")
FACTS = os.path.join(MEM, "facts.md")
RULES = os.path.join(MEM, "rules.md")
EPISODES = os.path.join(MEM, "episodes.jsonl")
LOGS = os.path.join(MEM, "conversations")
DONE = os.path.join(MEM, "consolidated.txt")
OLLAMA = "http://127.0.0.1:11435"
MODEL = "qwen3.5:9b"   # replaced at startup by hardware.detect()
OPTS = {}
EMBED = "nomic-embed-text"
_lock = threading.Lock()

os.makedirs(LOGS, exist_ok=True)
for p in (FACTS, RULES):
    if not os.path.exists(p): open(p, "w").close()

def _post(path, body, timeout=300):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())

def _ask(system, user, schema=None, num_predict=800):
    body = {"model": MODEL, "stream": False, "think": False, "keep_alive": "60m",
            "options": {"temperature": 0.2, "num_ctx": 16384, "num_predict": num_predict, **OPTS},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if schema: body["format"] = schema
    out = _post("/api/chat", body)["message"]["content"].strip()
    return json.loads(out) if schema else out

# ---------------- fast / slow router ----------------
FORCE_THINK = re.compile(r"\b(think (hard|about (it|this|that)|carefully|it through)|take your time|really think)\b[,.!]?\s*", re.I)
HARD = re.compile(r"\b(why|how (do|can|should|would|does|could)|should (i|we)|explain|plan|figure out|diagnos\w*|troubleshoot|what'?s wrong|"
                  r"compare|difference between|calculate|step by step|pros and cons|best way|decide|strategy|design|debug|"
                  r"fix|broken|not working|won'?t|keeps? (crashing|failing)|is it safe|what would happen|what if)\b", re.I)

def route(text):
    """Return (cleaned_text, think: bool, reason)."""
    if FORCE_THINK.search(text):
        return FORCE_THINK.sub("", text).strip() or text, True, "asked to think"
    if len(text.split()) > 30: return text, True, "long question"
    if HARD.search(text): return text, True, "hard question"
    return text, False, "quick"

# ---------------- embeddings ----------------
def embed(text, kind="search_query"):
    try:
        r = _post("/api/embed", {"model": EMBED, "input": f"{kind}: {text}", "keep_alive": "60m"}, timeout=60)
        return r["embeddings"][0]
    except Exception:
        return None

def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-9)

def _episodes():
    if not os.path.exists(EPISODES): return []
    out = []
    for line in open(EPISODES):
        try: out.append(json.loads(line))
        except Exception: pass
    return out

def recall(query, k=3, min_sim=0.6):
    """Past-conversation summaries that relate to `query`."""
    eps = [e for e in _episodes() if e.get("vec")]
    if not eps: return []
    q = embed(query)
    if not q: return []
    scored = sorted(((_cos(q, e["vec"]), e) for e in eps), key=lambda t: t[0], reverse=True)
    return [e for s, e in scored[:k] if s >= min_sim]

def t_recall(query):
    hits = recall(query, k=5, min_sim=0.45)
    if not hits: return "No past conversations about that."
    return "\n".join(f"- {e['date']}: {e['summary']}" for e in hits)

def is_duplicate(text, path, threshold=0.86):
    """True if `text` says the same thing as a line already in `path` (compared by meaning, not spelling)."""
    lines = [l.lstrip("- ").rsplit("  (", 1)[0] for l in read(path).splitlines() if l.strip()]
    if not lines: return False
    if text.strip().lower() in (l.lower() for l in lines): return True
    v = embed(text, "search_document")
    if not v: return False
    return any(_cos(v, w) >= threshold for w in (embed(l, "search_document") for l in lines) if w)

def recent_episodes(n=3):
    return _episodes()[-n:]

# ---------------- what's always in his head ----------------
def read(path):
    try: return open(path).read().strip()
    except Exception: return ""

def memory_block(user_text=""):
    facts, rules = read(FACTS) or "(nothing yet)", read(RULES)
    parts = [f"# What you know about the user and this PC\n{facts}"]
    if rules:
        parts.append(f"# Rules the user has taught you — follow these strictly, they override your habits\n{rules}")
    recent = recent_episodes(2)
    related = recall(user_text) if user_text else []
    seen, lines = set(), []
    for e in related + recent:
        if e["date"] + e["summary"][:40] in seen: continue
        seen.add(e["date"] + e["summary"][:40]); lines.append(f"- {e['date']}: {e['summary']}")
    if lines:
        parts.append("# Past conversations you remember (use if relevant; don't recite them)\n" + "\n".join(lines))
    return "\n\n".join(parts)

# ---------------- learning from corrections (immediate) ----------------
CORRECTION = re.compile(r"\b(no+|nope|wrong|incorrect|that'?s not|not what i|i (said|told you|meant|asked)|don'?t (ever|do that|say|call|use)|"
                        r"stop (doing|saying|calling)|never (do|say|call)|always|from now on|next time|i prefer|i like it when|i hate when|actually)\b", re.I)

RULE_SYS = """You maintain a list of standing rules an AI butler named Joshua must follow for his user.
Given Joshua's last reply and the user's response, decide whether the user corrected Joshua or stated a lasting preference/instruction.
If yes, write ONE short rule in second person starting with a verb (e.g. "Call me Todd, not sir." / "Check the bookshelf before giving medical advice.").
If it is only a one-off answer, small talk, or already covered by the existing rules, return an empty rule.
Saying no to a single action (declining a permission request, "don't delete that one") is a one-off decision, NOT a rule."""

def learn_from(user_text, last_reply):
    if not CORRECTION.search(user_text): return None
    try:
        out = _ask(RULE_SYS, f"Existing rules:\n{read(RULES) or '(none)'}\n\nJoshua said: {last_reply[:800]}\n\nUser replied: {user_text}",
                   schema={"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]}, num_predict=120)
        rule = (out.get("rule") or "").strip()
    except Exception:
        return None
    if len(rule) < 6 or is_duplicate(rule, RULES): return None
    with _lock, open(RULES, "a") as f: f.write(f"- {rule}  ({datetime.date.today()})\n")
    return rule

# ---------------- conversation log + consolidation ----------------
class Session:
    def __init__(self):
        self.path = os.path.join(LOGS, datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".jsonl")
        self.turns = 0
    def log(self, role, text):
        if not text: return
        if role == "user": self.turns += 1
        with open(self.path, "a") as f: f.write(json.dumps({"t": time.time(), "role": role, "text": text}) + "\n")

SUMMARY_SYS = """You are Joshua's memory. Read a conversation between the user and Joshua (an AI butler on the user's PC) and file it.
- summary: 1-3 sentences, specific (names, numbers, decisions, what got done or left unfinished). Written as plain notes about what happened, past tense. Don't judge or explain what Joshua did or didn't file.
- facts: NEW lasting facts the user shared or that were discovered, which are not already in the known facts. ANY personal detail counts:
  family, friends, pets (with names), where they live, work, health, hobbies, likes/dislikes, plans, possessions, hardware, projects, problems they're dealing with.
  One short sentence each, e.g. "User's dog is a beagle named Biscuit." Only skip things that are clearly temporary (e.g. "user is hungry right now").
- rules: NEW standing instructions or corrections the user gave about how Joshua should behave, not already in the known rules. Start each with a verb.
  Only include things the user meant to last (they corrected his behaviour, or said always / never / from now on / I prefer).
  The user declining or denying a single action, or permission request, is a one-time decision: it is NOT a rule. Never turn it into one.
Return empty lists when there is nothing new. Never invent anything not in the conversation."""
SUMMARY_SCHEMA = {"type": "object", "required": ["summary", "facts", "rules"], "properties": {
    "summary": {"type": "string"}, "facts": {"type": "array", "items": {"type": "string"}}, "rules": {"type": "array", "items": {"type": "string"}}}}

def _done():
    return set(read(DONE).splitlines())

def consolidate(path):
    """Summarize one finished conversation into an episode, and harvest facts/rules."""
    name = os.path.basename(path)
    if name in _done(): return None
    lines = [json.loads(l) for l in open(path) if l.strip()]
    users = [l for l in lines if l["role"] == "user"]
    if len(users) >= 1 and sum(len(l["text"].split()) for l in users) >= 4:
        convo = "\n".join(f"{'User' if l['role'] == 'user' else 'Joshua'}: {l['text'][:1500]}" for l in lines)[-12000:]
        try:
            out = _ask(SUMMARY_SYS, f"Known facts:\n{read(FACTS)}\n\nKnown rules:\n{read(RULES) or '(none)'}\n\nConversation:\n{convo}", schema=SUMMARY_SCHEMA)
        except Exception:
            return None  # brain unavailable; try again next start
        date = name[:10]
        ep = {"date": date, "file": name, "summary": out["summary"].strip(), "vec": embed(out["summary"], "search_document")}
        with _lock:
            with open(EPISODES, "a") as f: f.write(json.dumps(ep) + "\n")
            for items, path in ((out.get("facts", []), FACTS), (out.get("rules", []), RULES)):
                for x in (i.strip() for i in items):
                    if x and not is_duplicate(x, path):
                        with open(path, "a") as f: f.write(f"- {x}  ({date})\n")
    with _lock, open(DONE, "a") as f: f.write(name + "\n")
    return True

def consolidate_pending(exclude=None):
    """File any conversations that never got summarized (crash, Ctrl+C, power cut)."""
    for name in sorted(os.listdir(LOGS)):
        p = os.path.join(LOGS, name)
        if p != exclude and name.endswith(".jsonl"): consolidate(p)
