from __future__ import annotations

import atexit
import threading
import weakref

import requests


_thread_local = threading.local()
_sessions_lock = threading.Lock()
_sessions: weakref.WeakSet[requests.Session] = weakref.WeakSet()


def local_session() -> requests.Session:
    """Return one persistent Session per worker thread.

    Local control-plane requests are frequent and always target loopback. Reusing
    the Session keeps HTTP connections warm without sharing a requests.Session
    across threads. Weak references prevent short-lived worker threads from
    leaving completed Sessions retained for the lifetime of the application.
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _thread_local.session = session
        with _sessions_lock:
            _sessions.add(session)
    return session


def close_local_sessions() -> None:
    with _sessions_lock:
        sessions = list(_sessions)
        _sessions.clear()
    for session in sessions:
        try:
            session.close()
        except Exception:
            pass


def local_get(url, **kwargs):
    return local_session().get(url, **kwargs)


def local_put(url, **kwargs):
    return local_session().put(url, **kwargs)


def local_post(url, **kwargs):
    return local_session().post(url, **kwargs)


def local_delete(url, **kwargs):
    return local_session().delete(url, **kwargs)


atexit.register(close_local_sessions)
