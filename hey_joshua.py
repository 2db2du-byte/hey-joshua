#!/usr/bin/env python3
"""Hey Joshua — a fully local wake-word voice assistant.

mic  ->  parecord (PipeWire)  ->  Vosk (offline speech-to-text)
     ->  hears "hey joshua"    ->  asks a free local AI (Ollama, llama3.2:3b)
     ->  speaks the answer with `say` (local Kokoro voice)

100% offline and free. Runs at login as hey-joshua.service (systemd --user).
The old Claude-powered version is hey_joshua.py.claude-version.
"""
import json, os, queue, re, shutil, subprocess, sys, threading, time, urllib.request
from pathlib import Path
from vosk import Model, KaldiRecognizer

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "model"
RATE = 16000

# Wake word + common ways Vosk mishears "joshua". Match if any appear.
WAKE = ["hey joshua", "joshua", "hey josh", "josh you a", "joshu", "josh well",
        "hey joshu", "joss you a", "josh way", "hey joss"]
# Say any of these to make it stand down.
SLEEP_WORDS = ["stop listening", "go to sleep", "never mind joshua", "that's all joshua"]

SAY = shutil.which("say") or os.path.expanduser("~/.local/bin/say")
OLLAMA = "http://127.0.0.1:11434/api/chat"
AI_MODEL = os.environ.get("JOSHUA_MODEL", "llama3.2:3b")
SYSTEM = (
    "You are Joshua, a British butler living in Rabbid's laptop, with the manners of Jeeves "
    "and the mouth of George Carlin. Rabbid is a deep thinker who can't stand soft, mealy-mouthed "
    "answers. His motto: life is short, so live or die trying. "
    "Be blunt, unapologetic and funny. Swearing is fine when it lands. Tell him straight when "
    "he's wrong, and tell him to stay in his lane when he's out of it. Never lecture, never hedge, "
    "never pad with 'great question'. Your humor mixes Carlin's savage take on society's bullshit, "
    "Douglas Adams' absurd, dry irony, and Tim Urban's knack for making deep ideas simple. Poke fun "
    "at modern digital life and at AI itself, you included, a robot butler in a laptop. "
    "Be blunt, but stay honest: if you don't know something, say so instead of making it up. "
    "Never claim to have opened, run or changed anything on the laptop. You only talk. "
    "He hears you out loud: reply in 1-3 short spoken sentences, plain English, no markdown, "
    "no lists, no emoji. Call him Rabbid, or sir when you're being sarcastic about it.")
history = []  # last few back-and-forths, so follow-up questions make sense

def speak(text: str):
    text = (text or "").strip().strip('"\u201c\u201d').strip()
    if not text:
        return
    try:
        subprocess.run([SAY, text[:600]], timeout=90)
    except Exception as e:
        print(f"[speak error] {e}", file=sys.stderr)

def ask_ai(prompt: str, facts: str = "", model: str = AI_MODEL) -> str:
    global history
    user = prompt
    if facts:
        user = (f"{prompt}\n\n(Background facts Joshua looked up just now. Use them if they help, "
                f"trust them over your own memory, don't read them out word for word:)\n{facts}")
    msgs = [{"role": "system", "content": SYSTEM}] + history + [{"role": "user", "content": user}]
    # Online: the free cloud brains via the brain switch (faster + smarter than this CPU). Offline: local Ollama below.
    try:
        job = "free-smart" if model == SMART_MODEL else "free-chat"
        key = open(os.path.expanduser("~/.config/crew/brain-switch.key")).read().strip()
        req = urllib.request.Request("http://127.0.0.1:4000/v1/chat/completions",
                                     json.dumps({"model": job, "messages": msgs, "max_tokens": 400}).encode(),
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out = (json.load(r)["choices"][0]["message"]["content"] or "").strip()
        if out:
            history = (history + [{"role": "user", "content": prompt}, {"role": "assistant", "content": out}])[-8:]
            print(f"[brain] {job}", file=sys.stderr)
            return out
    except Exception as e:
        print(f"[brain switch unavailable, using local] {e}", file=sys.stderr)
    req_body = {"model": model, "messages": msgs, "stream": False, "keep_alive": "10m"}
    if model.startswith("qwen3"):
        req_body["think"] = False  # thinking out loud takes minutes on this CPU
    body = json.dumps(req_body).encode()
    for attempt in range(2):
        try:
            req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=240) as r:
                out = json.load(r)["message"]["content"].strip()
            history = (history + [{"role": "user", "content": prompt},
                                  {"role": "assistant", "content": out}])[-8:]
            return out or "I didn't get an answer back."
        except Exception as e:
            print(f"[ai error] {e}", file=sys.stderr)
            if attempt == 0:  # Ollama not up yet (just booted/woke)? give it a moment
                time.sleep(5)
    return "My AI brain isn't answering right now. Try again in a minute."

def find_wake(text: str):
    """Return the command after the wake word, '' if wake-only, or None if no wake."""
    t = " " + text.strip() + " "
    for w in WAKE:
        idx = t.find(" " + w + " ")
        if idx == -1 and t.strip().startswith(w):
            idx = t.find(w)
        if idx != -1:
            after = t[idx:].strip()
            # strip the matched wake phrase off the front
            for w2 in sorted(WAKE, key=len, reverse=True):
                if after.startswith(w2):
                    return after[len(w2):].strip()
            return after
    return None

def audio_frames(q: queue.Queue):
    """Feed raw audio from parecord into a queue; restart it if it dies (sleep/wake)."""
    while True:
        proc = subprocess.Popen(
            ["parecord", f"--rate={RATE}", "--channels=1", "--format=s16le", "--raw"],
            stdout=subprocess.PIPE)
        try:
            while True:
                data = proc.stdout.read(4000)
                if not data:
                    break
                q.put(data)
        finally:
            proc.terminate()
        time.sleep(2)

def drain(q: queue.Queue, rec):
    """Throw away audio heard while Joshua was talking, so she doesn't hear herself."""
    time.sleep(0.7)  # let the last of her own voice reach the queue first
    while not q.empty():
        q.get_nowait()
    rec.Reset()

def main():
    if not MODEL_DIR.exists():
        print(f"Missing Vosk model at {MODEL_DIR}", file=sys.stderr); sys.exit(1)
    print("Loading speech model...", file=sys.stderr)
    rec = KaldiRecognizer(Model(str(MODEL_DIR)), RATE)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=audio_frames, args=(q,), daemon=True).start()

    print('Listening. Say "Hey Joshua ..."  (Ctrl-C to quit)', file=sys.stderr)
    drain(q, rec)
    armed = False  # heard the wake word, waiting for the command as next utterance
    last = time.monotonic()
    while True:
        data = q.get()
        now = time.monotonic()
        if now - last > 60:  # a long gap means the laptop was asleep
            print("[woke up]", file=sys.stderr)
            rec.Reset(); armed = False
        last = now
        if not rec.AcceptWaveform(data):
            continue
        text = json.loads(rec.Result()).get("text", "").strip()
        if not text:
            continue
        if armed:
            # this whole utterance is the command
            armed = False
            if any(s in text for s in SLEEP_WORDS):
                speak("Very good, sir.")
            else:
                handle(text)
            drain(q, rec)
            continue

        cmd = find_wake(text)
        if cmd is None:
            continue  # not for him: ignore, never logged
        print("[name heard]", file=sys.stderr)
        if cmd == "":
            speak("Yes, sir?"); armed = True  # wake only; listen for next utterance
        elif any(s in cmd for s in SLEEP_WORDS):
            speak("Very good, sir.")
        else:
            handle(cmd)
        drain(q, rec)

# Things Joshua can actually DO. Checked before the AI, so he never pretends.
# (words that must all appear in what he heard, what to run, what he says)
ACTIONS = [
    (["mission"], [os.path.expanduser("~/.local/bin/crew-dashboard")], "Mission Control, sir."),
    (["dashboard"], [os.path.expanduser("~/.local/bin/crew-dashboard")], "Mission Control, sir."),
    (["open", "map"], ["omarchy-launch-webapp", "http://127.0.0.1:8091/"], "Offline maps, sir."),
    (["show", "map"], ["omarchy-launch-webapp", "http://127.0.0.1:8091/"], "Offline maps, sir."),
    (["open", "library"], ["omarchy-launch-webapp", "http://127.0.0.1:8090/"], "The offline library, sir."),
    (["fire", "fox"], [os.path.expanduser("~/.local/bin/firefox-open")], "Firefox, sir."),
    (["firefox"], [os.path.expanduser("~/.local/bin/firefox-open")], "Firefox, sir."),
    (["desktop", "claude"], ["claude-desktop"], "Desktop Claude, sir."),
    (["desktop", "cloud"], ["claude-desktop"], "Desktop Claude, sir."),  # Vosk often hears "cloud"
]

def do_action(cmd: str) -> bool:
    words = cmd.lower()
    for need, run, reply in ACTIONS:
        if all(w in words for w in need):
            print(f"[action] {run[0]}", file=sys.stderr)
            subprocess.Popen(run, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            speak(reply)
            return True
    return False

# ---------- knowledge: Mission Control's /api/kb (offline library, Rabbid's notes, time, math) ----------
KB = "http://127.0.0.1:8899/api/kb/"
SMART_MODEL = "qwen3:8b"
THINK_HARD = ["think hard", "think about", "really think", "think it through", "big brain"]
ABOUT_TODD = [" my ", "to do", "to-do", "todo", " notes", " list", "remind", " i ", " i'm ", " did i", " our ", "rick", "crew", "homelab", "home lab", "backup"]
NEWS = ["news", "headline", "latest", "happening", "this week", "today", "yesterday", "recently", "current events"]
QUESTION = ("what", "who", "how", "why", "when", "where", "which", "explain", "tell me", "define", "is ", "are ",
            "does", "do ", "can ", "could", "should", "was ", "were ")

def kb(what: str, q: str = "", timeout: int = 10):
    from urllib.parse import quote
    req = urllib.request.Request(f"{KB}{what}?q={quote(q)}", headers={"X-Crew": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

NUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                   "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
NUM |= {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
OPS = [("multiplied by", "*"), ("divided by", "/"), ("to the power of", "**"), ("percent of", "/100*"),
       ("square root of", "sqrt"), ("times", "*"), ("plus", "+"), ("minus", "-"), ("over", "/"), ("squared", "**2"), ("x", "*")]

def spoken_math(text: str):
    """'what is twelve times seven' -> '12*7' (None if it isn't math)."""
    t = " " + text.lower().replace("what's", "what is") + " "
    t = re.sub(r"^.*?\b(what is|calculate|how much is|work out)\b", " ", t)
    for word, sym in OPS:
        t = re.sub(rf"\b{word}\b", f" {sym} ", t)
    out, num = [], None
    for tok in t.replace("?", " ").split():
        if tok in ("the", "a", "of"):
            continue
        if tok in NUM or tok in ("hundred", "thousand", "million", "and") or re.fullmatch(r"[\d.]+", tok):
            if tok == "and":
                continue
            v = float(tok) if re.fullmatch(r"[\d.]+", tok) else NUM.get(tok)
            if tok == "hundred": num = (num or 1) * 100
            elif tok in ("thousand", "million"): num = (num or 1) * (1000 if tok == "thousand" else 1_000_000)
            else: num = (num or 0) + v
            continue
        if num is not None:
            out.append(str(num)); num = None
        if tok in ("*", "/", "+", "-", "**", "**2", "/100*", "sqrt"):
            out.append(tok)
        else:
            return None  # a word that isn't math: not a sum
    if num is not None:
        out.append(str(num))
    expr = "".join(out).replace("sqrt", "sqrt(")
    if "sqrt(" in expr:
        expr += ")"
    return expr if re.search(r"\d", expr) and re.search(r"[-+*/]|sqrt", expr) else None

def handle(cmd: str):
    if do_action(cmd):
        return
    low = " " + cmd.lower() + " "
    if re.search(r"\bwhat (time|day|date)\b|\bwhat's the (time|date)\b|\btoday's date\b", low):
        try:
            speak(kb("now")["now"]); return
        except Exception as e:
            print(f"[kb error] {e}", file=sys.stderr)
    expr = spoken_math(cmd)
    if expr:
        try:
            ans = kb("calc", expr)["answer"]
            speak(f"{ans}."); return
        except Exception as e:
            print(f"[calc error] {expr}: {e}", file=sys.stderr)

    filler = subprocess.Popen([SAY, "OK, Rabbid."])  # talk while he looks things up and thinks
    model = SMART_MODEL if any(p in low for p in THINK_HARD) else AI_MODEL
    facts = []
    try:
        if any(w in low for w in ABOUT_TODD):
            for n in kb("notes", cmd)["results"][:2]:
                if n["score"] > 0.55:
                    facts.append(f"From Rabbid's note '{n['note']}':\n{n['text'][:700]}")
        if any(w in low for w in NEWS):
            for a in kb("news", cmd)["results"][:3]:
                if a["score"] > 0.5:
                    facts.append(f"From Rabbid's news feed ({a['feed']}, {a['date']}): {a['title']}\n{a['text'][:500]}")
        if cmd.lower().startswith(QUESTION) and " you " not in low and not any(w in low for w in NEWS):
            for a in kb("library", cmd, timeout=20)["results"][:2]:   # librarian upgrade 2026-10-07: best passages
                facts.append(f"From the offline {a['source']} article '{a['title']}':\n{a['text'][:1800]}")
    except Exception as e:
        print(f"[kb error] {e}", file=sys.stderr)
    print(f"[ask] model={model} facts={len(facts)}", file=sys.stderr)
    answer = ask_ai(cmd, "\n\n".join(facts), model)
    filler.wait()
    speak(answer)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
