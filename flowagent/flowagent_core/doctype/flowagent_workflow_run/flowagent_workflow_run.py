# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FlowAgentWorkflowRun(Document):
    """One row per workflow execution. The Step child rows give a
    per-node trace (input/output/error/ms) for debugging.
    """
    pass
