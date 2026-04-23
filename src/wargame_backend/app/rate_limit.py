"""Shared rate-limiter instance.

Importing from here avoids circular imports between app.main and the API routers.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
