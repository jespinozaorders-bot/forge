"""HTTP server: the web app plus a small JSON API. Python standard library only."""
import json
import mimetypes
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, db, docs, health, hevy_import, stats
from .db import ROOT

STATIC = os.path.join(ROOT, "static")
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class HTTPError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def need(cond, code=404, msg="Not found"):
    if not cond:
        raise HTTPError(code, msg)


def attach_previous(doc):
    for ex in doc.get("exercises", []):
        ex["previous"] = stats.last_performance(ex["exercise_id"], exclude_workout=doc.get("id"))
    return doc


# ---------- bootstrap / settings ----------

@route("GET", "/api/bootstrap")
def bootstrap(req):
    active = db.one("SELECT id FROM workouts WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1")
    return {
        "settings": {r["key"]: r["value"] for r in db.rows("SELECT * FROM settings")},
        "exercises": db.rows("""SELECT id,name,equipment,muscle,secondary,kind,image_url,custom FROM exercises
                                WHERE archived=0 ORDER BY name COLLATE NOCASE"""),
        "folders": db.rows("SELECT * FROM folders ORDER BY position, id"),
        "routines": routine_list(),
        "active": attach_previous(docs.workout_doc(active["id"])) if active else None,
    }


def routine_list():
    rs = db.rows("SELECT * FROM routines ORDER BY position, id")
    for r in rs:
        r["exercises"] = [x["name"] for x in db.rows(
            """SELECT e.name FROM routine_exercises re JOIN exercises e ON e.id=re.exercise_id
               WHERE routine_id=? ORDER BY position""", (r["id"],))]
    return rs


@route("PUT", "/api/settings")
def put_settings(req):
    allowed = {"units", "default_rest", "max_hr", "birth_year", "rep_min", "rep_max", "bodyweight"}
    for k, v in req.json().items():
        if k in allowed:
            db.set_setting(k, v)
    return {"ok": True}


# ---------- exercises ----------

KINDS = {"weight_reps", "bodyweight_reps", "weighted_bodyweight", "assisted_bodyweight", "reps_only",
         "duration", "weight_duration", "distance_duration"}


@route("POST", "/api/exercises")
def new_exercise(req):
    d = req.json()
    name = (d.get("name") or "").strip()
    need(name, 400, "Name is required")
    need(not db.one("SELECT id FROM exercises WHERE name=? COLLATE NOCASE", (name,)), 400,
         "An exercise with that name already exists")
    kind = d.get("kind") if d.get("kind") in KINDS else "weight_reps"
    c = db.conn()
    eid = c.execute("INSERT INTO exercises(name,equipment,muscle,secondary,kind,custom) VALUES(?,?,?,?,?,1)",
                    (name, d.get("equipment", ""), d.get("muscle", ""), d.get("secondary", ""), kind)).lastrowid
    c.commit()
    return db.one("SELECT * FROM exercises WHERE id=?", (eid,))


@route("GET", "/api/exercises/{id}")
def get_exercise(req, id):
    ex = db.one("SELECT * FROM exercises WHERE id=?", (int(id),))
    need(ex)
    ex["history"] = stats.exercise_history(ex["id"])
    return ex


@route("PUT", "/api/exercises/{id}")
def put_exercise(req, id):
    d = req.json()
    ex = db.one("SELECT * FROM exercises WHERE id=?", (int(id),))
    need(ex)
    fields = {k: d[k] for k in ("name", "equipment", "muscle", "secondary", "notes", "archived") if k in d}
    if d.get("kind") in KINDS:
        fields["kind"] = d["kind"]
    fields.pop("notes", None)
    if fields:
        c = db.conn()
        c.execute("UPDATE exercises SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                  list(fields.values()) + [int(id)])
        c.commit()
    return db.one("SELECT * FROM exercises WHERE id=?", (int(id),))


@route("GET", "/api/exercises/{id}/last")
def last_ex(req, id):
    ex = req.query.get("exclude")
    return stats.last_performance(int(id), exclude_workout=int(ex) if ex else None)


# ---------- folders & routines ----------

@route("POST", "/api/folders")
def new_folder(req):
    c = db.conn()
    fid = c.execute("INSERT INTO folders(name,position) VALUES(?,(SELECT COALESCE(MAX(position),0)+1 FROM folders))",
                    ((req.json().get("name") or "New folder").strip(),)).lastrowid
    c.commit()
    return {"id": fid}


@route("PUT", "/api/folders/{id}")
def put_folder(req, id):
    c = db.conn()
    c.execute("UPDATE folders SET name=? WHERE id=?", (req.json().get("name", "Folder"), int(id)))
    c.commit()
    return {"ok": True}


@route("DELETE", "/api/folders/{id}")
def del_folder(req, id):
    c = db.conn()
    c.execute("UPDATE routines SET folder_id=NULL WHERE folder_id=?", (int(id),))
    c.execute("DELETE FROM folders WHERE id=?", (int(id),))
    c.commit()
    return {"ok": True}


@route("GET", "/api/routines")
def list_routines(req):
    return routine_list()


@route("GET", "/api/routines/{id}")
def get_routine(req, id):
    r = docs.routine_doc(int(id))
    need(r)
    return r


@route("POST", "/api/routines")
def post_routine(req):
    return docs.routine_doc(docs.save_routine(req.json()))


@route("PUT", "/api/routines/{id}")
def put_routine(req, id):
    need(db.one("SELECT id FROM routines WHERE id=?", (int(id),)))
    return docs.routine_doc(docs.save_routine(req.json(), int(id)))


@route("DELETE", "/api/routines/{id}")
def del_routine(req, id):
    c = db.conn()
    c.execute("DELETE FROM routines WHERE id=?", (int(id),))
    c.commit()
    return {"ok": True}


@route("POST", "/api/routines/reorder")
def reorder_routines(req):
    c = db.conn()
    for i, rid in enumerate(req.json().get("ids", [])):
        c.execute("UPDATE routines SET position=? WHERE id=?", (i, int(rid)))
    c.commit()
    return {"ok": True}


# ---------- workouts ----------

@route("POST", "/api/workouts")
def start_workout(req):
    """Start a workout, empty or from a routine. Only one can be in progress."""
    active = db.one("SELECT id FROM workouts WHERE ended_at IS NULL")
    need(not active, 409, "A workout is already in progress")
    d = req.json()
    doc = {"name": d.get("name") or "Workout", "started_at": docs.now_iso(), "exercises": []}
    if d.get("routine_id"):
        r = docs.routine_doc(int(d["routine_id"]))
        need(r)
        doc.update(name=r["name"], routine_id=r["id"], notes="")
        for ex in r["exercises"]:
            prev = stats.last_performance(ex["exercise_id"])
            sets, seen = [], {"w": 0, "x": 0}
            for s in ex["sets"] or [{"set_type": "normal", "weight": None, "reps": None, "duration": None,
                                     "distance": None}]:
                # prefill: routine target, else last time's matching set (warm-up to warm-up, work to work)
                k = "w" if s["set_type"] == "warmup" else "x"
                same = [p for p in prev if ("w" if p["set_type"] == "warmup" else "x") == k]
                p = same[seen[k]] if seen[k] < len(same) else ({} if k == "w" or not same else same[-1])
                seen[k] += 1
                sets.append({"set_type": s["set_type"],
                             "weight": s["weight"] if s["weight"] is not None else p.get("weight"),
                             "reps": s["reps"] if s["reps"] is not None else p.get("reps"),
                             "duration": s["duration"] if s["duration"] is not None else p.get("duration"),
                             "distance": s["distance"] if s["distance"] is not None else p.get("distance"),
                             "done": False})
            doc["exercises"].append({"exercise_id": ex["exercise_id"], "superset": ex["superset"],
                                     "rest_seconds": ex["rest_seconds"], "notes": ex["notes"], "sets": sets})
    wid = docs.save_workout(doc)
    return attach_previous(docs.workout_doc(wid))


@route("GET", "/api/workouts")
def list_workouts(req):
    return docs.workout_list(limit=int(req.query.get("limit", 30)), before=req.query.get("before"))


@route("GET", "/api/workouts/{id}")
def get_workout(req, id):
    w = docs.workout_doc(int(id))
    need(w)
    if w["ended_at"]:
        w["prs"] = stats.workout_prs(w["id"])
        w["heart_rate"] = health.workout_hr(w["started_at"], w["ended_at"])
    return attach_previous(w)


@route("PUT", "/api/workouts/{id}")
def put_workout(req, id):
    w = db.one("SELECT * FROM workouts WHERE id=?", (int(id),))
    need(w)
    d = req.json()
    if "ended_at" not in d:
        d["ended_at"] = w["ended_at"]
    docs.save_workout(d, int(id))
    return {"ok": True, "updated_at": docs.now_iso()}


@route("POST", "/api/workouts/{id}/finish")
def finish_workout(req, id):
    wid = int(id)
    need(db.one("SELECT id FROM workouts WHERE id=?", (wid,)))
    d = req.json()
    d["ended_at"] = d.get("ended_at") or docs.now_iso()
    docs.save_workout(d, wid)
    docs.finish_cleanup(wid)
    return get_workout(req, wid)


@route("DELETE", "/api/workouts/{id}")
def del_workout(req, id):
    c = db.conn()
    c.execute("DELETE FROM workouts WHERE id=?", (int(id),))
    c.commit()
    return {"ok": True}


@route("POST", "/api/workouts/{id}/to-routine")
def workout_to_routine(req, id):
    w = docs.workout_doc(int(id))
    need(w)
    doc = {"name": req.json().get("name") or w["name"], "exercises": [
        {"exercise_id": ex["exercise_id"], "superset": ex["superset"], "rest_seconds": ex["rest_seconds"],
         "notes": ex["notes"], "sets": [{"set_type": s["set_type"], "weight": s["weight"], "reps": s["reps"],
                                         "duration": s["duration"], "distance": s["distance"]} for s in ex["sets"]]}
        for ex in w["exercises"]]}
    return docs.routine_doc(docs.save_routine(doc))


# ---------- stats, progression, health ----------

@route("GET", "/api/stats")
def get_stats(req):
    starts = db.rows("SELECT started_at FROM workouts WHERE ended_at IS NOT NULL AND started_at >= date('now','-400 days')")
    total = db.one("SELECT COUNT(*) n FROM workouts WHERE ended_at IS NOT NULL")["n"]
    return {"weeks": stats.weekly_summary(12), "muscles": stats.muscle_split(int(req.query.get("days", 30))),
            "workout_starts": [r["started_at"] for r in starts], "total_workouts": total,
            "resting_hr": health.resting_trend(90),
            "bodyweight": db.rows("SELECT day, weight FROM bodyweight ORDER BY day DESC LIMIT 120")[::-1]}


@route("GET", "/api/progression")
def get_progression(req):
    return stats.progression(int(req.query.get("days", 7)))


@route("POST", "/api/health/heart-rate")
def post_hr(req):
    return health.ingest(req.body, req.headers.get("Content-Type", ""))


@route("POST", "/api/bodyweight")
def post_bw(req):
    d = req.json()
    c = db.conn()
    c.execute("INSERT OR REPLACE INTO bodyweight(day,weight) VALUES(?,?)", (d["day"], float(d["weight"])))
    c.commit()
    return {"ok": True}


@route("POST", "/api/import/hevy")
def import_hevy(req):
    try:
        return hevy_import.import_csv(req.body)
    except ValueError as e:
        raise HTTPError(400, str(e))


@route("GET", "/api/export")
def export(req):
    out = {}
    for t in ("settings", "exercises", "folders", "routines", "routine_exercises", "routine_sets", "workouts",
              "workout_exercises", "workout_sets", "resting_hr", "bodyweight"):
        out[t] = db.rows(f"SELECT * FROM {t}")
    return out


# ---------- plumbing ----------

class Request:
    def __init__(self, handler, body, query):
        self.handler, self.body, self.query = handler, body, query
        self.headers = handler.headers

    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            raise HTTPError(400, "Invalid JSON")


class Handler(BaseHTTPRequestHandler):
    server_version = "Forge"
    cfg = {}

    def log_message(self, fmt, *args):
        if os.environ.get("FORGE_DEBUG"):
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query):
        key = self.cfg.get("access_key")
        if not key:
            return True
        given = (self.headers.get("X-Forge-Key") or query.get("key")
                 or (self.headers.get("Authorization") or "").replace("Bearer ", "").strip())
        if not given:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "forge_key":
                    given = v
        return given == key

    def _static(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        fp = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not fp.startswith(STATIC) or not os.path.isfile(fp):
            fp = os.path.join(STATIC, "index.html")
        with open(fp, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if fp.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        self._send(200, data, ctype)

    def _handle(self, method):
        u = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not u.path.startswith("/api/"):
            if method != "GET":
                return self._send(405, {"error": "Method not allowed"})
            return self._static(u.path)
        if u.path == "/api/ping":
            return self._send(200, {"ok": True, "auth": self._authorized(query)})
        if not self._authorized(query):
            return self._send(401, {"error": "Wrong or missing access key"})
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        for m, rx, fn in ROUTES:
            if m != method:
                continue
            mt = rx.match(u.path)
            if mt:
                try:
                    return self._send(200, fn(Request(self, body, query), **mt.groupdict()))
                except HTTPError as e:
                    return self._send(e.code, {"error": e.msg})
                except Exception as e:  # noqa
                    traceback.print_exc()
                    return self._send(500, {"error": str(e)})
        self._send(404, {"error": "Not found"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")


def make_server(host=None, port=None, cfg=None):
    cfg = cfg if cfg is not None else config.load()
    db.init()
    Handler.cfg = cfg
    return ThreadingHTTPServer((host or cfg["host"], int(port or cfg["port"])), Handler)


def serve():
    cfg = config.ensure()
    srv = make_server(cfg=cfg)
    print(f"Forge running on http://{cfg['host']}:{cfg['port']}")
    srv.serve_forever()
