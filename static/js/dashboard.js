const socket = io();

socket.on("status_update", (results) => {
  Object.entries(results).forEach(([machineId, info]) => {
    const card = document.querySelector(`.machine-card[data-id="${machineId}"]`);
    if (!card) return;

    const dot = card.querySelector('[data-role="dot"]');
    dot.classList.remove("up", "down", "unknown");
    dot.classList.add(info.status);

    const latency = card.querySelector('[data-role="latency"]');
    latency.textContent = info.latency_ms ? ` · ${info.latency_ms} ms` : "";

    const services = info.services || {};
    card.querySelectorAll("[data-svc]").forEach((badge) => {
      const svc = badge.dataset.svc;
      if (!(svc in services)) return;
      badge.classList.toggle("ok", !!services[svc]);
      badge.classList.toggle("ko", !services[svc]);
    });
  });
});

// Ouvre le popup d'actions rapides (défini dans actions.js) au clic sur
// le bouton "Actions" d'une carte machine.
document.querySelectorAll("[data-action-id]").forEach((btn) => {
  btn.addEventListener("click", () => {
    openActionModal(btn.dataset.actionId, btn.dataset.actionName);
  });
});
