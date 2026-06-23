# Copyright (c) 2026, FlowAgent
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


class FlowAgentForm(Document):
    def validate(self):
        self._normalise_slug()
        self._validate_schema()

    def _normalise_slug(self):
        """Lowercase, dash-separated. Must be URL-safe."""
        if self.slug:
            self.slug = self.slug.strip().lower()
        if not self.slug or not _SLUG_RE.match(self.slug):
            frappe.throw(
                "URL Slug must be lowercase, start and end with a letter/digit, "
                "and contain only letters, digits and dashes (e.g. 'contact-us')."
            )

    def _validate_schema(self):
        """Parse the schema JSON and sanity-check field definitions."""
        import json
        if not self.schema:
            frappe.throw("Form schema is required")
        try:
            fields = json.loads(self.schema)
        except Exception as e:
            frappe.throw(f"Schema is not valid JSON: {e}")
        if not isinstance(fields, list):
            frappe.throw("Schema must be a JSON array of field objects")

        allowed_types = {
            "text", "email", "number", "textarea", "select",
            "checkbox", "date", "tel", "url",
        }
        seen_names = set()
        for i, f in enumerate(fields):
            if not isinstance(f, dict):
                frappe.throw(f"Field {i+1}: must be an object")
            name = f.get("name")
            if not name or not isinstance(name, str):
                frappe.throw(f"Field {i+1}: 'name' is required (string)")
            if name in seen_names:
                frappe.throw(f"Field {i+1}: duplicate name '{name}'")
            seen_names.add(name)
            if not f.get("label"):
                frappe.throw(f"Field '{name}': 'label' is required")
            ftype = f.get("type", "text")
            if ftype not in allowed_types:
                frappe.throw(
                    f"Field '{name}': unknown type '{ftype}'. "
                    f"Allowed: {', '.join(sorted(allowed_types))}"
                )
            if ftype == "select" and not f.get("options"):
                frappe.throw(f"Field '{name}': select type requires 'options' (array)")
