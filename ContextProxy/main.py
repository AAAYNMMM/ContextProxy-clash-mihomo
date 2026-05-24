import os
import signal
import sys
import threading

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.backend_service import BackendService


stop_event = threading.Event()
service = BackendService()


def exit_handler(signum, frame):
    _ = signum, frame
    print("shutdown signal received")
    service.stop()
    stop_event.set()


def main():
    signal.signal(signal.SIGINT, exit_handler)
    signal.signal(signal.SIGTERM, exit_handler)

    ok, error = service.start()
    if not ok:
        print(f"backend service start failed: {error}")
        return 1

    print("backend service started; press Ctrl+C to stop")
    try:
        stop_event.wait()
    finally:
        service.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
