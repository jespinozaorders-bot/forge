#!/bin/bash
# Installs Forge on macOS and keeps it running with launchd.
#   bash install.sh              set up the database, config and background jobs
#   bash install.sh --refresh    re-create the background jobs only (used by update.sh)
#   bash install.sh --uninstall  remove the background jobs (keeps your data)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
UIDN="$(id -u)"
JOBS=(com.forge.server com.forge.weekly com.forge.backup)
PY="$(command -v python3)"

case "$DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    echo "Move the forge folder to your home folder first (e.g. ~/forge)."
    echo "macOS blocks background jobs from reading Documents, Desktop and Downloads."
    exit 1;;
esac

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "Python 3.9 or newer is needed. Install it with: brew install python@3.12"; exit 1; }

loaded() { launchctl print "gui/$UIDN/$1" >/dev/null 2>&1; }

unload() {
  for l in "$@"; do
    launchctl bootout "gui/$UIDN/$l" 2>/dev/null || true
    for _ in $(seq 1 30); do loaded "$l" || break; sleep 0.5; done
    rm -f "$AGENTS/$l.plist"
  done
}

start() {
  for _ in 1 2 3 4 5; do
    launchctl bootstrap "gui/$UIDN" "$AGENTS/$1.plist" 2>/dev/null && return 0
    loaded "$1" && return 0
    sleep 2
  done
  echo "Couldn't start $1. Try: launchctl bootstrap gui/$UIDN $AGENTS/$1.plist" >&2
  return 1
}

plist() {  # label, schedule xml, then the forge command
  local label="$1" sched="$2"; shift 2
  local args=""
  for a in "$@"; do args+="    <string>$a</string>"$'\n'; done
  cat > "$AGENTS/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>-m</string><string>forge</string>
$args  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StandardOutPath</key><string>$DIR/data/logs/$label.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/logs/$label.log</string>
$sched
</dict></plist>
EOF
}

if [[ "${1:-}" == "--uninstall" ]]; then
  unload "${JOBS[@]}"
  echo "Background jobs removed. Your data is still in $DIR/data."
  exit 0
fi

mkdir -p "$AGENTS" "$DIR/data/logs"
chmod +x "$DIR/forgectl" "$DIR/update.sh" 2>/dev/null || true

if [[ "${1:-}" != "--refresh" ]]; then
  (cd "$DIR" && "$PY" -m forge init >/dev/null)
  echo "Downloading exercise pictures (free-exercise-db)..."
  (cd "$DIR" && "$PY" -m forge library) || echo "Skipped pictures for now. Run ./forgectl library later."
fi

unload "${JOBS[@]}"
# the app itself, always on
plist com.forge.server "  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>" serve
# weekly overload report: Sundays 6:37 pm
plist com.forge.weekly "  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>37</integer></dict>" weekly
# nightly database backup: 3:13 am
plist com.forge.backup "  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>13</integer></dict>" backup
for j in "${JOBS[@]}"; do start "$j"; done

sleep 1
echo
echo "Forge is running."
(cd "$DIR" && "$PY" -m forge info)
