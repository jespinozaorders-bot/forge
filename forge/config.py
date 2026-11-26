"""config.json next to the app. Created by install.sh from config.example.json."""
import json
import os
import secrets

from .db import ROOT

PATH = os.path.join(ROOT, "config.json")
DEFAULTS = {"host": "0.0.0.0", "port": 8790, "access_key": "", "discord_webhook": ""}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(PATH):
        with open(PATH) as f:
            cfg.update(json.load(f))
    return cfg


def ensure():
    """Create config.json with a fresh access key if it doesn't exist yet."""
    cfg = load()
    if not cfg.get("access_key"):
        cfg["access_key"] = secrets.token_urlsafe(18)
        with open(PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(PATH, 0o600)
    return cfg
