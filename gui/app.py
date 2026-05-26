import os
import sys
import threading
import traceback
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

os.chdir(PROJECT_ROOT)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_console_log = None


def _stream_usable(stream) -> bool:
    if stream is None:
        return False
    try:
        stream.write("")
        stream.flush()
        return True
    except Exception:
        return False


def _configure_stdio():
    global _console_log

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if getattr(sys, "frozen", False) or not _stream_usable(sys.stdout) or not _stream_usable(sys.stderr):
        _console_log = open(LOG_DIR / "console.log", "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = _console_log
        sys.stderr = _console_log


_configure_stdio()


def _write_uncaught_exception(exc_type, exc_value, exc_traceback):
    try:
        with open(LOG_DIR / "console.log", "a", encoding="utf-8", errors="replace") as file:
            file.write("\n[uncaught exception]\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=file)
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_traceback):
    _write_uncaught_exception(exc_type, exc_value, exc_traceback)


def _threading_excepthook(args):
    _write_uncaught_exception(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = _excepthook
if hasattr(threading, "excepthook"):
    threading.excepthook = _threading_excepthook

from backend.default_files import ensure_default_files

ensure_default_files()

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow

ICON_PATH = PROJECT_ROOT / "icon.ico"


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ContextProxy")
    app.setOrganizationName("ContextProxy")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    app_icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = MainWindow()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)

    def _shutdown_backend_on_quit():
        try:
            from backend.activity_bus import write_log

            write_log("lifecycle", "QApplication aboutToQuit cleanup started")
        except Exception:
            pass

        try:
            window._disable_system_proxy_on_exit()
        except Exception:
            pass

        try:
            from gui.process_manager import get_proxy_state, stop_proxy_process, cleanup_proxy_residue
            from backend.activity_bus import write_log

            if get_proxy_state() in {"starting", "running", "stopping", "failed"}:
                write_log("lifecycle", "stop_proxy_process called, reason=QApplication_aboutToQuit")
                stop_proxy_process()
            else:
                cleanup_proxy_residue()
        except Exception:
            pass

    app.aboutToQuit.connect(_shutdown_backend_on_quit)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
