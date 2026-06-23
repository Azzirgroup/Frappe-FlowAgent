// Copyright (c) 2026, FlowAgent and contributors
// For license information, please see license.txt

frappe.ui.form.on('FlowAgent Settings', {
    regenerate_webhook_secret(frm) {
        frappe.confirm(
            'Regenerating the webhook secret will invalidate every existing webhook URL until you copy the new secret. Continue?',
            () => {
                frappe.call({
                    method: 'flowagent.flowagent_core.doctype.flowagent_settings.flowagent_settings.regenerate_webhook_secret',
                    callback(r) {
                        if (r.message) {
                            frm.reload_doc();
                            frappe.show_alert({
                                message: 'New webhook secret generated',
                                indicator: 'green',
                            });
                        }
                    },
                });
            }
        );
    },
});
