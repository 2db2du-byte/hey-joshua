"""Joshua's book shopping: browse the official Kiwix catalog and add books (ZIM files) to his own shelf.
Downloads run in the background (aria2c + .meta4 mirrors, checksum-verified), then get shelved with kiwix-manage.
Run directly (python shelf.py <meta4-url>) to do one download in the foreground; brain.py starts it detached.
"""
import html, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SHELF = os.path.join(HERE, "bookshelf")
ZIM = os.path.join(SHELF, "zim")
LIB = os.path.join(SHELF, "library.xml")
JOBS = os.path.join(SHELF, "downloads.json")
CATALOG = "https://library.kiwix.org/catalog/v2/entries"
KEEP_FREE_GB = 50   # never fill the drive past this


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "joshua-bookshelf/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode("utf-8", "replace")

def _entries(xml):
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        g = lambda tag: html.unescape((re.search(rf"<{tag}>([^<]*)</{tag}>", e) or [None, ""])[1]).strip()
        acq = re.search(r'rel="http://opds-spec.org/acquisition/open-access"[^>]*href="([^"]+)"[^>]*length="(\d+)"', e)
        if not acq: continue
        out.append({"title": g("title"), "summary": g("summary"), "name": g("name"), "flavour": g("flavour"),
                    "lang": g("language"), "articles": g("articleCount"), "url": acq.group(1), "gb": int(acq.group(2)) / 1e9,
                    "file": os.path.basename(acq.group(1)).replace(".meta4", "")})
    return out

def search(query, lang="eng"):
    try: xml = _fetch(CATALOG + "?" + urllib.parse.urlencode({"q": query, "lang": lang, "count": 40}))
    except Exception as e: return f"Can't reach the Kiwix catalog (internet down?): {e}"
    ents = _entries(xml)
    if not ents: return f"The catalog has nothing for '{query}'. Try a broader word (e.g. 'history', 'science', 'books')."
    have = owned()
    lines = []
    for x in sorted(ents, key=lambda x: (x["name"], -x["gb"]))[:25]:
        mark = "  [ALREADY ON SHELF]" if x["file"] in have else ""
        flav = f" ({x['flavour']})" if x["flavour"] else ""
        lines.append(f"- {x['title']}{flav}: {x['summary']} | {x['gb']:.1f} GB | {x['articles']} articles{mark}\n  get: {x['url']}")
    return (f"Kiwix catalog, '{query}':\n" + "\n".join(lines) +
            "\n\nFlavours: maxi = full with pictures, nopic = text only (much smaller), mini = intros only. "
            "Use shelf_add with a 'get' url to download one.")

def owned():
    try: return set(f for f in os.listdir(ZIM) if f.endswith(".zim"))
    except FileNotFoundError: return set()

def free_gb():
    return shutil.disk_usage(ZIM if os.path.isdir(ZIM) else HERE).free / 1e9

def _jobs():
    try: return json.load(open(JOBS))
    except Exception: return {}

def _save_jobs(j):
    tmp = JOBS + ".tmp"; json.dump(j, open(tmp, "w"), indent=1); os.replace(tmp, JOBS)

def _size_of(meta4_url):
    try:
        xml = _fetch(meta4_url); m = re.search(r"<size>(\d+)</size>", xml)
        return int(m.group(1)) / 1e9 if m else None
    except Exception: return None

def start(meta4_url, confirm=None):
    """Kick off a background download. Returns a sentence for Joshua."""
    if not re.match(r"https://([\w.-]+\.)?kiwix\.org/.+\.zim\.meta4$", meta4_url):
        return "Only Kiwix catalog downloads (a 'get' url ending .zim.meta4) can be shelved."
    f = os.path.basename(meta4_url).replace(".meta4", "")
    if f in owned(): return f"{f} is already on the shelf."
    j = _jobs()
    if j.get(f, {}).get("state") == "downloading": return f"{f} is already downloading. shelf_status shows progress."
    gb = _size_of(meta4_url)
    if gb is None: return "Couldn't reach the catalog to check the size; internet down?"
    if free_gb() - gb < KEEP_FREE_GB:
        return f"Not enough room: {f} is {gb:.1f} GB and only {free_gb():.0f} GB is free (I keep {KEEP_FREE_GB} GB spare). Tell the user."
    if gb > 5 and confirm and not confirm(f"download {f} ({gb:.1f} GB) to the bookshelf"):
        return "DENIED BY USER. Nothing was downloaded. Tell the user plainly."
    j[f] = {"state": "downloading", "gb": round(gb, 2), "url": meta4_url, "started": time.time()}; _save_jobs(j)
    log = open(os.path.join(SHELF, f"{f}.log"), "w")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), meta4_url], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    eta = gb * 1000 / 40 / 60  # ~40 MB/s seen with aria2 on this line
    return f"Downloading {f} ({gb:.1f} GB) in the background, roughly {max(1, round(eta))} min. It goes on the shelf by itself when done."

def status():
    j = _jobs()
    if not j: return "No downloads, now or before."
    out = []
    for f, x in sorted(j.items(), key=lambda kv: -kv[1].get("started", 0))[:12]:
        if x["state"] == "downloading":
            part = os.path.join(ZIM, f)
            done = os.path.getsize(part) / 1e9 if os.path.exists(part) else 0
            out.append(f"- {f}: downloading, {done:.1f} of {x['gb']:.1f} GB")
        else: out.append(f"- {f}: {x['state']}")
    return "Bookshelf downloads:\n" + "\n".join(out) + f"\nFree space: {free_gb():.0f} GB"

def _run(meta4_url):
    """Foreground worker (the detached child)."""
    f = os.path.basename(meta4_url).replace(".meta4", ""); os.makedirs(ZIM, exist_ok=True)
    ok = False
    for attempt in range(5):
        r = subprocess.run(["aria2c", "-q", "-x", "8", "-s", "8", "-j", "1", "--file-allocation=none", "--follow-metalink=mem",
                            "--check-integrity=true", "--console-log-level=warn", "--summary-interval=0", "-d", ZIM, meta4_url])
        if r.returncode == 0 and os.path.exists(os.path.join(ZIM, f)): ok = True; break
        print(f"retry {attempt + 1}", flush=True); time.sleep(20)
    j = _jobs()
    if not ok:
        j[f] = {**j.get(f, {}), "state": "FAILED (say 'try again' to retry)"}; _save_jobs(j)
        try:
            import world; world.notify(f"Book download failed: {f}", "Joshua's bookshelf", "default", "warning")
        except Exception: pass
        return
    open(os.path.join(ZIM, f + ".ok"), "w").close()
    subprocess.run(["kiwix-manage", LIB, "add", os.path.join(ZIM, f)])
    subprocess.run(["systemctl", "--user", "restart", "joshua-library"])
    j = _jobs(); j[f] = {**j.get(f, {}), "state": "on the shelf", "finished": time.time()}; _save_jobs(j)
    print("shelved", f, flush=True)
    try:
        import world; world.notify(f"New book on my shelf: {f.replace('.zim', '')}", "Joshua's bookshelf", "low", "books")
    except Exception: pass

if __name__ == "__main__":
    _run(sys.argv[1])
