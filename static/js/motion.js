(() => {
    "use strict";

    const root = document.documentElement;
    const body = document.body;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");

    root.classList.add("motion-capable");

    const selectAll = (selector, scope = document) =>
        Array.from(scope.querySelectorAll(selector));

    const setupScrollProgress = () => {
        const progress = document.createElement("div");
        progress.className = "experience-progress";
        progress.setAttribute("aria-hidden", "true");
        body.append(progress);

        let frameRequested = false;

        const update = () => {
            const scrollable = document.documentElement.scrollHeight - window.innerHeight;
            const ratio = scrollable > 0 ? window.scrollY / scrollable : 0;
            progress.style.transform = `scaleX(${Math.min(1, Math.max(0, ratio))})`;
            frameRequested = false;
        };

        window.addEventListener("scroll", () => {
            if (!frameRequested) {
                frameRequested = true;
                window.requestAnimationFrame(update);
            }
        }, { passive: true });

        update();
    };

    const setupSectionReveals = () => {
        const sections = selectAll(
            ".workspace-content > section, .workspace-content > .page-alert"
        );

        sections.forEach((section, index) => {
            section.dataset.motionSection = "";
            section.style.setProperty("--section-order", index);
        });

        const staggerGroups = [
            [".command-center", ".command-card"],
            [".review-journey-rail", ".journey-stage"],
            [".review-command-map", ".journey-node-card"],
            [".employee-table tbody", "tr"],
            [".team-grid", ".team-card"],
            [".cycle-grid", ".cycle-node-card"],
            [".blueprint-grid", ".blueprint-lane"],
            [".action-stream-list", ".action-stream-item"],
            [".manager-evidence-list", ".manager-evidence-card"],
            [".review-history-list", ".review-history-card"],
            [".assessment-items", ".assessment-item-card"],
            [".supervisor-evaluation-list", ".supervisor-evaluation-item"]
        ];

        staggerGroups.forEach(([groupSelector, itemSelector]) => {
            selectAll(groupSelector).forEach((group) => {
                group.dataset.motionGroup = "";
                selectAll(itemSelector, group).forEach((item, index) => {
                    item.dataset.motionItem = "";
                    item.style.setProperty("--item-order", index);
                });
            });
        });

        if (reduceMotion.matches || !("IntersectionObserver" in window)) {
            sections.forEach((section) => section.classList.add("is-visible"));
            selectAll("[data-motion-group]").forEach((group) =>
                group.classList.add("is-visible")
            );
            body.classList.add("motion-ready");
            return;
        }

        const observer = new IntersectionObserver((entries, currentObserver) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }

                entry.target.classList.add("is-visible");
                currentObserver.unobserve(entry.target);
            });
        }, {
            threshold: 0.08,
            rootMargin: "0px 0px -5% 0px"
        });

        sections.forEach((section) => observer.observe(section));
        selectAll("[data-motion-group]").forEach((group) => observer.observe(group));

        window.requestAnimationFrame(() => body.classList.add("motion-ready"));
    };

    const setupSurfaceSpotlights = () => {
        if (reduceMotion.matches || !finePointer.matches) {
            return;
        }

        const surfaces = selectAll([
            ".command-card",
            ".profile-card",
            ".team-card",
            ".cycle-node-card",
            ".assessment-item-card",
            ".manager-evidence-card",
            ".review-history-card",
            ".security-form-card",
            ".flowboard-live-card"
        ].join(","));

        surfaces.forEach((surface) => {
            surface.classList.add("motion-surface");

            const glow = document.createElement("span");
            glow.className = "motion-surface-glow";
            glow.setAttribute("aria-hidden", "true");
            surface.prepend(glow);

            let frame = null;

            surface.addEventListener("pointermove", (event) => {
                if (frame !== null) {
                    window.cancelAnimationFrame(frame);
                }

                frame = window.requestAnimationFrame(() => {
                    const bounds = surface.getBoundingClientRect();
                    glow.style.setProperty("--glow-x", `${event.clientX - bounds.left}px`);
                    glow.style.setProperty("--glow-y", `${event.clientY - bounds.top}px`);
                    frame = null;
                });
            });
        });
    };

    const setupRipples = () => {
        if (reduceMotion.matches) {
            return;
        }

        const controls = selectAll([
            ".primary-button",
            ".cycle-configure-button",
            ".schedule-cycle-button",
            ".manager-approve-button",
            ".flow-primary-link",
            ".quick-control-link",
            ".action-open-link",
            ".security-submit-button"
        ].join(","));

        controls.forEach((control) => {
            control.classList.add("motion-ripple-host");
            control.addEventListener("pointerdown", (event) => {
                const bounds = control.getBoundingClientRect();
                const diameter = Math.max(bounds.width, bounds.height) * 1.35;
                const ripple = document.createElement("span");

                ripple.className = "motion-ripple";
                ripple.style.width = `${diameter}px`;
                ripple.style.height = `${diameter}px`;
                ripple.style.left = `${event.clientX - bounds.left - diameter / 2}px`;
                ripple.style.top = `${event.clientY - bounds.top - diameter / 2}px`;

                control.append(ripple);
                ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
            });
        });
    };

    const setupCounters = () => {
        if (reduceMotion.matches || !("IntersectionObserver" in window)) {
            return;
        }

        const counters = selectAll([
            ".command-card > strong",
            ".supervisor-pulse-metrics > div > strong",
            ".employee-count strong",
            ".action-stream-counter strong"
        ].join(",")).filter((element) => /^\s*\d+\s*$/.test(element.textContent));

        const observer = new IntersectionObserver((entries, currentObserver) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }

                const element = entry.target;
                const target = Number.parseInt(element.textContent.trim(), 10);
                const start = performance.now();
                const duration = 650;

                const animate = (time) => {
                    const progress = Math.min(1, (time - start) / duration);
                    const eased = 1 - Math.pow(1 - progress, 3);
                    element.textContent = String(Math.round(target * eased));

                    if (progress < 1) {
                        window.requestAnimationFrame(animate);
                    }
                };

                window.requestAnimationFrame(animate);
                currentObserver.unobserve(element);
            });
        }, { threshold: 0.55 });

        counters.forEach((counter) => observer.observe(counter));
    };

    const setupQuickNavigation = () => {
        const input = document.getElementById("workspaceQuickSearch");
        const container = input?.closest(".search-area");

        if (!input || !container) {
            return;
        }

        const destinations = selectAll(".nav-link")
            .map((link) => ({
                label: link.querySelector(".nav-label")?.textContent.trim(),
                icon: link.querySelector(".nav-icon")?.textContent.trim(),
                href: link.href
            }))
            .filter((item) => item.label && item.href && !item.label.match(/sign out|logout/i));

        const palette = document.createElement("div");
        palette.className = "quick-navigation";
        palette.setAttribute("role", "listbox");
        palette.setAttribute("aria-label", "Workspace destinations");
        container.append(palette);

        const close = () => {
            container.classList.remove("quick-navigation-open");
            input.setAttribute("aria-expanded", "false");
        };

        const render = () => {
            const query = input.value.trim().toLowerCase();
            const matches = destinations
                .filter((item) => item.label.toLowerCase().includes(query))
                .slice(0, 7);

            palette.replaceChildren();

            const heading = document.createElement("span");
            heading.className = "quick-navigation-heading";
            heading.textContent = query ? "MATCHING WORKSPACES" : "QUICK NAVIGATION";
            palette.append(heading);

            if (matches.length === 0) {
                const empty = document.createElement("span");
                empty.className = "quick-navigation-empty";
                empty.textContent = "No matching workspace";
                palette.append(empty);
            }

            matches.forEach((item) => {
                const link = document.createElement("a");
                const icon = document.createElement("span");
                const label = document.createElement("strong");
                const hint = document.createElement("small");

                link.href = item.href;
                link.setAttribute("role", "option");
                icon.textContent = item.icon || "→";
                label.textContent = item.label;
                hint.textContent = "Open";

                link.append(icon, label, hint);
                palette.append(link);
            });

            container.classList.add("quick-navigation-open");
            input.setAttribute("aria-expanded", "true");
        };

        input.addEventListener("focus", render);
        input.addEventListener("input", render);

        input.addEventListener("keydown", (event) => {
            const links = selectAll("a", palette);

            if (event.key === "Escape") {
                close();
                input.blur();
            } else if (event.key === "ArrowDown" && links[0]) {
                event.preventDefault();
                links[0].focus();
            } else if (event.key === "Enter" && links[0]) {
                event.preventDefault();
                links[0].click();
            }
        });

        palette.addEventListener("keydown", (event) => {
            const links = selectAll("a", palette);
            const index = links.indexOf(document.activeElement);

            if (event.key === "ArrowDown") {
                event.preventDefault();
                links[(index + 1) % links.length]?.focus();
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                (index <= 0 ? input : links[index - 1]).focus();
            } else if (event.key === "Escape") {
                close();
                input.focus();
            }
        });

        document.addEventListener("pointerdown", (event) => {
            if (!container.contains(event.target)) {
                close();
            }
        });

        document.addEventListener("keydown", (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
                event.preventDefault();
                input.focus();
                input.select();
            }
        });
    };

    const setupPageTransitions = () => {
        if (reduceMotion.matches) {
            return;
        }

        const selector = [
            ".nav-link",
            ".flow-primary-link",
            ".quick-control-link",
            ".action-open-link",
            ".table-action",
            ".back-link",
            ".security-cancel-link"
        ].join(",");

        document.addEventListener("click", (event) => {
            const link = event.target.closest(selector);

            if (!link || event.defaultPrevented || event.button !== 0 ||
                event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
                link.target || link.hasAttribute("download")) {
                return;
            }

            const destination = new URL(link.href, window.location.href);

            if (destination.origin !== window.location.origin ||
                (destination.pathname === window.location.pathname && destination.hash)) {
                return;
            }

            event.preventDefault();
            body.classList.add("page-exiting");
            window.setTimeout(() => {
                window.location.href = destination.href;
            }, 145);
        });
    };

    const initialise = () => {
        setupScrollProgress();
        setupSectionReveals();
        setupSurfaceSpotlights();
        setupRipples();
        setupCounters();
        setupQuickNavigation();
        setupPageTransitions();
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialise, { once: true });
    } else {
        initialise();
    }
})();
