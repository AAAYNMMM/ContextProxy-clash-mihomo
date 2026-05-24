import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
try:
    _console_log = open(LOG_DIR / "console.log", "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = _console_log
    sys.stderr = _console_log
except Exception:
    pass

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ContextProxy")
    app.setOrganizationName("ContextProxy")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()

    def _shutdown_backend_on_quit():
        try:
            window._disable_system_proxy_on_exit()
        except Exception:
            pass

        try:
            from gui.process_manager import get_proxy_state, stop_proxy_process, cleanup_proxy_residue

            if get_proxy_state() in {"starting", "running", "stopping", "failed"}:
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
