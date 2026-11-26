"""Exercise pictures and instructions from the open free-exercise-db project
(https://github.com/yuhonas/free-exercise-db, released into the public domain).

`./forge library` downloads it once, attaches pictures to the built-in exercises
it can match by name, and adds the rest of its exercises to the library."""
import json
import re
import urllib.request

from . import db

DATA_URL = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
IMG_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"

EQUIP = {"body only": "none", "e-z curl bar": "barbell", "cable": "cable", "machine": "machine",
         "barbell": "barbell", "dumbbell": "dumbbell", "kettlebells": "kettlebell", "bands": "other",
         "other": "other", "exercise ball": "other", "medicine ball": "other", "foam roll": "other"}
MUSCLE = {"middle back": "upper back", "lower back": "lower back", "lats": "lats", "traps": "traps",
          "chest": "chest", "shoulders": "shoulders", "biceps": "biceps", "triceps": "triceps",
          "forearms": "forearms", "quadriceps": "quadriceps", "hamstrings": "hamstrings",
          "glutes": "glutes", "calves": "calves", "abdominals": "abdominals", "abductors": "abductors",
          "adductors": "adductors", "neck": "neck"}
STOP = {"the", "a", "with", "on", "to", "of", "and", "-", "medium", "grip", "standing", "seated"}


def _tokens(name):
    n = name.lower().replace("dumbbell", "dumbbell ").replace("barbell", "barbell ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    toks = {t.rstrip("s") for t in n.split() if t not in STOP}
    return toks


def _score(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0
    return len(ta & tb) / len(ta | tb)


def download():
    with urllib.request.urlopen(DATA_URL, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def apply(items):
    c = db.conn()
    mine = db.rows("SELECT id, name, equipment FROM exercises WHERE image_url='' OR image_url IS NULL")
    matched, added = 0, 0
    used = set()
    for ex in mine:
        best, bs = None, 0.0
        for it in items:
            eq = EQUIP.get((it.get("equipment") or "other").lower(), "other")
            s = _score(ex["name"], it["name"]) + (0.15 if eq == ex["equipment"] else 0)
            if s > bs:
                best, bs = it, s
        if best and bs >= 0.6:
            imgs = best.get("images") or []
            c.execute("UPDATE exercises SET image_url=?, image_url2=?, instructions=? WHERE id=?",
                      (IMG_BASE + imgs[0] if imgs else "", IMG_BASE + imgs[1] if len(imgs) > 1 else "",
                       "\n".join(best.get("instructions") or []), ex["id"]))
            used.add(best["id"])
            matched += 1
    have = {r["name"].lower() for r in db.rows("SELECT name FROM exercises")}
    for it in items:
        if it["id"] in used or it["name"].lower() in have:
            continue
        if (it.get("category") or "") in ("stretching",):
            continue
        imgs = it.get("images") or []
        muscles = it.get("primaryMuscles") or []
        cat = it.get("category")
        kind = "distance_duration" if cat == "cardio" else (
            "bodyweight_reps" if (it.get("equipment") or "") == "body only" else "weight_reps")
        c.execute("""INSERT INTO exercises(name,equipment,muscle,secondary,kind,image_url,image_url2,instructions)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (it["name"], EQUIP.get((it.get("equipment") or "other").lower(), "other"),
                   MUSCLE.get(muscles[0], muscles[0]) if muscles else "",
                   ",".join(MUSCLE.get(m, m) for m in it.get("secondaryMuscles") or []), kind,
                   IMG_BASE + imgs[0] if imgs else "", IMG_BASE + imgs[1] if len(imgs) > 1 else "",
                   "\n".join(it.get("instructions") or [])))
        added += 1
    c.commit()
    return {"pictures_matched": matched, "exercises_added": added}
