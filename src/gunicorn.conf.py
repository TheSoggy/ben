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
# Keep idle HTTP connections alive for 30 s. Default is 2 s, which means
# any connection sitting between bot turns longer than that gets closed
# server-side — Phoenix's Finch pool then hands out the dead socket on
# the next checkout, and the request fails with `:closed`. We saw this
# fire ~3 times per 30 min of load testing.
#
# Pair this with Phoenix's Finch `conn_max_idle_time` < 30 s so the
# CLIENT closes idle connections before the SERVER does — that way the
# checkout-vs-close race never happens.
keepalive = 30

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

    # Pin this worker to a single CPU. Without pinning, the kernel can
    # migrate a worker between vCPUs between requests; each migration
    # cold-evicts L1/L2 cache, so the next TF inference re-warms the
    # cache from L3/RAM (~5-15 % per-call slowdown for small TF graphs
    # like ours). With 4 workers on a 4-vCPU box, one worker per CPU
    # is the natural assignment.
    #
    # Each worker atomically claims the next CPU index via a small
    # lock file under /tmp. The file is wiped per container start so
    # every restart begins fresh at index 0.
    try:
        import fcntl

        cpu_list = sorted(_os.sched_getaffinity(0))
        if cpu_list:
            counter_file = "/tmp/ben_cpu_pin_index"
            with open(counter_file, "a+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                contents = f.read().strip()
                idx = int(contents) if contents else 0
                f.seek(0)
                f.truncate()
                f.write(str(idx + 1))
            target_cpu = cpu_list[idx % len(cpu_list)]
            _os.sched_setaffinity(0, {target_cpu})
            worker.log.info(
                "ben: pinned worker pid=%s to cpu=%d (idx=%d, available=%s)",
                worker.pid, target_cpu, idx, cpu_list,
            )
    except (AttributeError, OSError, ImportError) as exc:
        # macOS / non-Linux dev environments don't have sched_setaffinity;
        # `fcntl.flock` may also be missing on Windows. Either way, just
        # skip pinning and let the kernel scheduler do its thing.
        worker.log.info("ben: skipping CPU pinning (%s)", exc)

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
