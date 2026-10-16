"""Start the LinkVault server (API + vault web page) without a console window.

Windows runs this at login via the shortcut in your Startup folder. Output goes to
backend/server.log. If the server is already running (port 8000 busy) it exits quietly.
"""

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "backend" / "server.log"


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    if port_in_use(8000):
        sys.exit(0)
    # pythonw has no console: send everything to a log file instead.
    log = open(LOG, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = log
    sys.path.insert(0, str(ROOT))

    import uvicorn

    uvicorn.run("serve:app", host="127.0.0.1", port=8000, log_config=None)
