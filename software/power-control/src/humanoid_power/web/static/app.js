"use strict";

(() => {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const globalMessage = document.getElementById("global-message");

    function showMessage(message, kind = "error") {
        if (!globalMessage) return;
        globalMessage.textContent = message;
        globalMessage.className = `alert alert-${kind}`;
        globalMessage.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    function clearMessage() {
        if (!globalMessage) return;
        globalMessage.textContent = "";
        globalMessage.className = "alert hidden";
    }

    async function requestJson(url, options = {}) {
        const headers = new Headers(options.headers || {});
        headers.set("Accept", "application/json");
        if (options.body) headers.set("Content-Type", "application/json");
        if (!/^(GET|HEAD)$/i.test(options.method || "GET")) {
            headers.set("X-CSRFToken", csrfToken);
        }
        const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
            if (response.redirected || response.status === 401) {
                window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
                throw new Error("Authentication required.");
            }
            throw new Error(`Unexpected server response (${response.status}).`);
        }
        const payload = await response.json();
        if (!response.ok) {
            const error = new Error(payload.error?.message || `Request failed (${response.status}).`);
            error.code = payload.error?.code || "REQUEST_FAILED";
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function newId() {
        if (crypto.randomUUID) return crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
            const random = Math.random() * 16 | 0;
            const value = character === "x" ? random : (random & 3) | 8;
            return value.toString(16);
        });
    }

    function formatTimestamp(value) {
        if (!value) return "Never confirmed";
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return "Time unavailable";
        return parsed.toLocaleString([], {
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit", second: "2-digit"
        });
    }

    async function waitForOperation(operationId, timeoutMilliseconds = 10000) {
        const deadline = Date.now() + timeoutMilliseconds;
        while (Date.now() < deadline) {
            const operation = await requestJson(`/api/v1/operations/${operationId}`);
            if (!["QUEUED", "RUNNING"].includes(operation.status)) return operation;
            await new Promise((resolve) => window.setTimeout(resolve, 500));
        }
        return { operation_id: operationId, status: "UNKNOWN", result_code: "CLIENT_WAIT_TIMEOUT" };
    }

    window.PowerApp = {
        requestJson,
        showMessage,
        clearMessage,
        newId,
        formatTimestamp,
        waitForOperation,
    };
})();
