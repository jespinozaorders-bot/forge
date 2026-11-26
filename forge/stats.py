"""PRs, estimated 1-rep max, per-exercise history, muscle split and weekly
overload suggestions. Suggestions are only ever reported, never applied."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import db

WORK = ("normal", "failure", "drop")  # sets that count (warm-ups don't)


def e1rm(weight, reps):
    """Epley estimate. Only meaningful for 1-12 reps with a load."""
    if not weight or not reps or reps < 1 or weight <= 0:
        return None
    if reps == 1:
        return float(weight)
    if reps > 12:
        return None
    return round(weight * (1 + reps / 30.0), 1)


def _sets_for_exercise(exercise_id, before=None, workout_id=None):
    sql = """SELECT s.*, w.id workout_id, w.started_at, w.name workout_name
             FROM workout_sets s
             JOIN workout_exercises we ON we.id=s.workout_exercise_id
             JOIN workouts w ON w.id=we.workout_id
             WHERE we.exercise_id=? AND w.ended_at IS NOT NULL AND s.set_type IN ('normal','failure','drop')"""
    args = [exercise_id]
    if before:
        sql += " AND w.started_at < ?"
        args.append(before)
    if workout_id:
        sql += " AND w.id = ?"
        args.append(workout_id)
    return db.rows(sql + " ORDER BY w.started_at, s.position", args)


def _bests(sets):
    b = {"weight": None, "e1rm": None, "set_volume": None, "reps": None, "duration": None}
    for s in sets:
        w, r = s["weight"] or 0, s["reps"] or 0
        if w and (b["weight"] is None or w > b["weight"]):
            b["weight"] = w
        est = e1rm(w, r)
        if est and (b["e1rm"] is None or est > b["e1rm"]):
            b["e1rm"] = est
        if w and r and (b["set_volume"] is None or w * r > b["set_volume"]):
            b["set_volume"] = w * r
        if r and (b["reps"] is None or r > b["reps"]):
            b["reps"] = r
        d = s.get("duration") or 0
        if d and (b["duration"] is None or d > b["duration"]):
            b["duration"] = d
    return b


LABELS = {"weight": "Heaviest weight", "e1rm": "Best est. 1RM", "set_volume": "Best set volume",
          "reps": "Most reps", "duration": "Longest time"}


def workout_prs(workout_id):
    """PRs set in this workout, compared against everything logged before it."""
    w = db.one("SELECT * FROM workouts WHERE id=?", (workout_id,))
    if not w:
        return []
    out = []
    for ex in db.rows("""SELECT DISTINCT we.exercise_id, e.name, e.kind FROM workout_exercises we
                         JOIN exercises e ON e.id=we.exercise_id WHERE we.workout_id=?""", (workout_id,)):
        prior = _sets_for_exercise(ex["exercise_id"], before=w["started_at"])
        if not prior:
            continue  # first time doing it: nothing to beat
        now = _bests(_sets_for_exercise(ex["exercise_id"], workout_id=workout_id))
        old = _bests(prior)
        weighted = ex["kind"] in ("weight_reps", "weighted_bodyweight", "weight_duration")
        keys = ["weight", "e1rm", "set_volume"] if weighted else ["reps", "duration"]
        for k in keys:
            if now[k] is not None and (old[k] is None or now[k] > old[k]):
                out.append({"exercise_id": ex["exercise_id"], "exercise": ex["name"], "type": k,
                            "label": LABELS[k], "value": now[k], "previous": old[k]})
    return out


def exercise_history(exercise_id):
    sets = _sets_for_exercise(exercise_id)
    by_w = defaultdict(list)
    meta = {}
    for s in sets:
        by_w[s["workout_id"]].append(s)
        meta[s["workout_id"]] = (s["started_at"], s["workout_name"])
    sessions = []
    for wid, ss in by_w.items():
        b = _bests(ss)
        top = max(ss, key=lambda s: ((s["weight"] or 0), (s["reps"] or 0)))
        sessions.append({"workout_id": wid, "date": meta[wid][0], "workout": meta[wid][1],
                         "best_weight": b["weight"], "e1rm": b["e1rm"], "max_reps": b["reps"],
                         "volume": sum((s["weight"] or 0) * (s["reps"] or 0) for s in ss),
                         "top_set": {"weight": top["weight"], "reps": top["reps"]},
                         "sets": [{"weight": s["weight"], "reps": s["reps"], "rpe": s["rpe"],
                                   "set_type": s["set_type"], "duration": s["duration"]} for s in ss]})
    sessions.sort(key=lambda x: x["date"])
    records = _bests(sets)
    # best weight at each rep count (Hevy-style "set records")
    rep_records = {}
    for s in sets:
        if s["reps"] and s["weight"]:
            r = min(s["reps"], 20)
            if s["weight"] > rep_records.get(r, 0):
                rep_records[r] = s["weight"]
    return {"sessions": sessions, "records": records,
            "rep_records": [{"reps": k, "weight": v} for k, v in sorted(rep_records.items())]}


def last_performance(exercise_id, exclude_workout=None):
    """Sets from the most recent finished workout with this exercise (for 'previous' column)."""
    sql = """SELECT w.id FROM workouts w JOIN workout_exercises we ON we.workout_id=w.id
             WHERE we.exercise_id=? AND w.ended_at IS NOT NULL"""
    args = [exercise_id]
    if exclude_workout:
        sql += " AND w.id != ?"
        args.append(exclude_workout)
    w = db.one(sql + " ORDER BY w.started_at DESC LIMIT 1", args)
    if not w:
        return []
    return db.rows("""SELECT s.set_type, s.weight, s.reps, s.rpe, s.duration, s.distance
                      FROM workout_sets s JOIN workout_exercises we ON we.id=s.workout_exercise_id
                      WHERE we.workout_id=? AND we.exercise_id=? ORDER BY we.position, s.position""",
                   (w["id"], exercise_id))


def muscle_split(days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.rows("""SELECT e.muscle, COUNT(s.id) sets FROM workout_sets s
                      JOIN workout_exercises we ON we.id=s.workout_exercise_id
                      JOIN workouts w ON w.id=we.workout_id JOIN exercises e ON e.id=we.exercise_id
                      WHERE w.ended_at IS NOT NULL AND w.started_at>=? AND s.set_type!='warmup'
                      GROUP BY e.muscle ORDER BY sets DESC""", (since,))


def weekly_summary(weeks=12):
    ws = db.rows("""SELECT w.started_at, w.ended_at,
                    (SELECT COALESCE(SUM(s.weight*s.reps),0) FROM workout_sets s
                     JOIN workout_exercises we ON we.id=s.workout_exercise_id
                     WHERE we.workout_id=w.id AND s.set_type!='warmup') vol,
                    (SELECT COUNT(*) FROM workout_sets s JOIN workout_exercises we ON we.id=s.workout_exercise_id
                     WHERE we.workout_id=w.id AND s.set_type!='warmup') sets
                    FROM workouts w WHERE w.ended_at IS NOT NULL""")
    out = defaultdict(lambda: {"workouts": 0, "volume": 0, "sets": 0, "minutes": 0})
    for w in ws:
        d = _parse(w["started_at"])
        wk = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        o = out[wk]
        o["workouts"] += 1
        o["volume"] += w["vol"] or 0
        o["sets"] += w["sets"] or 0
        if w["ended_at"]:
            o["minutes"] += max(0, (_parse(w["ended_at"]) - d).total_seconds() / 60)
    keys = sorted(out)[-weeks:]
    return [{"week": k, **{kk: round(v) for kk, v in out[k].items()}} for k in keys]


def _parse(s):
    s = s.replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


# ---------- weekly overload suggestions (double progression) ----------

def _increment(ex, units):
    big = units == "lbs"
    lower = ex["muscle"] in ("quadriceps", "hamstrings", "glutes", "lower back")
    if ex["equipment"] == "barbell":
        return (10 if lower else 5) if big else (5 if lower else 2.5)
    if ex["equipment"] == "dumbbell":
        return 5 if big else 2
    return 5 if big else 2.5


def _round_to(x, step):
    return round(round(x / step) * step, 2)


def progression(days=7):
    """For each exercise trained in the last `days`, look at the latest session and
    the one before it and suggest: add weight, add reps, hold, or back off."""
    units = db.get_setting("units", "lbs")
    dmin, dmax = int(db.get_setting("rep_min", 8) or 8), int(db.get_setting("rep_max", 12) or 12)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = db.rows("""SELECT DISTINCT we.exercise_id FROM workout_exercises we JOIN workouts w ON w.id=we.workout_id
                        WHERE w.ended_at IS NOT NULL AND w.started_at>=?""", (since,))
    out = []
    for r in recent:
        ex = db.one("SELECT * FROM exercises WHERE id=?", (r["exercise_id"],))
        if ex["kind"] not in ("weight_reps", "weighted_bodyweight", "bodyweight_reps"):
            continue
        # rep range: from the routine that uses it, else defaults
        rr = db.one("""SELECT rep_min, rep_max FROM routine_exercises WHERE exercise_id=? AND rep_min IS NOT NULL
                       ORDER BY id DESC LIMIT 1""", (ex["id"],))
        lo, hi = (rr["rep_min"], rr["rep_max"] or rr["rep_min"]) if rr else (dmin, dmax)
        sessions = exercise_history(ex["id"])["sessions"]
        if not sessions:
            continue
        last = sessions[-1]
        work = [s for s in last["sets"] if s["set_type"] in ("normal", "failure") and s["reps"]]
        if not work:
            continue
        top_w = max((s["weight"] or 0) for s in work)
        at_top = [s for s in work if (s["weight"] or 0) == top_w]
        reps = [s["reps"] for s in at_top]
        rpes = [s["rpe"] for s in at_top if s["rpe"] is not None]
        hard = bool(rpes) and max(rpes) >= 10
        item = {"exercise_id": ex["id"], "exercise": ex["name"], "last_date": last["date"],
                "last_weight": top_w, "last_reps": reps, "rep_range": [lo, hi], "units": units}
        weighted = ex["kind"] != "bodyweight_reps" and top_w > 0
        if min(reps) >= hi and not hard:
            if weighted:
                inc = _increment(ex, units)
                item.update(action="increase", weight=_round_to(top_w + inc, inc / 2 if inc < 5 else 2.5),
                            reps=lo, reason=f"Hit {hi}+ reps on every working set at {top_w:g}.")
            else:
                item.update(action="reps", weight=top_w, reps=hi + 1,
                            reason=f"Hit {hi}+ reps on every set. Add a rep or add weight.")
        elif min(reps) < lo:
            prev = sessions[-2] if len(sessions) > 1 else None
            prev_miss = prev and any(s["reps"] and s["reps"] < lo and (s["weight"] or 0) >= top_w
                                     for s in prev["sets"] if s["set_type"] in ("normal", "failure"))
            if prev_miss and weighted:
                inc = _increment(ex, units)
                new_w = _round_to(top_w * 0.9, inc / 2 if inc < 5 else 2.5)
                item.update(action="deload", weight=new_w, reps=lo,
                            reason=f"Under {lo} reps two sessions running. Drop ~10% and build back up.")
            else:
                item.update(action="hold", weight=top_w, reps=lo,
                            reason=f"Below {lo} reps last time. Keep the weight and aim for {lo}.")
        else:
            target = min(hi, max(reps) + 1)
            item.update(action="reps", weight=top_w, reps=target,
                        reason=f"Inside the {lo}-{hi} range. Same weight, aim for {target} reps.")
        out.append(item)
    order = {"increase": 0, "deload": 1, "hold": 2, "reps": 3}
    out.sort(key=lambda x: (order[x["action"]], x["exercise"]))
    return out
