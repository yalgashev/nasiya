(() => {
    "use strict";

    const externalLinkSelector = "a[data-telegram-external-link]";
    const armedExternalLinks = new WeakSet();

    const findExternalLink = (event) => {
        if (!(event.target instanceof Element)) {
            return null;
        }
        return event.target.closest(externalLinkSelector);
    };

    const armExternalLink = (link, event) => {
        if (!event.isTrusted) {
            return;
        }
        const externalUrl = link.dataset.telegramExternalLink;
        if (!externalUrl || !externalUrl.startsWith("https://t.me/")) {
            return;
        }
        link.setAttribute("href", externalUrl);
        armedExternalLinks.add(link);
    };

    document.addEventListener(
        "pointerdown",
        (event) => {
            const link = findExternalLink(event);
            if (link) {
                armExternalLink(link, event);
            }
        },
        true,
    );

    document.addEventListener(
        "keydown",
        (event) => {
            if (event.key !== "Enter") {
                return;
            }
            const link = findExternalLink(event);
            if (link) {
                armExternalLink(link, event);
            }
        },
        true,
    );

    document.addEventListener(
        "click",
        (event) => {
            const link = findExternalLink(event);
            if (!link) {
                return;
            }
            if (
                !armedExternalLinks.has(link) &&
                event.isTrusted &&
                event.detail === 0 &&
                document.activeElement === link
            ) {
                armExternalLink(link, event);
            }
            if (!armedExternalLinks.has(link)) {
                event.preventDefault();
            }
        },
        true,
    );

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
