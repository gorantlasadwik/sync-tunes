import sys
import os

# Ensure project root is on path so we can import app:app
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app as vercel_app  # Flask app instance defined in app.py

# Vercel Python expects a module-level variable named `app`
app = vercel_app


