import os
from pathlib import Path
import subprocess
import sys

import pytest

import app.main as main


def is_running(pid: int) -> bool:
    # A killed process may linger as a zombie until it is reaped; that counts as stopped.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(os.name != "posix" or not Path("/proc").exists(), reason="needs Linux /proc")
def test_timeout_also_kills_the_processes_the_tool_started(tmp_path):
    # Like soffice: a launcher that leaves the real work to a child process.
    pid_file = tmp_path / "child.pid"
    launcher = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        main.run_tool([sys.executable, "-c", launcher], timeout=2)

    child_pid = int(pid_file.read_text())
    assert not is_running(child_pid)


def test_returns_output_and_exit_code():
    result = main.run_tool([sys.executable, "-c", "import sys; print('hola'); sys.exit(3)"], timeout=30)

    assert result.returncode == 3
    assert result.stdout.strip() == "hola"
