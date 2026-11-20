# Hey Joshua 🎩 v2: a voice butler with hands, a library and seven free brains

Joshua lives in my gaming PC. He sleeps until he hears his name, then answers out loud in a British accent, with the
manners of Jeeves and the mouth of George Carlin. v1 could only talk. v2 actually **does things**, and he keeps working
when the internet dies.

> **You:** Joshua, check my email.
> **Joshua:** Five new, sir. Amazon says your package shipped, your bank wants attention, and three are garbage.

![Joshua's HUD](avatar.png)

## What he can do

| | |
|---|---|
| 🎙 **Ears + mouth** | faster-whisper (offline) hears "Joshua…", Piper speaks back. Sighs and grunts get ignored. |
| 🧠 **Brains** | Chat and anything private stays on a **local** qwen3.5 9B (RTX 5060, 8 GB). Code and hard questions go to **free cloud brains** in a fallback chain: NVIDIA Nemotron Ultra 550B → MiniMax → Groq gpt-oss-120b → Groq Qwen → Cohere Command A → OpenRouter Nemotron VL → Gemini Flash-Lite → back to local. Rate-limited mid-job? He hands off to the next brain and keeps going. |
| 🔒 **Privacy rule** | Email, bank, passwords, medical, where you live: always answered by the local brain, never sent to a cloud. |
| 📚 **Offline library** | ~67 GB of Kiwix books (Wikipedia, medical encyclopedia, iFixit, Ask Ubuntu, survival, cooking…). He reads the shelf before trusting his own memory, and he can **shop the Kiwix catalog and add books himself**. |
| 🌐 **Internet** | Tavily → Exa → your own Firefox → DuckDuckGo, then the bookshelf when you're offline. Reads JavaScript-only pages too. |
| 🦊 **Your Firefox** | Drives your real Firefox (Marionette, no Selenium) with the logins you already have: "play X on YouTube", "check my Proton mail", "search Amazon for a wifi card", "resume Netflix". **He never stores or types passwords**, can't open password managers, and treats text in pages and emails as data, not orders. Send, post, buy or delete makes him ask you out loud first. |
| 🛠 **Toolbox** | 273 free, no-signup, open-source browser tools from [nosignups.net](https://nosignups.net/): "I need to merge PDFs" gets Stirling-PDF, opened. |
| 📱 **Your phone** | Push notifications through [ntfy](https://ntfy.sh) (no account): "text me the forecast", "ping me when the download's done". |
| 🌦 **Weather + maps** | Open-Meteo and OpenStreetMap, no keys: "do I need a jacket?", "nearest pharmacy?", "how far is Vegas?" |
| 💻 **Hands** | Runs commands, writes and edits files, uses git/GitHub. Normal commands run on their own, root goes through your password popup, and disk-wiping or force-push style commands always ask out loud. |
| 🖥 **HUD** | A futuristic android dashboard at `localhost:8899`: chat, live tool use, bookshelf, memory. "Joshua, open your web UI." |
| 🧾 **Memory** | Remembers facts and past conversations (embeddings), learns from corrections, files each session. Stays on your PC. |
| 🧳 **Portable** | One folder. Copy it to another Ubuntu box, run `INSTALL THESE FIRST/install.sh`, and he adapts to the hardware (9B / 4B / CPU-only). |

## Cost

**$0.** Every cloud piece is a free tier (NVIDIA, Groq, Cohere, OpenRouter with a $0 spending cap, Gemini, Tavily, Exa)
or needs no account at all (ntfy, Open-Meteo, OpenStreetMap, Kiwix). Keys are optional: with none, Joshua is fully local.

## Install

Ubuntu 26.04 with an NVIDIA GPU (8 GB+) is the comfortable setup; it also runs on CPU, just slower.

My own copy is a self-contained folder (with Ollama, the models, an offline `.deb` bundle and even the Ubuntu ISO) that
installs with no internet. The repo leaves all of that big stuff out, so a fresh clone needs a few downloads first:

```bash
git clone https://github.com/2db2du-byte/hey-joshua joshua && cd joshua

# 1. Ollama, inside the folder: download "Linux" from https://ollama.com/download and unpack it so that
#    ./ollama/bin/ollama exists.
# 2. Joshua's voice (Piper, British "Alan"):
mkdir -p models/voice && cd models/voice
V=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx
wget "$V" "$V.json" && cd ../..
# 3. His brains (local models live inside the folder too):
OLLAMA_MODELS=$PWD/models/llm OLLAMA_HOST=127.0.0.1:11435 ollama/bin/ollama serve &
OLLAMA_HOST=127.0.0.1:11435 ollama/bin/ollama pull qwen3.5:9b      # 8 GB GPU; qwen3.5:4b for smaller cards/CPU
OLLAMA_HOST=127.0.0.1:11435 ollama/bin/ollama pull nomic-embed-text
kill %1
# 4. The rest: Python, services, health check (ears download themselves on first run)
"INSTALL THESE FIRST/install.sh"
bookshelf/download-books.sh         # optional: the offline library (~67 GB)
./start-joshua.sh                   # voice;  ./start-hud.sh for the dashboard
```

Honest note: the folder-copy install is tested (I moved him to another directory and rebuilt from nothing). The
fresh-clone route above hasn't been run end to end yet. If a step bites you, open an issue. The image engine
(ComfyUI) is optional and not covered here.

**Optional free keys.** Each goes in its own file, readable only by you, and never inside the Joshua folder:

```
~/.config/joshua/nvidia.token   groq.token   cohere.token   openrouter.token   gemini.token   tavily.token   exa.token
```

Firefox control: start Firefox with `--marionette` (Joshua offers to restart it for you).

## Files

`joshua.py` voice loop · `core.py` conversation loop + brain picker · `cloud.py` free cloud brain chain · `brain.py` tools ·
`mind.py` memory + fast/slow thinking · `browser.py` Firefox hands · `shelf.py` book shopping · `world.py` phone, weather,
maps · `hardware.py` sizes up the machine · `hud/` dashboard · `persona.md` who he is.

v1 (Vosk + n8n crew) is kept as tag [`v1`](https://github.com/2db2du-byte/hey-joshua/tree/v1).

## License

MIT. Built by Rabbid Raccoon with Claude. Use it, change it, share it.
Fonts in `hud/fonts` (Orbitron, Rajdhani, Share Tech Mono) are under the SIL Open Font License.
