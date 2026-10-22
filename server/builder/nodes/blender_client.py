"""Client for the headless Blender bridge (blender-mcp compatible protocol)."""
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent / "blender_bridge.py"


class BlenderError(RuntimeError):
    pass


class Blender:
    def __init__(self, port=9876, blender=None):
        self.port = port
        self.exe = blender or shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen([self.exe, "-b", "--factory-startup", "--python", str(BRIDGE), "--", "--port", str(self.port)],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if "BRIDGE_READY" in line:
                return self
            if self.proc.poll() is not None:
                break
        raise BlenderError("Blender bridge did not start")

    def __exit__(self, *exc):
        try:
            self.call("shutdown")
        except Exception:  # noqa: BLE001
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def call(self, type_, **params):
        with socket.create_connection(("127.0.0.1", self.port), timeout=600) as s:
            s.sendall(json.dumps({"type": type_, "params": params}).encode())
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                try:
                    res = json.loads(data.decode())
                    break
                except json.JSONDecodeError:
                    continue
        if res.get("status") != "success":
            raise BlenderError(res.get("message", "error") + "\n" + res.get("traceback", ""))
        return res["result"]
