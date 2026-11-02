"""Vercel entry point. Vercel's Python runtime looks for an ASGI `app` in api/*.py; vercel.json
rewrites every path here, and the FastAPI app in ../app.py serves both the API and the static page."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402,F401
