"""Process-local ownership of expensive inference runtimes.

The live consumer owns the lease for its session, never the capture callback.
Switching owners evicts the previous cache only after its inference has ended.
"""
from contextlib import contextmanager
from threading import RLock


INFERENCE_LOCK = RLock()
_owner = None
_release = None


def activate(identity, release):
    global _owner, _release
    if _owner != identity:
        if _release is not None:
            _release()
        _owner, _release = identity, release


@contextmanager
def inference_session(identity, release, should_stop=lambda: False):
    acquired = False
    try:
        while not should_stop():
            if INFERENCE_LOCK.acquire(timeout=0.05):
                acquired = True
                break
        if not acquired:
            yield False
            return
        activate(identity, release)
        yield True
    finally:
        if acquired:
            INFERENCE_LOCK.release()
