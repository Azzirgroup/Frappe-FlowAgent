# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class FlowAgentWorkflowVersion(Document):
    """Immutable snapshot of a workflow at a point in time.

    Versions are created on every successful Save in the Studio (auto)
    and can also be created manually with a custom message ("before
    refactor", "added Slack step"). Restoring a version copies its
    nodes/edges/trigger back onto the parent workflow.
    """
    pass
