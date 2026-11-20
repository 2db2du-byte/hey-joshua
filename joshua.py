#!/usr/bin/env python3
"""Joshua — offline voice agent. Ears: faster-whisper. Mouth: Piper. Brain + hands: twin (Ollama).

Say "Joshua, ..." to wake him. After he answers he keeps listening ~20s for follow-ups.
Options:  --always   respond to everything (no wake word)
          --text     type instead of talk (he still speaks)
Env:      JOSHUA_MIC=<pipewire source name>   pick a mic (see: wpctl status)
"""
import os, re, sys, json, time, queue, threading, subprocess, urllib.request
import numpy as np

HOME = os.path.expanduser("~")
JDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, JDIR)
import core  # noqa: E402  (shared conversation loop: brain, memory, tools, hardware)
from core import T, M, HWP  # noqa: E402
import random  # noqa: E402

VOICE = os.path.join(JDIR, "models/voice/en_GB-alan-medium.onnx")
WHISPER_DIR = os.path.join(JDIR, "models/whisper")
WAKE = re.compile(r"\b(joshua|josh|jeshua|joshuah|yoshua)\b[\s,.!?]*", re.I)
FOLLOWUP_SECS = 20
ALWAYS = "--always" in sys.argv
TEXT_MODE = "--text" in sys.argv

RATE, FRAME_MS = 16000, 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2
HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "thank you for watching", ".", "", "joshua, a butler", "joshua a butler"}
# sighs, grunts and filler: never worth a reply unless he's addressed by name
NOISE = re.compile(r"(?i)^[\s,.!?]*((a+h+|o+h+|u+h+|u+m+|h+m+|m+|e+r+m*|hu+h|ha+|whoa+|wow|yeah|oh,? yeah|okay|ok|right|so|well|anyway)[\s,.!?]*)+$")

# ---------------- mouth ----------------
class Mouth:
    def __init__(self):
        from piper import PiperVoice
        self.voice = PiperVoice.load(VOICE)
        self.rate = self.voice.config.sample_rate
        self.q = queue.Queue()
        self.busy = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text):
        text = speakable(text)
        if text: self.busy.set(); self.q.put(text)

    def wait(self):
        self.q.join()
        time.sleep(0.3)  # let the tail of the audio clear before listening again

    def _run(self):
        while True:
            text = self.q.get()
            self.busy.set()
            try:
                p = subprocess.Popen(["pw-play", "--raw", "--rate", str(self.rate), "--channels", "1", "--format", "s16", "-"],
                                     stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                for ch in self.voice.synthesize(text): p.stdin.write(ch.audio_int16_bytes)
                p.stdin.close(); p.wait()
            except Exception as e:
                print(f"[voice error: {e}]")
            finally:
                self.q.task_done()
                if self.q.empty(): self.busy.clear()

def speakable(t):
    t = re.sub(r"```.*?```", " I've put the details on screen. ", t, flags=re.S)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"[*_#>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()

# ---------------- ears ----------------
class Ears:
    def __init__(self, mouth):
        from faster_whisper import WhisperModel
        self.model = WhisperModel("small.en", device="cpu", compute_type="int8", download_root=WHISPER_DIR, cpu_threads=HWP["whisper_threads"])
        self.mouth = mouth
        self.frames = queue.Queue(maxsize=2000)
        cmd = ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16"]
        if os.environ.get("JOSHUA_MIC"): cmd += ["--target", os.environ["JOSHUA_MIC"]]
        self.proc = subprocess.Popen(cmd + ["-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True).start()
        self.noise = self._calibrate()

    def _read(self):
        while True:
            b = self.proc.stdout.read(FRAME_BYTES)
            if not b: break
            if self.mouth.busy.is_set(): continue  # don't listen to ourselves
            try: self.frames.put_nowait(b)
            except queue.Full: pass

    @staticmethod
    def rms(b):
        a = np.frombuffer(b, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(a * a))) if a.size else 0.0

    def _calibrate(self):
        T.say("dim", "  (calibrating mic — stay quiet for a second)")
        vals = [self.rms(self.frames.get()) for _ in range(40)]
        n = float(np.median(vals))
        T.say("dim", f"  (background noise level {n:.0f})")
        return n

    def flush(self):
        while not self.frames.empty():
            try: self.frames.get_nowait()
            except queue.Empty: break

    def listen(self, timeout=None):
        """Block until one utterance is captured; return its text ('' on timeout)."""
        self.flush()
        start_thr = max(self.noise * 3.0, 350)
        stop_thr = max(self.noise * 2.0, 250)
        pre, buf, voiced, silence, talking = [], [], 0, 0, False
        t0 = time.time()
        while True:
            if timeout and not talking and time.time() - t0 > timeout: return ""
            try: b = self.frames.get(timeout=0.5)
            except queue.Empty: continue
            e = self.rms(b)
            if not talking:
                pre = (pre + [b])[-10:]
                voiced = voiced + 1 if e > start_thr else 0
                if voiced >= 3:
                    talking, buf = True, pre[:]
                    print(f"{T.C['dim']}  🎙  listening...{T.C['x']}", end="\r", flush=True)
            else:
                buf.append(b)
                silence = silence + 1 if e < stop_thr else 0
                if silence * FRAME_MS > 800 or len(buf) * FRAME_MS > 30000: break
        if len(buf) * FRAME_MS < 400: return self.listen(timeout)
        audio = np.frombuffer(b"".join(buf), dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, hotwords="Joshua")
        text = " ".join(s.text for s in segs).strip()
        print(" " * 30, end="\r")
        return "" if text.lower().strip(" .!?") in HALLUCINATIONS else text

# ---------------- brain ----------------
YES = re.compile(r"\b(yes|yeah|yep|yup|sure|do it|go ahead|go for it|please|affirmative|run it|ok|okay)\b", re.I)

def make_voice_confirm(mouth, ears):
    def confirm(what):
        print(f"{T.C['yel']}  ⚠ wants to run: {what}{T.C['x']}")
        if TEXT_MODE:
            mouth.say("Shall I proceed, sir?"); mouth.wait()
            try: return input(f"{T.C['yel']}  y/n: {T.C['x']}").strip().lower().startswith("y")
            except EOFError: return False
        mouth.say("Shall I proceed, sir? It's on your screen."); mouth.wait()
        ans = ears.listen(timeout=12)
        print(f"{T.C['grn']}  you › {ans or '(nothing)'}{T.C['x']}")
        ok = bool(YES.search(ans)) and not re.search(r"\b(no|don't|stop|cancel|nope)\b", ans, re.I)
        if not ok: mouth.say("Very well, I'll leave it.")
        return ok
    return confirm

# ---------------- main ----------------
def terminal_emitter(mouth):
    state = {"thinking": False, "started": False}
    def emit(ev):
        t = ev["type"]
        if t == "route":
            T.say("dim", f"  ({'☁ ' + ev['why'] if ev.get('brain') == 'cloud' else ('🐢 thinking — ' + ev['why'] if ev['think'] else '⚡ ' + ev['why'])})")
            if ev["think"]: mouth.say(random.choice(core.THINK_LINES))
        elif t == "learned": T.say("yel", f"  📝 learned: {ev['rule']}")
        elif t == "selfcheck": print(); T.say("red", f"  ⟲ self-check: {ev['text']}"); state["started"] = False
        elif t == "thinking":
            if not state["thinking"]: print(f"{T.C['dim']}💭 ", end=""); state["thinking"] = True
            print(ev["text"], end="", flush=True)
        elif t == "token":
            if state["thinking"]: print(T.C["x"]); state["thinking"] = False
            if not state["started"]: print(f"{T.C['cyan']}joshua ›{T.C['x']} ", end=""); state["started"] = True
            print(ev["text"], end="", flush=True)
        elif t == "sentence": mouth.say(ev["text"])
        elif t == "tool":
            if state["thinking"]: print(T.C["x"]); state["thinking"] = False
            if state["started"]: print(); state["started"] = False
            mouth.wait(); T.say("cyan", f"  ⚙ {ev['name']} {json.dumps(ev['args'])[:200]}")
        elif t == "tool_result":
            lines = ev["text"].strip().splitlines()
            T.say("dim", "    " + "\n    ".join(lines[:6]) + ("\n    ..." if len(lines) > 6 else ""))
        elif t == "error":
            print(f"\n[error: {ev['text']}]"); mouth.say("Something's gone sideways in my head, sir. Check the screen.")
        elif t == "done":
            if state["thinking"]: print(T.C["x"])
            print(); state.update(thinking=False, started=False)
    return emit

def main():
    try: urllib.request.urlopen(T.OLLAMA + "/api/tags", timeout=3)
    except Exception: print("Joshua's brain isn't running:  systemctl --user start joshua-brain"); sys.exit(1)
    T.say("dim", "  (warming up ears and voice...)")
    mouth = Mouth()
    ears = None if TEXT_MODE else Ears(mouth)
    T.confirm = make_voice_confirm(mouth, ears)
    convo = core.Conversation(style="voice")
    threading.Thread(target=M.consolidate_pending, kwargs={"exclude": convo.session.path}, daemon=True).start()
    emit = terminal_emitter(mouth)
    awake_until = 0.0
    mode = "typing" if TEXT_MODE else ("always listening" if ALWAYS else 'say "Joshua" to wake me')
    T.say("b", f"Joshua · {T.cfg['model']} · {mode} · Ctrl+C to quit")
    T.say("dim", f"  ({HWP['summary']})")
    for n in HWP["notes"]: T.say("yel", f"  ! {n}")
    mouth.say("Joshua here. Try not to waste my time, sir.")
    if HWP["mode"] != "full": mouth.say(HWP["notes"][0])
    mouth.wait()
    while True:
        try:
            text = input(f"\n{T.C['grn']}you ›{T.C['x']} ").strip() if TEXT_MODE else ears.listen()
        except (KeyboardInterrupt, EOFError):
            print()
            if convo.session.turns: mouth.say("Filing today's notes, sir."); mouth.wait()
            convo.file(wait=True); break
        if not text: continue
        woke = bool(WAKE.search(text))
        if not woke and not TEXT_MODE and NOISE.match(text):
            T.say("dim", f"  (heard, ignoring: {text})"); continue
        if not (TEXT_MODE or ALWAYS or woke or time.time() < awake_until):
            T.say("dim", f"  (heard, ignoring: {text})"); continue
        if not TEXT_MODE: print(f"{T.C['grn']}you ›{T.C['x']} {text}")
        clean = WAKE.sub("", text, count=1).strip() if woke else text
        low = clean.lower().strip(" .!?")
        if re.fullmatch(r"(that'?s all|go to sleep|never mind|nevermind|thanks?( you)?|cheers)", low):
            mouth.say("Very good, sir."); awake_until = 0; mouth.wait()
            convo.file(wait=False); continue
        if re.fullmatch(r"(goodbye|shut down|shutdown|exit|quit)", low):
            if convo.session.turns: mouth.say("Filing today's notes, sir."); mouth.wait()
            convo.file(wait=True)
            mouth.say("Goodbye, sir. Try to survive without me."); mouth.wait(); break
        if not clean:
            mouth.say("Sir?"); awake_until = time.time() + FOLLOWUP_SECS; mouth.wait(); continue
        try: convo.turn(clean, emit)
        except KeyboardInterrupt: print("\n(interrupted)")
        mouth.wait()
        awake_until = time.time() + FOLLOWUP_SECS

if __name__ == "__main__":
    main()
