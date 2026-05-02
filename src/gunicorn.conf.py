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
workers = int(os.environ.get("BEN_WORKERS", "4"))
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


# --- Hooks ---
def post_worker_init(worker):
    """Warm up every TF predictor immediately after the worker forks.

    Concurrent first-calls to ``@tf.function`` race on the trace cache and
    corrupted state in the reverted ``BoundedSemaphore(2)`` experiment. The
    fix is to populate every trace once per worker process before any real
    request arrives. After this call, ``model_lock_play`` can in principle
    be relaxed because no further tracing happens on the hot path.

    Failures here downgrade to warnings rather than aborting boot — a
    partial warmup is still better than a worker that refuses to start.
    Set ``BEN_DISABLE_WARMUP=1`` to bypass entirely (debug only).
    """
    import os as _os

    if _os.environ.get("BEN_DISABLE_WARMUP") == "1":
        worker.log.info("ben warmup: disabled via BEN_DISABLE_WARMUP")
        return

    try:
        import gameapi as _gameapi
    except Exception as exc:  # noqa: BLE001
        worker.log.exception("ben warmup: gameapi import failed (%s)", exc)
        return

    models = getattr(_gameapi, "models", None)
    if models is None or not hasattr(models, "warm_up"):
        worker.log.warning("ben warmup: gameapi.models has no warm_up()")
        return

    worker.log.info("ben warmup: starting TF tracing warmup")
    try:
        models.warm_up()
        worker.log.info("ben warmup: complete")
    except Exception as exc:  # noqa: BLE001
        worker.log.exception("ben warmup: failed mid-flight (%s)", exc)
