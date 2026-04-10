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
workers = int(os.environ.get("BEN_WORKERS", "2"))
worker_class = "gevent"
worker_connections = 100

# --- Timeouts ---
timeout = 120  # matches previous gevent WSGIServer connection_timeout
graceful_timeout = 30

# --- Preload ---
# Disabled: BBA .NET finalizers segfault during GC (uncatchable by Python).
# Each worker loads models independently. Uses more memory but avoids crash.
preload_app = False

# --- Logging ---
accesslog = "-"
errorlog = "-"
loglevel = "info"

# --- Process naming ---
proc_name = "ben-bridge-ai"
