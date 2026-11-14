"""Bounded subprocess I/O for fixed read-only observation commands."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

_slot = threading.BoundedSemaphore(1)


class ObservationCommandBusy(RuntimeError):
    pass


class ObservationCommandFailed(RuntimeError):
    pass


def run_observation(argv, *, timeout=6, maximum=2 * 1024 * 1024, runner=None):
    """One command at a time; no shell, bounded combined stdout/stderr.

    A stuck descendant that cannot be reaped keeps the slot unavailable rather
    than permitting an unbounded number of new observation processes.
    """
    if not _slot.acquire(blocking=False):
        raise ObservationCommandBusy("A read-only observation command is already running")
    release_slot = True
    process, cleanup = None, None
    streams, readers, errors, chunks = [], [], [], [[], []]
    try:
        if runner is not None:
            result = runner(argv, input=None, text=True, capture_output=True, timeout=timeout, check=False, shell=False)
            if sum(len(str(value or "").encode()) for value in (result.stdout, result.stderr)) > maximum:
                raise ObservationCommandFailed("Observation command output exceeded its limit")
            return result
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, bufsize=0, start_new_session=os.name == "posix")
        def terminate():
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, OSError):
                pass
        size, guard = [0], threading.Lock()
        def read(stream, index):
            try:
                while chunk := stream.read(65536):
                    with guard:
                        size[0] += len(chunk)
                        if size[0] > maximum:
                            errors.append("output_limit")
                            terminate()
                            return
                        chunks[index].append(chunk)
            except OSError:
                errors.append("output_read")
        streams = [process.stdout, process.stderr]
        for index, stream in enumerate(streams):
            thread = threading.Thread(target=read, args=(stream, index), daemon=True, name="observation-output")
            readers.append(thread)
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            errors.append("timeout")
            terminate()
        cleanup = time.monotonic() + 2
        if process.poll() is None:
            try:
                process.wait(timeout=max(.01, cleanup - time.monotonic()))
            except subprocess.TimeoutExpired:
                errors.append("termination")
        # The parent can exit while a credential helper still owns its pipes.
        for thread in readers:
            if thread.ident is not None:
                thread.join(timeout=max(0, min(.05, cleanup - time.monotonic())))
        if any(thread.is_alive() for thread in readers):
            errors.append("descendant_held_output")
            terminate()
            for thread in readers:
                if thread.ident is not None:
                    thread.join(timeout=max(0, cleanup - time.monotonic()))
        if any(thread.is_alive() for thread in readers) or process.poll() is None:
            release_slot = False
            errors.append("unreaped_process")
        if errors:
            raise ObservationCommandFailed("Observation command exceeded its time or output budget")
        try:
            return subprocess.CompletedProcess(argv, process.returncode,
                b"".join(chunks[0]).decode("utf-8"), b"".join(chunks[1]).decode("utf-8"))
        except UnicodeError as error:
            raise ObservationCommandFailed("Observation command output is not valid UTF-8") from error
    finally:
        if process is not None and (process.poll() is None or any(thread.is_alive() for thread in readers)):
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, OSError):
                pass
            cleanup = cleanup or time.monotonic() + 2
            try:
                process.wait(timeout=max(.01, cleanup - time.monotonic()))
            except subprocess.TimeoutExpired:
                release_slot = False
            for thread in readers:
                if thread.ident is not None:
                    thread.join(timeout=max(0, cleanup - time.monotonic()))
            if any(thread.is_alive() for thread in readers):
                release_slot = False
        for index, stream in enumerate(streams):
            if index >= len(readers) or not readers[index].is_alive():
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if release_slot:
            _slot.release()
