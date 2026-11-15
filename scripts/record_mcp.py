"""Transparent stdio relay used only by integration verification, never by the plugin."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

executable, transcript = sys.argv[1:]
lock = threading.Lock()
with Path(transcript).open("w", encoding="utf-8") as log:
    child = subprocess.Popen([executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=sys.stderr, creationflags=0x08000000 if sys.platform == "win32" else 0)

    def relay(source, destination, direction):
        try:
            for line in source:
                with lock:
                    log.write(json.dumps({"direction": direction, "at": time.monotonic(),
                                          "message": json.loads(line)}, ensure_ascii=False) + "\n")
                    log.flush()
                destination.write(line)
                destination.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            if direction == "sent":
                child.stdin.close()

    threading.Thread(target=relay, args=(sys.stdin.buffer, child.stdin, "sent"), daemon=True).start()
    relay(child.stdout, sys.stdout.buffer, "received")
    child.wait()
