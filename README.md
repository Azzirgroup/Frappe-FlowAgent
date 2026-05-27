# FlowAgent — Visual AI Workflow Automation for Frappe

A native, drag-and-drop **n8n / Zapier-style workflow builder** for Frappe and ERPNext, with first-class AI nodes (LLM, structured extraction, classification, agentic tool use).

No external service required — workflows run inside your Frappe site, triggered by DocType events, schedules, webhooks, or manual invocation.

---

## Why

The Frappe marketplace has no native visual workflow builder. `frappe_assistant_core` exposes an MCP endpoint, but there's no drag-and-drop way for non-developers to wire **DocType event → condition → multi-step actions** without writing code or running a separate n8n server.

FlowAgent fills that gap.

---

## Features

- **Visual canvas** — drag, drop, wire. Branch nodes have Y/N output ports. Loop nodes iterate downstream.
- **27 node types** across 6 categories:
  - **Triggers**: DocType Event, Webhook, Schedule (cron), Manual
  - **AI Agents**: LLM Prompt, Extractor (structured JSON), Classifier, Sentiment, Auto Agent (tool-use loop), Vision/OCR
  - **Logic**: Condition (Y/N), Loop, Wait, Parallel
  - **Frappe Actions**: Create Doc, Update Doc, Fetch Doc, Submit Doc, Server Script (sandboxed)
  - **Integrations**: Email, WhatsApp, HTTP, Slack, Google Sheets, Razorpay
  - **Transforms**: Field Mapper, Jinja Template, Python Code (sandboxed)
- **Variable interpolation** — `{{var.path | filter}}` resolves against the run context
- **Step trace** — every node logs input, output, duration, error to `FlowAgent Workflow Run`
- **AI Build** — type "When a Sales Invoice is submitted, extract the amount with AI, and if it's > 50000 send a WhatsApp alert" → Claude returns a workflow graph that drops onto the canvas
- **Templates** — three preset workflows shipped (Invoice Approval, Lead Auto-Qualify, Daily Digest)
- **Per-workflow error policy** — Stop / Continue / Retry
- **DocType trigger index** — wildcard `doc_events` listener does an indexed lookup against a flat trigger table; no overhead for DocTypes nobody is watching
- **Webhook URLs with HMAC token auth**
- **Sandboxed Python** — `tf_code` and `frappe_script` use Frappe's `safe_exec`
- **Auto Agent with whitelisted DocType tools** — the agent can only touch DocTypes you explicitly list, and only with `can_write: true` can it modify them

---

## Install

```bash
# In your bench
bench get-app https://github.com/your-org/flowagent
bench --site <yoursite> install-app flowagent
bench --site <yoursite> migrate
bench restart    # ← REQUIRED. The doc_events wildcard hook only loads after worker restart.
```

If you skip `bench restart`, workflows will save and run manually but **will not fire automatically** when DocTypes change. Use the **Diagnose** button (stethoscope icon in the Studio toolbar) to verify the hook is loaded.

Then set the Anthropic API key:

1. Go to **FlowAgent Settings** in the Desk
2. Paste your key
3. Save

(Or set `anthropic_api_key` in `site_config.json`, or export `ANTHROPIC_API_KEY` in the bench env.)

---

## Quick start

1. Open `/app/flowagent-studio` (or click **FlowAgent** in the sidebar → Open Studio)
2. Click **Templates** → pick **Invoice Approval**
3. Edit any node (click it, configure on the right)
4. Click **Save**, then toggle **Enabled**
5. Hit **Run** for a manual sync test — you'll see each node's status change live
6. Submit a Sales Invoice in your ERPNext to see it trigger automatically

---

## AI Build

Click the **AI Build** tab in the right panel and type a description in plain English. Examples:

> When a new Lead is created, classify it with AI as hot/warm/cold, update the lead score, and notify the sales channel on Slack.

> Every day at 9am, fetch all overdue invoices, summarize them with AI, and email the digest to accounts@company.com.

> When a Purchase Invoice is submitted with grand_total > 100000, send a WhatsApp approval request to the manager.

Claude returns a workflow JSON with nodes, edges, and config; the canvas drops it in.

---

## Variable interpolation

Inside any node config field, `{{var.path}}` resolves against the run context. Anything you save under a node's `output` field becomes available downstream.

```
{{trigger.doc.name}}            — the document name
{{trigger.doc.grand_total}}     — any doc field
{{extracted.amount}}            — output of an upstream ai_extract with output="extracted"
{{category}}                    — output of ai_classify with output="category"
{{trigger.doc.grand_total * 100}}  — basic arithmetic
{{customer_name | upper}}       — filters: upper, lower, title, strip, json, length, default(...), round(N), currency
```

For anything more complex, use a `tf_code` or `tf_jinja` node.

---

## Auto Agent

The `ai_agent` node runs a ReAct-style loop:

1. Claude gets your task + a system prompt + a list of tools
2. Each tool is a thin wrapper over a Frappe DB operation (`list_documents`, `get_document`, `count_documents`, optionally `update_document`, `create_document`)
3. Claude decides which tools to call and in what order
4. Loop terminates when Claude returns a final text answer or hits `max_iters`

**Whitelist required.** The agent literally cannot touch a DocType not in `allowed_doctypes`. Writes are gated by `can_write: true`.

Example task:

> Find all overdue Sales Invoices for customer "Acme Inc" submitted in the last 30 days, total them up, and tell me the largest one.

With `allowed_doctypes: Sales Invoice, Customer` the agent will issue `list_documents` and `get_document` calls and return a summary.

---

## Webhook triggers

When you set a workflow's trigger to **Webhook**, FlowAgent generates a path. The webhook URL is:

```
POST {site}/api/method/flowagent.api.webhook.handle?path={webhook_path}&token={webhook_secret}
```

Or send the token as `X-FlowAgent-Token` header.

The full request body becomes `{{body}}` in the workflow context.

To rotate the secret: **FlowAgent Settings → Regenerate Secret**. (This invalidates all existing webhook URLs.)

---

## Scheduled workflows

Set trigger type to **Schedule** and a standard 5-field cron. The scheduler ticks every minute and fires any workflow whose previous cron tick falls in the past 60s.

```
0 9 * * *     — every day at 9am
*/15 * * * *  — every 15 minutes
0 0 1 * *     — first of every month at midnight
```

---

## Architecture

```
DocType save        ▼
  hooks.doc_events ─┐
Schedule tick       │
  scheduler_events ─┼─► triggers/*.py
Webhook POST        │       │
  api/webhook.py ───┘       ▼
                       frappe.enqueue(...)
                            │
                            ▼
                  engine.run_workflow_background
                            │
                            ▼
                       engine.Runner
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
          context     node executors    Workflow Run + Steps
                            │
                            ▼
                  engine/nodes/*.py
                  (ai, logic, frappe, integration, transform)
```

- Workflows store nodes & edges as JSON Text fields for fast canvas round-trips
- `FlowAgent Workflow Trigger Index` is a flat lookup table; the wildcard `doc_events` dispatcher does an indexed query against it (no overhead for unwatched DocTypes)
- The engine is event-driven, not topo-sorted: branch nodes only enqueue one of two children, loops re-enter the body subgraph

---

## Permissions

Two roles can use FlowAgent:

- **System Manager** — full access
- **FlowAgent Manager** — created automatically; assign to users who should build/run workflows without giving them System Manager

---

## Settings

| Setting                    | Default              | Purpose                                                          |
| -------------------------- | -------------------- | ---------------------------------------------------------------- |
| `anthropic_api_key`        | empty                | Required for AI nodes                                            |
| `default_model`            | `claude-sonnet-4-5`  | Used when a node doesn't override                                |
| `max_agent_iterations`     | 8                    | Ceiling for Auto Agent's ReAct loop                              |
| `max_steps_per_run`        | 100                  | Hard ceiling per workflow run — protects against runaway loops  |
| `default_timeout_seconds`  | 30                   | Per-node timeout (not yet enforced — placeholder for v0.2)       |
| `run_retention_days`       | 30                   | Workflow Run history is purged after N days (0 = keep forever)   |
| `webhook_secret`           | auto-generated       | Shared secret for webhook URLs                                   |

Per-DocType-event credentials (WhatsApp, Slack, Razorpay, Google Sheets) read from `site_config.json`:

```json
{
  "whatsapp_phone_id": "...",
  "whatsapp_access_token": "...",
  "slack_webhook_url": "https://hooks.slack.com/...",
  "razorpay_key_id": "...",
  "razorpay_key_secret": "...",
  "google_sa_json": { ... full service account JSON ... }
}
```

---

## Extending

To add a new node type:

1. Create or edit a file in `flowagent/engine/nodes/`
2. Register an executor:
   ```python
   from . import BaseExecutor, node

   @node("my_new_node")
   class MyExecutor(BaseExecutor):
       def run(self, *, node, cfg, context, runner):
           return {"hello": cfg.get("name")}
   ```
3. Add a definition in `flowagent/public/js/flowagent.js` under `NODE_DEFS`:
   ```js
   my_new_node: {
       label: 'My Node', icon: 'ti-star', color: '#...', iconColor: '#...',
       category: 'integration',
       fields: [
           { k: 'name', l: 'Name', t: 'text', v: 'default' },
       ],
   },
   ```
4. Add the type string to the sidebar group in `SIDEBAR_GROUPS`
5. Add it to `VALID_NODE_TYPES` in `flowagent/api/ai_build.py` so the AI Build feature can generate it
6. `bench build` and reload

---

## Status

`v0.1.0` — functional first release. Known gaps for v0.2:

- Per-node timeouts (currently global only)
- Async parallel fan-out (parallel node currently relies on enqueue depth)
- Workflow versioning & rollback
- Visual diff / undo on the canvas
- Test runs with mock payloads

---

## License

MIT
