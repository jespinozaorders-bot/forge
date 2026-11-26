"""Heart rate from Apple Health (recorded by the Apple Watch).

Accepted payloads on POST /api/health/heart-rate:
  * text/plain, one sample per line: "<date>,<bpm>"  (what the iPhone Shortcut sends)
  * JSON {"samples":[{"date": "...", "bpm": 120}, ...]}
  * JSON from the "Health Auto Export" app (metrics heart_rate / resting_heart_rate)
"""
import json
import re
from datetime import datetime, timezone

from . import db

_FORMATS = ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%b %d, %Y at %I:%M:%S %p", "%b %d, %Y at %I:%M %p",
            "%m/%d/%y, %I:%M:%S %p", "%m/%d/%Y, %I:%M:%S %p"]


def parse_date(s):
    s = (s or "").strip().replace(" ", " ")
    if not s:
        return None
    if re.fullmatch(r"\d{9,11}(\.\d+)?", s):
        return datetime.fromtimestamp(float(s), timezone.utc)
    t = s.replace("Z", "+0000")
    t = re.sub(r"([+-]\d\d):(\d\d)$", r"\1\2", t)
    for f in _FORMATS:
        try:
            d = datetime.strptime(t, f)
            if d.tzinfo is None:
                d = d.astimezone()  # naive -> this machine's local time
            return d
        except ValueError:
            continue
    return None


def _num(v):
    try:
        return float(str(v).split()[0])
    except (ValueError, IndexError, TypeError):
        return None


def parse_payload(body, content_type=""):
    """Returns (hr_samples [(ts,bpm)], resting [(day,bpm)])."""
    hr, rest = [], []
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    data = None
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if data is not None:
        if isinstance(data, list):
            data = {"samples": data}
        for s in data.get("samples", []) or []:
            d, b = parse_date(str(s.get("date") or s.get("startDate") or "")), _num(s.get("bpm") or s.get("value"))
            if d and b:
                hr.append((int(d.timestamp()), b))
        for m in ((data.get("data") or {}).get("metrics") or []):
            name = (m.get("name") or "").lower()
            for p in m.get("data") or []:
                d = parse_date(str(p.get("date", "")))
                b = _num(p.get("Avg") or p.get("avg") or p.get("qty"))
                if not d or not b:
                    continue
                if name == "heart_rate":
                    hr.append((int(d.timestamp()), b))
                elif name == "resting_heart_rate":
                    rest.append((d.strftime("%Y-%m-%d"), b))
        if "resting" in data:
            for p in data["resting"]:
                d = parse_date(str(p.get("date", "")))
                if d and _num(p.get("bpm")):
                    rest.append((d.strftime("%Y-%m-%d"), _num(p.get("bpm"))))
        return hr, rest
    # plain text lines: "<date>,<bpm>" or "<date> | <bpm>" ; a line starting with "resting" is resting HR
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        if not line:
            continue
        is_rest = line.lower().startswith("resting")
        if is_rest:
            line = line.split(None, 1)[1] if " " in line else ""
        m = re.match(r"^(.*?)[,|;\t]\s*([\d.]+)(\s*(count/min|bpm))?\s*$", line)
        if not m:
            continue
        d, b = parse_date(m.group(1)), _num(m.group(2))
        if d and b and 25 <= b <= 250:
            if is_rest:
                rest.append((d.strftime("%Y-%m-%d"), b))
            else:
                hr.append((int(d.timestamp()), b))
    return hr, rest


def ingest(body, content_type=""):
    hr, rest = parse_payload(body, content_type)
    c = db.conn()
    c.executemany("INSERT OR REPLACE INTO hr_samples(ts,bpm) VALUES(?,?)", hr)
    c.executemany("INSERT OR REPLACE INTO resting_hr(day,bpm) VALUES(?,?)", rest)
    c.commit()
    return {"heart_rate": len(hr), "resting": len(rest)}


def max_hr():
    m = db.get_setting("max_hr")
    if m:
        try:
            return float(m)
        except ValueError:
            pass
    by = db.get_setting("birth_year")
    if by:
        try:
            return 220 - (datetime.now().year - int(by))
        except ValueError:
            pass
    return 190.0


ZONES = [(0.5, 0.6, "Z1 Warm-up"), (0.6, 0.7, "Z2 Fat burn"), (0.7, 0.8, "Z3 Aerobic"),
         (0.8, 0.9, "Z4 Threshold"), (0.9, 9.9, "Z5 Max")]


def workout_hr(started_at, ended_at):
    from .stats import _parse
    if not ended_at:
        return None
    a, b = int(_parse(started_at).timestamp()), int(_parse(ended_at).timestamp())
    pts = db.rows("SELECT ts,bpm FROM hr_samples WHERE ts BETWEEN ? AND ? ORDER BY ts", (a - 60, b + 60))
    if len(pts) < 2:
        return None
    mx = max_hr()
    # time in zone: each sample covers the gap to the next one (capped at 2 min)
    zone_secs = [0] * len(ZONES)
    for i, p in enumerate(pts):
        nxt = pts[i + 1]["ts"] if i + 1 < len(pts) else p["ts"] + 5
        dur = min(120, max(0, nxt - p["ts"]))
        pct = p["bpm"] / mx
        for zi, (lo, hi, _) in enumerate(ZONES):
            if lo <= pct < hi:
                zone_secs[zi] += dur
                break
    # downsample to <=120 points for the chart
    step = max(1, len(pts) // 120)
    series = [{"t": p["ts"] - a, "bpm": round(p["bpm"])} for p in pts[::step]]
    bpms = [p["bpm"] for p in pts]
    return {"avg": round(sum(bpms) / len(bpms)), "max": round(max(bpms)), "min": round(min(bpms)),
            "max_hr": mx, "series": series,
            "zones": [{"zone": z[2], "seconds": s} for z, s in zip(ZONES, zone_secs)]}


def resting_trend(days=90):
    return db.rows("SELECT day, bpm FROM resting_hr ORDER BY day DESC LIMIT ?", (days,))[::-1]
