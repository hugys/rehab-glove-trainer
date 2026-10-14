#!/bin/bash
# Double-click to start the Rehab Glove Trainer (uses the project's own Python environment).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up the Python environment (takes a few minutes)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || { echo "Setup failed"; read -n 1; exit 1; }
fi
.venv/bin/python main.py "$@"
