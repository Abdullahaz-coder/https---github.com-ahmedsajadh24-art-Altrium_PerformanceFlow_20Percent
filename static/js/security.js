(function () {
    "use strict";

    const meta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = meta ? meta.content : "";

    if (!csrfToken) {
        return;
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("form").forEach(function (form) {
            const method = (form.method || "GET").toUpperCase();

            if (method === "GET" || form.querySelector('[name="csrf_token"]')) {
                return;
            }

            const input = document.createElement("input");
            input.type = "hidden";
            input.name = "csrf_token";
            input.value = csrfToken;
            form.appendChild(input);
        });
    });

    const originalFetch = window.fetch.bind(window);

    window.fetch = function (input, options) {
        const requestOptions = options ? Object.assign({}, options) : {};
        const method = (
            requestOptions.method
            || (input instanceof Request ? input.method : "GET")
        ).toUpperCase();

        if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
            const target = input instanceof Request ? input.url : String(input);
            const url = new URL(target, window.location.href);

            if (url.origin === window.location.origin) {
                const headers = new Headers(
                    requestOptions.headers
                    || (input instanceof Request ? input.headers : undefined)
                );
                headers.set("X-CSRF-Token", csrfToken);
                requestOptions.headers = headers;
            }
        }

        return originalFetch(input, requestOptions);
    };
})();
