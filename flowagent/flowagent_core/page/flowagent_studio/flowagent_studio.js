// Copyright (c) 2026, FlowAgent and contributors
// For license information, please see license.txt
//
// FlowAgent Studio — visual workflow builder for Frappe.
// Hosted at /app/flowagent-studio. Enters fullscreen on load and
// restores normal Desk layout when navigating away.

// Lazily load the heavy external CSS (Tabler Icons + Geist fonts) ONLY
// when the Studio is opened. Loading these via @import in the bundled
// CSS would force every Frappe Desk page to download them, which makes
// the rest of Desk feel sluggish.
function _flowagentLoadDeps() {
    const links = [
        { id: 'fa-tabler-css', href: 'https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.7.0/dist/tabler-icons.min.css' },
        { id: 'fa-geist-css',  href: 'https://fonts.googleapis.com/css2?family=Geist:wght@400;600&family=Geist+Mono:wght@500&display=swap' },
    ];
    links.forEach(l => {
        if (document.getElementById(l.id)) return;
        const el = document.createElement('link');
        el.id = l.id;
        el.rel = 'stylesheet';
        el.href = l.href;
        document.head.appendChild(el);
    });
}

frappe.pages['flowagent-studio'].on_page_load = function (wrapper) {
    _flowagentLoadDeps();
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
// chrome AND tear down our global event listeners so the rest of Desk
// isn't paying for them on every page.
frappe.pages['flowagent-studio'].on_page_show = function () {
    _flowagentLoadDeps();
    document.body.classList.add('flowagent-fullscreen');
};

$(document).on('page-change', function () {
    if (!frappe.get_route || frappe.get_route()[0] !== 'flowagent-studio') {
        document.body.classList.remove('flowagent-fullscreen');
        if (typeof window.flowagent_studio_teardown === 'function') {
            window.flowagent_studio_teardown();
        }
    }
});
