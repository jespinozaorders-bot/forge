"""Load and save routines and workouts as whole documents (exercise list + sets).
The app always sends the full document, which keeps phone/desktop sync simple."""
from datetime import datetime, timezone

from . import db


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v, cast=float):
    if v is None or v == "":
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


# ---------- routines ----------

def routine_doc(rid):
    r = db.one("SELECT * FROM routines WHERE id=?", (rid,))
    if not r:
        return None
    exs = db.rows("""SELECT re.*, e.name, e.kind, e.equipment, e.muscle, e.image_url
                     FROM routine_exercises re JOIN exercises e ON e.id=re.exercise_id
                     WHERE routine_id=? ORDER BY position""", (rid,))
    for ex in exs:
        ex["sets"] = db.rows("SELECT * FROM routine_sets WHERE routine_exercise_id=? ORDER BY position", (ex["id"],))
    r["exercises"] = exs
    return r


def save_routine(doc, rid=None):
    c = db.conn()
    name = (doc.get("name") or "Untitled routine").strip()
    if rid is None:
        pos = (db.one("SELECT COALESCE(MAX(position),0)+1 p FROM routines") or {"p": 0})["p"]
        rid = c.execute("INSERT INTO routines(name,folder_id,notes,position,updated_at) VALUES(?,?,?,?,?)",
                        (name, doc.get("folder_id"), doc.get("notes", ""), pos, now_iso())).lastrowid
    else:
        c.execute("UPDATE routines SET name=?,folder_id=?,notes=?,updated_at=? WHERE id=?",
                  (name, doc.get("folder_id"), doc.get("notes", ""), now_iso(), rid))
        c.execute("DELETE FROM routine_exercises WHERE routine_id=?", (rid,))
    for i, ex in enumerate(doc.get("exercises") or []):
        reid = c.execute("""INSERT INTO routine_exercises(routine_id,exercise_id,position,superset,rest_seconds,
                            rep_min,rep_max,notes) VALUES(?,?,?,?,?,?,?,?)""",
                         (rid, int(ex["exercise_id"]), i, _num(ex.get("superset"), int),
                          _num(ex.get("rest_seconds"), int), _num(ex.get("rep_min"), int),
                          _num(ex.get("rep_max"), int), ex.get("notes", ""))).lastrowid
        for j, s in enumerate(ex.get("sets") or []):
            c.execute("""INSERT INTO routine_sets(routine_exercise_id,position,set_type,weight,reps,duration,distance)
                         VALUES(?,?,?,?,?,?,?)""",
                      (reid, j, s.get("set_type") or "normal", _num(s.get("weight")), _num(s.get("reps"), int),
                       _num(s.get("duration"), int), _num(s.get("distance"))))
    c.commit()
    return rid


# ---------- workouts ----------

def workout_doc(wid):
    w = db.one("SELECT * FROM workouts WHERE id=?", (wid,))
    if not w:
        return None
    exs = db.rows("""SELECT we.*, e.name, e.kind, e.equipment, e.muscle, e.image_url
                     FROM workout_exercises we JOIN exercises e ON e.id=we.exercise_id
                     WHERE workout_id=? ORDER BY position""", (wid,))
    for ex in exs:
        ex["sets"] = db.rows("SELECT * FROM workout_sets WHERE workout_exercise_id=? ORDER BY position", (ex["id"],))
    w["exercises"] = exs
    return w


def save_workout(doc, wid=None, source="forge"):
    c = db.conn()
    name = (doc.get("name") or "Workout").strip()
    started = doc.get("started_at") or now_iso()
    if wid is None:
        wid = c.execute("""INSERT INTO workouts(name,routine_id,started_at,ended_at,notes,source,updated_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (name, doc.get("routine_id"), started, doc.get("ended_at"), doc.get("notes", ""),
                         source, now_iso())).lastrowid
    else:
        c.execute("UPDATE workouts SET name=?,routine_id=?,started_at=?,ended_at=?,notes=?,updated_at=? WHERE id=?",
                  (name, doc.get("routine_id"), started, doc.get("ended_at"), doc.get("notes", ""), now_iso(), wid))
        c.execute("DELETE FROM workout_exercises WHERE workout_id=?", (wid,))
    for i, ex in enumerate(doc.get("exercises") or []):
        weid = c.execute("""INSERT INTO workout_exercises(workout_id,exercise_id,position,superset,rest_seconds,notes)
                            VALUES(?,?,?,?,?,?)""",
                         (wid, int(ex["exercise_id"]), i, _num(ex.get("superset"), int),
                          _num(ex.get("rest_seconds"), int), ex.get("notes", ""))).lastrowid
        for j, s in enumerate(ex.get("sets") or []):
            c.execute("""INSERT INTO workout_sets(workout_exercise_id,position,set_type,weight,reps,rpe,duration,
                         distance,done) VALUES(?,?,?,?,?,?,?,?,?)""",
                      (weid, j, s.get("set_type") or "normal", _num(s.get("weight")), _num(s.get("reps"), int),
                       _num(s.get("rpe")), _num(s.get("duration"), int), _num(s.get("distance")),
                       1 if s.get("done") else 0))
    c.commit()
    return wid


def finish_cleanup(wid):
    """On finish: drop sets never ticked off, then exercises left with no sets."""
    c = db.conn()
    c.execute("""DELETE FROM workout_sets WHERE done=0 AND workout_exercise_id IN
                 (SELECT id FROM workout_exercises WHERE workout_id=?)""", (wid,))
    c.execute("""DELETE FROM workout_exercises WHERE workout_id=? AND id NOT IN
                 (SELECT workout_exercise_id FROM workout_sets)""", (wid,))
    c.commit()


def workout_list(limit=30, before=None):
    args = []
    where = "WHERE ended_at IS NOT NULL"
    if before:
        where += " AND started_at < ?"
        args.append(before)
    ws = db.rows(f"SELECT * FROM workouts {where} ORDER BY started_at DESC LIMIT ?", args + [limit])
    for w in ws:
        summ = db.rows("""SELECT e.name, COUNT(s.id) sets, MAX(s.weight) top
                          FROM workout_exercises we JOIN exercises e ON e.id=we.exercise_id
                          LEFT JOIN workout_sets s ON s.workout_exercise_id=we.id AND s.set_type!='warmup'
                          WHERE we.workout_id=? GROUP BY we.id ORDER BY we.position""", (w["id"],))
        w["exercises"] = summ
        tot = db.one("""SELECT COALESCE(SUM(s.weight*s.reps),0) vol, COUNT(s.id) sets
                        FROM workout_sets s JOIN workout_exercises we ON we.id=s.workout_exercise_id
                        WHERE we.workout_id=? AND s.set_type!='warmup'""", (w["id"],))
        w["volume"] = tot["vol"]
        w["set_count"] = tot["sets"]
    return ws
