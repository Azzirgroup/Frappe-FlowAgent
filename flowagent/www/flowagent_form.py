# Copyright (c) 2026, FlowAgent
# For license information, please see license.txt
"""
Renders a hosted FlowAgent Form at /f/<slug>.

The slug is extracted from the URL path via the website_route_rule
declared in hooks.py. We look up the matching FlowAgent Form, parse
its schema, and pass everything into the .html template.
"""
from __future__ import annotations

import json

import frappe


no_cache = 1  # Don't let Frappe cache submission pages


def get_context(context):
    slug = frappe.local.form_dict.get("slug") or ""
    slug = slug.strip().lower()

    if not slug:
        frappe.local.flags.redirect_location = "/"
        raise frappe.Redirect

    forms = frappe.get_all(
        "FlowAgent Form",
        filters={"slug": slug, "enabled": 1},
        fields=["name"],
        limit=1,
        ignore_permissions=True,
    )
    if not forms:
        # 404-style "not found" page handled by Frappe — raise the
        # built-in PageDoesNotExistError so the standard 404 renders.
        raise frappe.PageDoesNotExistError

    form = frappe.get_doc("FlowAgent Form", forms[0]["name"])

    try:
        schema = json.loads(form.schema or "[]")
    except Exception:
        schema = []

    context.form = form
    context.schema = schema
    context.schema_json = json.dumps(schema)
    context.title = form.title or form.form_name
    context.submit_url = "/api/method/flowagent.api.forms.submit"
    # Suppress Frappe's default header/footer chrome — this is a
    # standalone landing page, not a desk page.
    context.no_header = 1
    context.no_breadcrumbs = 1
    context.show_sidebar = False
