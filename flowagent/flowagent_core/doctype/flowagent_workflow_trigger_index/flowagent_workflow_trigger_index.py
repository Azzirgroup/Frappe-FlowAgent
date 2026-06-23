# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FlowAgentWorkflowTriggerIndex(Document):
    """Flat lookup row managed by FlowAgent Workflow.after_save.

    Don't edit these manually — they get recreated on every workflow save.
    """
    pass
