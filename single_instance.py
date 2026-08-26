"""Single-instance lock, shared by the widget and the hook bridge.

The widget holds a bound local port for its whole life. The hook bridge checks
the same port to decide whether a widget is already up, so it can skip spawning
a process that would only import PIL and tkinter before exiting on the lock.

Imports nothing but the standard library on purpose: the hook runs on every tool
call, so this has to stay cheap.
"""

import socket

HOST = "127.0.0.1"
LOCK_PORT = 47311  # arbitrary, only ever bound and never connected to

_lock = None  # kept at module scope so the socket outlives acquire()


def acquire():
    """Take the lock. True if this process now owns it, False if one is running."""
    global _lock
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, LOCK_PORT))
    except OSError:
        sock.close()
        return False
    _lock = sock
    return True


def is_running():
    """True if another process holds the lock.

    Probed by binding rather than connecting: the owner never calls listen(), so
    a connect would fail even while the widget is alive.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, LOCK_PORT))
    except OSError:
        return True
    else:
        return False
    finally:
        sock.close()
