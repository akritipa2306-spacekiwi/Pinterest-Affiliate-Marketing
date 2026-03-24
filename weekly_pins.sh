#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/akritiparida/Pinterest-Affiliate-Marketing"
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

cd "$PROJECT_DIR"

# Load all API keys from .env
set -a
source .env
set +a

mkdir -p logs
TODAY=$(date +%F)
MONTH=$(date +%m)
LOG="logs/weekly_pins.log"

{
  echo "===== Run started: $(date) ====="

  # ── Monthly: refresh search queries (1st of each month) ──────────────────
  if [ "$MONTH" != "$(cat logs/last_discovery_month 2>/dev/null || echo 'none')" ]; then
    echo "--- Running discovery agent (monthly refresh) ---"
    /usr/bin/python3 discovery_agent.py
    echo "$MONTH" > logs/last_discovery_month
    echo "--- Discovery complete ---"
  fi

  # ── Weekly: generate 6 pins + images ─────────────────────────────────────
  echo "--- Generating 6 pins ---"
  /usr/bin/python3 pin_generator.py --count 6

  echo "--- Generating images ---"
  /usr/bin/python3 image_generator.py --count 6

  # ── Push all outputs to git so Lovable picks them up ─────────────────────
  git add generated_pins/latest.json
  git add generated_pins/latest_with_images.json
  git add generated_pins/images/
  git commit -m "Weekly pins: ${TODAY}"
  git push origin main

  echo "===== Run finished: $(date) ====="
} >> "$LOG" 2>&1
