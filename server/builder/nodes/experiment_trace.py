import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def emit(event, **fields):
    destination = os.environ.get("GENOVA_VR_EXPERIMENT_TRACE")
    if not destination:
        return
    row = {"event": event, "at_utc": datetime.now(timezone.utc).isoformat(),
           "monotonic_ns": time.monotonic_ns(), **fields}
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
