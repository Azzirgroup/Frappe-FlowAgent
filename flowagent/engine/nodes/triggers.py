# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Trigger nodes.

At execution time, a trigger is just an entry point: the actual
triggering happened upstream (DocType event, cron tick, webhook hit).
The trigger node's job here is simply to expose the inbound payload as
context variables. Whatever the trigger source put under `payload` is
already in the context — the trigger node primarily acts as a labelled
DAG root and a place to declare expectations.
"""

from __future__ import annotations

from . import BaseExecutor, node


@node("trigger_doctype")
class DocTypeTrigger(BaseExecutor):
    def run(self, *, node, cfg, context, runner):
        # Payload was injected by the dispatcher. We just acknowledge it.
        return {
            "doctype": cfg.get("doctype"),
            "event": cfg.get("event"),
            "doc": context.get("doc"),
        }


@node("trigger_webhook")
class WebhookTrigger(BaseExecutor):
    def run(self, *, node, cfg, context, runner):
        return {
            "path": cfg.get("path"),
            "body": context.get("body"),
            "headers": context.get("headers"),
        }


@node("trigger_schedule")
class ScheduleTrigger(BaseExecutor):
    def run(self, *, node, cfg, context, runner):
        return {"cron": cfg.get("cron"), "tick_at": context.get("tick_at")}


@node("trigger_manual")
class ManualTrigger(BaseExecutor):
    def run(self, *, node, cfg, context, runner):
        return {"user": runner.user, "payload": context.get("trigger")}


@node("trigger_form")
class FormTrigger(BaseExecutor):
    """Entry point for runs spawned by a hosted FlowAgent Form submission.

    The forms API (flowagent.api.forms.submit) creates a Workflow Run
    with the submitted data as the payload — this node simply marks the
    workflow's intent to be triggered by a form and surfaces the form
    metadata to downstream nodes via context.

    cfg.form_slug — informational only; the actual binding happens on
    the FlowAgent Form record (which references the workflow).
    """

    def run(self, *, node, cfg, context, runner):
        return {
            "form_slug": cfg.get("form_slug") or context.get("form", {}).get("slug"),
            "submission": context.get("doc") or {},
            "submitted_at": context.get("form", {}).get("submitted_at"),
            "ip": context.get("form", {}).get("ip"),
        }
