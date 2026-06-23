# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Inbound webhook handler.

Public URL shape:
  POST /api/method/flowagent.api.webhook.handle?path=<workflow_webhook_path>
  Header: X-FlowAgent-Token: <secret>   (or ?token=<secret>)

Body is forwarded as `payload.body`. Query string lives in `payload.query`.

Two layers of auth:
  1. Global token (FlowAgent Settings.webhook_secret) — required for ALL
     webhooks. Provides minimal auth and rate-limit signal.
  2. Per-workflow HMAC signature — optional. If the workflow has
     webhook_hmac_secret set, the inbound request must include
     X-Signature-256: sha256=<hex digest of HMAC-SHA256(secret, raw_body)>.
     Matches the convention used by Stripe, GitHub, Razorpay, etc.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import frappe


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def handle(path: str | None = None, token: str | None = None):
    """Webhook entry point."""
    # Path can come from query or form; token from query or header
    path = path or frappe.form_dict.get("path")
    if not path:
        frappe.throw("Missing 'path' parameter", frappe.ValidationError)

    token = (
        token
        or frappe.form_dict.get("token")
        or frappe.get_request_header("X-FlowAgent-Token")
        or ""
    )
    settings = frappe.get_single("FlowAgent Settings")
    if not (settings.webhook_secret and _safe_compare(settings.webhook_secret, token)):
        frappe.local.response["http_status_code"] = 401
        return {"error": "Invalid token"}

    # Find the workflow
    wf_name = frappe.db.get_value(
        "FlowAgent Workflow",
        {"trigger_type": "Webhook", "webhook_path": path, "enabled": 1},
        "name",
    )
    if not wf_name:
        frappe.local.response["http_status_code"] = 404
        return {"error": "No enabled workflow for that path"}

    # Read the raw body once — needed both for HMAC verification AND for
    # the workflow payload. Decoding mid-stream after reading would lose
    # the bytes, so grab it now and reuse.
    body_raw = frappe.request.get_data(as_text=True) if frappe.request else ""

    # Per-workflow HMAC verification (optional). When the workflow has a
    # webhook_hmac_secret configured, inbound requests must include a
    # signature header matching HMAC-SHA256 of the raw body.
    hmac_secret = frappe.db.get_value(
        "FlowAgent Workflow", wf_name, "webhook_hmac_secret"
    )
    if hmac_secret:
        # Try the common header names in order. Stripe uses Stripe-Signature
        # (different format — we don't auto-support that). Standard GitHub
        # / generic style is X-Signature-256.
        sig_header = (
            frappe.get_request_header("X-Signature-256")
            or frappe.get_request_header("X-Hub-Signature-256")
            or ""
        ).strip()
        if not sig_header:
            frappe.local.response["http_status_code"] = 401
            return {"error": "Missing X-Signature-256 header"}

        # The header looks like "sha256=abcdef..." — strip the prefix
        if sig_header.startswith("sha256="):
            sig_header = sig_header[len("sha256="):]

        expected = hmac.new(
            hmac_secret.encode("utf-8"),
            (body_raw or "").encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            frappe.local.response["http_status_code"] = 403
            return {"error": "Invalid HMAC signature"}

    # Build the payload from the request
    body: object
    if body_raw:
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError:
            body = body_raw
    else:
        body = dict(frappe.form_dict)
        body.pop("path", None)
        body.pop("token", None)
        body.pop("cmd", None)

    payload = {
        "path": path,
        "body": body,
        "headers": dict(frappe.request.headers) if frappe.request else {},
    }

    # Enqueue
    frappe.enqueue(
        "flowagent.engine.run_workflow_background",
        queue="default",
        timeout=600,
        workflow_name=wf_name,
        trigger_source=f"webhook:{path}",
        payload=payload,
        user="Administrator",
    )
    return {"queued": True, "workflow": wf_name}


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time comparison so we don't leak the secret via timing."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())
