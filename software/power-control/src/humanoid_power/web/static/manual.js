"use strict";

(() => {
    const list = document.getElementById("outlet-list");
    if (!list || !window.PowerApp) return;
    const refreshButton = document.getElementById("refresh-state");
    const criticalDialog = document.getElementById("critical-dialog");
    const criticalSummary = document.getElementById("critical-summary");
    const criticalAutomationNote = document.getElementById("critical-automation-note");
    const criticalConfirm = document.getElementById("critical-confirm");
    let currentState = null;
    let pendingCommand = null;
    let timer = null;
    let viewStale = false;

    function outletById(id) {
        return currentState?.outlets.find((item) => item.outlet_id === Number(id));
    }

    function render(state) {
        currentState = state;
        list.dataset.configVersion = String(state.config_version);
        for (const outlet of state.outlets) {
            const row = list.querySelector(`[data-outlet-id="${outlet.outlet_id}"]`);
            if (!row) continue;
            row.hidden = !outlet.dashboard_visible && !outlet.forced_visible;
            row.dataset.confirmOn = String(outlet.confirm_on);
            row.dataset.confirmOff = String(outlet.confirm_off);
            row.querySelector(".outlet-name").textContent = outlet.name;
            const stateValue = row.querySelector(".state-value");
            stateValue.textContent = outlet.reported_state;
            stateValue.className = `state-value state-${outlet.reported_state.toLowerCase()}`;
            const quality = row.querySelector(".quality");
            quality.textContent = outlet.data_quality;
            quality.className = `quality quality-${outlet.data_quality.toLowerCase()}`;
            row.querySelector(".confirmed-time").textContent = outlet.confirmed_at_utc
                ? `CONTROLLER CONFIRMED · ${window.PowerApp.formatTimestamp(outlet.confirmed_at_utc)}`
                : "Never confirmed";
            row.querySelector(".forced-visible").classList.toggle("hidden", !outlet.forced_visible);
            row.querySelector(".badge-critical").classList.toggle("hidden", outlet.criticality !== "CRITICAL");
            const automation = row.querySelector(".automation-block");
            automation.classList.toggle("hidden", !outlet.schedule.active);
            if (outlet.schedule.active) {
                const next = outlet.schedule.next_event_local
                    ? `${window.PowerApp.formatTimestamp(outlet.schedule.next_event_local)} · ${outlet.schedule.next_action || "CHANGE"}`
                    : "Next event unknown";
                row.querySelector(".next-event").textContent = `Next ${next}`;
            }
            const operation = outlet.active_operation || outlet.last_operation;
            row.querySelector(".operation-status").textContent = operation
                ? `${operation.status}${operation.result_code ? ` · ${operation.result_code}` : ""}`
                : "";
            const button = row.querySelector(".outlet-action");
            const target = outlet.reported_state === "ON" ? "OFF" : "ON";
            button.dataset.target = target;
            button.textContent = outlet.active_operation
                ? outlet.active_operation.status === "QUEUED" ? "Command queued" : "Command in progress"
                : outlet.reported_state === "ON" ? "Turn off"
                : outlet.reported_state === "OFF" ? "Turn on" : "State unknown";
            button.disabled = !outlet.manual_allowed;
        }
    }

    async function refreshState(silent = true) {
        try {
            render(await window.PowerApp.requestJson("/api/v1/state"));
            if (viewStale) window.PowerApp.clearMessage();
            viewStale = false;
            if (!silent) window.PowerApp.clearMessage();
        } catch (error) {
            viewStale = true;
            window.PowerApp.showMessage(
                `VIEW STALE · ${error.message} Actions are disabled until the connection recovers.`
            );
            for (const button of list.querySelectorAll(".outlet-action")) button.disabled = true;
        }
    }

    async function sendCommand(outlet, target, confirmed) {
        const row = list.querySelector(`[data-outlet-id="${outlet.outlet_id}"]`);
        const button = row.querySelector(".outlet-action");
        button.disabled = true;
        button.textContent = "Command queued";
        window.PowerApp.clearMessage();
        try {
            const operation = await window.PowerApp.requestJson(
                `/api/v1/outlets/${outlet.outlet_id}/operations`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        target_state: target,
                        idempotency_key: window.PowerApp.newId(),
                        expected_config_version: currentState.config_version,
                        confirmed_critical_action: confirmed,
                    }),
                }
            );
            button.textContent = "Command in progress";
            const result = await window.PowerApp.waitForOperation(operation.operation_id);
            if (result.status !== "CONFIRMED") {
                window.PowerApp.showMessage(
                    result.status === "UNKNOWN"
                        ? "Command result is unknown. Check the controller state before trying again."
                        : `Command not confirmed: ${result.result_code || result.status}`
                );
            }
        } catch (error) {
            window.PowerApp.showMessage(error.message);
        } finally {
            await refreshState(true);
        }
    }

    function requestCommand(outlet, target) {
        const confirmationRequired = target === "ON" ? outlet.confirm_on : outlet.confirm_off;
        if (!confirmationRequired) {
            sendCommand(outlet, target, false);
            return;
        }
        pendingCommand = { outlet, target };
        criticalSummary.textContent = `${outlet.name} · Switch ${target}`;
        criticalAutomationNote.textContent = outlet.schedule.active
            ? "A hardware schedule remains active and may change the outlet again."
            : "This confirmation is an operational check, not a safety circuit.";
        criticalDialog.showModal();
    }

    list.addEventListener("click", (event) => {
        const button = event.target.closest(".outlet-action");
        if (!button || button.disabled) return;
        const row = button.closest(".outlet-row");
        const outlet = outletById(row.dataset.outletId);
        if (outlet) requestCommand(outlet, button.dataset.target);
    });

    criticalConfirm?.addEventListener("click", () => {
        if (pendingCommand) sendCommand(pendingCommand.outlet, pendingCommand.target, true);
        pendingCommand = null;
    });

    refreshButton?.addEventListener("click", async () => {
        refreshButton.disabled = true;
        try {
            await window.PowerApp.requestJson("/api/v1/reconcile", { method: "POST" });
            await new Promise((resolve) => window.setTimeout(resolve, 500));
            await refreshState(false);
        } catch (error) {
            window.PowerApp.showMessage(error.message);
        } finally {
            refreshButton.disabled = false;
        }
    });

    function scheduleNextPoll() {
        window.clearTimeout(timer);
        timer = window.setTimeout(async () => {
            await refreshState(true);
            scheduleNextPoll();
        }, document.hidden ? 10000 : 2000);
    }

    document.addEventListener("visibilitychange", scheduleNextPoll);
    refreshState(true).then(scheduleNextPoll);
})();
