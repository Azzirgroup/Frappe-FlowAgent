# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt

from . import __version__ as app_version  # noqa: F401

app_name = "flowagent"
app_title = "FlowAgent"
app_publisher = "FlowAgent"
app_description = "Visual AI Workflow Automation for Frappe — drag-and-drop builder with AI agents, DocType triggers, and multi-step orchestration"
app_email = "hello@flowagent.dev"
app_license = "MIT"
# Newer bench versions parse required_apps with NPM-style semver ranges.
# Format: "<app_name>@<range>", e.g. "frappe@>=15.0.0".
required_apps = ["frappe@>=15.0.0"]

# Includes
# ----------------------------------------------------------------------
# Bundled assets for the Studio page and Desk integrations.
app_include_css = "/assets/flowagent/css/flowagent.css"
app_include_js = "/assets/flowagent/js/flowagent.js"

# Installation
# ----------------------------------------------------------------------
after_install = "flowagent.install.after_install"
after_migrate = "flowagent.install.after_migrate"

# DocType Events
# ----------------------------------------------------------------------
# The wildcard listener catches every DocType save/submit/cancel and
# dispatches to any FlowAgent Workflow that has a matching trigger
# binding. The dispatcher itself is cheap — it does an indexed lookup
# against Workflow Trigger Index and bails out fast if nothing matches.
doc_events = {
    "*": {
        "after_insert": "flowagent.triggers.doctype_dispatcher.on_event",
        "on_update": "flowagent.triggers.doctype_dispatcher.on_event",
        "on_submit": "flowagent.triggers.doctype_dispatcher.on_event",
        "on_cancel": "flowagent.triggers.doctype_dispatcher.on_event",
        "on_trash": "flowagent.triggers.doctype_dispatcher.on_event",
        "on_change": "flowagent.triggers.doctype_dispatcher.on_event",
    }
}

# Scheduled Tasks
# ----------------------------------------------------------------------
scheduler_events = {
    "cron": {
        # Every minute — the scheduler tick that evaluates cron triggers.
        "* * * * *": [
            "flowagent.triggers.schedule_dispatcher.tick"
        ]
    },
    "hourly": [
        # Housekeeping: trim Workflow Run history older than retention
        "flowagent.tasks.cleanup_old_runs"
    ]
}

# Whitelisted methods exposed under /api/method/<dotted.path>
# (Frappe whitelists are declared on the method itself; this is just
# documentation of the public surface.)
#
# flowagent.api.studio.load_workflow
# flowagent.api.studio.save_workflow
# flowagent.api.studio.list_workflows
# flowagent.api.studio.delete_workflow
# flowagent.api.studio.run_workflow_now
# flowagent.api.studio.get_run
# flowagent.api.studio.recent_runs
# flowagent.api.ai_build.build_from_prompt
# flowagent.api.webhook.handle  (with token)

# Website route rules: the FlowAgent Studio page lives in the Desk,
# not the website, so no rules here.

# Fixtures: ship a few example workflows on install
fixtures = []
