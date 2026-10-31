"""Configuration contains file paths, never captured credential values."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import GatewayError


@dataclass(frozen=True)
class Settings:
    gateway_key_file: Path
    models: dict = field(default_factory=dict)
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 90.0
    total_timeout_seconds: float = 300.0
    queue_timeout_seconds: float = 30.0
    max_waiting_requests: int = 4
    max_concurrent_requests: int = 1
    imported_credentials_file: Path | None = None
    site_signing_file: Path | None = None


def load_settings() -> Settings:
    default = Path(__file__).resolve().parent.parent / "config" / "local.json"
    config_path = Path(os.environ.get("SHANGHAITECH_GENAI_CONFIG", str(default))).expanduser()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError()
        paths = {}
        imported = os.environ.get('IMPORTED_CREDENTIALS_FILE') or data.get('imported_credentials_file', 'browser-credentials/credentials.json')
        if not isinstance(imported, str) or not imported.strip():
            raise ValueError()
        imported_path = Path(imported).expanduser()
        paths['imported_credentials_file'] = imported_path if imported_path.is_absolute() else config_path.resolve().parent / imported_path
        signing = os.environ.get('SITE_SIGNING_FILE') or data.get('site_signing_file')
        if signing is None:
            paths['site_signing_file'] = paths['imported_credentials_file'].with_name('site-signing.json')
        else:
            if not isinstance(signing, str) or not signing.strip():
                raise ValueError()
            signing_path = Path(signing).expanduser()
            paths['site_signing_file'] = signing_path if signing_path.is_absolute() else config_path.resolve().parent / signing_path
        for key, env in [("gateway_key_file", "GATEWAY_KEY_FILE")]:
            value = os.environ.get(env) or data.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError()
            path = Path(value).expanduser()
            paths[key] = path if path.is_absolute() else config_path.resolve().parent / path
        raw_models = data["models"]
        if not isinstance(raw_models, dict):
            raise ValueError()
        models = {}
        for name, profile in raw_models.items():
            if not isinstance(name, str) or not isinstance(profile, dict):
                raise ValueError()
            if not profile.get("enabled", True):
                continue
            if profile.get("root_ai_type") not in {"azure", "xinference"}:
                raise ValueError()
            limit = profile.get("max_tokens", 16384)
            if type(limit) is not int or not 1 <= limit <= 16384:
                raise ValueError()
            if type(profile.get("enable_thinking", False)) is not bool or type(profile.get("thinking", False)) is not bool:
                raise ValueError()
            if any(type(profile.get(k, False)) is not bool for k in ['supports_images', 'supports_files', 'supports_search', 'supports_thinking']):
                raise ValueError()
            if type(profile.get('net_go', False)) is not bool:
                raise ValueError()
            upstream_model = profile.get('upstream_model', name)
            if not isinstance(upstream_model, str) or not upstream_model.strip():
                raise ValueError()
            models[name] = dict(profile)
        if not models:
            raise ValueError()
        options = {}
        for key, value in [("connect_timeout_seconds", 10), ("read_timeout_seconds", 90), ("total_timeout_seconds", 300), ("queue_timeout_seconds", 30)]:
            number = data.get(key, value)
            if type(number) not in {int, float} or not 0 < number <= 3600:
                raise ValueError()
            options[key] = float(number)
        waiting = data.get("max_waiting_requests", 4)
        if type(waiting) is not int or not 0 <= waiting <= 32:
            raise ValueError()
        concurrent = data.get("max_concurrent_requests", 1)
        if type(concurrent) is not int or not 1 <= concurrent <= 4:
            raise ValueError()
        return Settings(**paths, models=models, max_waiting_requests=waiting,
                        max_concurrent_requests=concurrent, **options)
    except (OSError, ValueError, TypeError, KeyError):
        raise GatewayError(503, "configuration_required", "Create config/local.json using the example and configure protected file paths.") from None
