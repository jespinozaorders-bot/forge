#!/bin/bash
# Pull the latest Forge from GitHub and restart it. Your data in data/ is untouched.
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only
bash install.sh --refresh
