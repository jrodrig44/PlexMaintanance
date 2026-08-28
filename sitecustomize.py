"""Project-wide Python startup configuration for Windows.

Streamlit's Uvicorn server only needs socket-based async I/O.  The selector
event loop handles that workload and, unlike the default Proactor loop, does
not emit a traceback when a browser abruptly closes a WebSocket connection.
Python imports ``sitecustomize`` automatically during interpreter startup.
"""

from __future__ import annotations

import asyncio
import sys


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
