# Copyright (c) 2026, FlowAgent and contributors
# For license information, please see license.txt
"""
Loop guard — prevents workflow runs from triggering themselves recursively.

The classic failure: a workflow listens for "On Save" of DocType X, and
its body updates a doc of type X. That update fires the same workflow on
itself → infinite loop → the queue fills with millions of jobs.

Three defenses, layered:

1. **In-process owned-doc set** (`frappe.flags.flowagent_owned_docs`):
   while a workflow is running in this process, any docs it mutates are
   recorded. The dispatcher checks this set before firing — if the doc
   being saved is currently owned by a workflow, that workflow's reaction
   to its own write is suppressed.

2. **Cross-process Redis dedupe** (`flowagent:fired:{wf}:{dt}:{name}`):
   a short-lived Redis key recording the last fire of (workflow, doctype,
   doc_name). If the same triple fires again within COOLDOWN_SECONDS,
   it's a loop — skip. This catches loops that span the user request →
   background worker → re-trigger boundary.

3. **Per-workflow rate cap** (`flowagent:ratelimit:{wf}`):
   a workflow can fire at most MAX_FIRES_PER_MINUTE times across the
   whole system in any 60s window. Hard backstop for runaway scenarios
   the other defenses miss (e.g. workflow that mutates 1000 different
   docs of the same type).

All three are best-effort: if Redis is unavailable, we degrade to
in-process only. We log all suppressions so the user can see them.
"""

from __future__ import annotations

import frappe


# Defaults; tunable via FlowAgent Settings if needed in future
COOLDOWN_SECONDS = 10        # min seconds between fires of same (wf, dt, doc)
MAX_FIRES_PER_MINUTE = 60    # per workflow, across all docs
RATE_WINDOW_SECONDS = 60


# -----------------------------------------------------------------------
# Defense 1: in-process owned-doc set + running-workflow set
# -----------------------------------------------------------------------
def mark_doc_owned(workflow_name: str, doctype: str, doc_name: str):
    """Called by the runner before it writes to a doc. Records that the
    current workflow run owns this doc, so the dispatcher skips re-fires.
    """
    if not doctype or not doc_name:
        return
    owned = getattr(frappe.local, "flowagent_owned_docs", None)
    if owned is None:
        owned = set()
        frappe.local.flowagent_owned_docs = owned
    owned.add((workflow_name, doctype, doc_name))


def mark_workflow_running(workflow_name: str):
    """Called by the runner at start of execute(). Adds this workflow
    name to the set of currently-running workflows in this process.
    The dispatcher consults this to suppress *any* re-fire of a workflow
    that's already running — the most reliable defense against the classic
    self-loop where a workflow updates its own trigger doctype.
    """
    running = getattr(frappe.local, "flowagent_running_workflows", None)
    if running is None:
        running = set()
        frappe.local.flowagent_running_workflows = running
    running.add(workflow_name)


def unmark_workflow_running(workflow_name: str):
    """Called by the runner in a finally block."""
    running = getattr(frappe.local, "flowagent_running_workflows", None)
    if running:
        running.discard(workflow_name)


def is_workflow_running(workflow_name: str) -> bool:
    """Returns True if the given workflow is already executing in this process."""
    running = getattr(frappe.local, "flowagent_running_workflows", None)
    return bool(running and workflow_name in running)


def clear_owned_docs():
    """Called by the runner at end-of-run."""
    if hasattr(frappe.local, "flowagent_owned_docs"):
        frappe.local.flowagent_owned_docs = set()


def is_doc_owned_by_running_workflow(doctype: str, doc_name: str) -> str | None:
    """Returns the workflow name that owns this doc, or None.

    The dispatcher uses this to decide whether to suppress a fire.
    """
    owned = getattr(frappe.local, "flowagent_owned_docs", None)
    if not owned:
        return None
    for wf, dt, dn in owned:
        if dt == doctype and dn == doc_name:
            return wf
    return None


# -----------------------------------------------------------------------
# Defense 2: cross-process Redis dedupe
# -----------------------------------------------------------------------
def should_suppress_recent_fire(workflow_name: str, doctype: str, doc_name: str) -> bool:
    """Return True if this (wf, dt, doc) triple fired in the last
    COOLDOWN_SECONDS. The Redis key is set immediately after the check
    passes, so callers don't need to update state separately.
    """
    if not doc_name:
        return False
    key = f"flowagent:fired:{workflow_name}:{doctype}:{doc_name}"
    try:
        cache = frappe.cache()
        recent = cache.get_value(key)
        if recent:
            return True
        # Record this fire with a TTL. Frappe's cache wrapper around Redis
        # supports `expires_in_sec` for set_value.
        cache.set_value(key, "1", expires_in_sec=COOLDOWN_SECONDS)
    except Exception:
        # Redis not available — defense-in-depth means we still have the
        # in-process guard. Don't block dispatch on a cache failure.
        pass
    return False


# -----------------------------------------------------------------------
# Defense 3: per-workflow rate cap
# -----------------------------------------------------------------------
def should_suppress_rate_limited(workflow_name: str) -> tuple[bool, int]:
    """Return (suppress, current_count). Increments the rolling counter
    in Redis. If suppress=True, the dispatcher should refuse this fire
    and log it as a rate-limit violation.

    We use a simple bucket-per-minute approach: the key includes the
    current minute, and each fire increments it. Old buckets expire
    naturally via TTL.
    """
    try:
        import time
        bucket = int(time.time() // RATE_WINDOW_SECONDS)
        key = f"flowagent:ratelimit:{workflow_name}:{bucket}"
        cache = frappe.cache()
        # Use raw redis incr if available for atomicity; fall back to
        # get/set otherwise. Frappe's cache exposes a `redis` attr.
        try:
            count = cache.redis.incr(key)
            if count == 1:
                cache.redis.expire(key, RATE_WINDOW_SECONDS * 2)
        except Exception:
            current = int(cache.get_value(key) or 0)
            count = current + 1
            cache.set_value(key, str(count), expires_in_sec=RATE_WINDOW_SECONDS * 2)
        return (count > MAX_FIRES_PER_MINUTE, count)
    except Exception:
        return (False, 0)


# -----------------------------------------------------------------------
# The combined check used by the dispatcher
# -----------------------------------------------------------------------
def should_fire(workflow_name: str, doctype: str, doc_name: str) -> tuple[bool, str]:
    """Master gate. Returns (allowed, reason_if_blocked).

    The dispatcher consults this before enqueueing. If `allowed` is False,
    the workflow is suppressed and the reason is logged.
    """
    # 1a. Same workflow already executing in this process → always suppress.
    #     This is the strongest, simplest defense against self-loops.
    #     A workflow can never trigger itself, regardless of which doc.
    if is_workflow_running(workflow_name):
        return (False, f"loop guard: {workflow_name} is already running in this process")

    # 1b. Doc owned by some workflow run? Different workflow can still chain,
    #     so we don't block on this alone — just record for logging.
    owner = is_doc_owned_by_running_workflow(doctype, doc_name)
    if owner and owner == workflow_name:
        return (False, f"loop guard: workflow is currently mutating {doctype} {doc_name}")

    # 2. Recent-fire dedupe (cross-process, catches loops across worker
    #    invocations even when defense 1 doesn't apply)
    if should_suppress_recent_fire(workflow_name, doctype, doc_name):
        return (False, f"cooldown: {workflow_name} fired on {doctype} {doc_name} within {COOLDOWN_SECONDS}s")

    # 3. Rate cap — hard backstop
    rate_limited, count = should_suppress_rate_limited(workflow_name)
    if rate_limited:
        return (False, f"rate limit: {workflow_name} fired {count} times in {RATE_WINDOW_SECONDS}s (max {MAX_FIRES_PER_MINUTE})")

    return (True, "")
