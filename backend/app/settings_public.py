from __future__ import annotations

import hashlib
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
    fp = ""
    if configured:
        try:
            b = str(openai.get("api_key") or "").encode("utf-8", "ignore")
            fp = hashlib.sha256(b).hexdigest()[:10]
        except Exception:
            fp = ""
    openai["api_key"] = ""
    openai["api_key_configured"] = configured
    openai["api_key_fingerprint"] = fp
    integrations["openai"] = openai
    d["integrations"] = integrations
    return d
