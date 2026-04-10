"""Gunicorn configuration for Ben AI bridge engine.

Uses gevent workers (compatible with existing monkey patching in gameapi.py).
Preloads app so TensorFlow models are loaded once in the master process and
shared across workers via copy-on-write memory.

Override workers at runtime:  BEN_WORKERS=8 gunicorn -c gunicorn.conf.py gameapi:app
"""

import os

# --- Server socket ---
bind = f"0.0.0.0:{os.environ.get('BEN_PORT', '8085')}"

# --- Worker processes ---
workers = int(os.environ.get("BEN_WORKERS", "16"))
worker_class = "gevent"
worker_connections = 100

# --- Timeouts ---
timeout = 120  # matches previous gevent WSGIServer connection_timeout
graceful_timeout = 30

# --- Preload ---
# Load app in master process so TF models are shared via COW across workers.
# This dramatically reduces memory usage (models ~hundreds of MB, loaded once).
preload_app = True

# --- Logging ---
accesslog = "-"
errorlog = "-"
loglevel = "info"

# --- Process naming ---
proc_name = "ben-bridge-ai"
