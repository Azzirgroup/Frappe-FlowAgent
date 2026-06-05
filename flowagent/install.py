# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Post-install / post-migrate hooks.

We ensure the FlowAgent Settings single exists and seed a couple of
example workflows so a fresh install isn't an empty canvas.
"""

import frappe


def after_install():
    """Run once after `bench install-app flowagent`."""
    _ensure_role()
    _ensure_settings()
    # Order matters: ensure the cards/charts exist BEFORE the workspace
    # is re-imported, otherwise the workspace's references would point
    # to records that don't exist yet and Frappe would silently drop them.
    _refresh_dashboard_assets()
    _refresh_workspace()
    frappe.db.commit()


def after_migrate():
    """Run after every `bench migrate` — idempotent."""
    _ensure_role()
    _ensure_settings()
    _refresh_dashboard_assets()
    _refresh_workspace()
    frappe.db.commit()


def _ensure_role():
    if not frappe.db.exists("Role", "FlowAgent Manager"):
        role = frappe.new_doc("Role")
        role.role_name = "FlowAgent Manager"
        role.desk_access = 1
        role.flags.ignore_permissions = True
        role.insert(ignore_permissions=True)


def _ensure_settings():
    if not frappe.db.exists("FlowAgent Settings", "FlowAgent Settings"):
        doc = frappe.new_doc("FlowAgent Settings")
        doc.default_model = "claude-sonnet-4-5"
        doc.max_steps_per_run = 100
        doc.max_agent_iterations = 8
        doc.run_retention_days = 30
        doc.webhook_secret = frappe.generate_hash(length=32)
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)


def _refresh_dashboard_assets():
    """Create / update every FlowAgent Number Card and Dashboard Chart
    referenced by the workspace.

    Why we don't just use `frappe.modules.import_file.import_file_by_path`:
    Number Card has an `autoname: "field:label"` rule. When Frappe inserts
    a new Number Card via the standard import path, the autoname rule
    runs during insert and overwrites the explicit `name` from the JSON
    with the value of the `label` field. So a JSON declaring
    `{name: "FlowAgent Runs Today", label: "Runs Today"}` ends up
    creating a record named just "Runs Today" — and the workspace's
    content blocks (which reference the explicit name) silently fail to
    render.

    We sidestep this by doing the upsert ourselves with
    `doc.flags.name_set = True`, which tells Frappe to skip the autoname
    rule and respect the explicit name.

    We also clean up orphan records (anything in FlowAgent Core module
    that isn't in our shipped JSON set) — that catches the "Runs Today"
    style mis-named records left behind by previous installs.
    """
    import glob
    import json as _json
    import os

    app_path = frappe.get_app_path("flowagent")
    expected_names: dict[str, set[str]] = {
        "Number Card": set(),
        "Dashboard Chart": set(),
    }

    for subdir, doctype in (("number_card", "Number Card"),
                            ("dashboard_chart", "Dashboard Chart")):
        pattern = os.path.join(
            app_path, "flowagent_core", subdir, "*", "*.json",
        )
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path) as f:
                    spec = _json.load(f)
                expected_name = spec.get("name")
                if not expected_name:
                    continue
                expected_names[doctype].add(expected_name)
                _upsert_with_explicit_name(doctype, expected_name, spec)
                # Force is_public so the workspace can render the record
                # regardless of the viewing user's role.
                if frappe.db.exists(doctype, expected_name):
                    if not frappe.db.get_value(doctype, expected_name, "is_public"):
                        frappe.db.set_value(doctype, expected_name, "is_public", 1)
            except Exception as e:
                frappe.log_error(
                    title=f"FlowAgent: failed to install {os.path.basename(path)}",
                    message=f"{type(e).__name__}: {e}",
                )

    # Orphan cleanup: anything in our module that we no longer ship.
    # Important safety check: we ONLY delete records whose name matches a
    # `label` we shipped. That catches the "Runs Today"-style mis-named
    # records that the v0.3.4/v0.3.5 import bug created (autonamed from
    # the label field), without touching any cards/charts a user may
    # have created themselves and tagged with module=FlowAgent Core.
    shipped_labels: dict[str, set[str]] = {
        "Number Card": set(),
        "Dashboard Chart": set(),
    }
    for subdir, doctype, label_field in (
        ("number_card",     "Number Card",     "label"),
        ("dashboard_chart", "Dashboard Chart", "chart_name"),
    ):
        pattern = os.path.join(
            app_path, "flowagent_core", subdir, "*", "*.json",
        )
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path) as f:
                    s = _json.load(f)
                lbl = s.get(label_field)
                if lbl:
                    shipped_labels[doctype].add(lbl)
            except Exception:
                pass

    for doctype, expected in expected_names.items():
        try:
            existing = frappe.get_all(
                doctype,
                filters={"module": "FlowAgent Core"},
                pluck="name",
            )
        except Exception:
            existing = []
        for name in existing:
            if name in expected:
                continue
            # Only delete if the record's name matches one of the labels
            # we shipped — that's the signature of a record auto-named
            # from a label by the v0.3.4/v0.3.5 bug.
            if name not in shipped_labels[doctype]:
                continue
            try:
                frappe.delete_doc(
                    doctype, name,
                    ignore_permissions=True, force=True,
                )
            except Exception as e:
                frappe.log_error(
                    title=f"FlowAgent: orphan cleanup failed for {doctype} {name}",
                    message=f"{type(e).__name__}: {e}",
                )


def _upsert_with_explicit_name(doctype: str, name: str, spec: dict):
    """Create or update a record, preserving the explicit name even
    when the doctype's autoname rule would override it.

    The critical flag is `doc.flags.name_set = True`, which Frappe's
    naming logic checks before running autoname. Without it, the
    `field:label` autoname on Number Card overwrites our explicit name.
    """
    SKIP = {"doctype", "creation", "modified", "modified_by", "owner",
            "idx", "docstatus", "name"}

    is_new = not frappe.db.exists(doctype, name)
    if is_new:
        doc = frappe.new_doc(doctype)
        doc.flags.name_set = True
        doc.name = name
    else:
        doc = frappe.get_doc(doctype, name)

    for k, v in spec.items():
        if k in SKIP:
            continue
        try:
            doc.set(k, v)
        except Exception:
            # Field doesn't exist on this Frappe version — skip silently.
            pass

    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    try:
        if is_new:
            doc.insert(ignore_permissions=True)
        else:
            doc.save()
    except frappe.DuplicateEntryError:
        # Already exists despite our existence check — race or rename
        # collision. Move on.
        pass
    except Exception as e:
        frappe.log_error(
            title=f"FlowAgent: upsert failed for {doctype} {name}",
            message=f"{type(e).__name__}: {e}",
        )


def _refresh_workspace():
    """Re-import the FlowAgent workspace from disk on every migrate.

    Why: Frappe's standard workspace sync during migrate preserves user
    edits, which is normally what you want — but here we ship layout
    updates with new releases (number cards, charts, links to reports)
    and want users on a fresh upgrade to see them.

    We only force-refresh `is_standard=1` workspaces with no `for_user`
    set — never touch a workspace someone has customised as their own.
    """
    try:
        if frappe.db.exists("Workspace", "FlowAgent"):
            ws_info = frappe.db.get_value(
                "Workspace", "FlowAgent",
                ["for_user", "is_standard"], as_dict=True,
            ) or {}
            if not ws_info.get("for_user") and ws_info.get("is_standard"):
                frappe.delete_doc(
                    "Workspace", "FlowAgent",
                    ignore_permissions=True, force=True,
                )

        # Re-import from disk so the new layout takes effect immediately,
        # without waiting for another migrate cycle.
        import os
        from frappe.modules.import_file import import_file_by_path
        ws_path = os.path.join(
            frappe.get_app_path("flowagent"),
            "flowagent_core", "workspace", "flowagent", "flowagent.json",
        )
        if os.path.exists(ws_path):
            import_file_by_path(ws_path, force=True)
    except Exception as e:
        # Workspace refresh is best-effort; never let it break migrate.
        frappe.log_error(
            title="FlowAgent: workspace refresh skipped",
            message=f"{type(e).__name__}: {e}",
        )
