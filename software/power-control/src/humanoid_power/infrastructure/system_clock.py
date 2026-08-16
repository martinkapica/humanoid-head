from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    def __init__(self, timezone: str, force_synchronized: bool | None = None) -> None:
        self.timezone = ZoneInfo(timezone)
        self.force_synchronized = force_synchronized
        self._lock = threading.RLock()
        self._synchronized = bool(force_synchronized)

    def now_local(self) -> datetime:
        return datetime.now(self.timezone)

    def is_synchronized(self) -> bool:
        with self._lock:
            return self._synchronized

    def refresh_synchronization(self) -> bool:
        if self.force_synchronized is not None:
            with self._lock:
                self._synchronized = self.force_synchronized
                return self._synchronized
        try:
            completed = subprocess.run(
                ["/usr/bin/timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired):
            synchronized = False
        else:
            synchronized = completed.returncode == 0 and completed.stdout.strip().lower() == "yes"
        with self._lock:
            self._synchronized = synchronized
            return self._synchronized
