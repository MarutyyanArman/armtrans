"""One shared clock. All internal times are time.perf_counter(); wall time is only for display."""
import time
from datetime import datetime


class Clock:
    def __init__(self):
        self._perf0 = time.perf_counter()
        self._wall0 = time.time()

    @staticmethod
    def now() -> float:
        return time.perf_counter()

    def to_wall(self, perf_t: float) -> datetime:
        return datetime.fromtimestamp(self._wall0 + (perf_t - self._perf0))
