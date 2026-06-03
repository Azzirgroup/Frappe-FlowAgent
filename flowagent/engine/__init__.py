# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
FlowAgent execution engine package.

Public surface:
  run_workflow_background -- entry point for frappe.enqueue
  Runner                  -- directly callable synchronously (used by tests
                             and the "Run Now" button)
"""

from __future__ import annotations

import frappe

from .runner import Runner


def run_workflow_background(workflow_name, trigger_source="manual",
                            payload=None, user=None, dry_run=False,
                            existing_run_name=None):
    """Background-worker entry point.

    Lives at this dotted path so it can be referenced by frappe.enqueue
    without circular import worries.
    """
    if user and user != frappe.session.user:
        frappe.set_user(user)
    runner = Runner(
        workflow_name=workflow_name,
        trigger_source=trigger_source,
        payload=payload or {},
        user=user,
        dry_run=dry_run,
        existing_run_name=existing_run_name,
    )
    return runner.execute()


__all__ = ["Runner", "run_workflow_background"]
