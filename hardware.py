"""Joshua sizes up whatever machine he wakes up on and picks settings to match.

  GPU with  >= 7.5 GB  ->  "full":    9B brain, deep thinking on hard questions, picture studio
  GPU with 3.5-7.5 GB  ->  "lighter": 4B brain, deep thinking on hard questions
  no usable GPU        ->  "cpu":     4B brain, smaller memory window, deep thinking only when asked

Override: config.json  {"model": "qwen3.5:9b"}  (or "auto", the default).
Testing:  JOSHUA_FAKE_HW="gpu_mb=0,ram_gb=8,cores=4"  pretends to be another machine
          (and forces the brain onto the CPU so the slowdown is real).
"""
import json, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
_cached = None

def _sh(cmd):
    try: return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception: return ""

def _hardware():
    hw = {"gpu_name": "", "gpu_mb": 0, "nvidia_present": False, "nvidia_driver": False, "ram_gb": 0.0, "cores": os.cpu_count() or 2}
    q = _sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1")
    if q and "," in q:
        name, mb = [x.strip() for x in q.split(",", 1)]
        hw.update(gpu_name=name, gpu_mb=int(float(mb)), nvidia_driver=True, nvidia_present=True)
    elif re.search(r"(VGA|3D).*NVIDIA", _sh("lspci 2>/dev/null"), re.I):
        hw.update(nvidia_present=True, gpu_name=re.sub(r".*NVIDIA Corporation\s*", "", re.search(r"(VGA|3D).*NVIDIA.*", _sh("lspci"), re.I).group(0)))
    m = re.search(r"MemTotal:\s+(\d+)", open("/proc/meminfo").read())
    hw["ram_gb"] = round(int(m.group(1)) / 1048576, 1) if m else 0.0
    fake = os.environ.get("JOSHUA_FAKE_HW")
    if fake:
        for kv in fake.split(","):
            k, _, v = kv.partition("=")
            if k.strip() in hw: hw[k.strip()] = type(hw[k.strip()])(float(v)) if k.strip() != "gpu_name" else v
        if hw["gpu_mb"] == 0:
            hw.update(gpu_name="", nvidia_driver=False)
            if "nvidia_present" not in fake: hw["nvidia_present"] = False
        hw["fake"] = True
    return hw

def detect():
    global _cached
    if _cached: return _cached
    hw = _hardware()
    cfg = {}
    try: cfg = json.load(open(os.path.join(HERE, "config.json")))
    except Exception: pass
    notes = []
    if hw["gpu_mb"] >= 7500:
        p = dict(mode="full", model="qwen3.5:9b", num_ctx=16384, auto_think=True, num_gpu=None)
    elif hw["gpu_mb"] >= 3500:
        p = dict(mode="lighter", model="qwen3.5:4b", num_ctx=16384, auto_think=True, num_gpu=None)
        notes.append(f"Only {round(hw['gpu_mb'] / 1024)} GB of graphics memory, so I'm running my smaller brain.")
    else:
        p = dict(mode="cpu", model="qwen3.5:4b", num_ctx=8192, auto_think=False, num_gpu=0 if hw.get("fake") else None)
        notes.append("No usable graphics card, so I'm thinking on the CPU with my smaller brain. Expect me to be slow. Say 'think hard' if you want me to really reason something out.")
        if hw["nvidia_present"] and not hw["nvidia_driver"]:
            notes.append("There IS an NVIDIA card in here but its driver isn't installed. Run the installer in 'INSTALL THESE FIRST' and reboot, and I'll get much faster.")
    if hw["ram_gb"] and hw["ram_gb"] < 7:
        notes.append(f"Only {hw['ram_gb']:.0f} GB of RAM. I may struggle; close other programs.")
        p["num_ctx"] = min(p["num_ctx"], 4096)
    if cfg.get("model") and cfg["model"] != "auto":
        p["model"] = cfg["model"]; notes.append(f"Model pinned in config.json: {cfg['model']}.")
    # picture studio needs a big NVIDIA card; its fast 4-bit model is built for RTX 50-series
    p["studio_ok"] = hw["gpu_mb"] >= 7500
    if not p["studio_ok"]: p["studio_note"] = "The picture studio needs an NVIDIA card with 8 GB or more; it won't work well on this machine."
    elif not re.search(r"RTX 50\d\d|RTX PRO|B\d{3}", hw["gpu_name"]): p["studio_note"] = "This card isn't RTX 50-series; pictures will be slower or may fail with the 4-bit image model."
    p.update(hw=hw, notes=notes, whisper_threads=max(2, min(8, hw["cores"])))
    gpu = f"{hw['gpu_name']} ({round(hw['gpu_mb'] / 1024)} GB)" if hw["gpu_mb"] else ("NVIDIA card without driver" if hw["nvidia_present"] else "no NVIDIA GPU")
    p["summary"] = f"{p['mode']} mode — brain {p['model']}, {gpu}, {hw['ram_gb']:.0f} GB RAM, {hw['cores']} CPU threads"
    _cached = p
    return p

def ollama_options(p, **extra):
    o = {"num_ctx": p["num_ctx"], **extra}
    if p.get("num_gpu") is not None: o["num_gpu"] = p["num_gpu"]
    return o

if __name__ == "__main__":
    p = detect()
    print(p["summary"])
    for n in p["notes"] + ([p["studio_note"]] if p.get("studio_note") else []): print(" -", n)
