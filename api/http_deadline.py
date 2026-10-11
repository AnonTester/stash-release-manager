"""Hard wall-clock deadline wrapper for outbound HTTP calls.

Python's `requests` `timeout=` parameter caps connect time and the gap
*between* received bytes -- it does not cap total response time. A
server that trickles bytes slowly enough to keep resetting that
inter-byte timer can hold a call open far longer than the configured
timeout suggests. This is the confirmed-likely cause of a scan thread
hanging indefinitely (see PROGRESS.md's "Scan appeared to hang
permanently" section) -- four occurrences, ~2h12-13m apart with
suspicious consistency (pointing to the same specific entity/response
each time, not random flakiness), each with zero network/log activity
for 40+ minutes despite every StashDB/ThePornDB client call already
having a finite `requests` timeout.

This wraps a call in a background thread and gives up waiting after a
hard total deadline, regardless of what the call itself is doing --
independent of, and in addition to, the request's own `timeout=`. The
abandoned call's thread is NOT killed (Python cannot force-kill a
thread) -- it keeps running until it eventually finishes or the
process exits, occupying one worker slot in the shared executor below.
That's an accepted, bounded cost: this situation is rare (observed at
most once per ~2h12m scan cycle), so a slow thread leak is a far better
outcome than the whole scan hanging forever while holding the global
scan lock (main.py's `_scan_lock`), which had no self-recovery at all.
"""
import concurrent.futures

# A handful of workers is plenty -- normal calls return almost
# immediately (well under the deadline) and free their worker; only a
# genuinely stuck call ties one up for longer, and those are rare.
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="http-deadline")


def call_with_deadline(func, *args, deadline, **kwargs):
    """Runs func(*args, **kwargs) in the shared executor and waits at
    most `deadline` seconds (a hard wall-clock budget, independent of
    any timeout= already passed through **kwargs to func itself).
    Raises concurrent.futures.TimeoutError if exceeded -- callers
    already treat any exception from an HTTP call as a normal
    per-entity failure (see watcher.py), so this needs no special
    handling beyond letting it propagate."""
    future = _executor.submit(func, *args, **kwargs)
    return future.result(timeout=deadline)
