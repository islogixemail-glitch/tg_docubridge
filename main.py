# -*- coding: utf-8 -*-
"""DocuBridge Telegram bot — Phase 2 (quote-first, 4 routes, tickets, xAI)."""
# Readable modular entry (no packing). Unit tests: `import main as m`.

from docubridge_helpers import *  # noqa: F401,F403
from docubridge_config import *  # noqa: F401,F403
from docubridge_db import *  # noqa: F401,F403
from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow import *  # noqa: F401,F403
import docubridge_handlers as _handlers  # noqa: F401,E402

for _mod in (_handlers,):
    for _name in dir(_mod):
        if not _name.startswith("_") and _name not in globals():
            globals()[_name] = getattr(_mod, _name)

if __name__ == "__main__":
    if SKIP_STARTUP:
        print("SKIP_STARTUP=1 — not starting server")
