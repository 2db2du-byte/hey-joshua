#!/usr/bin/env python3
"""Joshua's HUD — the browser face of Joshua.  http://127.0.0.1:8899

Runs inside Joshua's own Python (.venv) so it can use his voice (Piper) and ears (Whisper),
and shares his brain, memory and tools through core.py.
"""
import io, json, os, queue, random, re, shutil, subprocess, sys, threading, time, urllib.parse, urllib.request, uuid, wave
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
JDIR = os.path.dirname(HERE)
sys.path.insert(0, JDIR)
import core  # noqa: E402
from core import T, M, HWP  # noqa: E402
import hardware  # noqa: E402

PORT = int(os.environ.get("JOSHUA_HUD_PORT", "8899"))
COMFY = "http://127.0.0.1:8188"
LIBRARY = "http://127.0.0.1:8090"
BRAINS = ["http://127.0.0.1:11435", "http://127.0.0.1:11434"]
OUTPUT = os.path.join(JDIR, "ComfyUI", "output", "joshua")
VOICE = os.path.join(JDIR, "models/voice/en_GB-alan-medium.onnx")
WHISPER_DIR = os.path.join(JDIR, "models/whisper")
STARTED = time.time()

def http(url, data=None, timeout=30, raw=False):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        return b if raw else (json.loads(b) if b else {})

def up(url):
    try: urllib.request.urlopen(url, timeout=2); return True
    except Exception: return False

# ---------------- conversation ----------------
convo = core.Conversation(style="hud")
last_activity = time.time()
current = {"q": None, "stop": None}
pending_confirms = {}

def hud_confirm(what):
    """Ask the person at the HUD for permission; wait for their click."""
    q = current["q"]
    if q is None: return False
    cid = uuid.uuid4().hex[:8]
    ev = threading.Event(); pending_confirms[cid] = {"event": ev, "ok": False}
    q.put({"type": "confirm", "id": cid, "what": what})
    ev.wait(timeout=180)
    return pending_confirms.pop(cid, {}).get("ok", False)
T.confirm = hud_confirm

def idle_filer():
    """File the conversation into long-term memory after 15 quiet minutes."""
    while True:
        time.sleep(60)
        if convo.session.turns and time.time() - last_activity > 900 and not convo.lock.locked():
            convo.file(wait=False)
threading.Thread(target=idle_filer, daemon=True).start()
threading.Thread(target=M.consolidate_pending, kwargs={"exclude": convo.session.path}, daemon=True).start()

# ---------------- voice & ears ----------------
_voice, _whisper, _vlock, _wlock = None, None, threading.Lock(), threading.Lock()

def tts(text):
    global _voice
    text = core.speakable(text)
    if not text: return b""
    with _vlock:
        if _voice is None:
            from piper import PiperVoice
            _voice = PiperVoice.load(VOICE)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(_voice.config.sample_rate)
            for ch in _voice.synthesize(text): w.writeframes(ch.audio_int16_bytes)
        return buf.getvalue()

def decode_audio(data):
    """Browser recording (webm/ogg/wav) -> 16 kHz mono float32, which is what Whisper wants."""
    import av, numpy as np
    out = []
    with av.open(io.BytesIO(data)) as c:
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in c.decode(audio=0):
            out += [f.to_ndarray() for f in rs.resample(frame)]
        out += [f.to_ndarray() for f in rs.resample(None)]
    return (np.concatenate(out, axis=1).flatten().astype(np.float32) / 32768.0) if out else np.zeros(1600, np.float32)

def stt(audio_bytes):
    global _whisper
    with _wlock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel("small.en", device="cpu", compute_type="int8", download_root=WHISPER_DIR, cpu_threads=HWP["whisper_threads"])
        segs, _ = _whisper.transcribe(decode_audio(audio_bytes), language="en", beam_size=1, vad_filter=True, hotwords="Joshua")
        text = " ".join(s.text for s in segs).strip()
    return "" if text.lower().strip(" .!?") in {"thank you", "thanks for watching", "you", "bye", ""} else text

# ---------------- system readouts ----------------
_cpu_prev = None
def cpu_percent():
    global _cpu_prev
    f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    idle, total = f[3] + f[4], sum(f)
    if _cpu_prev is None: _cpu_prev = (idle, total); return 0
    di, dt = idle - _cpu_prev[0], total - _cpu_prev[1]; _cpu_prev = (idle, total)
    return round(100 * (1 - di / dt)) if dt else 0

def status():
    s = {"mode": HWP["mode"], "summary": HWP["summary"], "notes": HWP["notes"], "model": HWP["model"],
         "studio_ok": HWP["studio_ok"], "studio_note": HWP.get("studio_note", ""), "uptime": int(time.time() - STARTED)}
    g = subprocess.run("nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits",
                       shell=True, capture_output=True, text=True).stdout.strip().split("\n")[0]
    if g and "," in g:
        n, u, mu, mt, t, p = [x.strip() for x in g.split(",")]
        def num(x):
            try: return float(x)
            except ValueError: return 0
        s["gpu"] = {"name": n, "util": num(u), "mem_used": num(mu), "mem_total": num(mt), "temp": num(t), "power": num(p)}
    mi = dict(l.split(":", 1) for l in open("/proc/meminfo").read().splitlines() if ":" in l)
    tot, av = int(mi["MemTotal"].split()[0]), int(mi["MemAvailable"].split()[0])
    s["ram"] = {"used": round((tot - av) / 1048576, 1), "total": round(tot / 1048576, 1)}
    s["cpu"] = {"util": cpu_percent(), "threads": os.cpu_count()}
    s["services"] = {"brain": up(BRAINS[0] + "/api/tags"), "library": up(LIBRARY + "/"), "imaging": up(COMFY + "/system_stats")}
    try: s["loaded"] = [m["name"] for m in http(BRAINS[0] + "/api/ps", timeout=2).get("models", [])]
    except Exception: s["loaded"] = []
    s["memory"] = {"facts": len([l for l in M.read(M.FACTS).splitlines() if l.strip()]),
                   "rules": len([l for l in M.read(M.RULES).splitlines() if l.strip()]), "episodes": len(M._episodes())}
    s["busy"] = convo.lock.locked()
    return s

# ---------------- memory ----------------
def memory():
    clean = lambda p: [l[2:] if l.startswith("- ") else l for l in M.read(p).splitlines() if l.strip()]
    return {"facts": clean(M.FACTS), "rules": clean(M.RULES),
            "episodes": [{"date": e["date"], "summary": e["summary"]} for e in reversed(M._episodes())]}

def forget(kind, index):
    if kind in ("facts", "rules"):
        path = M.FACTS if kind == "facts" else M.RULES
        lines = [l for l in M.read(path).splitlines() if l.strip()]
        if 0 <= index < len(lines):
            del lines[index]
            with M._lock, open(path, "w") as f: f.write("\n".join(lines) + ("\n" if lines else ""))
    elif kind == "episodes":
        eps = M._episodes(); i = len(eps) - 1 - index   # shown newest-first
        if 0 <= i < len(eps):
            del eps[i]
            with M._lock, open(M.EPISODES, "w") as f: f.writelines(json.dumps(e) + "\n" for e in eps)

# ---------------- library ----------------
def books():
    xml = http(LIBRARY + "/catalog/v2/entries?count=500", raw=True, timeout=10).decode()
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        g = lambda tag: (re.search(rf"<{tag}>([^<]*)", e) or [None, ""])[1]
        link = re.search(r'href="/content/([^"/]+)', e)
        out.append({"title": T.html.unescape(g("title")), "summary": T.html.unescape(g("summary")), "articles": int(g("articleCount") or 0),
                    "name": link.group(1) if link else ""})
    return sorted(out, key=lambda b: -b["articles"])

def search(q, book=""):
    params = {"pattern": q, "format": "xml", "pageLength": 25}
    if book: params["books.name"] = book
    xml = http(LIBRARY + "/search?" + urllib.parse.urlencode(params), raw=True, timeout=20).decode()
    items = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        g = lambda pat: (re.search(pat, it, re.S) or [None, ""])[1]
        items.append({"title": T.html.unescape(g(r"<title>([^<]*)")), "link": g(r"<link>([^<]*)"),
                      "book": T.html.unescape(g(r"<book>\s*<title>([^<]*)")), "snippet": T.html.unescape(g(r"<description>([^<]*)"))[:260]})
    total = re.search(r"<opensearch:totalResults>(\d+)", xml)
    return {"total": int(total.group(1)) if total else len(items), "items": items}

# ---------------- imaging (ComfyUI + Z-Image Turbo) ----------------
def free_brains_and_wait():
    """Images and the brain can't share 8 GB: unload chat models and wait until they're really gone."""
    for base in BRAINS:
        try:
            for m in http(base + "/api/ps", timeout=3).get("models", []):
                http(base + "/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30)
        except Exception: pass
    for _ in range(30):
        try:
            if not any(http(b + "/api/ps", timeout=3).get("models") for b in BRAINS): break
        except Exception: break
        time.sleep(0.5)
    time.sleep(1)

def workflow(prompt, w, h, seed, steps):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_nvfp4.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
              "seed": seed, "steps": steps, "cfg": 1, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "joshua/joshua"}},
    }

ENHANCE_SYS = """You write prompts for an image generator. The subject is always Joshua: a sleek male humanoid android AI
(glossy white synthetic skin, chrome mechanical details, glowing cyan eyes) who lives inside a gaming PC.
Given the user's idea and the base description, write ONE vivid image prompt (60-120 words): subject, expression, pose,
setting, lighting, camera or art medium, mood. Keep Joshua's look consistent. Output only the prompt text."""

def enhance(idea, base):
    r = http(BRAINS[0] + "/api/chat", {"model": HWP["model"], "stream": False, "think": False, "keep_alive": 0,
              "options": hardware.ollama_options(HWP, temperature=0.9, num_ctx=4096),
              "messages": [{"role": "system", "content": ENHANCE_SYS}, {"role": "user", "content": f"Base: {base}\n\nIdea: {idea or 'surprise me'}"}]}, timeout=300)
    return r["message"]["content"].strip().strip('"')

def gallery():
    if not os.path.isdir(OUTPUT): return []
    files = sorted((f for f in os.listdir(OUTPUT) if f.endswith(".png")), key=lambda f: os.path.getmtime(os.path.join(OUTPUT, f)), reverse=True)
    out = []
    for f in files[:300]:
        meta = {}
        try: meta = json.load(open(os.path.join(OUTPUT, f + ".json")))
        except Exception: pass
        out.append({"file": f, **meta})
    return out

# ---------------- HTTP ----------------
STATIC = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml"}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); path = u.path
        try:
            if path == "/": path = "/index.html"
            if re.fullmatch(r"/(index\.html|hud\.css|hud\.js|fonts/[\w.-]+\.(woff2|css))", path):
                fp = os.path.join(HERE, path.lstrip("/"))
                return self.send(200, open(fp, "rb").read(), STATIC.get(os.path.splitext(fp)[1], "application/octet-stream"))
            if path == "/avatar.png":
                p = os.path.join(JDIR, "avatar.png")
                return self.send(200, open(p, "rb").read(), "image/png") if os.path.exists(p) else self.send(404, "none", "text/plain")
            m = re.fullmatch(r"/img/([\w.\-]+\.png)", path)
            if m:
                p = os.path.join(OUTPUT, m.group(1))
                return self.send(200, open(p, "rb").read(), "image/png") if os.path.exists(p) else self.send(404, "none", "text/plain")
            if path == "/api/sleep":  # used only by screenshot tests to delay the page's load event
                time.sleep(min(10, float(q.get("s", ["1"])[0]))); return self.send(200, b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "image/gif")
            if path == "/api/status": return self.send(200, status())
            if path == "/api/memory": return self.send(200, memory())
            if path == "/api/books": return self.send(200, books())
            if path == "/api/search": return self.send(200, search(q.get("q", [""])[0], q.get("book", [""])[0]))
            if path == "/api/history": return self.send(200, [m for m in convo.messages if m["role"] in ("user", "assistant") and m.get("content")])
            if path == "/api/img/gallery": return self.send(200, gallery())
            if path == "/api/img/status":
                pid = q.get("id", [""])[0]
                h = http(COMFY + "/history/" + urllib.parse.quote(pid), timeout=5).get(pid)
                if h:
                    st = h.get("status", {})
                    if st.get("status_str") == "error":
                        msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m[0] == "execution_error"]
                        return self.send(200, {"state": "error", "error": " ".join(msgs) or "generation failed"})
                    return self.send(200, {"state": "done", "images": [i["filename"] for o in h.get("outputs", {}).values() for i in o.get("images", [])]})
                running = any(item[1] == pid for item in http(COMFY + "/queue", timeout=5).get("queue_running", []))
                return self.send(200, {"state": "running" if running else "queued"})
        except Exception as e:
            return self.send(500, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        global last_activity
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/stt":
                return self.send(200, {"text": stt(self.body())})
            b = json.loads(self.body() or b"{}")
            if path == "/api/chat": return self.chat(b)
            if path == "/api/tts":
                wav = tts(b.get("text", ""))
                return self.send(200, wav, "audio/wav") if wav else self.send(204, b"", "audio/wav")
            if path == "/api/confirm":
                c = pending_confirms.get(b.get("id"))
                if c: c["ok"] = bool(b.get("ok")); c["event"].set()
                return self.send(200, {"ok": True})
            if path == "/api/stop":
                if current["stop"]: current["stop"].set()
                for c in list(pending_confirms.values()): c["event"].set()
                return self.send(200, {"ok": True})
            if path == "/api/new":
                filed = convo.session.turns > 0
                convo.file(wait=False); last_activity = time.time()
                return self.send(200, {"ok": True, "filed": filed})
            if path == "/api/forget":
                forget(b.get("kind"), int(b.get("index", -1))); return self.send(200, memory())
            if path == "/api/img/start":
                if not up(COMFY + "/system_stats"): subprocess.Popen(["systemctl", "--user", "start", "joshua-comfyui"])
                return self.send(200, {"ok": True})
            if path == "/api/img/generate":
                prompt = (b.get("prompt") or "").strip()
                if not prompt: return self.send(400, {"error": "empty prompt"})
                if not up(COMFY + "/system_stats"): return self.send(503, {"error": "imaging engine is starting — try again in a few seconds"})
                w, h = int(b.get("width", 1024)), int(b.get("height", 1024))
                seed = int(b.get("seed") or random.randint(1, 2**48))
                if convo.lock.locked(): return self.send(409, {"error": "Joshua is busy thinking — paint when he's done"})
                free_brains_and_wait()
                r = http(COMFY + "/prompt", {"prompt": workflow(prompt, w, h, seed, max(4, min(20, int(b.get("steps", 8))))), "client_id": "hud"})
                return self.send(200, {"id": r["prompt_id"], "seed": seed})
            if path == "/api/img/meta":
                f = os.path.basename(b.get("file", ""))
                if f.endswith(".png") and os.path.exists(os.path.join(OUTPUT, f)):
                    json.dump({k: b.get(k) for k in ("prompt", "seed", "width", "height")}, open(os.path.join(OUTPUT, f + ".json"), "w"))
                return self.send(200, {"ok": True})
            if path == "/api/img/enhance": return self.send(200, {"prompt": enhance(b.get("idea", ""), b.get("base", ""))})
            if path == "/api/img/avatar":
                f = os.path.basename(b.get("file", ""))
                shutil.copy(os.path.join(OUTPUT, f), os.path.join(JDIR, "avatar.png")); return self.send(200, {"ok": True})
            if path == "/api/img/delete":
                f = os.path.basename(b.get("file", ""))
                for p in (os.path.join(OUTPUT, f), os.path.join(OUTPUT, f + ".json")):
                    if f.endswith(".png") and os.path.exists(p): os.remove(p)
                return self.send(200, {"ok": True})
        except Exception as e:
            return self.send(500, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def chat(self, b):
        """Stream one turn as newline-delimited JSON events."""
        global last_activity
        text = (b.get("text") or "").strip()
        if not text: return self.send(400, {"error": "empty"})
        if convo.lock.locked(): return self.send(409, {"error": "busy"})
        last_activity = time.time()
        q, stop = queue.Queue(), threading.Event()
        current.update(q=q, stop=stop)
        def run():
            try: convo.turn(text, q.put, force_think=bool(b.get("think")), stop=stop)
            except Exception as e: q.put({"type": "error", "text": str(e)}); q.put({"type": "done", "text": ""})
        threading.Thread(target=run, daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson"); self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no"); self.end_headers()
        try:
            while True:
                try: ev = q.get(timeout=15)
                except queue.Empty: ev = {"type": "ping"}
                self.wfile.write((json.dumps(ev) + "\n").encode()); self.wfile.flush()
                if ev["type"] == "done": break
        except (BrokenPipeError, ConnectionResetError):
            stop.set()
        finally:
            current.update(q=None, stop=None); last_activity = time.time()

if __name__ == "__main__":
    print(f"Joshua HUD → http://127.0.0.1:{PORT}   ({HWP['summary']})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
