"""Joshua's cloud brain: free OpenAI-compatible APIs for hard questions and code, when the internet is up.
Keys live OUTSIDE the portable folder, in ~/.config/joshua/<provider>.token (chmod 600). No key or no internet = local brain.

Messages are kept in Ollama's format inside Joshua; this module converts them on the way out and converts
tool calls back on the way in, so core.py doesn't care which brain answered.
"""
import json, os, re, socket, time, urllib.request

KEYDIR = os.path.expanduser("~/.config/joshua")
PROVIDERS = [
    # tested 2026-11-20: Nemotron Ultra ~2s with correct tool calls; MiniMax ~4s. Kimi K3.1 Pro works but ~60s.
    {"name": "nvidia", "url": "https://integrate.api.nvidia.com/v1/chat/completions", "key": "nvidia.token",
     "models": ["nvidia/nemotron-3-ultra-550b-a55b", "minimaxai/minimax-m3.1-flash"]},
    # Groq free tier: 0.6s(!), 1000 requests/day but only 8000 tokens/min and a Joshua request is ~3.1k tokens,
    # so multi-step jobs hit 429 -> 2 min rest -> next model takes over mid-turn (history is model-agnostic).
    {"name": "groq", "url": "https://api.groq.com/openai/v1/chat/completions", "key": "groq.token",
     "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]},
    # Cohere trial key: free, ~1000 calls/month, so it's the backup. command-a-03-2025: 1.5s, clean tool calls.
    # (command-a-plus works but burns ~2000 reasoning tokens per call; north-mini-code wrote broken one-liners)
    {"name": "cohere", "url": "https://api.cohere.ai/compatibility/v1/chat/completions", "key": "cohere.token",
     "models": ["command-a-03-2025"]},
    # OpenRouter: key has a $0 spending limit (can't ever charge), free models capped at 50 calls/day.
    # Its free tool-capable list is mostly NVIDIA's own models via other hosts = insurance if NVIDIA's API is down.
    # nemotron-3.5-super-vl:free ~5.5s, clean tool calls (also sees images). ultra:free works but ~70s.
    {"name": "openrouter", "url": "https://openrouter.ai/api/v1/chat/completions", "key": "openrouter.token",
     "models": ["nvidia/nemotron-3.5-super-vl:free"]},
    # Gemini free tier (2026-11-20): Pro/Omni = quota 0 (paid), newest Flash 503 "high demand", 3.5/3.6 Flash ~45s.
    # Flash-Lite ~9s with good tool calls -> last cloud stop before the local brain.
    {"name": "gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "key": "gemini.token",
     "models": ["gemini-3.5-flash-lite"]},
]
FIRST_BYTE_TIMEOUT = 45      # seconds to wait for a model to start answering before trying the next
_cooldown = {}               # model -> time it may be retried (after rate limit / outage)


def _key(p):
    f = os.path.join(KEYDIR, p["key"])
    try: return open(f).read().strip() or None
    except OSError: return None

def online(host="integrate.api.nvidia.com"):
    try: socket.create_connection((host, 443), timeout=2).close(); return True
    except OSError: return False

def available():
    """[(provider, model, key)] usable right now, best first."""
    out = []
    for p in PROVIDERS:
        k = _key(p)
        if not k: continue
        for m in p["models"]:
            if _cooldown.get(m, 0) <= time.time(): out.append((p, m, k))
    return out


def to_openai(messages):
    """Ollama-style history -> OpenAI-style (tool calls need ids and string arguments)."""
    out, pending = [], []
    n = 0
    for m in messages:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                n += 1; cid = c.get("id") or f"call_{n}"
                args = c["function"].get("arguments", {})
                oc = {"id": cid, "type": "function",
                      "function": {"name": c["function"]["name"], "arguments": args if isinstance(args, str) else json.dumps(args)}}
                if c.get("extra_content"): oc["extra_content"] = c["extra_content"]   # Gemini's thought_signature must come back
                calls.append(oc)
                pending.append(cid)
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            cid = pending.pop(0) if pending else f"call_orphan_{n}"
            out.append({"role": "tool", "tool_call_id": cid, "content": m.get("content") or ""})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def stream(messages, tools, emit, stop=None, sent_end=None, max_tokens=4000):
    """Try each available cloud model in turn. Returns (content, calls, model) or raises RuntimeError if none worked
    before producing output."""
    errors = []
    for p, model, key in available():
        body = {"model": model, "messages": to_openai(messages), "tools": tools, "stream": True,
                "max_tokens": max_tokens, "temperature": 0.6}
        req = urllib.request.Request(p["url"], data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "joshua/1.0",
                                              "Accept": "text/event-stream"})
        try:
            r = urllib.request.urlopen(req, timeout=FIRST_BYTE_TIMEOUT)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:160]
            if e.code in (404, 410): _cooldown[model] = time.time() + 86400       # retired model
            elif e.code == 429: _cooldown[model] = time.time() + 120             # rate limited
            elif e.code in (401, 402, 403): _cooldown[model] = time.time() + 3600  # key/credits problem
            else: _cooldown[model] = time.time() + 60
            errors.append(f"{model}: HTTP {e.code} {msg}"); continue
        except Exception as e:
            _cooldown[model] = time.time() + 60
            errors.append(f"{model}: {e}"); continue

        content, pending, calls = "", "", {}
        with r:
            for raw in r:
                if stop and stop.is_set(): break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"): continue
                data = line[5:].strip()
                if data == "[DONE]": break
                try: ch = json.loads(data)
                except ValueError: continue
                if not ch.get("choices"): continue
                d = ch["choices"][0].get("delta") or {}
                think = d.get("reasoning_content") or d.get("reasoning")
                if think: emit({"type": "thinking", "text": think})
                piece = d.get("content") or ""
                if piece:
                    content += piece; pending += piece
                    emit({"type": "token", "text": piece})
                    while sent_end:
                        mm = sent_end.match(pending)
                        if not mm or len(mm.group(1)) < 12 or "```" in pending: break
                        emit({"type": "sentence", "text": mm.group(1)}); pending = pending[mm.end():]
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "args": ""})
                    if tc.get("extra_content"): slot["extra"] = tc["extra_content"]
                    if tc.get("id"): slot["id"] = tc["id"]
                    f = tc.get("function") or {}
                    if f.get("name"): slot["name"] += f["name"]
                    if f.get("arguments"): slot["args"] += f["arguments"]
        if pending.strip(): emit({"type": "sentence", "text": pending.strip()})
        out_calls = []
        for i in sorted(calls):
            try: args = json.loads(calls[i]["args"] or "{}")
            except ValueError: args = {}
            if calls[i]["name"]:
                oc = {"function": {"name": calls[i]["name"], "arguments": args}}
                if calls[i].get("extra"): oc["extra_content"] = calls[i]["extra"]
                if calls[i].get("id"): oc["id"] = calls[i]["id"]
                out_calls.append(oc)
        return content, out_calls, model
    raise RuntimeError("no cloud brain answered: " + " | ".join(errors) if errors else "no cloud keys")
