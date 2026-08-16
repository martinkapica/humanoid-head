"use strict";

(() => {
    const form = document.getElementById("schedule-form");
    if (!form || !window.PowerApp) return;
    const eventList = document.getElementById("event-list");
    const addButton = document.getElementById("add-event");
    const template = document.getElementById("event-row-template");
    const repeatMode = document.getElementById("repeat-mode");
    const loopGroup = document.getElementById("custom-loop-group");
    const loopMinutes = document.getElementById("loop-minutes");
    const deleteButton = document.getElementById("delete-schedule");
    const dialog = document.getElementById("schedule-dialog");
    const dialogTitle = document.getElementById("schedule-dialog-title");
    const dialogSummary = document.getElementById("schedule-dialog-summary");
    const dialogConfirm = document.getElementById("schedule-dialog-confirm");
    let pendingAction = null;

    function rows() {
        return [...eventList.querySelectorAll(".event-row")];
    }

    function updateRows() {
        const currentRows = rows();
        currentRows.forEach((row) => {
            const remove = row.querySelector(".event-remove");
            remove.disabled = form.dataset.editable !== "true" || currentRows.length <= 1;
        });
        addButton.disabled = form.dataset.editable !== "true" || currentRows.length >= 16;
    }

    eventList.addEventListener("click", (event) => {
        const remove = event.target.closest(".event-remove");
        if (!remove || remove.disabled || rows().length <= 1) return;
        remove.closest(".event-row").remove();
        updateRows();
    });

    addButton?.addEventListener("click", () => {
        if (rows().length >= 16) return;
        eventList.appendChild(template.content.cloneNode(true));
        updateRows();
    });

    repeatMode?.addEventListener("change", () => {
        loopGroup.classList.toggle("hidden", repeatMode.value !== "CUSTOM");
    });

    function collectPayload() {
        const events = rows().map((row) => ({
            local_date: row.querySelector(".event-date").value,
            local_time: row.querySelector(".event-time").value,
            action: row.querySelector(".event-action").value,
        }));
        if (events.some((event) => !event.local_date || !event.local_time)) {
            throw new Error("Complete every event date and time.");
        }
        return {
            events,
            repeat_mode: repeatMode.value,
            loop_minutes: repeatMode.value === "CUSTOM" ? Number(loopMinutes.value) : 0,
            idempotency_key: window.PowerApp.newId(),
            expected_config_version: Number(form.dataset.configVersion),
            expected_schedule_hash: form.dataset.scheduleHash,
        };
    }

    async function performSave(payload) {
        try {
            const operation = await window.PowerApp.requestJson(
                `/api/v1/outlets/${form.dataset.outletId}/schedule`,
                { method: "PUT", body: JSON.stringify(payload) }
            );
            const result = await window.PowerApp.waitForOperation(operation.operation_id, 12000);
            if (result.status === "CONFIRMED") {
                window.location.reload();
                return;
            }
            window.PowerApp.showMessage(
                result.status === "UNKNOWN"
                    ? "Schedule result is unknown. Reload and verify the controller schedule."
                    : `Schedule not confirmed: ${result.result_code || result.status}`
            );
        } catch (error) {
            window.PowerApp.showMessage(error.message);
        }
    }

    async function performDelete() {
        try {
            const operation = await window.PowerApp.requestJson(
                `/api/v1/outlets/${form.dataset.outletId}/schedule`,
                {
                    method: "DELETE",
                    body: JSON.stringify({
                        idempotency_key: window.PowerApp.newId(),
                        expected_config_version: Number(form.dataset.configVersion),
                        expected_schedule_hash: form.dataset.scheduleHash,
                    }),
                }
            );
            const result = await window.PowerApp.waitForOperation(operation.operation_id, 12000);
            if (result.status === "CONFIRMED") {
                window.location.assign("/schedules");
                return;
            }
            window.PowerApp.showMessage(
                result.status === "UNKNOWN"
                    ? "Delete result is unknown. Reload and verify the controller schedule."
                    : `Schedule deletion not confirmed: ${result.result_code || result.status}`
            );
        } catch (error) {
            window.PowerApp.showMessage(error.message);
        }
    }

    function review(title, summary, action) {
        pendingAction = action;
        dialogTitle.textContent = title;
        dialogSummary.textContent = summary;
        dialog.showModal();
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        try {
            const payload = collectPayload();
            review(
                "Confirm hardware schedule",
                `${payload.events.length} event(s) · ${payload.repeat_mode}. The controller schedule will be replaced.`,
                () => performSave(payload)
            );
        } catch (error) {
            window.PowerApp.showMessage(error.message);
        }
    });

    deleteButton?.addEventListener("click", () => {
        review(
            "Delete hardware schedule",
            `Remove all controller events for outlet ${form.dataset.outletId}. The automation will stop only after controller readback confirms deletion.`,
            performDelete
        );
    });

    dialogConfirm?.addEventListener("click", () => {
        const action = pendingAction;
        pendingAction = null;
        if (action) action();
    });

    updateRows();
})();
