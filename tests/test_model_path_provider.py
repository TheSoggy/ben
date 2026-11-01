"""Tests for src/nn/model_path_provider.py — model copies must not outlive a killed worker.

Every gunicorn worker copies each model file (~250 MB in all) into its own
``isolated_<file>_<pid>_*`` directory and removes it only from ``atexit``,
which never runs after SIGKILL. A load test that OOM-killed workers filled
a host's disk in 40 minutes, until a respawned worker died with ENOSPC
while copying a model. These tests kill real worker processes with SIGKILL,
respawn them, and count what is left in the temp directory.

Run with:
    pytest engines/ben/tests/test_model_path_provider.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from nn.model_path_provider import IsolatedModelPathProvider  # noqa: E402

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"), reason="needs POSIX SIGKILL"
)

# A stand-in for one gunicorn worker booting: build the provider the way
# Models.from_conf does, copy the models, report, then serve forever.
WORKER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from nn.model_path_provider import IsolatedModelPathProvider
provider = IsolatedModelPathProvider(base_temp_storage_path=sys.argv[2])
for model in sys.argv[3:]:
    provider.get_path(model)
print("ready", flush=True)
time.sleep(600)
"""


def isolated_dirs(base: Path) -> list[Path]:
    return sorted(p for p in base.iterdir() if p.name.startswith("isolated_"))


@pytest.fixture
def models(tmp_path: Path) -> list[str]:
    paths = []
    for name in ("bidder.keras", "RPDD_2024-07-08-E02.keras"):
        path = tmp_path / "models" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(os.urandom(64 * 1024))
        paths.append(str(path))
    return paths


@pytest.fixture
def temp_base(tmp_path: Path) -> Path:
    base = tmp_path / "tmp"
    base.mkdir()
    return base


@pytest.fixture
def spawn(temp_base: Path, models: list[str]):
    workers: list[subprocess.Popen] = []

    def _spawn() -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", WORKER, str(SRC), str(temp_base), *models],
            stdout=subprocess.PIPE,
            text=True,
        )
        workers.append(proc)
        assert proc.stdout.readline().strip() == "ready"
        return proc

    yield _spawn

    for proc in workers:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def sigkill(proc: subprocess.Popen) -> None:
    os.kill(proc.pid, signal.SIGKILL)
    # Reaped, as gunicorn's arbiter reaps before it forks the replacement.
    assert proc.wait() == -signal.SIGKILL


def test_a_sigkilled_worker_leaves_its_copies_behind(spawn, temp_base, models):
    """The leak itself: atexit does not run, so the copies stay on disk."""
    worker = spawn()
    assert len(isolated_dirs(temp_base)) == len(models)

    sigkill(worker)

    assert len(isolated_dirs(temp_base)) == len(models)


def test_temp_dir_does_not_grow_across_kill_and_respawn(spawn, temp_base, models):
    worker = spawn()
    for _ in range(5):
        sigkill(worker)
        worker = spawn()
        dirs = isolated_dirs(temp_base)
        assert len(dirs) == len(models)
        assert all(f"_{worker.pid}_" in d.name for d in dirs)


def test_a_live_workers_copies_survive_a_sibling_booting(spawn, temp_base, models):
    first = spawn()
    second = spawn()

    dirs = isolated_dirs(temp_base)
    assert len(dirs) == 2 * len(models)
    for pid in (first.pid, second.pid):
        assert sum(f"_{pid}_" in d.name for d in dirs) == len(models)


def test_the_sweep_runs_again_in_a_forked_child(temp_base, models):
    """gunicorn forks workers from one arbiter; the once-per-process guard
    must not be inherited across fork."""
    if not hasattr(os, "fork"):
        pytest.skip("needs fork")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    orphan = temp_base / f"isolated_bidder_keras_{dead.pid}_abcd_fgh"
    orphan.mkdir()

    IsolatedModelPathProvider._swept_pid = os.getpid()  # as if the parent swept
    child = os.fork()
    if child == 0:
        try:
            IsolatedModelPathProvider(base_temp_storage_path=str(temp_base))
        finally:
            os._exit(0)
    os.waitpid(child, 0)

    assert not orphan.exists()


def test_only_orphans_of_the_exact_shape_are_removed(temp_base):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    live = os.getppid()

    # tempfile's 8-character suffix may contain underscores.
    orphan = temp_base / f"isolated_RPDD_2024_07_08_E02_keras_{dead.pid}_a_b_c_d_"
    live_dir = temp_base / f"isolated_bidder_keras_{live}_abcdefgh"
    unrelated = [
        temp_base / f"isolated_bidder_keras_{dead.pid}",  # no suffix
        temp_base / f"isolated_bidder_keras_{dead.pid}_abcdefghi",  # 9-char suffix
        temp_base / f"other_bidder_keras_{dead.pid}_abcdefgh",
    ]
    for d in (orphan, live_dir, *unrelated):
        d.mkdir()
    orphan_file = temp_base / f"isolated_contract_keras_{dead.pid}_abcdefgh"
    orphan_file.write_text("a file, not a copy directory")

    removed = IsolatedModelPathProvider._sweep_orphans(str(temp_base))

    assert removed == 1
    assert not orphan.exists()
    assert live_dir.exists()
    assert orphan_file.exists()
    for d in unrelated:
        assert d.exists()


def test_a_stale_dir_carrying_our_own_reused_pid_is_removed(temp_base, models):
    own = os.getpid()
    IsolatedModelPathProvider._swept_pid = own  # keep the constructor's sweep out of it
    stale = temp_base / f"isolated_bidder_keras_{own}_zzzzzzzz"
    stale.mkdir()
    mine = Path(
        IsolatedModelPathProvider(base_temp_storage_path=str(temp_base)).get_path(models[0])
    ).parent

    removed = IsolatedModelPathProvider._sweep_orphans(str(temp_base))

    assert removed == 1
    assert not stale.exists()
    assert mine.exists()


def test_a_missing_temp_dir_does_not_raise():
    missing = os.path.join(tempfile.gettempdir(), "ben-no-such-dir-for-sweep")
    assert IsolatedModelPathProvider._sweep_orphans(missing) == 0
