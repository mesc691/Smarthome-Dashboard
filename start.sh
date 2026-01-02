#!/bin/bash
# =============================================================================
# Smart Home Dashboard - Startskript
# =============================================================================

# Ins Skript-Verzeichnis wechseln
cd "$(dirname "$0")"

# Display setzen falls nicht gesetzt
export DISPLAY="${DISPLAY:-:0}"

# Dashboard starten
exec python3 netatmo_dashboard.py "$@"
