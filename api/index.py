import sys
import os

# Ensure project root is on path so we can import app:app
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app as flask_app
from vercel_wsgi import handle


def handler(event, context):
    return handle(flask_app, event, context)


