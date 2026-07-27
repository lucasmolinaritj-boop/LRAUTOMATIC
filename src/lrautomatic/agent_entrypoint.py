from __future__ import annotations

import ctypes
import sys
from contextlib import contextmanager
from typing import Iterator


MUTEX_NAME = "Local\\LRAutomaticSessionAgent"
ERROR_ALREADY_EXISTS = 183


@contextmanager
def single_agent_instance() -> Iterator[bool]:
    if sys.platform != "win32":
        yield True
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    acquired = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    try:
        yield acquired
    finally:
        kernel32.CloseHandle(handle)


def main() -> None:
    with single_agent_instance() as acquired:
        if not acquired:
            return
        from .session_agent_stable import main as run_agent

        run_agent()


if __name__ == "__main__":
    main()
