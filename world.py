"""Joshua's view of the outside world — all free, no keys, no signups:
  ntfy.sh      push notifications to the user's phone (secret topic name = the password)
  Open-Meteo   weather + geocoding
  OpenStreetMap (Nominatim) places, OSRM driving routes

Personal settings live outside the portable folder, in ~/.config/joshua/:
  ntfy.topic   the secret channel name
  home.json    {"name", "lat", "lon", "country"} — "home" for weather and "near me"
"""
import json, math, os, re, secrets, time, urllib.parse, urllib.request

CONF = os.path.expanduser("~/.config/joshua")
TOPIC_FILE = os.path.join(CONF, "ntfy.topic")
HOME_FILE = os.path.join(CONF, "home.json")
UA = "Joshua-home-assistant/1.0 (personal, low volume)"   # OSM policy: identify the app
_last_osm = [0.0]


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)

def _osm(url):
    wait = 1.1 - (time.time() - _last_osm[0])   # Nominatim: max 1 request/second
    if wait > 0: time.sleep(wait)
    _last_osm[0] = time.time()
    return _get(url)


# ---------------- phone notifications (ntfy) ----------------
def topic():
    try: return open(TOPIC_FILE).read().strip()
    except OSError: pass
    os.makedirs(CONF, mode=0o700, exist_ok=True)
    t = "joshua-" + secrets.token_urlsafe(18).replace("_", "").replace("-", "")[:22].lower()
    with open(os.open(TOPIC_FILE, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f: f.write(t + "\n")
    return t

def notify(message, title="Joshua", priority="default", tags=""):
    pr = {"low": "2", "default": "3", "high": "4", "urgent": "5"}.get(str(priority).lower(), "3")
    req = urllib.request.Request("https://ntfy.sh/" + topic(), data=message.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("utf-8").decode("latin-1", "replace"), "Priority": pr,
                                          "Tags": tags or "tophat", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: r.read()
        return "Sent to the user's phone."
    except Exception as e:
        return f"Couldn't reach ntfy.sh ({e}); internet down? Nothing was sent."


# ---------------- places ----------------
def home():
    try: return json.load(open(HOME_FILE))
    except Exception: return None

def geocode(place):
    """Place name -> {name, lat, lon, country}. 'home'/'here'/'' -> saved home."""
    p = (place or "").strip()
    if p.lower() in ("", "home", "here", "my house", "near me", "me", "my place"):
        return home()
    try:
        d = _get("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": p.split(",")[0], "count": 5, "language": "en"}))
        res = d.get("results") or []
        if "," in p and res:   # "Springfield, Illinois" -> prefer the matching region/country
            rest = p.split(",", 1)[1].strip().lower()
            res = sorted(res, key=lambda r: rest not in (f"{r.get('admin1', '')} {r.get('country', '')} {r.get('country_code', '')}").lower())
        if res:
            r = res[0]
            return {"name": ", ".join(x for x in [r["name"], r.get("admin1"), r.get("country")] if x),
                    "lat": r["latitude"], "lon": r["longitude"], "country": r.get("country_code", "")}
    except Exception: pass
    try:   # street addresses and landmarks: Nominatim
        d = _osm("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": p, "format": "jsonv2", "limit": 1, "addressdetails": 1}))
        if d:
            return {"name": d[0]["display_name"][:120], "lat": float(d[0]["lat"]), "lon": float(d[0]["lon"]),
                    "country": (d[0].get("address") or {}).get("country_code", "").upper()}
    except Exception: pass
    return None

def set_home(place):
    g = geocode(place)
    if not g: return f"Couldn't find '{place}' on the map. Try 'City, State' or a full address."
    os.makedirs(CONF, mode=0o700, exist_ok=True)
    with open(os.open(HOME_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(g, f)
    return f"Home set to {g['name']} ({g['lat']:.3f}, {g['lon']:.3f})."

def _no_home():
    return "NO HOME SET: ask the user which city they live in, then call set_home with it."

def _imperial(country):
    return (country or "").upper() in ("US", "LR", "MM")

def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ---------------- weather (Open-Meteo) ----------------
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
       82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
       96: "thunderstorm with hail", 99: "thunderstorm with heavy hail"}

def weather(place="", days=3):
    g = geocode(place)
    if not g: return _no_home() if not (place or "").strip() or place.lower() in ("home", "here") else f"Couldn't find '{place}'."
    imp = _imperial(g.get("country"))
    q = {"latitude": g["lat"], "longitude": g["lon"], "timezone": "auto", "forecast_days": max(1, min(int(days or 3), 7)),
         "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
         "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max,sunrise,sunset,uv_index_max"}
    if imp: q.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
    try: d = _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
    except Exception as e: return f"Weather service unreachable ({e}); internet down?"
    T, W, P = ("°F", "mph", "in") if imp else ("°C", "km/h", "mm")
    c = d["current"]
    out = [f"Weather for {g['name']} (local time {c['time'].replace('T', ' ')}):",
           f"NOW: {WMO.get(c['weather_code'], '?')}, {c['temperature_2m']:.0f}{T} (feels {c['apparent_temperature']:.0f}{T}), "
           f"humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']:.0f} {W}"]
    dd = d["daily"]
    for i, day in enumerate(dd["time"]):
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else time.strftime("%A", time.strptime(day, "%Y-%m-%d")))
        out.append(f"{label}: {WMO.get(dd['weather_code'][i], '?')}, high {dd['temperature_2m_max'][i]:.0f}{T} / low {dd['temperature_2m_min'][i]:.0f}{T}, "
                   f"rain chance {dd['precipitation_probability_max'][i] or 0}% ({dd['precipitation_sum'][i] or 0} {P}), "
                   f"wind up to {dd['wind_speed_10m_max'][i]:.0f} {W}, UV {dd['uv_index_max'][i] or 0:.0f}, "
                   f"sunrise {dd['sunrise'][i][-5:]}, sunset {dd['sunset'][i][-5:]}")
    return "\n".join(out) + "\n(Source: Open-Meteo)"


# ---------------- places + routes (OpenStreetMap) ----------------
def find_place(what, near=""):
    g = geocode(near)
    if not g: return _no_home() if not (near or "").strip() else f"Couldn't find '{near}' on the map."
    box = 0.25   # ~25 km around the centre
    q = {"q": what, "format": "jsonv2", "limit": 8, "bounded": 1, "addressdetails": 1,
         "viewbox": f"{g['lon'] - box},{g['lat'] + box},{g['lon'] + box},{g['lat'] - box}"}
    try: d = _osm("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(q))
    except Exception as e: return f"Map service unreachable ({e})."
    if not d: return f"OpenStreetMap has no '{what}' within ~25 km of {g['name']}. Try another word (e.g. 'pharmacy' not 'drugstore')."
    imp = _imperial(g.get("country"))
    rows = []
    for x in sorted(d, key=lambda x: _km(g, {"lat": float(x["lat"]), "lon": float(x["lon"])})):
        km = _km(g, {"lat": float(x["lat"]), "lon": float(x["lon"])})
        dist = f"{km * 0.621:.1f} mi" if imp else f"{km:.1f} km"
        a = x.get("address") or {}
        street = " ".join(v for v in [a.get("house_number"), a.get("road")] if v)
        town = a.get("city") or a.get("town") or a.get("village") or a.get("suburb") or ""
        rows.append(f"- {x.get('name') or x['display_name'].split(',')[0]} ({x.get('type', '').replace('_', ' ')}): {street}{', ' if street and town else ''}{town} — {dist} straight-line")
    return f"Near {g['name']}:\n" + "\n".join(rows) + "\n(Source: OpenStreetMap)"

def directions(to, origin=""):
    a = geocode(origin); b = geocode(to)
    if not a: return _no_home() if not (origin or "").strip() else f"Couldn't find '{origin}'."
    if not b: return f"Couldn't find '{to}'."
    imp = _imperial(a.get("country"))
    try:
        d = _get(f"https://router.project-osrm.org/route/v1/driving/{a['lon']},{a['lat']};{b['lon']},{b['lat']}?overview=false")
        r = d["routes"][0]
    except Exception:
        km = _km(a, b)
        return f"Routing service unavailable. Straight-line distance {a['name']} to {b['name']}: " + (f"{km * 0.621:.0f} miles." if imp else f"{km:.0f} km.")
    km, mins = r["distance"] / 1000, r["duration"] / 60
    dist = f"{km * 0.621:.0f} miles" if imp else f"{km:.0f} km"
    dur = f"{int(mins // 60)} h {int(mins % 60)} min" if mins >= 60 else f"{mins:.0f} min"
    return (f"Driving {a['name']} → {b['name']}: about {dist}, {dur} without traffic. "
            f"(Source: OpenStreetMap/OSRM; real traffic not included.)")
