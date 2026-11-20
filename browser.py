"""Joshua's hands on the user's real Firefox (their logins, their bookmarks) via Firefox's
built-in Marionette remote-control port. No passwords are ever stored or typed by Joshua:
he uses the sessions Firefox already has.

Firefox must be started with --marionette (the desktop launcher override does that).
"""
import json, os, re, socket, sqlite3, shutil, subprocess, time, glob, tempfile, urllib.parse

PORT = int(os.environ.get("JOSHUA_MARIONETTE_PORT", "2828"))
HOME = os.path.expanduser("~")
PROFILE = os.environ.get("JOSHUA_FIREFOX_PROFILE") or next(iter(sorted(
    glob.glob(f"{HOME}/snap/firefox/common/.mozilla/firefox/*.default*") + glob.glob(f"{HOME}/.mozilla/firefox/*.default*"),
    key=lambda p: (not p.endswith(".default-release"), p))), "")

# password vaults: Joshua never opens these, full stop
FORBIDDEN = re.compile(r"(?i)pass\.proton\.me|bitwarden|1password|lastpass|dashlane|keepass|about:logins|vault\.")

# clicking a button with one of these words changes something in the world -> ask first
RISKY = re.compile(r"(?i)\b(send|post|publish|tweet|share|reply|forward|buy|order|checkout|check out|pay|purchase|place your order|"
                   r"subscribe|unsubscribe|delete|remove|trash|archive|confirm|submit|transfer|sign out|log ?out)\b")


class Marionette:
    def __init__(self, port=PORT, timeout=30):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = b""; self.id = 0
        self._read()  # server hello
        self.cmd("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"pageLoadStrategy": "normal"}}})

    def _read(self):
        while b":" not in self.buf: self.buf += self.s.recv(65536)
        n, _, rest = self.buf.partition(b":"); n = int(n)
        while len(rest) < n: rest += self.s.recv(65536)
        self.buf = rest[n:]
        return json.loads(rest[:n])

    def cmd(self, name, params=None):
        self.id += 1
        data = json.dumps([0, self.id, name, params or {}]).encode()
        self.s.sendall(str(len(data)).encode() + b":" + data)
        while True:
            msg = self._read()
            if msg[0] == 1 and msg[1] == self.id: break
        if msg[2]: raise RuntimeError(f"{msg[2].get('error')}: {msg[2].get('message', '')[:200]}")
        r = msg[3]
        return r.get("value", r) if isinstance(r, dict) else r

    def js(self, script, *args):
        return self.cmd("WebDriver:ExecuteScript", {"script": script, "args": list(args)})

    def close(self):
        try: self.cmd("WebDriver:DeleteSession")
        except Exception: pass
        try: self.s.close()
        except Exception: pass


def _port_open():
    try: socket.create_connection(("127.0.0.1", PORT), timeout=1).close(); return True
    except OSError: return False

def _firefox_running():
    return subprocess.run(["pgrep", "-f", "firefox/firefox( |$)"], capture_output=True).returncode == 0 or \
           subprocess.run(["pgrep", "-x", "firefox"], capture_output=True).returncode == 0

def connect():
    """Return a Marionette session, starting Firefox if it's closed. Raises with a plain explanation if it can't."""
    if not _port_open():
        if _firefox_running():
            raise RuntimeError("FIREFOX NOT REMOTE-CONTROLLABLE: Firefox is open but was started without remote control. "
                               "Ask the user to close Firefox (or offer to do it with browser_restart); it reopens with their tabs.")
        subprocess.Popen(["firefox", "--marionette"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(40):
            time.sleep(0.5)
            if _port_open(): break
        else: raise RuntimeError("Started Firefox but its remote-control port never opened.")
        time.sleep(1.5)
    return Marionette()


def restart_firefox():
    """Close Firefox politely and reopen it with remote control. Session restore brings tabs back."""
    subprocess.run(["pkill", "-TERM", "-f", "firefox/firefox"], capture_output=True)
    for _ in range(30):
        time.sleep(0.5)
        if not _firefox_running(): break
    subprocess.Popen(["firefox", "--marionette"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        time.sleep(0.5)
        if _port_open(): return "Firefox restarted with remote control. Tabs restore themselves."
    return "Restarted Firefox, but remote control didn't come up."


# ---------- bookmarks ----------
def bookmarks(folder_only_toolbar=False):
    db = os.path.join(PROFILE, "places.sqlite")
    if not os.path.exists(db): return []
    tmp = tempfile.mkdtemp()
    try:
        for ext in ("", "-wal"):
            if os.path.exists(db + ext): shutil.copy(db + ext, os.path.join(tmp, "places.sqlite" + ext))
        con = sqlite3.connect(os.path.join(tmp, "places.sqlite"))
        rows = con.execute("""SELECT b.title, p.url, parent.guid FROM moz_bookmarks b JOIN moz_places p ON p.id=b.fk
                              JOIN moz_bookmarks parent ON parent.id=b.parent WHERE b.type=1 AND p.url LIKE 'http%'""").fetchall()
        con.close()
    finally: shutil.rmtree(tmp, ignore_errors=True)
    out = [(t or urllib.parse.urlparse(u).netloc, u, g == "toolbar_____") for t, u, g in rows]
    if folder_only_toolbar: out = [o for o in out if o[2]]
    return sorted(out, key=lambda o: (not o[2], o[0].lower()))

def find_bookmark(name):
    n = re.sub(r"\s+", " ", name.lower().strip())
    bms = [(t.lower().strip(), u) for t, u, _ in bookmarks() if not FORBIDDEN.search(u)]
    for t, u in bms:                                   # exact title
        if t == n: return u
    for t, u in bms:                                   # whole words, e.g. "proton mail" / "mail" in "Proton Mail"
        if re.search(rf"\b{re.escape(n)}\b", t): return u
    for t, u in bms:                                   # domain label, e.g. "netflix" -> www.netflix.com
        if n.replace(" ", "") in urllib.parse.urlparse(u).netloc.lower().split("."): return u
    return None


# ---------- page reading ----------
READ_JS = r"""
const max = arguments[0];
function vis(e){const r=e.getBoundingClientRect(), s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';}
const main = document.querySelector('main,[role=main],#content,#main') || document.body;
let text = (main.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
if (text.length < 300) text = (document.body.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
const seen = new Set(), controls = [];
for (const e of document.querySelectorAll('a[href],button,[role=button],input[type=submit],[role=link],[role=tab],[role=menuitem]')) {
  if (!vis(e)) continue;
  const t = (e.innerText || e.value || e.getAttribute('aria-label') || e.title || '').trim().replace(/\s+/g,' ').slice(0,80);
  if (!t || seen.has(t)) continue; seen.add(t); controls.push(t);
  if (controls.length >= 60) break;
}
const fields = [];
for (const e of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable=true]')) {
  if (!vis(e)) continue;
  const lab = e.getAttribute('aria-label') || e.placeholder || e.name || e.id || e.type || 'field';
  fields.push(lab.slice(0,50));
  if (fields.length >= 15) break;
}
return {title: document.title, url: location.href, text: text.slice(0, max), total: text.length, controls, fields};
"""

def _wait_loaded(m, secs=12):
    end = time.time() + secs
    while time.time() < end:
        try:
            if m.js("return document.readyState") == "complete": break
        except Exception: pass
        time.sleep(0.4)
    time.sleep(1.2)  # let JS apps (mail, YouTube) paint

def _summary(m, max_chars=4000):
    r = m.js(READ_JS, max_chars)
    s = f"PAGE: {r['title']}\nURL: {r['url']}\n\n{r['text']}"
    if r["total"] > max_chars: s += f"\n[...{r['total'] - max_chars} more chars; browser_read with more=true]"
    if r["controls"]: s += "\n\nCLICKABLE: " + " | ".join(r["controls"])
    if r["fields"]: s += "\nTYPE-INTO FIELDS: " + " | ".join(r["fields"])
    return s

def _go(m, url, new_tab=True):
    if new_tab:
        h = m.cmd("WebDriver:NewWindow", {"type": "tab", "focus": True})
        m.cmd("WebDriver:SwitchToWindow", {"handle": h["handle"] if isinstance(h, dict) else h, "focus": True})
    else:
        _to_front_tab(m)
    m.cmd("WebDriver:Navigate", {"url": url})
    _wait_loaded(m)

def _to_front_tab(m):
    """Point the session at the tab the user is looking at (the most recently selected one)."""
    hs = m.cmd("WebDriver:GetWindowHandles")
    for h in reversed(hs):
        m.cmd("WebDriver:SwitchToWindow", {"handle": h, "focus": False})
        try:
            if m.js("return document.visibilityState") == "visible": return h
        except Exception: continue
    m.cmd("WebDriver:SwitchToWindow", {"handle": hs[-1], "focus": True})
    return hs[-1]

def resolve(target):
    """Bookmark name, bare site name, or URL -> URL."""
    t = target.strip()
    if re.match(r"https?://", t): return t
    exact = [u for title, u, _ in bookmarks() if title.lower().strip() == t.lower()]
    if exact and not FORBIDDEN.search(exact[0]): return exact[0]
    alias = {"proton": "proton mail", "protonmail": "proton mail", "email": "proton mail", "my email": "proton mail", "twitter": "x"}
    bm = find_bookmark(alias.get(t.lower(), t))
    if bm: return bm
    if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", t): return "https://" + t
    known = {"youtube": "https://www.youtube.com", "netflix": "https://www.netflix.com/browse", "amazon": "https://www.amazon.com",
             "proton": "https://mail.proton.me", "proton mail": "https://mail.proton.me", "protonmail": "https://mail.proton.me",
             "gmail": "https://mail.google.com", "facebook": "https://www.facebook.com", "reddit": "https://www.reddit.com",
             "x": "https://x.com", "twitter": "https://x.com", "instagram": "https://www.instagram.com", "github": "https://github.com"}
    return known.get(t.lower(), "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(t))


def with_browser(fn):
    try: m = connect()
    except Exception as e: return str(e)
    try: return fn(m)
    except Exception as e: return f"Browser error: {e}"
    finally: m.close()


def open_page(target):
    if FORBIDDEN.search(resolve(target)) or FORBIDDEN.search(target):
        return "REFUSED: that's the user's password vault. Joshua never opens password managers."
    return with_browser(lambda m: (_go(m, resolve(target)), _summary(m))[1])

def read_page(more=False):
    return with_browser(lambda m: (_to_front_tab(m), _summary(m, 12000 if more else 4000))[1])

CLICK_JS = r"""
const want = arguments[0].toLowerCase().trim();
function vis(e){const r=e.getBoundingClientRect();return r.width>0&&r.height>0;}
const els = [...document.querySelectorAll('a[href],button,[role=button],input[type=submit],[role=link],[role=tab],[role=menuitem],[role=option],[tabindex]')].filter(vis);
const label = e => (e.innerText || e.value || e.getAttribute('aria-label') || e.title || '').trim().replace(/\s+/g,' ');
let hit = els.find(e => label(e).toLowerCase() === want) || els.find(e => label(e).toLowerCase().includes(want));
if (!hit) return null;
hit.scrollIntoView({block:'center'}); hit.click(); return label(hit).slice(0,100);
"""

def click(text, confirm=None):
    if RISKY.search(text) and confirm and not confirm(f"click '{text}' in Firefox"):
        return "DENIED BY USER. Nothing was clicked. Tell the user plainly you did not do it."
    def go(m):
        _to_front_tab(m)
        hit = m.js(CLICK_JS, text)
        if not hit: return f"No button or link matching '{text}' on this page. Use browser_read to see what's clickable."
        _wait_loaded(m, 8)
        return f"Clicked '{hit}'.\n\n" + _summary(m)
    return with_browser(go)

TYPE_JS = r"""
const want = arguments[0].toLowerCase(), val = arguments[1], enter = arguments[2];
function vis(e){const r=e.getBoundingClientRect();return r.width>0&&r.height>0;}
const els = [...document.querySelectorAll('input:not([type=hidden]),textarea,[contenteditable=true]')].filter(vis);
const lab = e => [e.getAttribute('aria-label'), e.placeholder, e.name, e.id, e.type, e.title].filter(Boolean).join(' ').toLowerCase();
let el = want ? els.find(e => lab(e).includes(want)) : null;
if (!el) el = els.find(e => /search|query|q\b/.test(lab(e))) || (document.activeElement && els.includes(document.activeElement) ? document.activeElement : els[0]);
if (!el) return null;
el.focus();
if (el.isContentEditable) { document.execCommand('selectAll'); document.execCommand('insertText', false, val); }
else {
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
  el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
}
if (enter) {
  for (const t of ['keydown','keypress','keyup']) el.dispatchEvent(new KeyboardEvent(t, {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
  if (el.form) { if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit(); }
}
return lab(el).slice(0,60) || 'field';
"""

def type_text(field, text, enter=False, confirm=None):
    if enter and confirm and not re.search(r"(?i)search|query|find|^q$|^$", field or "") and \
       not confirm(f"type '{text[:80]}' into '{field}' and press Enter (that may send/post it)"):
        return "DENIED BY USER. Nothing was typed or sent. Tell the user plainly you did not do it."
    if re.search(r"(?i)pass(word)?|card|cvv|ssn", field or ""):
        return "Refused: Joshua never types passwords or card numbers. The user's Firefox is already logged in; if a site asks to log in, tell the user to do it once themselves."
    def go(m):
        _to_front_tab(m)
        hit = m.js(TYPE_JS, field or "", text, bool(enter))
        if not hit: return "No text field on this page."
        if enter: _wait_loaded(m, 8)
        return f"Typed into '{hit}'" + (" and pressed Enter." if enter else ".") + ("\n\n" + _summary(m) if enter else "")
    return with_browser(go)


# ---------- site shortcuts ----------
def youtube_play(query):
    def go(m):
        _go(m, "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
        for _ in range(20):
            href = m.js("""const a=[...document.querySelectorAll('ytd-video-renderer a#video-title, ytd-video-renderer a#thumbnail, a#video-title')]
                           .find(a=>a.href && a.href.includes('/watch')); return a ? a.href : null;""")
            if href: break
            time.sleep(0.5)
        else: return "YouTube loaded but I couldn't find a video in the results.\n\n" + _summary(m, 1500)
        m.cmd("WebDriver:Navigate", {"url": href}); _wait_loaded(m, 10)
        m.js("const v=document.querySelector('video'); if(v){v.muted=false; v.play().catch(()=>{});}")
        return f"Playing: {m.js('return document.title')}  ({href})"
    return with_browser(go)

def web_search(query):
    """Search through the user's own Firefox, in a tab that closes afterwards (a real browser doesn't get the bot captcha)."""
    def go(m):
        orig = m.cmd("WebDriver:GetWindowHandle")
        h = m.cmd("WebDriver:NewWindow", {"type": "tab", "focus": False})
        m.cmd("WebDriver:SwitchToWindow", {"handle": h["handle"] if isinstance(h, dict) else h, "focus": False})
        try:
            m.cmd("WebDriver:Navigate", {"url": "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)})
            _wait_loaded(m, 10)
            res = m.js("""return [...document.querySelectorAll('.result')].slice(0,8).map(r=>{
                const a=r.querySelector('.result__a'), s=r.querySelector('.result__snippet'); if(!a) return null;
                let u=a.href; try{const q=new URL(u).searchParams.get('uddg'); if(q) u=q;}catch(e){}
                return {t:a.innerText.trim(), u, s: s? s.innerText.trim().slice(0,250):''};}).filter(x=>x && !x.u.includes('y.js'));""")
            if not res:
                return "SEARCH BLOCKED even in Firefox. Tell the user; answer from the bookshelf, or offer open_url to show the search."
            return "Web results:\n" + "\n".join(f"- {r['t']}\n  url: {r['u']}\n  {r['s']}" for r in res) + "\n\nUse web_read on a url to read it."
        finally:
            try: m.cmd("WebDriver:CloseWindow")
            except Exception: pass
            try: m.cmd("WebDriver:SwitchToWindow", {"handle": orig, "focus": True})
            except Exception: pass
    return with_browser(go)
