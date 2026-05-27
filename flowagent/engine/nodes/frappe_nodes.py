# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Frappe DocType operations.
"""

from __future__ import annotations

import json

import frappe

from . import BaseExecutor, node


def _parse_dict(raw):
    """Accept either a JSON string or a dict; return dict (empty on fail)."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


@node("frappe_create")
class CreateDocNode(BaseExecutor):
    """Create a new document. cfg.doctype, cfg.values (JSON)."""

    def run(self, *, node, cfg, context, runner):
        doctype = cfg.get("doctype")
        if not doctype:
            frappe.throw("frappe_create requires a doctype")
        values = _parse_dict(cfg.get("values") or cfg.get("fields"))
        doc = frappe.get_doc({"doctype": doctype, **values})
        doc.insert()
        return {"name": doc.name, **{k: doc.get(k) for k in values.keys() if doc.get(k) is not None}}


@node("frappe_update")
class UpdateDocNode(BaseExecutor):
    """Update fields on an existing doc. cfg.doctype, cfg.name, cfg.fields/values."""

    def run(self, *, node, cfg, context, runner):
        doctype = cfg.get("doctype")
        name = cfg.get("name") or context.get("doc_name") or context.get("doc", {}).get("name")
        if not doctype:
            frappe.throw("frappe_update: 'DocType' is empty. Set it on the node.")
        if not name:
            raw_name = (node.get("cfg") or {}).get("name") or ""
            hint = (
                f" The template was: {raw_name!r}. If it contains "
                f"{{{{trigger.doc.name}}}} or similar, the trigger context "
                f"may be empty — run this workflow against an actual record "
                f"via the 'Run' dialog or wait for it to fire from a real "
                f"DocType event."
            ) if "{{" in raw_name else ""
            frappe.throw(f"frappe_update: 'Document name' resolved to empty.{hint}")
        values = _parse_dict(cfg.get("fields") or cfg.get("values"))
        if not values:
            frappe.throw(
                "frappe_update: 'Fields' is empty or not valid JSON. "
                "Provide a JSON object like {\"status\": \"Done\"}."
            )
        doc = frappe.get_doc(doctype, name)
        for k, v in values.items():
            doc.set(k, v)
        doc.save()
        return {"name": doc.name, "updated_fields": list(values.keys())}


@node("frappe_fetch")
class FetchDocNode(BaseExecutor):
    """Fetch documents. Two modes:
       - cfg.name set → fetch single doc, return dict
       - else → list query with cfg.filters (JSON), cfg.fields (csv), cfg.limit
    """

    def run(self, *, node, cfg, context, runner):
        doctype = cfg.get("doctype")
        if not doctype:
            frappe.throw("frappe_fetch requires a doctype")

        if cfg.get("name"):
            doc = frappe.get_doc(doctype, cfg["name"])
            return doc.as_dict()

        filters = _parse_dict(cfg.get("filters"))
        fields_csv = cfg.get("fields") or "name"
        fields = [f.strip() for f in fields_csv.split(",") if f.strip()]
        limit = int(cfg.get("limit") or 20)
        order_by = cfg.get("order_by") or "modified desc"
        return frappe.get_all(doctype, filters=filters, fields=fields, limit=limit, order_by=order_by)


@node("frappe_submit")
class SubmitDocNode(BaseExecutor):
    """Submit (docstatus → 1) a submittable doc."""

    def run(self, *, node, cfg, context, runner):
        doctype = cfg.get("doctype")
        name = cfg.get("name")
        if not (doctype and name):
            frappe.throw("frappe_submit requires doctype and name")
        doc = frappe.get_doc(doctype, name)
        doc.submit()
        return {"name": doc.name, "docstatus": 1}


@node("frappe_script")
class ServerScriptNode(BaseExecutor):
    """Run a sandboxed Python snippet.

    Uses Frappe's safe_exec which restricts builtins. The snippet has
    access to `context` (dict) and `frappe` (limited interface). It
    should assign to `result` for the output.
    """

    def run(self, *, node, cfg, context, runner):
        from frappe.utils.safe_exec import safe_exec

        code = cfg.get("script") or cfg.get("code") or ""
        if not code:
            return None
        local = {"context": context.data, "result": None, "input": context.data}
        safe_exec(code, _locals=local)
        return local.get("result")
