(() => {
    'use strict';
    const initialise = () => {
        const panels = Array.from(document.querySelectorAll('.employee-drawer, .submission-gate, .manager-confirm-dialog, .signal-center'));
        const controls = 'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
        const focusable = panel => Array.from(panel.querySelectorAll(controls)).filter(node => node.getClientRects().length && !node.closest('[hidden]'));
        let activePanel = null;
        let opener = null;
        document.addEventListener('click', event => {
            if (!activePanel) opener = event.target.closest('button, a');
        }, true);
        panels.forEach(panel => {
            const heading = panel.querySelector('h2');
            panel.setAttribute('role', 'dialog');
            panel.setAttribute('aria-modal', 'true');
            if (heading) {
                heading.id ||= `${panel.id}-heading`;
                panel.setAttribute('aria-labelledby', heading.id);
            }
            const sync = () => {
                const open = panel.classList.contains('open') || panel.classList.contains('visible');
                panel.inert = !open;
                panel.setAttribute('aria-hidden', String(!open));
                if (open) {
                    activePanel = panel;
                    if (!panel.contains(document.activeElement)) focusable(panel)[0]?.focus();
                } else if (activePanel === panel) {
                    activePanel = null;
                    opener?.focus();
                }
            };
            new MutationObserver(sync).observe(panel, {attributes: true, attributeFilter: ['class']});
            sync();
        });
        document.addEventListener('keydown', event => {
            if (!activePanel || event.key !== 'Tab') return;
            const items = focusable(activePanel);
            const first = items[0], last = items[items.length - 1];
            if (!first) return;
            if (event.shiftKey && (document.activeElement === first || !activePanel.contains(document.activeElement))) {
                event.preventDefault(); last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !activePanel.contains(document.activeElement))) {
                event.preventDefault(); first.focus();
            }
        });
        document.querySelectorAll('.nav-bottom .nav-link').forEach(link => {
            link.setAttribute('aria-label', link.querySelector('.nav-label')?.textContent.trim() || 'Account');
            link.title = link.getAttribute('aria-label');
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialise, {once: true});
    else initialise();
    window.addEventListener('pageshow', () => document.body.classList.remove('page-exiting'));
})();
