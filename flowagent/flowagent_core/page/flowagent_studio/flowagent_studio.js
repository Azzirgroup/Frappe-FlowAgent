// Copyright (c) 2026, FlowAgent and contributors
// For license information, please see license.txt
//
// FlowAgent Studio — visual workflow builder for Frappe.
// Hosted at /app/flowagent-studio. Enters fullscreen on load and
// restores normal Desk layout when navigating away.

frappe.pages['flowagent-studio'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'FlowAgent Studio',
        single_column: true,
    });

    // Mount the Studio UI
    const $body = $(wrapper).find('.layout-main-section');
    $body.empty();
    $body.html(window.flowagent_studio_html());
    window.flowagent_studio_init(page, wrapper);

    // Enter fullscreen — hide Desk chrome, give Studio the full viewport
    document.body.classList.add('flowagent-fullscreen');
};

// Frappe fires page-change when the user navigates away. Restore Desk
// chrome so other pages render normally.
frappe.pages['flowagent-studio'].on_page_show = function () {
    document.body.classList.add('flowagent-fullscreen');
};

$(document).on('page-change', function () {
    if (!frappe.get_route || frappe.get_route()[0] !== 'flowagent-studio') {
        document.body.classList.remove('flowagent-fullscreen');
    }
});
