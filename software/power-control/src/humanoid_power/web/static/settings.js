"use strict";

(() => {
    if (!window.PowerApp) return;

    async function save(url, payload) {
        window.PowerApp.clearMessage();
        try {
            await window.PowerApp.requestJson(url, { method: "PUT", body: JSON.stringify(payload) });
            window.PowerApp.showMessage("Settings saved and confirmed.", "success");
            window.setTimeout(() => window.location.reload(), 450);
        } catch (error) {
            window.PowerApp.showMessage(error.message);
        }
    }

    const profileForm = document.getElementById("profile-form");
    profileForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        const selected = profileForm.querySelector('input[name="profile"]:checked');
        if (!selected) return;
        save("/api/v1/settings/profile", {
            profile: selected.value,
            expected_config_version: Number(profileForm.dataset.configVersion),
        });
    });

    for (const form of document.querySelectorAll(".outlet-settings-form")) {
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            const data = new FormData(form);
            const parent = form.closest(".settings-outlet-list");
            save(`/api/v1/settings/outlets/${form.dataset.outletId}`, {
                name: data.get("name"),
                description: data.get("description"),
                criticality: data.get("criticality"),
                dashboard_visible: data.has("dashboard_visible"),
                control_enabled: data.has("control_enabled"),
                confirm_on: data.has("confirm_on"),
                confirm_off: data.has("confirm_off"),
                revision: Number(form.dataset.revision),
                expected_config_version: Number(parent.dataset.configVersion),
            });
        });
    }

    const interfaceForm = document.getElementById("interface-form");
    interfaceForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        const data = new FormData(interfaceForm);
        save("/api/v1/settings/interface", {
            show_schedules_tab: data.has("show_schedules_tab"),
            show_activity_tab: data.has("show_activity_tab"),
            technical_details_default: data.has("technical_details_default"),
            expected_config_version: Number(interfaceForm.dataset.configVersion),
        });
    });
})();
