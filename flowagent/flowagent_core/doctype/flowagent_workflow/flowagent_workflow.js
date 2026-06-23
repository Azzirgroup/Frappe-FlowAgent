// Copyright (c) 2026, FlowAgent and contributors
// For license information, please see license.txt

frappe.ui.form.on('FlowAgent Workflow', {
    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button('Open in Studio', () => {
                frappe.set_route('flowagent-studio');
                // Pass the workflow to load via session storage; the Studio
                // looks at this on init.
                try {
                    sessionStorage.setItem('flowagent.openWorkflow', frm.doc.name);
                } catch (_) {}
            }).addClass('btn-primary');

            frm.add_custom_button('Run Now', () => {
                frappe.prompt(
                    {
                        fieldname: 'payload', fieldtype: 'Code',
                        label: 'Payload (JSON, optional)', default: '{}',
                    },
                    (vals) => {
                        frappe.call({
                            method: 'flowagent.api.studio.run_workflow_now',
                            args: { name: frm.doc.name, payload: vals.payload || '{}', sync: 0 },
                            callback() {
                                frappe.show_alert({
                                    message: 'Run queued',
                                    indicator: 'green',
                                });
                            },
                        });
                    },
                    'Run workflow',
                    'Queue run'
                );
            });
        }

        if (frm.doc.trigger_type === 'Webhook' && frm.doc.webhook_path) {
            frm.dashboard.add_indicator(
                `Webhook URL: <code style="font-size:11px">${location.origin}/api/method/flowagent.api.webhook.handle?path=${frm.doc.webhook_path}&token=&lt;SECRET&gt;</code>`,
                'blue'
            );
        }
    },
});
