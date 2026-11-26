"""Command line: ./forge <command>"""
import argparse
import socket
import sys

from . import config, db


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main(argv=None):
    p = argparse.ArgumentParser(prog="forge")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the app")
    sub.add_parser("init", help="create the database and config")
    sub.add_parser("info", help="show the address and access key for your iPhone")
    sub.add_parser("library", help="download exercise pictures/instructions (free-exercise-db)")
    imp = sub.add_parser("import-hevy", help="import a Hevy CSV export")
    imp.add_argument("csv")
    sub.add_parser("weekly", help="send the weekly overload report (Discord, or print)")
    sub.add_parser("suggest", help="print this week's overload suggestions")
    sub.add_parser("backup", help="copy the database to data/backups (keeps 30)")
    a = p.parse_args(argv)

    if a.cmd == "serve":
        from .server import serve
        serve()
    elif a.cmd == "init":
        cfg = config.ensure()
        db.init()
        print("Database ready:", db.DEFAULT_PATH)
        print("Access key:", cfg["access_key"])
    elif a.cmd == "info":
        cfg = config.ensure()
        host = socket.gethostname()
        print(f"On your iPhone (same Wi-Fi), open:  http://{_lan_ip()}:{cfg['port']}")
        print(f"  or by name:                       http://{host.split('.')[0]}.local:{cfg['port']}")
        print(f"Access key:                         {cfg['access_key']}")
        print(f"Heart-rate URL for the Shortcut:    http://{_lan_ip()}:{cfg['port']}/api/health/heart-rate")
    elif a.cmd == "library":
        from . import library
        db.init()
        print("Downloading free-exercise-db ...")
        print(library.apply(library.download()))
    elif a.cmd == "import-hevy":
        from . import hevy_import
        db.init()
        with open(a.csv, "rb") as f:
            print(hevy_import.import_csv(f.read()))
    elif a.cmd == "weekly":
        from . import notify
        db.init()
        sent = notify.send_weekly()
        print("Sent to Discord." if sent else "(No Discord webhook set in config.json; printed above.)")
    elif a.cmd == "suggest":
        from . import notify
        db.init()
        print(notify.report_text())
    elif a.cmd == "backup":
        import datetime
        import os
        import sqlite3
        db.init()
        folder = os.path.join(os.path.dirname(db.DEFAULT_PATH), "backups")
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, f"forge-{datetime.date.today().isoformat()}.db")
        out = sqlite3.connect(dest)
        db.conn().backup(out)
        out.close()
        for old in sorted(f for f in os.listdir(folder) if f.endswith(".db"))[:-30]:
            os.remove(os.path.join(folder, old))
        print("Backed up to", dest)
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
