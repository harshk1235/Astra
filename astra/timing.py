"""Tiny timing helper so data-load latency shows up in the logs.

Log lines look like:  timing op=warstats.load ms=812
which is easy to grep and average:
    grep 'timing op=warstats.load' bot.log | sed 's/.*ms=//' | sort -n
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger("astra.timing")


@contextmanager
def timed(op: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        log.info("timing op=%s ms=%.0f", op, (time.perf_counter() - start) * 1000)
