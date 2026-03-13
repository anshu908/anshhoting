#!/bin/bash
echo "=== ANSHHOSTING Setup ==="
pip install flask werkzeug psutil flask-session --break-system-packages 2>/dev/null || \
pip install flask werkzeug psutil flask-session
echo "=== Done. Run: python3 app.py ==="
