import subprocess
import sys

import pytest

from sonar.runlock import RunLocked, run_lock

HOLD_THE_LOCK = """
import sys, time
from pathlib import Path
from sonar.runlock import run_lock
with run_lock("run", Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(60)
"""


def test_a_second_holder_is_refused_and_the_lock_is_freed_afterwards(tmp_path) -> None:
    with run_lock("run", tmp_path):
        with pytest.raises(RunLocked):
            with run_lock("run", tmp_path):
                pass
    with run_lock("run", tmp_path):  # free again once the first holder left
        pass


def test_different_names_do_not_block_each_other(tmp_path) -> None:
    with run_lock("run", tmp_path), run_lock("site-a", tmp_path):
        pass


def test_the_lock_is_released_when_the_block_fails(tmp_path) -> None:
    with pytest.raises(ValueError):
        with run_lock("run", tmp_path):
            raise ValueError("boom")
    with run_lock("run", tmp_path):
        pass


def test_another_process_is_refused_and_a_killed_process_frees_the_lock(tmp_path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLD_THE_LOCK, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(RunLocked):
            with run_lock("run", tmp_path):
                pass
    finally:
        child.kill()  # a hard kill, like a crash: no cleanup code runs
        child.wait()
    with run_lock("run", tmp_path):  # the OS released it, so nothing is stuck
        pass
