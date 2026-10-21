from __future__ import annotations

from typing import Any

_PAYLOAD_CTX_KEYS = (
    "context_length",
    "max_model_len",
    "max_context_length",
    "context_window",
    "inputTokenLimit",
)


def positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def ctx_length_from_payload(item: dict) -> int | None:
    """Read a context window from a provider catalogue object, if present."""
    if not isinstance(item, dict):
        return None
    for key in _PAYLOAD_CTX_KEYS:
        found = positive_int(item.get(key))
        if found:
            return found
    details = item.get("details")
    if isinstance(details, dict):
        found = positive_int(details.get("context_length"))
        if found:
            return found
    info = item.get("model_info")
    if isinstance(info, dict):
        for key, raw in info.items():
            if str(key).endswith("context_length"):
                found = positive_int(raw)
                if found:
                    return found
    return None


def resolve_ctx_length(
    provider: str,
    name: str,
    *,
    litellm_provider: str | None = None,
    payload_ctx: int | None = None,
) -> int | None:
    """Prefer a catalogue window, then LiteLLM's registry for the model id."""
    found = positive_int(payload_ctx)
    if found:
        return found
    return _litellm_ctx(name, litellm_provider or provider)


_CHAT_CTX_SECTIONS = ("chat_model", "utility_model")
_EMBEDDING_SECTION = "embedding_model"


def _is_manual(section: dict) -> bool:
    return bool(section.get("ctx_length_manual"))


def apply_auto_ctx_lengths(config: dict, previous: dict | None = None) -> dict:
    """Fill chat/utility ctx_length unless the operator set it manually."""
    if not isinstance(config, dict):
        return config
    prior = previous if isinstance(previous, dict) else {}

    embedding = config.get(_EMBEDDING_SECTION)
    if isinstance(embedding, dict):
        embedding.pop("ctx_length", None)
        embedding.pop("ctx_length_manual", None)

    for section_name in _CHAT_CTX_SECTIONS:
        section = config.get(section_name)
        if not isinstance(section, dict):
            continue
        if _is_manual(section):
            section["ctx_length_manual"] = True
            typed = positive_int(section.get("ctx_length"))
            if typed:
                section["ctx_length"] = typed
            continue
        section.pop("ctx_length_manual", None)
        provider = str(section.get("provider") or "")
        detected = resolve_ctx_length(
            provider,
            str(section.get("name") or ""),
            litellm_provider=_litellm_provider_for(provider),
            payload_ctx=None,
        )
        if detected:
            section["ctx_length"] = detected
            continue
        form_ctx = positive_int(section.get("ctx_length"))
        previous_section = prior.get(section_name)
        previous_ctx = (
            positive_int(previous_section.get("ctx_length"))
            if isinstance(previous_section, dict)
            else None
        )
        if form_ctx:
            section["ctx_length"] = form_ctx
        elif previous_ctx:
            section["ctx_length"] = previous_ctx
        else:
            section.pop("ctx_length", None)
    return config


def _litellm_provider_for(provider: str) -> str:
    from helpers.providers import get_provider_config

    cfg = get_provider_config("chat", provider) or {}
    return str(cfg.get("litellm_provider") or provider)


def _litellm_ctx(name: str, litellm_provider: str) -> int | None:
    model_id = str(name or "").strip()
    if not model_id:
        return None
    try:
        import litellm
    except Exception:
        return None

    cost = getattr(litellm, "model_cost", None) or {}
    prefix = str(litellm_provider or "").strip()
    candidates: list[str] = [model_id]
    if prefix:
        candidates.append(f"{prefix}/{model_id}")
    candidates.append(model_id.lower())
    if prefix:
        candidates.append(f"{prefix}/{model_id}".lower())

    seen: list[str] = []
    for key in candidates:
        if key in seen:
            continue
        seen.append(key)
        info = cost.get(key) if isinstance(cost, dict) else None
        found = _ctx_from_cost_info(info)
        if found:
            return found

    if isinstance(cost, dict):
        suffix = "/" + model_id.lower()
        for key, info in cost.items():
            low = str(key).lower()
            if low == model_id.lower() or low.endswith(suffix):
                found = _ctx_from_cost_info(info)
                if found:
                    return found

    getter = getattr(litellm, "get_max_tokens", None)
    if not callable(getter):
        return None
    for key in seen:
        try:
            found = positive_int(getter(key))
        except Exception:
            found = None
        if found:
            return found
    return None


def _ctx_from_cost_info(info: Any) -> int | None:
    if not isinstance(info, dict):
        return None
    return positive_int(info.get("max_input_tokens")) or positive_int(info.get("max_tokens"))
