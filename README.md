# Hey Joshua 🎩 (offline voice butler)

An always-listening voice assistant that stays silent until you say his name. Say "Joshua, what's the weather like on
Mars?" and he answers out loud in a British butler voice, with the manners of Jeeves and the mouth of George Carlin.

- **Hearing:** [Vosk](https://alphacephei.com/vosk/) small English model, fully offline. Only lines containing his name are acted on.
- **Thinking:** a free cloud model through a LiteLLM brain switch when online, a local [Ollama](https://ollama.com) model
  (`llama3.2:3b`) when not. Optional knowledge from the crew API (offline Wikipedia, your notes, news, time, math).
- **Talking:** a `say` command (Kokoro / Piper text-to-speech).
- **Actions:** a small `ACTIONS` list checked before the AI (e.g. "open mission control"), so he can't pretend to do things.

## Run it

```
python3 -m venv .venv && .venv/bin/pip install vosk sounddevice
# download a Vosk model (vosk-model-small-en-us) into ./model, make sure `say "hello"` works
.venv/bin/python hey_joshua.py
```

As a systemd user service: `ExecStart=%h/path/to/.venv/bin/python %h/path/to/hey_joshua.py`, `Restart=always`.
Settings (wake words, models, personality, actions) are constants at the top of `hey_joshua.py`.

## License

MIT. Built by Rabbid Raccoon with Claude. Use it, change it, share it.
