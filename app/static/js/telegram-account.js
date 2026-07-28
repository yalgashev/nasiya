(() => {
    "use strict";

    document.addEventListener("htmx:afterRequest", (event) => {
        const form = event.detail.elt;
        if (
            !(form instanceof HTMLFormElement) ||
            !form.matches("[data-clear-password-after-request]")
        ) {
            return;
        }

        for (const input of form.querySelectorAll('input[type="password"]')) {
            input.value = "";
        }
    });
})();
