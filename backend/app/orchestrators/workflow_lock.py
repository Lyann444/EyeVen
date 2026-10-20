import logging
import threading
from functools import wraps

logger = logging.getLogger(__name__)
_CASE_WORKFLOW_LOCKS = {}
_CASE_WORKFLOW_LOCKS_GUARD = threading.Lock()


def prevent_concurrent_case_runs(method):
    @wraps(method)
    def wrapped(case_id, *args, **kwargs):
        with _CASE_WORKFLOW_LOCKS_GUARD:
            lock = _CASE_WORKFLOW_LOCKS.setdefault(case_id, threading.RLock())
        if not lock.acquire(blocking=False):
            logger.info("Workflow already running for case %d", case_id)
            return {
                "case_id": case_id,
                "status": "already_running",
                "stopping_reason": "concurrent_run_in_progress",
            }
        try:
            return method(case_id, *args, **kwargs)
        finally:
            lock.release()

    return wrapped
