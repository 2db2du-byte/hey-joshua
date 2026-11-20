#!/bin/sh
# Opens Joshua's HUD in the browser (starts his services if they're off).
systemctl --user start joshua-brain joshua-library joshua-hud
for i in 1 2 3 4 5 6 7 8 9 10; do curl -s http://127.0.0.1:8899/api/status >/dev/null && break; sleep 1; done
xdg-open http://127.0.0.1:8899 >/dev/null 2>&1 &
