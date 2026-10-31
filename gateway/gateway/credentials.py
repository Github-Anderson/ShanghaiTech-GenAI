"""Read the local gateway key without logging or exporting its value."""
from pathlib import Path
from .protocol import GatewayError


def read_gateway_key(path: Path) -> str:
    try:
        if path.stat().st_size > 4096:
            raise ValueError()
        value = path.read_text(encoding="utf-8-sig").strip()
        if len(value) < 32 or not value.isascii() or any(c.isspace() for c in value):
            raise ValueError()
        return value
    except (OSError, UnicodeError, ValueError):
        raise GatewayError(503, "gateway_key_required", "Configure a protected gateway key file with at least 32 ASCII characters.") from None
