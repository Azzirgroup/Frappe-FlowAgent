# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Schedule trigger dispatcher.

Frappe's scheduler ticks `tick()` every minute. We list all enabled
workflows with a Schedule trigger, parse their cron expressions, and
fire any whose previous scheduled fire-time falls inside the current
minute window.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.utils import now_datetime


def tick():
    """Called every minute by Frappe's scheduler."""
    try:
        from croniter import croniter
    except ImportError:
        frappe.log_error(
            title="FlowAgent: croniter missing",
            message="Schedule triggers require: pip install croniter",
        )
        return

    workflows = frappe.get_all(
        "FlowAgent Workflow",
        filters={"enabled": 1, "trigger_type": "Schedule"},
        fields=["name", "trigger_cron"],
    )
    if not workflows:
        return

    now = now_datetime()
    # Look at a 60-second window ending now. We treat a workflow as "due"
    # if croniter says its previous scheduled tick falls in [now-60s, now].
    window_start = now - timedelta(seconds=60)

    for wf in workflows:
        cron = (wf.get("trigger_cron") or "").strip()
        if not cron or not croniter.is_valid(cron):
            continue
        # Get the most recent fire time at or before now
        prev_fire = croniter(cron, now).get_prev(datetime)
        if window_start <= prev_fire <= now:
            _enqueue(wf["name"], prev_fire)


def _enqueue(wf_name: str, scheduled_for: datetime):
    """Schedule the workflow run on the background worker."""
    frappe.enqueue(
        "flowagent.engine.run_workflow_background",
        queue="default",
        timeout=600,
        workflow_name=wf_name,
        trigger_source="schedule",
        payload={
            "tick_at": scheduled_for.isoformat(),
        },
        user="Administrator",
    )
