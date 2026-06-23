# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

import os

import frappe
from frappe.model.document import Document


# Recommended default model per provider when none is explicitly configured.
_PROVIDER_DEFAULTS = {
    "Anthropic": "claude-sonnet-4-5",
    "OpenAI": "gpt-4o",
    "Google Gemini": "gemini-1.5-pro",
    "Ollama": "llama3.1",
    "OpenRouter": "anthropic/claude-sonnet-4-5",
}

# OpenAI-compatible base URLs for cloud providers
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class FlowAgentSettings(Document):
    """Singleton holding API keys, model defaults, and run policy."""

    def validate(self):
        if not self.webhook_secret:
            self.webhook_secret = frappe.generate_hash(length=32)
        if self.max_steps_per_run and self.max_steps_per_run < 1:
            frappe.throw("Max steps per run must be at least 1")
        if self.max_agent_iterations and self.max_agent_iterations < 1:
            frappe.throw("Max agent iterations must be at least 1")


@frappe.whitelist()
def regenerate_webhook_secret():
    """Mint a new webhook secret. Existing webhook URLs will stop working."""
    frappe.only_for("System Manager")
    settings = frappe.get_single("FlowAgent Settings")
    settings.webhook_secret = frappe.generate_hash(length=32)
    settings.save(ignore_permissions=True)
    return settings.webhook_secret


# ---------------------------------------------------------------------------
# Provider configuration helpers
# ---------------------------------------------------------------------------

def get_default_provider() -> str:
    """Return the configured default AI provider name."""
    settings = frappe.get_single("FlowAgent Settings")
    return settings.default_provider or "Anthropic"


def get_provider_key(provider: str | None = None) -> str:
    """Resolve the API key for *provider*, falling back to site_config then env vars.

    Pass provider=None to use the currently configured default provider.
    Returns an empty string when no key is found (caller should validate).
    """
    if provider is None:
        provider = get_default_provider()

    settings = frappe.get_single("FlowAgent Settings")

    if provider == "Anthropic":
        key = settings.get_password("anthropic_api_key", raise_exception=False)
        if key:
            return key
        return frappe.conf.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    if provider == "OpenAI":
        key = settings.get_password("openai_api_key", raise_exception=False)
        if key:
            return key
        return frappe.conf.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")

    if provider == "Google Gemini":
        key = settings.get_password("gemini_api_key", raise_exception=False)
        if key:
            return key
        return frappe.conf.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")

    if provider == "Ollama":
        return "ollama"  # Ollama doesn't require an API key

    if provider == "OpenRouter":
        key = settings.get_password("openrouter_api_key", raise_exception=False)
        if key:
            return key
        return frappe.conf.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY", "")

    return ""


def get_ollama_url() -> str:
    """Return the base URL for the local Ollama server."""
    settings = frappe.get_single("FlowAgent Settings")
    return (
        settings.ollama_base_url
        or frappe.conf.get("ollama_base_url")
        or "http://localhost:11434"
    )


def get_default_model() -> str:
    """Return the configured default model, falling back to the provider's recommended default."""
    settings = frappe.get_single("FlowAgent Settings")
    if settings.default_model:
        return settings.default_model
    provider = get_default_provider()
    return _PROVIDER_DEFAULTS.get(provider, "claude-sonnet-4-5")


# ---------------------------------------------------------------------------
# Unified AI text call (used by ai_build and importable by other modules)
# ---------------------------------------------------------------------------

def call_ai_text(
    messages: list,
    *,
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 1024,
) -> tuple[str, dict]:
    """Make a single-shot text call to the configured AI provider.

    Args:
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
        system:   Optional system prompt string.
        model:    Model override; falls back to get_default_model().
        provider: Provider override; falls back to get_default_provider().
        max_tokens: Maximum tokens in the response.

    Returns:
        (text, usage) where usage = {"input": int, "output": int}.
    """
    if provider is None:
        provider = get_default_provider()
    if model is None:
        model = get_default_model()

    key = get_provider_key(provider)
    if not key and provider != "Ollama":
        frappe.throw(
            f"No API key configured for {provider}. "
            "Set it in FlowAgent Settings → API Keys."
        )

    if provider == "Anthropic":
        try:
            from anthropic import Anthropic
        except ImportError:
            frappe.throw("Install the 'anthropic' package: pip install anthropic")

        client = Anthropic(api_key=key)
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        usage = {
            "input": getattr(response.usage, "input_tokens", 0),
            "output": getattr(response.usage, "output_tokens", 0),
        }
        return text, usage

    # OpenAI-compatible path (OpenAI, Google Gemini, Ollama)
    try:
        from openai import OpenAI
    except ImportError:
        frappe.throw("Install the 'openai' package: pip install openai")

    if provider == "Google Gemini":
        client = OpenAI(api_key=key, base_url=_GEMINI_BASE_URL)
    elif provider == "Ollama":
        base_url = get_ollama_url().rstrip("/")
        client = OpenAI(api_key="ollama", base_url=f"{base_url}/v1")
    elif provider == "OpenRouter":
        client = OpenAI(
            api_key=key,
            base_url=_OPENROUTER_BASE_URL,
            default_headers={"HTTP-Referer": "https://flowagent.ai", "X-Title": "FlowAgent"},
        )
    else:
        client = OpenAI(api_key=key)

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    response = client.chat.completions.create(
        model=model, max_tokens=max_tokens, messages=msgs
    )
    text = (response.choices[0].message.content or "").strip()
    usage = {
        "input": getattr(response.usage, "prompt_tokens", 0),
        "output": getattr(response.usage, "completion_tokens", 0),
    }
    return text, usage


# ---------------------------------------------------------------------------
# WhatsApp configuration helper
# ---------------------------------------------------------------------------

def get_whatsapp_config() -> dict:
    """Return the active WhatsApp provider config as a plain dict.

    Returns:
        {
            "provider": "Meta Cloud API" | "WaClient",
            # Meta Cloud API
            "phone_id": str,
            "access_token": str,
            # WaClient
            "endpoint": str,
            "session": str,
            "api_key": str,
        }
    Keys that don't apply to the active provider will be empty strings.
    Credentials fall back to site_config.json then environment variables.
    """
    settings = frappe.get_single("FlowAgent Settings")
    provider = settings.whatsapp_provider or "Meta Cloud API"

    if provider == "Meta Cloud API":
        phone_id = (
            settings.whatsapp_phone_id
            or frappe.conf.get("whatsapp_phone_id")
            or os.environ.get("WHATSAPP_PHONE_ID", "")
        )
        token = (
            settings.get_password("whatsapp_access_token", raise_exception=False)
            or frappe.conf.get("whatsapp_access_token")
            or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        )
        return {"provider": provider, "phone_id": phone_id, "access_token": token,
                "endpoint": "", "session": "", "api_key": ""}

    # WaClient
    endpoint = (
        settings.waclient_endpoint
        or frappe.conf.get("waclient_endpoint")
        or os.environ.get("WACLIENT_ENDPOINT", "http://localhost:3000")
    )
    session = (
        settings.waclient_session
        or frappe.conf.get("waclient_session")
        or os.environ.get("WACLIENT_SESSION", "")
    )
    api_key = (
        settings.get_password("waclient_api_key", raise_exception=False)
        or frappe.conf.get("waclient_api_key")
        or os.environ.get("WACLIENT_API_KEY", "")
    )
    return {"provider": provider, "phone_id": "", "access_token": "",
            "endpoint": endpoint, "session": session, "api_key": api_key}


# ---------------------------------------------------------------------------
# Backwards-compatibility shims
# ---------------------------------------------------------------------------

def get_anthropic_key() -> str:
    """Kept for backward compatibility. Use get_provider_key('Anthropic') instead."""
    return get_provider_key("Anthropic")
