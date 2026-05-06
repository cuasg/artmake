from __future__ import annotations

from typing import Any, Dict

from app.models.settings import RuntimeSettings


def public_settings_dict(settings: RuntimeSettings) -> Dict[str, Any]:
    """
    Serialize settings for API/Web UI: never expose raw API keys.
    """
    d = settings.model_dump()
    integrations = dict(d.get("integrations") or {})
    openai = dict(integrations.get("openai") or {})
    configured = bool(str(openai.get("api_key") or "").strip())
    openai["api_key"] = ""
    openai["api_key_configured"] = configured
    integrations["openai"] = openai
    d["integrations"] = integrations
    return d
