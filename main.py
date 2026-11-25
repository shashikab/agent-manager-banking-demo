"""Entrypoint for the banking agent. Agent Manager run command: ``python main.py``."""

from __future__ import annotations

import uvicorn

from app import app

# Agent Manager routes a Chat Agent to port 8000 and checks it with a TCP startup
# probe, so the port is fixed. Do NOT read the PORT env var: the deployed pod had
# PORT=8080, the agent listened where nothing was looking, the probe never
# connected and Kubernetes restarted the container every ~5 minutes. For another
# port locally, run: python -m uvicorn app:app --port 9000
PORT = 8000


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
