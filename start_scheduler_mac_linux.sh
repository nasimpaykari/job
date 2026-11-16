#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo 'Run start_mac_linux.sh once to install Job Coach and create your account first.'
  exit 1
fi
echo 'Background worker only. Keep this process and computer running. Press Ctrl+C to stop.'
exec .venv/bin/python main.py --scheduler-only
