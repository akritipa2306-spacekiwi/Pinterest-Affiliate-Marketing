#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/akritiparida/Pinterest-Affiliate-Marketing"
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

cd "$PROJECT_DIR"

# Source .env for ANTHROPIC_API_KEY
set -a
source .env
set +a

# Create logs directory if it doesn't exist
mkdir -p logs

TODAY=$(date +%F)
LOG="logs/weekly_pins.log"

{
  echo "===== Run started: $(date) ====="

  # Generate pins
  /usr/bin/python3 pin_generator.py --count 3

  # Copy dated file to latest.json
  cp "generated_pins/${TODAY}.json" generated_pins/latest.json

  # Commit and push
  git add "generated_pins/${TODAY}.json" generated_pins/latest.json pin_tracker.json
  git commit -m "Weekly pins: ${TODAY}"
  git push origin main

  echo "===== Run finished: $(date) ====="
} >> "$LOG" 2>&1
