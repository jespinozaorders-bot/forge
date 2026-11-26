"""Import workout history from a Hevy CSV export (Hevy: Settings > Export & Import Data).

Columns used (others are ignored): title, start_time, end_time, description,
exercise_title, superset_id, exercise_notes, set_index, set_type,
weight_lbs | weight_kg, reps, distance_miles | distance_km, duration_seconds, rpe.
Running it twice is safe: workouts already imported (same title + start) are skipped.
"""
import csv
import io
from datetime import datetime, timezone

from . import db, docs

TIME_FORMATS = ["%d %b %Y, %H:%M", "%d %b %Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%b %d, %Y, %I:%M %p", "%m/%d/%Y %H:%M"]
SET_TYPES = {"normal": "normal", "warmup": "warmup", "dropset": "drop", "drop": "drop", "failure": "failure"}


def _time(s):
    s = (s or "").strip()
    for f in TIME_FORMATS:
        try:
            # Hevy exports local wall-clock time; store as UTC
            return datetime.strptime(s, f).astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _guess_kind(name):
    n = name.lower()
    if "assisted" in n:
        return "assisted_bodyweight"
    if "weighted" in n:
        return "weighted_bodyweight"
    if any(k in n for k in ("plank", "hold", "stretch")):
        return "duration"
    return "weight_reps"


def _exercise_id(name, cache):
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    r = db.one("SELECT id FROM exercises WHERE name=? COLLATE NOCASE", (name.strip(),))
    if r:
        eid = r["id"]
    else:
        eq = ""
        if "(" in name:
            eq = name.rsplit("(", 1)[1].rstrip(")").lower()
        c = db.conn()
        eid = c.execute("INSERT INTO exercises(name,equipment,kind,custom) VALUES(?,?,?,1)",
                        (name.strip(), eq, _guess_kind(name))).lastrowid
        c.commit()
    cache[key] = eid
    return eid


def import_csv(text, units=None):
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    cols = reader.fieldnames or []
    if "exercise_title" not in cols or "start_time" not in cols:
        raise ValueError("This doesn't look like a Hevy export (missing exercise_title/start_time columns).")
    to_lbs = "weight_kg" in cols and (units or db.get_setting("units", "lbs")) == "lbs"
    to_kg = "weight_lbs" in cols and (units or db.get_setting("units", "lbs")) == "kg"

    workouts = {}
    order = []
    for row in reader:
        key = (row.get("title", ""), row.get("start_time", ""))
        if key not in workouts:
            workouts[key] = {"name": row.get("title") or "Workout", "started_at": _time(row.get("start_time")),
                             "ended_at": _time(row.get("end_time")), "notes": row.get("description") or "",
                             "exercises": [], "_ex": {}}
            order.append(key)
        w = workouts[key]
        ex_name = row["exercise_title"]
        sup = row.get("superset_id")
        # rows arrive in order; a new block starts whenever the exercise changes
        if not w["exercises"] or w["exercises"][-1]["_name"] != ex_name:
            w["exercises"].append({"_name": ex_name, "superset": int(_f(sup)) if _f(sup) is not None else None,
                                   "notes": row.get("exercise_notes") or "", "sets": []})
        ex = w["exercises"][-1]
        weight = _f(row.get("weight_lbs")) if "weight_lbs" in cols else _f(row.get("weight_kg"))
        if weight is not None:
            if to_lbs:
                weight = round(weight * 2.20462, 1)
            elif to_kg:
                weight = round(weight / 2.20462, 1)
        dist = _f(row.get("distance_miles")) if "distance_miles" in cols else _f(row.get("distance_km"))
        ex["sets"].append({"set_type": SET_TYPES.get((row.get("set_type") or "normal").lower(), "normal"),
                           "weight": weight, "reps": _f(row.get("reps")), "rpe": _f(row.get("rpe")),
                           "duration": _f(row.get("duration_seconds")), "distance": dist, "done": True})

    cache, added, skipped = {}, 0, 0
    before = db.one("SELECT COUNT(*) n FROM exercises")["n"]
    for key in order:
        w = workouts[key]
        if not w["started_at"]:
            skipped += 1
            continue
        if db.one("SELECT id FROM workouts WHERE name=? AND started_at=?", (w["name"], w["started_at"])):
            skipped += 1
            continue
        if not w["ended_at"]:
            w["ended_at"] = w["started_at"]
        for ex in w["exercises"]:
            ex["exercise_id"] = _exercise_id(ex["_name"], cache)
        docs.save_workout(w, source="hevy")
        added += 1
    return {"imported": added, "skipped": skipped, "new_exercises": db.one("SELECT COUNT(*) n FROM exercises")["n"] - before}
