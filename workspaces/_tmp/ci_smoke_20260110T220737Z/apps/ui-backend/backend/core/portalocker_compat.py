from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, TextIO

try:
    import portalocker  # type: ignore
except ImportError:  # pragma: no cover - fallback shim for test environments
    import threading

    class _LockException(Exception):
        pass

    class _Timeout(_LockException):
        pass

    class _Exceptions:
        Timeout = _Timeout
        LockException = _LockException

    _LOCKS: Dict[str, threading.Lock] = {}

    class _StubLock:
        def __init__(
            self,
            filename: str,
            mode: str = "r",
            timeout: Optional[float] = None,
            encoding: Optional[str] = None,
        ) -> None:
            self._filename = str(Path(filename))
            self._mode = mode
            self._encoding = encoding
            self._timeout = timeout
            self._file: Optional[TextIO] = None
            _LOCKS.setdefault(self._filename, threading.Lock())
            self._lock = _LOCKS[self._filename]

        def __enter__(self) -> TextIO:
            if self._timeout is None:
                acquired = self._lock.acquire()
            else:
                acquired = self._lock.acquire(timeout=self._timeout)
            if not acquired:
                raise _Timeout(f"Failed to acquire lock on {self._filename}")
            self._file = open(self._filename, self._mode, encoding=self._encoding)
            return self._file

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            try:
                if self._file:
                    self._file.close()
            finally:
                self._lock.release()
            return False

    def _lock_factory(
        filename: str,
        mode: str = "r",
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
    ) -> _StubLock:
        return _StubLock(filename, mode=mode, timeout=timeout, encoding=encoding)

    class _PortalockerStub:
        exceptions = _Exceptions()
        Lock = staticmethod(_lock_factory)
        Timeout = _Timeout
        LockException = _LockException

    portalocker = _PortalockerStub()  # type: ignore
else:  # pragma: no cover - ensure compatibility across portalocker versions
    timeout_cls = getattr(portalocker, "Timeout", RuntimeError)
    lock_exc_cls = getattr(portalocker, "LockException", RuntimeError)
    exceptions_obj = getattr(portalocker, "exceptions", None)
    if exceptions_obj is None:
        class _CompatExceptions:
            Timeout = timeout_cls
            LockException = lock_exc_cls

        portalocker.exceptions = _CompatExceptions()  # type: ignore[attr-defined]
    else:
        try:
            setattr(exceptions_obj, "Timeout", timeout_cls)
            setattr(exceptions_obj, "LockException", lock_exc_cls)
        except Exception:  # pragma: no cover - robustness
            pass

__all__ = ["portalocker"]

