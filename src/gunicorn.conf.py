"""Gunicorn configuration for Ben AI bridge engine.

Uses gevent workers (compatible with existing monkey patching in gameapi.py).
Preloads app so TensorFlow models load once in the master and copy-on-write
into each worker — saves ~800MB per worker for our 4 .keras models.

The .NET-backed engines (BBA, PIMC, ACE) are NOT fork-safe (pythonnet's
finalizers segfault on GC after fork from a CLR-initialized parent), so
gameapi.py defers their init until BEN_DEFER_NATIVE_INIT=1 + a post_fork
hook drives _init_native_engines() inside each worker.

Override workers at runtime:  BEN_WORKERS=8 gunicorn -c gunicorn.conf.py gameapi:app
"""

import os

# Tell gameapi to skip .NET init at module load — we'll call it from
# post_fork once each worker exists. This must be set BEFORE gunicorn
# imports the application.
os.environ["BEN_DEFER_NATIVE_INIT"] = "1"

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
# True: TF models load once in master, CoW into workers (~800MB saved per
# worker). Safe because .NET init is deferred to post_fork (see below).
preload_app = True


def post_fork(server, worker):
    """Initialize .NET engines after fork.

    pythonnet's CLR is not fork-safe — finalizers from a forked parent
    can segfault during GC in the child. Initializing inside the worker
    (post-fork) gives each worker a clean CLR and inherits TF models
    from the master via copy-on-write.
    """
    server.log.info("post_fork: initializing native engines for worker %s", worker.pid)
    try:
        import gameapi
        gameapi._init_native_engines()
    except Exception:
        # Worker startup must not silently swallow a real init failure.
        server.log.exception("post_fork: native engine init crashed; aborting worker")
        raise


# --- Logging ---
accesslog = "-"
errorlog = "-"
loglevel = "info"

# --- Process naming ---
proc_name = "ben-bridge-ai"
