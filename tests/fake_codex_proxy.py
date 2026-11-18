"""Independent WebSocket Codex host and raw stdio proxy; never invokes a model."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import threading


def windows_job(process_handle=None, allow_breakaway=True):
    """Model the host's launch job, or a strict MCP containment job in a test."""
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                    ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]
    class Io(ctypes.Structure):
        _fields_ = [(str(index), ctypes.c_uint64) for index in range(6)]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", Io), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x800 if allow_breakaway else 0x2000
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job, process_handle or kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def read_exact(reader, size):
    result = b""
    while len(result) < size:
        chunk = reader.read(size - len(result))
        if not chunk:
            raise EOFError
        result += chunk
    return result


def receive(reader):
    first, second = read_exact(reader, 2)
    size = second & 127
    if size == 126:
        size = struct.unpack("!H", read_exact(reader, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", read_exact(reader, 8))[0]
    mask = read_exact(reader, 4) if second & 128 else None
    payload = read_exact(reader, size)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if first & 15 == 8:
        raise EOFError
    return json.loads(payload)


def send(writer, value):
    payload = json.dumps(value).encode()
    size = len(payload)
    header = bytes([129, size]) if size < 126 else bytes([129, 126]) + struct.pack("!H", size)
    writer.write(header + payload)
    writer.flush()


def exchange(reader, writer):
    headers = b""
    while not headers.endswith(b"\r\n\r\n"):
        headers += read_exact(reader, 1)
    key = next(line.split(b":", 1)[1].strip() for line in headers.split(b"\r\n")
               if line.lower().startswith(b"sec-websocket-key:"))
    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    writer.flush()
    root = Path(os.environ["TERMINALHOOK_FAKE_STATE_DIR"])
    owner = os.environ["TERMINALHOOK_FAKE_THREAD_ID"]
    resumed = False
    while True:
        message = receive(reader)
        method = message.get("method")
        if "id" not in message:
            continue
        if method == "initialize":
            result = {"userAgent": "TerminalHook fake host", "capabilities": {}}
        elif method == "thread/read":
            assert message["params"] == {"threadId": owner, "includeTurns": False}
            status = "notLoaded" if (root / "unloaded").exists() else "idle"
            result = {"thread": {"id": owner, "cwd": str(root), "status": {"type": status}}}
        elif method == "thread/resume":
            assert message["params"] == {"threadId": owner, "excludeTurns": True}
            assert (root / "unloaded").exists() and not resumed
            if (root / "reject-resume").exists():
                send(writer, {"id": message["id"], "error": {
                    "code": -32600, "message": "original thread cannot be resumed",
                }})
                continue
            resumed = True
            result = {"thread": {"id": owner, "cwd": str(root), "status": {"type": "idle"}}}
        elif method == "command/exec":
            params = message["params"]
            environment = dict(os.environ, **params.get("env", {}))
            launched = subprocess.run(params["command"], cwd=params["cwd"], env=environment,
                                      capture_output=True, text=True, timeout=10)
            result = {"exitCode": launched.returncode, "stdout": launched.stdout, "stderr": launched.stderr}
        elif method == "turn/start":
            params = message["params"]
            assert resumed or not (root / "unloaded").exists()
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
        send(writer, {"id": message["id"], "result": result})


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            exchange(self.rfile, self.wfile)
        except (EOFError, BrokenPipeError, ConnectionResetError):
            pass


def host():
    # The independent daemon's command job permits worker detachment; the MCP
    # server's separate job deliberately does not. This is not a mock of spawn.
    job = windows_job() if os.name == "nt" else None
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        print(json.dumps({"port": server.server_address[1]}), flush=True)
        server.serve_forever()
    return job


def proxy():
    failure = Path(os.environ["TERMINALHOOK_FAKE_STATE_DIR"]) / "proxy-error"
    if failure.exists():
        print(failure.read_text(encoding="utf-8"), file=sys.stderr, flush=True)
        raise SystemExit(42)
    with socket.create_connection(("127.0.0.1", int(os.environ["TERMINALHOOK_FAKE_PORT"]))) as connection:
        def write():
            try:
                while chunk := sys.stdin.buffer.read1(4096):
                    connection.sendall(chunk)
            except (OSError, BrokenPipeError):
                pass
        threading.Thread(target=write, daemon=True).start()
        while chunk := connection.recv(4096):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    if "--host" in sys.argv:
        host()
    else:
        try:
            proxy()
        except (EOFError, BrokenPipeError, ConnectionResetError):
            pass
