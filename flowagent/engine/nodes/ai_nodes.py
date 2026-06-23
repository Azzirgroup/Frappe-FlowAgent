# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
AI nodes — the differentiator.

These wrap multiple AI providers with task-specific shapes:

* ai_llm        — open-ended prompt → text
* ai_extract    — JSON extraction with a declared schema
* ai_classify   — pick one label from a fixed set
* ai_sentiment  — short-cut classifier with predefined labels
* ai_vision     — image / PDF OCR + understanding
* ai_agent      — ReAct loop: LLM with tool use, can call Frappe DocType
                  reads/writes until it produces a final answer

All nodes honour a per-node "provider" config key that overrides the global
default set in FlowAgent Settings. This lets individual nodes in the same
workflow use different providers (e.g. GPT-4o for extraction, Claude for
the agent loop).

Supported providers:
  Anthropic    — Claude models via the anthropic SDK
  OpenAI       — GPT models via the openai SDK
  Google Gemini — Gemini models via the openai SDK (OpenAI-compatible endpoint)
  Ollama       — Local models via the openai SDK (OpenAI-compatible endpoint)
"""

from __future__ import annotations

import base64
import json
import re

import frappe

from . import BaseExecutor, node


# ---------------------------------------------------------------------------
# Approximate per-million-token prices (USD) for cost estimation.
# These are client-side fallbacks; prices may drift over time.
# ---------------------------------------------------------------------------
_MODEL_PRICES = {
    # Anthropic — Claude
    "claude-sonnet-4":          {"in": 3.0,   "out": 15.0},
    "claude-sonnet-4-5":        {"in": 3.0,   "out": 15.0},
    "claude-sonnet-4-20250514": {"in": 3.0,   "out": 15.0},
    "claude-opus-4":            {"in": 15.0,  "out": 75.0},
    "claude-opus-4-5":          {"in": 15.0,  "out": 75.0},
    "claude-haiku-4-5":         {"in": 1.0,   "out": 5.0},
    "claude-haiku-3-5":         {"in": 0.8,   "out": 4.0},
    # OpenAI — GPT
    "gpt-4o":                   {"in": 2.5,   "out": 10.0},
    "gpt-4o-mini":              {"in": 0.15,  "out": 0.6},
    "gpt-4-turbo":              {"in": 10.0,  "out": 30.0},
    "gpt-3.5-turbo":            {"in": 0.5,   "out": 1.5},
    # Google Gemini
    "gemini-1.5-pro":           {"in": 1.25,  "out": 5.0},
    "gemini-1.5-flash":         {"in": 0.075, "out": 0.3},
    "gemini-2.0-flash":         {"in": 0.1,   "out": 0.4},
    # Ollama — local, no cost
}

_GEMINI_BASE_URL     = "https://generativelanguage.googleapis.com/v1beta/openai/"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------

def _get_ai_config(cfg: dict) -> tuple[str, str, str, str]:
    """Resolve (provider, model, api_key, ollama_base_url) from node cfg + settings."""
    from flowagent.flowagent_core.doctype.flowagent_settings.flowagent_settings import (
        get_default_provider,
        get_default_model,
        get_provider_key,
        get_ollama_url,
    )
    provider = cfg.get("provider") or get_default_provider()
    model = cfg.get("model") or get_default_model()
    key = get_provider_key(provider)
    base_url = get_ollama_url() if provider == "Ollama" else ""
    return provider, model, key, base_url


def _make_anthropic_client(key: str):
    try:
        from anthropic import Anthropic
    except ImportError:
        frappe.throw("Install the 'anthropic' package: pip install anthropic")
    if not key:
        frappe.throw(
            "No Anthropic API key configured. Set it in FlowAgent Settings → API Keys."
        )
    return Anthropic(api_key=key)


def _make_openai_client(provider: str, key: str, base_url: str = ""):
    """Create an openai.OpenAI client for OpenAI, Google Gemini, or Ollama."""
    try:
        from openai import OpenAI
    except ImportError:
        frappe.throw("Install the 'openai' package: pip install openai")
    if not key and provider != "Ollama":
        frappe.throw(
            f"No API key configured for {provider}. Set it in FlowAgent Settings → API Keys."
        )
    if provider == "Google Gemini":
        return OpenAI(api_key=key, base_url=_GEMINI_BASE_URL)
    if provider == "Ollama":
        url = (base_url or "http://localhost:11434").rstrip("/")
        return OpenAI(api_key="ollama", base_url=f"{url}/v1")
    if provider == "OpenRouter":
        return OpenAI(
            api_key=key,
            base_url=_OPENROUTER_BASE_URL,
            default_headers={"HTTP-Referer": "https://flowagent.ai", "X-Title": "FlowAgent"},
        )
    return OpenAI(api_key=key)


def _call_text(
    provider: str,
    client,
    model: str,
    messages: list,
    *,
    system: str | None = None,
    max_tokens: int = 1024,
) -> tuple[str, dict]:
    """Unified single-shot text call. Returns (text, usage_dict)."""
    if provider == "Anthropic":
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        text = _extract_anthropic_text(response)
        usage = {
            "input": getattr(response.usage, "input_tokens", 0),
            "output": getattr(response.usage, "output_tokens", 0),
        }
        return text, usage

    # OpenAI-compatible (OpenAI, Gemini, Ollama)
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


def _extract_anthropic_text(response) -> str:
    """Pull text blocks out of an Anthropic Message response."""
    return "".join(
        b.text for b in (response.content or []) if getattr(b, "type", None) == "text"
    ).strip()


def _record_usage(runner, model: str, usage: dict) -> None:
    """Accumulate token usage and estimated cost onto the runner."""
    if not runner:
        return
    in_tok = usage.get("input", 0) or 0
    out_tok = usage.get("output", 0) or 0
    if not hasattr(runner, "_token_usage"):
        runner._token_usage = {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0}
    runner._token_usage["input"] += in_tok
    runner._token_usage["output"] += out_tok
    runner._token_usage["calls"] += 1
    price = _MODEL_PRICES.get(model)
    if not price:
        for k, p in _MODEL_PRICES.items():
            if model.startswith(k):
                price = p
                break
    if price:
        runner._token_usage["cost_usd"] += (
            in_tok * price["in"] / 1_000_000
            + out_tok * price["out"] / 1_000_000
        )


def _strip_code_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# 1. Open-ended LLM prompt
# ---------------------------------------------------------------------------
@node("ai_llm")
class LLMPromptNode(BaseExecutor):
    """Plain prompt → text response.

    cfg:
      prompt        — the (rendered) prompt string
      system        — optional system prompt
      provider      — optional provider override (Anthropic / OpenAI / Google Gemini / Ollama)
      model         — optional model override
      max_tokens    — default 1024
      output        — variable name to store the text under
    """

    def run(self, *, node, cfg, context, runner):
        provider, model, key, base_url = _get_ai_config(cfg)
        client = (
            _make_anthropic_client(key)
            if provider == "Anthropic"
            else _make_openai_client(provider, key, base_url)
        )
        text, usage = _call_text(
            provider,
            client,
            model,
            [{"role": "user", "content": cfg.get("prompt", "")}],
            system=cfg.get("system"),
            max_tokens=int(cfg.get("max_tokens") or 1024),
        )
        _record_usage(runner, model, usage)
        return text


# ---------------------------------------------------------------------------
# 2. Structured extraction
# ---------------------------------------------------------------------------
@node("ai_extract")
class ExtractNode(BaseExecutor):
    """Extract structured fields from arbitrary text.

    cfg:
      source     — the text to extract from (Jinja-rendered)
      fields     — comma-separated field names, or a JSON object describing
                   field name → description
      provider   — optional provider override
      model      — optional model override
      output     — variable name to store the result dict under
    """

    def run(self, *, node, cfg, context, runner):
        source = cfg.get("source") or cfg.get("text") or ""
        if not source:
            source = json.dumps(context.get("$last"), default=str)

        fields_raw = cfg.get("fields") or ""
        schema = self._build_schema(fields_raw)
        if not schema:
            frappe.throw("ai_extract needs at least one field to extract")

        field_lines = "\n".join(f"  - {k}: {desc}" for k, desc in schema.items())
        prompt = (
            "Extract the following fields from the source text. "
            "Return ONLY a JSON object with these keys, no prose, no markdown.\n\n"
            f"Fields to extract:\n{field_lines}\n\n"
            f"Source text:\n{source}\n\n"
            "JSON:"
        )

        provider, model, key, base_url = _get_ai_config(cfg)
        client = (
            _make_anthropic_client(key)
            if provider == "Anthropic"
            else _make_openai_client(provider, key, base_url)
        )
        text, usage = _call_text(
            provider,
            client,
            model,
            [{"role": "user", "content": prompt}],
            max_tokens=int(cfg.get("max_tokens") or 1024),
        )
        _record_usage(runner, model, usage)
        text = _strip_code_fences(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            frappe.throw(f"ai_extract: model returned non-JSON: {text[:300]}")

    def _build_schema(self, fields_raw: str) -> dict:
        fields_raw = (fields_raw or "").strip()
        if fields_raw.startswith("{"):
            try:
                return json.loads(fields_raw)
            except json.JSONDecodeError:
                pass
        return {f.strip(): f.strip() for f in fields_raw.split(",") if f.strip()}


# ---------------------------------------------------------------------------
# 3. Classifier
# ---------------------------------------------------------------------------
@node("ai_classify")
class ClassifyNode(BaseExecutor):
    """Pick one category from a fixed set.

    cfg:
      text         — input text
      categories   — comma-separated category labels
      instructions — optional extra steering
      provider     — optional provider override
      model        — optional model override
      output       — variable to store the chosen category
    """

    def run(self, *, node, cfg, context, runner):
        text = cfg.get("text") or json.dumps(context.get("$last"), default=str)
        cats = [c.strip() for c in (cfg.get("categories") or "").split(",") if c.strip()]
        if not cats:
            frappe.throw("ai_classify requires non-empty 'categories'")

        prompt = (
            f"Classify the input into exactly one of these categories: {', '.join(cats)}.\n"
            f"{cfg.get('instructions') or ''}\n\n"
            f"Input:\n{text}\n\n"
            "Reply with ONLY the chosen category label, nothing else."
        )

        provider, model, key, base_url = _get_ai_config(cfg)
        client = (
            _make_anthropic_client(key)
            if provider == "Anthropic"
            else _make_openai_client(provider, key, base_url)
        )
        chosen, usage = _call_text(
            provider, client, model,
            [{"role": "user", "content": prompt}],
            max_tokens=50,
        )
        _record_usage(runner, model, usage)
        chosen = chosen.strip().strip(".\"'")
        for c in cats:
            if c.lower() == chosen.lower():
                return c
        for c in cats:
            if c.lower() in chosen.lower():
                return c
        return chosen or cats[0]


# ---------------------------------------------------------------------------
# 4. Sentiment (specialised classifier)
# ---------------------------------------------------------------------------
@node("ai_sentiment")
class SentimentNode(BaseExecutor):
    """positive / negative / neutral / mixed.

    cfg:
      text     — input text
      provider — optional provider override
      model    — optional model override
    """

    LABELS = ["positive", "negative", "neutral", "mixed"]

    def run(self, *, node, cfg, context, runner):
        text = cfg.get("text") or ""
        prompt = (
            "Classify the sentiment of the following text. Reply with a JSON "
            f"object: {{\"sentiment\": one of {self.LABELS}, \"score\": float in [-1, 1]}}\n\n"
            f"Text:\n{text}\n\nJSON:"
        )
        provider, model, key, base_url = _get_ai_config(cfg)
        client = (
            _make_anthropic_client(key)
            if provider == "Anthropic"
            else _make_openai_client(provider, key, base_url)
        )
        raw, usage = _call_text(
            provider, client, model,
            [{"role": "user", "content": prompt}],
            max_tokens=80,
        )
        _record_usage(runner, model, usage)
        raw = _strip_code_fences(raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"sentiment": "neutral", "score": 0.0, "raw": raw}


# ---------------------------------------------------------------------------
# 5. Vision / OCR
# ---------------------------------------------------------------------------
@node("ai_vision")
class VisionNode(BaseExecutor):
    """Send an image (URL or Frappe File doc) to an AI model for OCR / understanding.

    cfg:
      file_url  — public or private Frappe file URL
      prompt    — what to do with the image
      provider  — optional provider override (Anthropic, OpenAI, Google Gemini, Ollama+llava)
      model     — optional model override
      output    — variable name
    """

    def run(self, *, node, cfg, context, runner):
        file_url = cfg.get("file_url", "")
        prompt = cfg.get("prompt") or "Extract all text from this image."
        if not file_url:
            frappe.throw("ai_vision requires file_url")

        provider, model, key, base_url = _get_ai_config(cfg)
        max_tokens = int(cfg.get("max_tokens") or 1500)

        if provider == "Anthropic":
            client = _make_anthropic_client(key)
            image_block = self._image_block_anthropic(file_url)
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{
                    "role": "user",
                    "content": [image_block, {"type": "text", "text": prompt}],
                }],
            )
            usage = {
                "input": getattr(response.usage, "input_tokens", 0),
                "output": getattr(response.usage, "output_tokens", 0),
            }
            _record_usage(runner, model, usage)
            return _extract_anthropic_text(response)

        # OpenAI-compatible vision (OpenAI GPT-4o, Gemini, Ollama llava, etc.)
        client = _make_openai_client(provider, key, base_url)
        image_content = self._image_content_openai(file_url)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [image_content, {"type": "text", "text": prompt}],
            }],
        )
        usage = {
            "input": getattr(response.usage, "prompt_tokens", 0),
            "output": getattr(response.usage, "completion_tokens", 0),
        }
        _record_usage(runner, model, usage)
        return response.choices[0].message.content or ""

    def _image_block_anthropic(self, file_url: str) -> dict:
        if file_url.startswith(("http://", "https://")):
            return {"type": "image", "source": {"type": "url", "url": file_url}}
        from frappe.utils.file_manager import get_file_path
        path = get_file_path(file_url)
        with open(path, "rb") as fh:
            raw = fh.read()
        media_type = _guess_media_type(file_url)
        b64 = base64.b64encode(raw).decode("ascii")
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}

    def _image_content_openai(self, file_url: str) -> dict:
        if file_url.startswith(("http://", "https://")):
            return {"type": "image_url", "image_url": {"url": file_url}}
        from frappe.utils.file_manager import get_file_path
        path = get_file_path(file_url)
        with open(path, "rb") as fh:
            raw = fh.read()
        media_type = _guess_media_type(file_url)
        b64 = base64.b64encode(raw).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}


def _guess_media_type(file_url: str) -> str:
    url_lower = file_url.lower()
    if url_lower.endswith(".png"):   return "image/png"
    if url_lower.endswith(".webp"):  return "image/webp"
    if url_lower.endswith(".gif"):   return "image/gif"
    if url_lower.endswith(".pdf"):   return "application/pdf"
    return "image/jpeg"


# ---------------------------------------------------------------------------
# 6. Auto Agent — the marquee node
# ---------------------------------------------------------------------------
@node("ai_agent")
class AgentNode(BaseExecutor):
    """ReAct-style agent loop with Frappe DocType tools.

    The LLM is given:
      - a system prompt describing its task
      - a set of tools backed by Frappe operations (read/list/count/update/create)
      - a maximum iteration budget (from settings)

    cfg:
      task              — natural-language description of what to do
      allowed_doctypes  — comma-separated list of DocTypes the agent can touch
      max_iters         — override the settings default
      can_write         — bool; if false, only read tools are exposed
      provider          — optional provider override
      model             — optional model override
      system            — optional system prompt override
    """

    def run(self, *, node, cfg, context, runner):
        provider, model, key, base_url = _get_ai_config(cfg)
        settings = runner.settings
        max_iters = int(cfg.get("max_iters") or settings.max_agent_iterations or 8)
        allowed = [d.strip() for d in (cfg.get("allowed_doctypes") or "").split(",") if d.strip()]
        can_write = bool(cfg.get("can_write") in (True, "true", "True", "1", 1))
        tools = _build_agent_tools(allowed, can_write)

        system = cfg.get("system") or (
            "You are a workflow automation agent inside a Frappe ERP system. "
            "You complete the user's task by calling tools to read or modify data, "
            "then summarise what you did in plain language. "
            "Prefer the smallest number of tool calls. When you're done, "
            "respond with a final text message describing the outcome."
        )

        task = cfg.get("task", "")
        ctx_summary = json.dumps(context.snapshot(), default=str)[:8000]
        initial = f"Task: {task}\n\nCurrent workflow context:\n{ctx_summary}"

        if provider == "Anthropic":
            client = _make_anthropic_client(key)
            return _run_agent_anthropic(
                client, model, system, initial, tools, max_iters, cfg, runner, allowed, can_write
            )

        client = _make_openai_client(provider, key, base_url)
        return _run_agent_openai(
            client, model, system, initial, tools, max_iters, cfg, runner, allowed, can_write
        )


# ---------------------------------------------------------------------------
# Agent tool definitions (Anthropic format is the canonical internal format)
# ---------------------------------------------------------------------------

def _build_agent_tools(allowed_doctypes: list, can_write: bool) -> list[dict]:
    """Construct the tool schema list (Anthropic format).

    Tools are converted to OpenAI format on demand by _anthropic_tools_to_openai().
    """
    doctype_enum = allowed_doctypes if allowed_doctypes else ["__none__"]
    tools = [
        {
            "name": "list_documents",
            "description": (
                "List documents of a given DocType with optional filters. "
                f"Allowed doctypes: {', '.join(allowed_doctypes) or 'NONE'}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": doctype_enum},
                    "filters": {"type": "object", "description": "Filter dict e.g. {\"status\": \"Open\"}"},
                    "limit": {"type": "integer", "default": 20, "maximum": 50},
                },
                "required": ["doctype"],
            },
        },
        {
            "name": "get_document",
            "description": "Fetch a single document by name with all its fields.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": doctype_enum},
                    "name": {"type": "string"},
                },
                "required": ["doctype", "name"],
            },
        },
        {
            "name": "count_documents",
            "description": "Count documents matching filters.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": doctype_enum},
                    "filters": {"type": "object"},
                },
                "required": ["doctype"],
            },
        },
    ]

    if can_write:
        tools.extend([
            {
                "name": "update_document",
                "description": "Update fields on an existing document.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "doctype": {"type": "string", "enum": doctype_enum},
                        "name": {"type": "string"},
                        "updates": {"type": "object", "description": "Field name → new value"},
                    },
                    "required": ["doctype", "name", "updates"],
                },
            },
            {
                "name": "create_document",
                "description": "Create a new document.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "doctype": {"type": "string", "enum": doctype_enum},
                        "values": {"type": "object"},
                    },
                    "required": ["doctype", "values"],
                },
            },
        ])

    return tools


def _anthropic_tools_to_openai(tools: list) -> list:
    """Convert Anthropic tool schema format to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _execute_agent_tool(tool_name: str, tool_input: dict, allowed: list, can_write: bool):
    """Run a tool the agent invoked. Strictly validates doctype access."""
    doctype = tool_input.get("doctype")
    if doctype and doctype not in allowed:
        return {"error": f"DocType '{doctype}' is not in the allowed list: {allowed}"}

    if tool_name == "list_documents":
        limit = min(int(tool_input.get("limit") or 20), 50)
        return frappe.get_all(doctype, filters=tool_input.get("filters") or {}, limit=limit, fields=["name"])

    if tool_name == "get_document":
        doc = frappe.get_doc(doctype, tool_input.get("name"))
        return doc.as_dict()

    if tool_name == "count_documents":
        return {"count": frappe.db.count(doctype, filters=tool_input.get("filters") or {})}

    if tool_name == "update_document":
        if not can_write:
            return {"error": "Write tools disabled — set can_write=true on the node"}
        doc = frappe.get_doc(doctype, tool_input["name"])
        for k, v in (tool_input.get("updates") or {}).items():
            doc.set(k, v)
        doc.save()
        return {"updated": doc.name}

    if tool_name == "create_document":
        if not can_write:
            return {"error": "Write tools disabled — set can_write=true on the node"}
        doc = frappe.get_doc({"doctype": doctype, **(tool_input.get("values") or {})})
        doc.insert()
        return {"created": doc.name}

    return {"error": f"Unknown tool '{tool_name}'"}


# ---------------------------------------------------------------------------
# Agent loop — Anthropic
# ---------------------------------------------------------------------------

def _run_agent_anthropic(client, model, system, initial, tools, max_iters, cfg, runner, allowed, can_write):
    messages = [{"role": "user", "content": initial}]
    final_text = ""
    tool_log = []
    iteration = 0

    for iteration in range(max_iters):
        response = client.messages.create(
            model=model,
            max_tokens=int(cfg.get("max_tokens") or 1500),
            system=system,
            tools=tools,
            messages=messages,
        )
        _record_usage(runner, model, {
            "input": getattr(response.usage, "input_tokens", 0),
            "output": getattr(response.usage, "output_tokens", 0),
        })

        messages.append({"role": "assistant", "content": response.content})

        tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]

        if not tool_use_blocks:
            final_text = "".join(b.text for b in text_blocks).strip()
            break

        tool_results = []
        for tb in tool_use_blocks:
            try:
                result = _execute_agent_tool(tb.name, tb.input, allowed, can_write)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": json.dumps(result, default=str)[:30000],
                })
                tool_log.append({"tool": tb.name, "input": tb.input, "result": result})
            except Exception as e:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "is_error": True,
                    "content": f"Error: {type(e).__name__}: {e}",
                })
                tool_log.append({"tool": tb.name, "input": tb.input, "error": str(e)})

        messages.append({"role": "user", "content": tool_results})

        if response.stop_reason == "end_turn":
            final_text = "".join(b.text for b in text_blocks).strip()
            break
    else:
        final_text = final_text or "[Agent hit max iterations without producing a final answer]"

    return {"text": final_text, "iterations": iteration + 1, "tool_calls": tool_log}


# ---------------------------------------------------------------------------
# Agent loop — OpenAI-compatible (OpenAI, Google Gemini, Ollama)
# ---------------------------------------------------------------------------

def _run_agent_openai(client, model, system, initial, tools, max_iters, cfg, runner, allowed, can_write):
    oai_tools = _anthropic_tools_to_openai(tools)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": initial},
    ]
    final_text = ""
    tool_log = []
    iteration = 0

    for iteration in range(max_iters):
        kwargs = {
            "model": model,
            "max_tokens": int(cfg.get("max_tokens") or 1500),
            "messages": messages,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools

        response = client.chat.completions.create(**kwargs)
        _record_usage(runner, model, {
            "input": getattr(response.usage, "prompt_tokens", 0),
            "output": getattr(response.usage, "completion_tokens", 0),
        })

        choice = response.choices[0]
        msg = choice.message

        # Build a serialisable assistant message
        assistant_msg = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        if not msg.tool_calls:
            final_text = msg.content or ""
            break

        # Execute each tool call and add tool result messages
        for tc in msg.tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments)
                result = _execute_agent_tool(tc.function.name, tool_input, allowed, can_write)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)[:30000],
                })
                tool_log.append({"tool": tc.function.name, "input": tool_input, "result": result})
            except Exception as e:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"Error: {type(e).__name__}: {e}",
                })
                tool_log.append({"tool": tc.function.name, "error": str(e)})

        if choice.finish_reason == "stop":
            final_text = msg.content or ""
            break
    else:
        final_text = final_text or "[Agent hit max iterations without producing a final answer]"

    return {"text": final_text, "iterations": iteration + 1, "tool_calls": tool_log}
