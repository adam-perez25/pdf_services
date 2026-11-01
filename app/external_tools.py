"""External programs: finding LibreOffice, Ghostscript and Tesseract, and
running them with a timeout that kills the whole process tree.

Endpoints call these through the module (external_tools.run_tool(...)),
so tests replace them in one place.
"""
import importlib.util
import os
import shutil
import signal
import subprocess


def find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("soffice.exe")


def find_ghostscript() -> str | None:
    return shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")


def find_tesseract() -> str | None:
    return shutil.which("tesseract") or shutil.which("tesseract.exe")


def ocrmypdf_installed() -> bool:
    return importlib.util.find_spec("ocrmypdf") is not None


def ocr_available() -> bool:
    return find_tesseract() is not None and ocrmypdf_installed()


def run_tool(command: list[str], timeout: float, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run an external tool, and on timeout kill it with every process it started.

    soffice is only a launcher: the conversion runs in a soffice.bin child, which
    subprocess.run's timeout would leave running after killing the launcher.
    On POSIX the tool gets its own process group, so the whole group is killed.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
        env=env,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        kill_process_tree(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        process.kill()
