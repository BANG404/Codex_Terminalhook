"""A WebSocket-over-stdio Codex host fixture; never invokes a model."""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys


def read_exact(size):
    result = b""
    while len(result) < size:
        chunk = sys.stdin.buffer.read(size - len(result))
        if not chunk:
            raise EOFError
        result += chunk
    return result


def receive():
    first, second = read_exact(2)
    size = second & 127
    if size == 126:
        size = struct.unpack("!H", read_exact(2))[0]
    elif size == 127:
        size = struct.unpack("!Q", read_exact(8))[0]
    mask = read_exact(4) if second & 128 else None
    payload = read_exact(size)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if first & 15 == 8:
        raise EOFError
    return json.loads(payload)


def send(value):
    payload = json.dumps(value).encode()
    size = len(payload)
    header = bytes([129, size]) if size < 126 else bytes([129, 126]) + struct.pack("!H", size)
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()


def main():
    headers = b""
    while not headers.endswith(b"\r\n\r\n"):
        headers += read_exact(1)
    key = next(line.split(b":", 1)[1].strip() for line in headers.split(b"\r\n")
               if line.lower().startswith(b"sec-websocket-key:"))
    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    sys.stdout.buffer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    sys.stdout.buffer.flush()
    root = Path(os.environ["TERMINALHOOK_FAKE_STATE_DIR"])
    owner = os.environ["TERMINALHOOK_FAKE_THREAD_ID"]
    while True:
        message = receive()
        method = message.get("method")
        if "id" not in message:
            continue
        if method == "initialize":
            result = {"userAgent": "TerminalHook fake host", "capabilities": {}}
        elif method == "thread/loaded/list":
            result = {"data": [] if (root / "offline").exists() else [owner]}
        elif method == "thread/read":
            result = {"thread": {"id": owner, "cwd": str(root), "status": {"type": "idle"}}}
        elif method == "turn/start":
            params = message["params"]
            assert params["threadId"] == owner
            assert params["input"] == []
            output = json.loads(params["toolOutput"]["output"])
            with (root / "deliveries.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(output) + "\n")
            if (root / "drop_after_accept").exists():
                return
            result = {"turn": {"id": "turn-" + output["task_id"], "status": "inProgress"}}
        else:
            raise AssertionError(method)
        send({"id": message["id"], "result": result})


if __name__ == "__main__":
    try:
        main()
    except (EOFError, BrokenPipeError):
        pass
