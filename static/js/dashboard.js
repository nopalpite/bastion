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

// --- Vue liste / grille ---------------------------------------------
//
// Préférence globale mémorisée en local (façon largeur du panneau SFTP
// dans terminal.js, ou noms masqués/affichés sur le plan) : une salle par
// groupe utilise son propre .machine-grid, donc la bascule s'applique à
// tous à la fois plutôt que par salle — pas de raison de vouloir un
// mélange grille/liste selon la salle affichée.
const LAYOUT_STORAGE_KEY = "bastion-dashboard-list-view";
const toggleLayoutBtn = document.getElementById("toggle-layout");

function applyLayout(listView) {
  document.querySelectorAll(".machine-grid").forEach((grid) => {
    grid.classList.toggle("list-view", listView);
  });
  toggleLayoutBtn.textContent = listView ? "Vue grille" : "Vue compacte";
}

applyLayout(localStorage.getItem(LAYOUT_STORAGE_KEY) === "1");

toggleLayoutBtn.addEventListener("click", () => {
  const firstGrid = document.querySelector(".machine-grid");
  const listView = !(firstGrid && firstGrid.classList.contains("list-view"));
  applyLayout(listView);
  localStorage.setItem(LAYOUT_STORAGE_KEY, listView ? "1" : "0");
});
