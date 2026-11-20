"use strict";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("hud_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("hud_" + k, JSON.stringify(v)); } catch {} },
};
async function api(path, body, opts = {}) {
  const r = await fetch(path, body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), ...opts } : opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).error || m; } catch {} throw new Error(m); }
  return r.json();
}

/* ======================= state ======================= */
const S = { busy: false, speaking: false, listening: false, voice: store.get("voice", true), hands: store.get("hands", false),
            deep: false, status: null, memTab: "facts", mem: null, face: "" };
function refreshState() {
  const st = S.listening ? "listening" : S.speaking ? "speaking" : S.busy ? "thinking" : "idle";
  document.body.className = "st-" + st;
  $("#stateLabel").textContent = { idle: "STANDBY", listening: "LISTENING", thinking: "PROCESSING", speaking: "SPEAKING" }[st];
  $("#send").classList.toggle("hidden", S.busy);
  $("#stop").classList.toggle("hidden", !S.busy && !S.speaking);
}

/* ======================= audio ======================= */
let AC = null, outAnalyser = null, micAnalyser = null, curSource = null;
function audio() {
  if (!AC) {
    AC = new (window.AudioContext || window.webkitAudioContext)();
    outAnalyser = AC.createAnalyser(); outAnalyser.fftSize = 256; outAnalyser.smoothingTimeConstant = .7;
    outAnalyser.connect(AC.destination);
  }
  if (AC.state === "suspended") AC.resume();
  return AC;
}
const speech = { q: [], playing: false };
function speak(text) {
  if (!S.voice || !text.trim()) return;
  const job = { text, buf: fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
      .then(r => r.status === 200 ? r.arrayBuffer() : null).then(b => b ? audio().decodeAudioData(b) : null).catch(() => null) };
  speech.q.push(job); pump();
}
async function pump() {
  if (speech.playing) return;
  speech.playing = true;
  while (speech.q.length) {
    const job = speech.q.shift(); const buf = await job.buf;
    if (!buf) continue;
    S.speaking = true; refreshState(); caption(job.text);
    await new Promise(res => {
      const src = audio().createBufferSource(); src.buffer = buf; src.connect(outAnalyser);
      curSource = src; src.onended = res; src.start();
    });
  }
  curSource = null; speech.playing = false; S.speaking = false; refreshState();
  setTimeout(() => { if (!speech.playing) caption(""); }, 2500);
  if (!S.busy && S.hands && !S.listening) setTimeout(() => { if (!S.busy && !S.speaking && !S.listening) listen(); }, 400);
}
function hush() { speech.q.length = 0; try { curSource && curSource.stop(); } catch {} }
let capTimer = null;
function caption(t) {
  const el = $("#caption"); clearInterval(capTimer); el.textContent = "";
  if (!t) return;
  let i = 0; capTimer = setInterval(() => { el.textContent = t.slice(0, i += 2); if (i >= t.length) clearInterval(capTimer); }, 18);
}

/* ======================= waveform around the face ======================= */
const cv = $("#wave"), cx = cv.getContext("2d"), data = new Uint8Array(128);
let lvlSmooth = 0, tick = 0;
function draw() {
  requestAnimationFrame(draw); tick++;
  const W = cv.width, R0 = W * 0.36, c = W / 2;
  cx.clearRect(0, 0, W, W);
  let an = S.listening ? micAnalyser : S.speaking ? outAnalyser : null, level = 0;
  if (an) { an.getByteFrequencyData(data); level = data.slice(2, 60).reduce((a, b) => a + b, 0) / (58 * 255); }
  lvlSmooth += (level - lvlSmooth) * .35;
  document.documentElement.style.setProperty("--level", Math.min(1, lvlSmooth * 2.2).toFixed(3));
  const col = S.listening ? "69,255,176" : S.busy && !S.speaking ? "255,184,77" : "41,230,255";
  const N = 96;
  for (let i = 0; i < N; i++) {
    const a = (i / N) * Math.PI * 2 - Math.PI / 2;
    let v;
    if (an) v = data[2 + Math.floor((i < N / 2 ? i : N - i) / (N / 2) * 56)] / 255;
    else if (S.busy) v = .12 + .1 * Math.sin(tick / 6 + i / 3) ** 2;
    else v = .04 + .035 * Math.sin(tick / 40 + i / 5) ** 2;
    const len = 6 + v * W * .11;
    cx.strokeStyle = `rgba(${col},${.25 + v * .75})`; cx.lineWidth = 3;
    cx.beginPath(); cx.moveTo(c + Math.cos(a) * R0, c + Math.sin(a) * R0);
    cx.lineTo(c + Math.cos(a) * (R0 + len), c + Math.sin(a) * (R0 + len)); cx.stroke();
  }
}
requestAnimationFrame(draw);

/* ======================= microphone ======================= */
let rec = null, micStream = null;
async function listen() {
  if (S.listening) { rec && rec.state === "recording" && rec.stop(); return; }
  if (S.busy) return;
  hush();
  try { micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
  catch (e) { addMsg("joshua", "I can't hear you, sir. The browser blocked the microphone. Allow it in the address bar and try again.", true); return; }
  audio();
  const src = AC.createMediaStreamSource(micStream);
  micAnalyser = AC.createAnalyser(); micAnalyser.fftSize = 256; src.connect(micAnalyser);
  const chunks = [];
  rec = new MediaRecorder(micStream);
  rec.ondataavailable = e => e.data.size && chunks.push(e.data);
  S.listening = true; $("#mic").classList.add("live"); refreshState(); caption("Listening…");
  const td = new Float32Array(micAnalyser.fftSize), t0 = performance.now();
  let floor = 0.01, spoke = false, lastLoud = t0, samples = 0;
  const vad = setInterval(() => {
    micAnalyser.getFloatTimeDomainData(td);
    const rms = Math.sqrt(td.reduce((a, b) => a + b * b, 0) / td.length), now = performance.now();
    if (now - t0 < 350) { floor = Math.max(floor, rms); samples++; return; }
    const thr = Math.max(0.018, floor * 2.6);
    if (rms > thr) { spoke = true; lastLoud = now; }
    if ((spoke && now - lastLoud > 1300) || (!spoke && now - t0 > 9000) || now - t0 > 30000) rec.state === "recording" && rec.stop();
  }, 60);
  rec.onstop = async () => {
    clearInterval(vad); micStream.getTracks().forEach(t => t.stop());
    S.listening = false; $("#mic").classList.remove("live"); refreshState();
    if (!spoke) { caption(""); return; }
    caption("Transcribing…");
    try {
      const r = await fetch("/api/stt", { method: "POST", body: new Blob(chunks, { type: rec.mimeType }) });
      const { text } = await r.json(); caption("");
      if (text) send(text); else caption("Didn't catch that.");
    } catch { caption("Transcription failed."); }
  };
  rec.start(250);
}
$("#mic").onclick = listen;
document.addEventListener("keydown", e => {
  if (e.code === "Space" && !e.repeat && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) && $("#boot").classList.contains("gone")) {
    e.preventDefault(); listen();
  }
  if (e.key === "Escape") { $$(".modal").forEach(m => m.id !== "confirm" && m.classList.add("hidden")); }
});

/* ======================= conversation ======================= */
function md(t) {
  let h = esc(t);
  h = h.replace(/```([\s\S]*?)```/g, (_, c) => `<code style="display:block;white-space:pre-wrap">${c.trim()}</code>`);
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  h = h.replace(/(^|\n)[-•] (.+)/g, "$1<li>$2</li>").replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>").replace(/<\/ul>\s*<ul>/g, "");
  return h.replace(/\n/g, "<br>");
}
function addMsg(who, text, err) {
  const el = document.createElement("div");
  el.className = `msg ${who}${err ? " err" : ""}`;
  el.innerHTML = `<div class="who">${who === "you" ? "YOU" : "JOSHUA"}</div><div class="body">${md(text)}</div>`;
  $("#log").appendChild(el); $("#log").scrollTop = 1e9;
  return el;
}
const now = () => new Date().toTimeString().slice(0, 8);
function feed(kind, label, html = "") {
  const f = $("#feed"); if (f.querySelector(".empty")) f.innerHTML = "";
  const el = document.createElement("div"); el.className = "ev " + kind;
  el.innerHTML = `<span class="t">${now()}</span><span class="k">${label}</span>${html}`;
  f.appendChild(el); f.scrollTop = 1e9; return el;
}
function toast(t) { const el = document.createElement("div"); el.className = "toast"; el.textContent = t; document.body.appendChild(el); setTimeout(() => el.remove(), 4000); }
const ICON = { library_search: "⌕ SEARCHING LIBRARY", library_read: "▤ READING", library_books: "▤ CHECKING SHELF", bash: "⌘ EXECUTING", read_file: "▤ READING FILE",
  write_file: "✎ WRITING FILE", edit_file: "✎ EDITING FILE", list_dir: "▦ LISTING", search: "⌕ SEARCHING FILES", remember: "◆ MEMORIZING", recall: "◇ RECALLING" };

async function send(text) {
  text = text.trim(); if (!text || S.busy) return;
  hush(); audio();
  addMsg("you", text);
  S.busy = true; refreshState();
  const msg = addMsg("joshua", ""), body = msg.querySelector(".body"); body.classList.add("cursor");
  let reply = "", thinkEl = null, acts = null;
  const deep = S.deep; S.deep = false; $("#deep").classList.remove("on");
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, think: deep }) });
    if (!r.ok) throw new Error(r.status === 409 ? "I'm still busy with the last thing, sir." : r.statusText);
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        switch (ev.type) {
          case "route": feed(ev.think ? "think" : "", ev.brain === "cloud" ? "☁ CLOUD BRAIN" : ev.think ? "◐ DEEP REASONING" : "⚡ QUICK RESPONSE", `<div class="args">${esc(ev.why)}</div>`);
            if (ev.think) speak(["Let me think on that, sir.", "Give me a moment, sir.", "Hmm. Hold that thought, sir."][Math.floor(Math.random() * 3)]); break;
          case "thinking":
            if (!thinkEl) { thinkEl = feed("think", "◐ THINKING", "<pre></pre>").querySelector("pre"); }
            thinkEl.textContent += ev.text; thinkEl.scrollTop = 1e9; $("#feed").scrollTop = 1e9; break;
          case "step": thinkEl = null; break;
          case "token": reply += ev.text; body.innerHTML = md(reply); $("#log").scrollTop = 1e9; break;
          case "sentence": speak(ev.text); break;
          case "tool": {
            thinkEl = null;
            const lab = ICON[ev.name] || "⚙ " + ev.name.toUpperCase();
            const a = ev.args.command || ev.args.query || ev.args.link || ev.args.path || ev.args.fact || JSON.stringify(ev.args);
            ev._el = feed("tool", lab, `<div class="args">${esc(a)}</div>`); window._lastTool = ev._el;
            if (!acts) { acts = document.createElement("div"); acts.className = "acts"; msg.appendChild(acts); }
            acts.insertAdjacentHTML("beforeend", `<span class="act">${esc(lab.replace(/^\S+ /, "").toLowerCase())}</span>`);
            if (ev.name === "remember" || ev.name === "write_file" || ev.name === "edit_file") setTimeout(loadMemory, 800);
            break; }
          case "tool_result": if (window._lastTool) window._lastTool.insertAdjacentHTML("beforeend", `<details><summary>result · ${ev.text.length} chars</summary><pre>${esc(ev.text.slice(0, 3000))}</pre></details>`); break;
          case "selfcheck": feed("err", "⟲ SELF-CHECK", `<div class="args">${esc(ev.text)}</div>`); reply += "\n\n"; break;
          case "learned": feed("learn", "◆ LEARNED", `<div class="args">${esc(ev.rule)}</div>`); toast("Joshua learned: " + ev.rule); loadMemory(); break;
          case "confirm": askPermission(ev); break;
          case "error": feed("err", "✕ ERROR", `<div class="args">${esc(ev.text)}</div>`); reply += (reply ? "\n\n" : "") + "Something's gone sideways in my head, sir: " + ev.text; body.innerHTML = md(reply); msg.classList.add("err"); break;
        }
      }
    }
  } catch (e) {
    reply = reply || e.message; body.innerHTML = md(reply); msg.classList.add("err");
  }
  body.classList.remove("cursor");
  if (!reply.trim()) body.innerHTML = "<i style='opacity:.6'>(stopped)</i>";
  S.busy = false; refreshState(); loadStatus();
  if (!speech.playing && S.hands) setTimeout(listen, 400);
}
$("#form").onsubmit = e => { e.preventDefault(); const t = $("#text").value; $("#text").value = ""; send(t); };
$("#stop").onclick = () => { hush(); api("/api/stop", {}).catch(() => {}); };
$("#deep").onclick = () => { S.deep = !S.deep; $("#deep").classList.toggle("on", S.deep); };
function toggle(id, key) { const b = $(id); b.classList.toggle("on", S[key]); b.onclick = () => { S[key] = !S[key]; store.set(key, S[key]); b.classList.toggle("on", S[key]); if (key === "voice" && !S.voice) hush(); }; }
toggle("#t-voice", "voice"); toggle("#t-hands", "hands");
$("#t-new").onclick = async () => {
  if (S.busy) return;
  const r = await api("/api/new", {}); $("#log").innerHTML = ""; $("#feed").innerHTML = "";
  feed("learn", "◆ NEW CONVERSATION", r.filed ? `<div class="args">Previous conversation is being filed into long-term memory.</div>` : "");
  setTimeout(loadMemory, 15000);
};

/* ======================= permission dialog ======================= */
let cdTimer = null;
function askPermission(ev) {
  $("#cmd").textContent = ev.what; $("#confirm").classList.remove("hidden");
  feed("think", "⚠ AWAITING AUTHORIZATION", `<div class="args">${esc(ev.what)}</div>`);
  speak("I need your authorization for this one, sir.");
  let left = 180; clearInterval(cdTimer);
  const tickCd = () => { $("#countdown").textContent = `auto-deny in ${left}s`; if (left-- <= 0) answer(false); };
  tickCd(); cdTimer = setInterval(tickCd, 1000);
  const answer = ok => { clearInterval(cdTimer); $("#confirm").classList.add("hidden"); api("/api/confirm", { id: ev.id, ok }).catch(() => {});
    feed(ok ? "learn" : "err", ok ? "✓ AUTHORIZED" : "✕ DENIED"); };
  $("#allow").onclick = () => answer(true); $("#deny").onclick = () => answer(false);
}

/* ======================= system readouts ======================= */
function gauge(id, pct, label) {
  const g = $(id), C = 2 * Math.PI * 38, v = g.querySelector(".val");
  v.style.strokeDasharray = C; v.style.strokeDashoffset = C * (1 - Math.max(0, Math.min(1, pct / 100)));
  g.querySelector("b").textContent = label;
}
function meter(id, pct, hot) { const m = $(id); m.querySelector("i").style.width = Math.max(0, Math.min(100, pct)) + "%"; m.classList.toggle("hot", !!hot); }
const led = (id, on) => { $(id).className = "led " + (on === null ? "idle" : on ? "on" : "off"); };
async function loadStatus() {
  let s; try { s = await api("/api/status"); } catch { led("#led-brain", false); return; }
  S.status = s;
  if (s.gpu) {
    gauge("#g-gpu", s.gpu.util, Math.round(s.gpu.util) + "%");
    gauge("#g-vram", 100 * s.gpu.mem_used / s.gpu.mem_total, (s.gpu.mem_used / 1024).toFixed(1) + "G");
    meter("#m-temp", s.gpu.temp, s.gpu.temp > 78); $("#v-temp").textContent = Math.round(s.gpu.temp) + "°C";
    meter("#m-pow", Math.min(100, s.gpu.power / 1.5)); $("#v-pow").textContent = Math.round(s.gpu.power) + "W";
  } else { gauge("#g-gpu", 0, "N/A"); gauge("#g-vram", 0, "N/A"); $("#v-temp").textContent = "--"; $("#v-pow").textContent = "--"; }
  meter("#m-cpu", s.cpu.util, s.cpu.util > 90); $("#v-cpu").textContent = s.cpu.util + "%";
  meter("#m-ram", 100 * s.ram.used / s.ram.total, s.ram.used / s.ram.total > .9); $("#v-ram").textContent = `${s.ram.used}/${Math.round(s.ram.total)}G`;
  $("#b-model").textContent = s.model;
  $("#b-loaded").textContent = s.busy ? "ACTIVE" : s.loaded.length ? "WARM (in VRAM)" : "COLD (loads on demand)";
  const up = s.uptime; $("#b-up").textContent = `${Math.floor(up / 3600)}h ${Math.floor(up / 60) % 60}m`;
  const mode = $("#mode"); mode.textContent = s.mode.toUpperCase() + " MODE"; mode.classList.toggle("warn", s.mode !== "full"); mode.title = s.summary;
  led("#led-brain", s.services.brain); led("#led-library", s.services.library); led("#led-imaging", s.services.imaging ? true : null); led("#led-voice", true);
  for (const k of ["facts", "rules", "episodes"]) $("#n-" + k).textContent = s.memory[k];
}
setInterval(loadStatus, 2500);
setInterval(() => { const d = new Date(); $("#clock").textContent = d.toTimeString().slice(0, 8);
  $("#date").textContent = d.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" }).toUpperCase(); }, 1000);

/* ======================= memory banks ======================= */
async function loadMemory() { try { S.mem = await api("/api/memory"); renderMemory(); } catch {} }
function renderMemory() {
  const list = $("#memlist"), items = S.mem ? S.mem[S.memTab] : [];
  if (!items.length) { list.innerHTML = `<div class="empty">${{ facts: "Nothing yet. Tell him about yourself.", rules: "No rules yet. Correct him and he'll learn.", episodes: "No filed conversations yet." }[S.memTab]}</div>`; return; }
  list.innerHTML = items.map((it, i) => {
    if (S.memTab === "episodes") return `<div class="item"><small>${esc(it.date)}</small>${esc(it.summary)}<button class="x" data-i="${i}" title="Forget">✕</button></div>`;
    const m = it.match(/^(.*?)\s+\((\d{4}-\d\d-\d\d)\)$/);
    return `<div class="item">${m ? `<small>${m[2]}</small>${esc(m[1])}` : esc(it)}<button class="x" data-i="${i}" title="Forget">✕</button></div>`;
  }).join("");
  list.querySelectorAll(".x").forEach(b => b.onclick = async () => {
    if (!confirm("Make Joshua forget this?")) return;
    S.mem = await api("/api/forget", { kind: S.memTab, index: +b.dataset.i }); renderMemory(); loadStatus();
  });
}
$$("#memtabs .tab").forEach(t => t.onclick = () => { $$("#memtabs .tab").forEach(x => x.classList.remove("on")); t.classList.add("on"); S.memTab = t.dataset.k; renderMemory(); });
setInterval(loadMemory, 30000);

/* ======================= right tabs ======================= */
$$("#rtabs .tab").forEach(t => t.onclick = () => {
  $$("#rtabs .tab").forEach(x => x.classList.remove("on")); t.classList.add("on");
  ["activity", "library", "imaging"].forEach(p => $("#p-" + p).classList.toggle("hidden", p !== t.dataset.p));
  store.set("rtab", t.dataset.p);
  if (t.dataset.p === "library") loadBooks();
  if (t.dataset.p === "imaging") enterImaging();
});

/* ======================= library ======================= */
let BOOKS = null;
async function loadBooks() {
  if (BOOKS) return;
  try { BOOKS = await api("/api/books"); } catch { $("#libres").innerHTML = `<div class="empty">The library server is offline.</div>`; return; }
  $("#libbook").innerHTML = `<option value="">All ${BOOKS.length} books</option>` + BOOKS.map(b => `<option value="${esc(b.name)}">${esc(b.title)}</option>`).join("");
  showShelf();
}
function showShelf() {
  $("#libres").innerHTML = `<div class="lbl">ON THE SHELF</div><div class="shelf">${BOOKS.map(b =>
    `<div class="book" data-n="${esc(b.name)}" title="${esc(b.summary)}">${esc(b.title)}<small>${b.articles.toLocaleString()} pages</small></div>`).join("")}</div>`;
  $$(".book").forEach(el => el.onclick = () => { $("#libbook").value = el.dataset.n; $("#libq").focus(); });
}
$("#libform").onsubmit = async e => {
  e.preventDefault(); const q = $("#libq").value.trim(); if (!q) return showShelf();
  $("#libres").innerHTML = `<div class="progress"><i></i></div>`;
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q)}&book=${encodeURIComponent($("#libbook").value)}`);
    $("#libres").innerHTML = `<div class="lbl">${r.total.toLocaleString()} RESULTS</div>` + (r.items.map((it, i) =>
      `<div class="result" data-i="${i}"><b>${esc(it.title)}</b><small>${esc(it.book)}</small><p>${esc(it.snippet)}</p></div>`).join("") || `<div class="empty">Nothing found.</div>`);
    $$(".result").forEach(el => el.onclick = () => openReader(r.items[+el.dataset.i]));
  } catch (err) { $("#libres").innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
};
let readerItem = null;
function openReader(it) {
  readerItem = it; $("#rtitle").textContent = it.title.toUpperCase(); $("#rframe").src = "http://127.0.0.1:8090" + it.link; $("#reader").classList.remove("hidden");
}
$("#rclose").onclick = () => { $("#reader").classList.add("hidden"); $("#rframe").src = "about:blank"; };
$("#rask").onclick = () => { $("#rclose").onclick(); send(`Read "${readerItem.title}" from my bookshelf (link: ${readerItem.link}) and give me the key points.`); };

/* ======================= imaging ======================= */
const BASE = "Joshua, a sleek male humanoid android with glossy white porcelain synthetic skin, fine panel seams, chrome mechanical ear, cable-filled neck and glowing cyan eyes";
const STYLES = {
  "PORTRAIT": "head and shoulders portrait, pure black background, cyan rim light, photorealistic 3D render, ultra detailed",
  "HOLOGRAM": "rendered as a translucent cyan hologram with scanlines and glitch artifacts, dark room, sci-fi HUD elements",
  "INSIDE THE PC": "standing inside a giant gaming PC tower, glowing RGB fans, GPU like a cathedral, cables like vines, cinematic lighting",
  "CYBERPUNK": "cyberpunk city at night, neon rain, holographic signs, teal and magenta lighting",
  "BLUEPRINT": "technical blueprint schematic, white lines on deep blue, annotations, exploded view",
  "NOIR": "1940s film noir, black and white, venetian blind shadows, dramatic contrast",
  "OIL PAINTING": "classical oil painting, aristocratic portrait, rich varnished colours, visible brushstrokes",
  "ANIME": "anime key visual, cel shaded, dramatic sky, vibrant",
};
const active = new Set();
let shape = { w: 1024, h: 1024 };
for (const k of Object.keys(STYLES)) {
  const b = document.createElement("button"); b.className = "chip2"; b.textContent = k;
  b.onclick = () => { active.has(k) ? active.delete(k) : active.add(k); b.classList.toggle("on"); }; $("#styles").appendChild(b);
}
$$("#shapes .chip2").forEach(b => b.onclick = () => { $$("#shapes .chip2").forEach(x => x.classList.remove("on")); b.classList.add("on"); shape = { w: +b.dataset.w, h: +b.dataset.h }; });
$("#prompt").value = store.get("prompt", ""); $("#prompt").oninput = () => store.set("prompt", $("#prompt").value);
function enterImaging() {
  api("/api/img/start", {}).catch(() => {});
  const s = S.status;
  const note = s && (!s.studio_ok || s.studio_note) ? s.studio_note : "";
  $("#imgnote").textContent = note ? "⚠ " + note : ""; $("#imgnote").classList.toggle("hidden", !note);
  loadGallery();
}
$("#write").onclick = async () => {
  const b = $("#write"); b.disabled = true; b.textContent = "✒ WRITING…";
  try { const r = await api("/api/img/enhance", { idea: $("#idea").value, base: BASE }); $("#prompt").value = r.prompt; store.set("prompt", r.prompt); }
  catch (e) { toast("Couldn't write it: " + e.message); }
  b.disabled = false; b.textContent = "✒ WRITE IT";
};
$("#paint").onclick = async () => {
  let p = $("#prompt").value.trim();
  const parts = []; if (!/joshua/i.test(p)) parts.push(BASE); if (p) parts.push(p); for (const s of active) parts.push(STYLES[s]);
  const prompt = parts.join(". "); if (!prompt) return;
  const btn = $("#paint"); btn.disabled = true; $("#prog").classList.remove("hidden"); const t0 = Date.now();
  btn.textContent = "◈ RENDERING…";
  try {
    let job;
    for (let i = 0; i < 20; i++) { try { job = await api("/api/img/generate", { prompt, width: shape.w, height: shape.h }); break; }
      catch (e) { if (!/starting/.test(e.message)) throw e; btn.textContent = "◈ STARTING ENGINE…"; await new Promise(r => setTimeout(r, 3000)); } }
    if (!job) throw new Error("imaging engine didn't start");
    btn.textContent = "◈ RENDERING…";
    let st; do { await new Promise(r => setTimeout(r, 1000)); st = await api("/api/img/status?id=" + job.id); } while (st.state !== "done" && st.state !== "error");
    if (st.state === "error") throw new Error(st.error);
    const file = st.images[0];
    await api("/api/img/meta", { file, prompt, seed: job.seed, width: shape.w, height: shape.h });
    showPreview({ file, prompt, seed: job.seed }, ((Date.now() - t0) / 1000).toFixed(1)); loadGallery();
  } catch (e) { $("#preview").innerHTML = `<div class="note">✕ ${esc(e.message)}</div>`; }
  btn.disabled = false; btn.textContent = "◈ RENDER"; $("#prog").classList.add("hidden");
};
function showPreview(it, secs) {
  $("#preview").innerHTML = `<img src="/img/${esc(it.file)}" alt="Render of Joshua"><div class="imgacts">
    <button class="tbtn" id="pv-face">◉ SET AS FACE</button><button class="tbtn" id="pv-reuse">↺ REUSE</button>
    <a class="tbtn" href="/img/${esc(it.file)}" download>⬇ SAVE</a><button class="tbtn" id="pv-del">✕ DELETE</button>
    ${secs ? `<span class="hint">rendered in ${secs}s · seed ${it.seed}</span>` : ""}</div>`;
  $("#preview img").onclick = () => { $("#lbimg").src = "/img/" + it.file; $("#lightbox").classList.remove("hidden"); };
  $("#pv-face").onclick = async () => { await api("/api/img/avatar", { file: it.file }); $("#face").src = "/avatar.png?" + Date.now(); toast("New face installed."); speak("Ooh. Very dashing, sir."); };
  $("#pv-reuse").onclick = () => { if (it.prompt) { $("#prompt").value = it.prompt; store.set("prompt", it.prompt); } };
  $("#pv-del").onclick = async () => { if (!confirm("Delete this render?")) return; await api("/api/img/delete", { file: it.file }); $("#preview").innerHTML = ""; loadGallery(); };
}
async function loadGallery() {
  let items = []; try { items = await api("/api/img/gallery"); } catch {}
  $("#gal").innerHTML = items.map((it, i) => `<div class="g" data-i="${i}"><img src="/img/${esc(it.file)}" loading="lazy" alt=""></div>`).join("") || `<div class="empty">No renders yet.</div>`;
  $$("#gal .g").forEach(el => el.onclick = () => showPreview(items[+el.dataset.i]));
}
$("#lightbox").onclick = () => $("#lightbox").classList.add("hidden");

/* ======================= boot ======================= */
const FAST = /noboot/.test(location.search);
async function boot() {
  const lines = $("#bootlines");
  const add = (t, cls = "") => new Promise(r => { const d = document.createElement("div"); d.className = "ln " + cls; lines.appendChild(d);
    if (FAST) { d.textContent = t; return r(); }
    let i = 0; const iv = setInterval(() => { d.textContent = t.slice(0, i += 3); if (i >= t.length) { clearInterval(iv); r(); } }, 12); });
  const pad = (a, b) => a + " " + ".".repeat(Math.max(2, 30 - a.length)) + " " + b;
  await add("> INITIALIZING CORE SYSTEMS");
  let s = null; for (let i = 0; i < 20 && !s; i++) { try { s = await api("/api/status"); } catch { await new Promise(r => setTimeout(r, 700)); } }
  if (!s) { await add("✕ HUD SERVER UNREACHABLE", "wn"); return; }
  S.status = s; loadStatus(); loadMemory();
  try { for (const m of await api("/api/history")) addMsg(m.role === "user" ? "you" : "joshua", m.content); } catch {}
  await add(pad("HARDWARE", (s.gpu ? s.gpu.name.replace("NVIDIA GeForce ", "") : "CPU ONLY") + ` · ${s.mode.toUpperCase()} MODE`), s.mode === "full" ? "ok" : "wn");
  await add(pad("NEURAL CORE", s.model + (s.services.brain ? " ONLINE" : " OFFLINE")), s.services.brain ? "ok" : "wn");
  await add(pad("MEMORY BANKS", `${s.memory.facts} FACTS · ${s.memory.rules} RULES · ${s.memory.episodes} LOGS`), "ok");
  let nb = "OFFLINE"; try { BOOKS = null; await loadBooks(); nb = BOOKS ? BOOKS.length + " VOLUMES" : nb; } catch {}
  await add(pad("LIBRARY", nb), s.services.library ? "ok" : "wn");
  await add(pad("VOICE SYNTH", "EN-GB · ONLINE"), "ok");
  await add(pad("IMAGING", s.services.imaging ? "ONLINE" : "STANDBY"), s.services.imaging ? "ok" : "");
  await add("> ALL SYSTEMS NOMINAL", "ok");
  $("#engage").classList.remove("hidden");
  const go = () => {
    $("#boot").classList.add("gone"); audio(); refreshState();
    const hour = new Date().getHours(), part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
    const greet = `Good ${part}, sir. Joshua online.` + (s.mode !== "full" ? " " + s.notes[0] : " What do you need?");
    if (!$("#log").children.length) addMsg("joshua", greet);
    if (!FAST) speak(greet);
    $("#text").focus();
  };
  $("#boot").onclick = go;
}
const URLQ = new URLSearchParams(location.search);
const tab = URLQ.get("tab") || store.get("rtab", "activity"); if (tab !== "activity") $(`#rtabs .tab[data-p="${tab}"]`).click();
refreshState(); boot();
if (URLQ.get("demo") === "confirm") setTimeout(() => $("#confirm").classList.remove("hidden") || ($("#cmd").textContent = "rm -rf ~/Downloads/old-junk"), 300);
if (FAST) { const iv = setInterval(() => { if (!$("#engage").classList.contains("hidden")) { clearInterval(iv); $("#boot").click(); } }, 50); }
if (/shot/.test(location.search)) { const i = new Image(); i.src = "/api/sleep?s=6"; i.style.display = "none"; document.body.appendChild(i); }  // holds the page "loading" for screenshot tools
