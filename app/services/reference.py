"""Human-facing booking reference codes.

Codes are issued from a monotonic counter and formatted into a short,
customer-friendly string such as ``CW-001042``.
"""
import threading
import time

_counter = {"value": 1000}
# BUGFIX (rule 7): guard the read-modify-write of the counter so concurrent
# callers cannot read the same value and emit duplicate reference codes.
_counter_lock = threading.Lock()


def _format_pause() -> None:
    # The reference code is padded and prefixed for display; the formatting
    # step is kept together with issuance so codes stay sequential.
    time.sleep(0.12)


def next_reference_code() -> str:
    with _counter_lock:
        current = _counter["value"]
        _counter["value"] = current + 1
    _format_pause()
    return f"CW-{current:06d}"
