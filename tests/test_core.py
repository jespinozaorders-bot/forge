import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge import db, docs, health, hevy_import, stats  # noqa: E402
from forge import server  # noqa: E402

HEVY_CSV = """title,start_time,end_time,description,exercise_title,superset_id,exercise_notes,set_index,set_type,weight_lbs,reps,distance_miles,duration_seconds,rpe
Push,"10 Nov 2026, 17:00","10 Nov 2026, 18:05",,Bench Press (Barbell),,,0,warmup,135,10,,,
Push,"10 Nov 2026, 17:00","10 Nov 2026, 18:05",,Bench Press (Barbell),,,1,normal,185,12,,,8
Push,"10 Nov 2026, 17:00","10 Nov 2026, 18:05",,Bench Press (Barbell),,,2,normal,185,12,,,9
Push,"10 Nov 2026, 17:00","10 Nov 2026, 18:05",,Cable Lateral Raise Thing,,,0,normal,20,15,,,
Push,"17 Nov 2026, 17:00","17 Nov 2026, 18:00",,Bench Press (Barbell),,,0,normal,190,10,,,
"""


class ForgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db.set_path(os.path.join(self.tmp, "t.db"))
        db.init()

    def ex(self, name):
        return db.one("SELECT id FROM exercises WHERE name=?", (name,))["id"]

    def log(self, name, started, ended, sets, ex="Bench Press (Barbell)"):
        doc = {"name": name, "started_at": started, "ended_at": ended,
               "exercises": [{"exercise_id": self.ex(ex), "sets": [
                   {"weight": w, "reps": r, "done": True, "set_type": t} for (w, r, t) in sets]}]}
        return docs.save_workout(doc)

    def test_prs_and_history(self):
        self.log("A", "2026-11-01T17:00:00Z", "2026-11-01T18:00:00Z", [(135, 10, "warmup"), (185, 8, "normal")])
        w2 = self.log("B", "2026-11-08T17:00:00Z", "2026-11-08T18:00:00Z", [(195, 6, "normal")])
        prs = {p["type"]: p for p in stats.workout_prs(w2)}
        self.assertIn("weight", prs)
        self.assertEqual(prs["weight"]["value"], 195)
        self.assertNotIn("e1rm", prs)  # 195x6 = 234.0 doesn't beat 185x8 = 234.3
        h = stats.exercise_history(self.ex("Bench Press (Barbell)"))
        self.assertEqual(len(h["sessions"]), 2)
        self.assertEqual(h["records"]["weight"], 195)

    def test_progression_increase_and_hold(self):
        from datetime import datetime, timedelta, timezone
        t = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        t2 = (datetime.now(timezone.utc) - timedelta(days=1) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.log("A", t, t2, [(185, 12, "normal"), (185, 12, "normal")])
        self.log("B", t, t2, [(30, 5, "normal")], ex="Lateral Raise (Dumbbell)")
        out = {o["exercise"]: o for o in stats.progression()}
        self.assertEqual(out["Bench Press (Barbell)"]["action"], "increase")
        self.assertEqual(out["Bench Press (Barbell)"]["weight"], 190)
        self.assertEqual(out["Lateral Raise (Dumbbell)"]["action"], "hold")

    def test_finish_cleanup_drops_unticked(self):
        wid = docs.save_workout({"name": "X", "started_at": "2026-11-01T17:00:00Z", "exercises": [
            {"exercise_id": self.ex("Push Up"), "sets": [{"reps": 10, "done": True}, {"reps": 10}]},
            {"exercise_id": self.ex("Crunch"), "sets": [{"reps": 10}]}]})
        docs.finish_cleanup(wid)
        w = docs.workout_doc(wid)
        self.assertEqual(len(w["exercises"]), 1)
        self.assertEqual(len(w["exercises"][0]["sets"]), 1)

    def test_hevy_import(self):
        r = hevy_import.import_csv(HEVY_CSV.encode())
        self.assertEqual(r["imported"], 2)
        self.assertEqual(r["new_exercises"], 1)
        again = hevy_import.import_csv(HEVY_CSV.encode())
        self.assertEqual(again["imported"], 0)
        w = docs.workout_list()
        self.assertEqual(len(w), 2)
        push = [x for x in w if x["set_count"] == 3][0]  # warm-up not counted
        self.assertEqual(len(push["exercises"]), 2)

    def test_heart_rate_text_and_workout_stats(self):
        lines = "\n".join(f"2026-11-01T17:{m:02d}:00Z,{100 + m}" for m in range(0, 60, 2))
        lines += "\nresting 2026-11-01T08:00:00Z,58\ngarbage line"
        r = health.ingest(lines.encode(), "text/plain")
        self.assertEqual(r, {"heart_rate": 30, "resting": 1})
        hr = health.workout_hr("2026-11-01T17:00:00Z", "2026-11-01T18:00:00Z")
        self.assertEqual(hr["max"], 158)
        self.assertTrue(sum(z["seconds"] for z in hr["zones"]) > 0)

    def test_heart_rate_shortcut_date_format(self):
        r = health.ingest("Nov 1, 2026 at 5:02:10 PM,121\n".encode(), "text/plain")
        self.assertEqual(r["heart_rate"], 1)

    def test_heart_rate_auto_export_json(self):
        body = json.dumps({"data": {"metrics": [
            {"name": "heart_rate", "units": "count/min", "data": [
                {"date": "2026-11-01 10:00:00 -0800", "Min": 90, "Avg": 110, "Max": 130}]},
            {"name": "resting_heart_rate", "data": [{"date": "2026-11-01 00:00:00 -0800", "qty": 55}]}]}})
        self.assertEqual(health.ingest(body.encode(), "application/json"), {"heart_rate": 1, "resting": 1})


class APITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        db.set_path(os.path.join(cls.tmp, "api.db"))
        cls.srv = server.make_server("127.0.0.1", 0, cfg={"access_key": "k123", "host": "127.0.0.1", "port": 0})
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, method, path, body=None, key="k123", raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Forge-Key": key, "Content-Type": ctype})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_auth(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.call("GET", "/api/bootstrap", key="wrong")
        self.assertEqual(e.exception.code, 401)

    def test_full_flow(self):
        b = self.call("GET", "/api/bootstrap")
        bench = [e for e in b["exercises"] if e["name"] == "Bench Press (Barbell)"][0]["id"]
        r = self.call("POST", "/api/routines", {"name": "Push A", "exercises": [
            {"exercise_id": bench, "rest_seconds": 150, "rep_min": 6, "rep_max": 8,
             "sets": [{"set_type": "warmup", "weight": 135, "reps": 10}, {"weight": 185, "reps": 8}]}]})
        self.assertEqual(len(r["exercises"][0]["sets"]), 2)
        w = self.call("POST", "/api/workouts", {"routine_id": r["id"]})
        self.assertEqual(w["name"], "Push A")
        self.assertEqual(w["exercises"][0]["sets"][1]["weight"], 185)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.call("POST", "/api/workouts", {})
        self.assertEqual(e.exception.code, 409)
        for s in w["exercises"][0]["sets"]:
            s["done"] = True
        w["exercises"][0]["sets"][1]["reps"] = 8
        fin = self.call("POST", f"/api/workouts/{w['id']}/finish", w)
        self.assertIsNotNone(fin["ended_at"])
        self.assertIn("prs", fin)
        self.assertEqual(self.call("GET", "/api/bootstrap")["active"], None)
        self.call("POST", "/api/health/heart-rate", raw=b"2026-11-01T17:00:00Z,120\n", ctype="text/plain")
        st = self.call("GET", "/api/stats")
        self.assertEqual(st["total_workouts"], 1)
        prog = self.call("GET", "/api/progression")
        self.assertEqual(prog[0]["rep_range"], [6, 8])
        self.assertEqual(prog[0]["action"], "increase")


if __name__ == "__main__":
    unittest.main()
