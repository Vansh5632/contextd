#!/bin/sh
# Host-side pytest for the harness. PYTHONPATH is cleared because host ROS
# site-packages on it break pytest plugin loading.
set -eu
cd "$(dirname "$0")/.."
exec env -u PYTHONPATH .venv/bin/python -m pytest "$@"
