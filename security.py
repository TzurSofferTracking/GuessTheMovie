import re
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, request


class RequestRateLimiter:
    """Small per-process limiter for deployments with a single app process."""

    _SPEC_PATTERN = re.compile(r"^(\d+)\s+per\s+(second|minute|hour)$")
    _PERIODS = {"second": 1, "minute": 60, "hour": 3600}

    def __init__(self):
        self._requests = defaultdict(deque)

    def limit(self, specification):
        match = self._SPEC_PATTERN.fullmatch(specification)
        if not match:
            raise ValueError("Rate limit must look like '10 per minute'.")
        maximum = int(match.group(1))
        period = self._PERIODS[match.group(2)]

        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                now = time.monotonic()
                key = (request.remote_addr or "unknown", specification)
                timestamps = self._requests[key]
                cutoff = now - period
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()
                if len(timestamps) >= maximum:
                    abort(429, description="Too many requests. Please try again later.")
                timestamps.append(now)
                return view(*args, **kwargs)

            return wrapped

        return decorator
