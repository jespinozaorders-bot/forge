"""Weekly overload report, sent to Discord (optional). Suggestions only:
nothing in your routines is changed."""
import json
import urllib.request

from . import config, stats

ICON = {"increase": "⬆️", "deload": "⬇️", "hold": "⏸️", "reps": "➕"}


def report_text(days=7):
    items = stats.progression(days)
    if not items:
        return "No lifting logged in the last 7 days, so there's nothing to suggest this week."
    lines = ["**Forge weekly overload check** (suggestions only, nothing was changed)"]
    for it in items:
        u = it["units"]
        last = f"{it['last_weight']:g} {u} × {', '.join(str(r) for r in it['last_reps'])}"
        nxt = f"{it['weight']:g} {u} × {it['reps']}"
        lines.append(f"{ICON[it['action']]} **{it['exercise']}**: last {last} → try {nxt}. {it['reason']}")
    return "\n".join(lines)


def send_weekly():
    cfg = config.load()
    text = report_text()
    hook = cfg.get("discord_webhook")
    if not hook:
        print(text)
        return False
    # Discord caps messages at 2000 characters
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for ch in chunks:
        req = urllib.request.Request(hook, data=json.dumps({"content": ch}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "Forge"})
        urllib.request.urlopen(req, timeout=20).read()
    return True
