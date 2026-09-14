// Popup d'actions rapides (SSH / VNC / Reboot / Shutdown) réutilisé sur
// le dashboard et sur la page plan. Nécessite le partial
// templates/_action_modal.html inclus dans la page.
const actionModal = document.getElementById("action-modal");
const actionCredsForm = document.getElementById("modal-creds-form");
const actionModalMessage = document.getElementById("modal-message");
let pendingAction = null;

function openActionModal(machineId, name) {
  document.getElementById("modal-machine-name").textContent = name;
  document.getElementById("modal-ssh").href = `/terminal/${machineId}`;
  document.getElementById("modal-vnc").href = `/vnc/${machineId}`;
  actionModal.dataset.machineId = machineId;
  actionModal.dataset.machineName = name;
  actionCredsForm.style.display = "none";
  actionModalMessage.textContent = "";
  actionModal.style.display = "flex";
}

document.getElementById("modal-close").addEventListener("click", () => {
  actionModal.style.display = "none";
});

// Sur le dashboard (tabs.js chargé, voir templates/dashboard.html),
// ouvre un onglet de session plutôt que de naviguer, pour ne pas perdre
// les onglets déjà ouverts. Ce popup est aussi utilisé sur la page plan
// (/map/<salle>, pas d'onglets là-bas) : sans tabs.js chargé,
// window.openSessionTab n'existe pas et on garde la navigation normale
// via le href déjà posé par openActionModal ci-dessus.
["modal-ssh", "modal-vnc"].forEach((id) => {
  document.getElementById(id).addEventListener("click", (e) => {
    if (typeof window.openSessionTab !== "function") return;
    e.preventDefault();
    const protocol = id === "modal-vnc" ? "vnc" : "ssh";
    openSessionTab(actionModal.dataset.machineId, actionModal.dataset.machineName, protocol);
    actionModal.style.display = "none";
  });
});

function requestAction(action) {
  pendingAction = action;
  actionModalMessage.textContent = "";
  sendAction(actionModal.dataset.machineId, action, null, null);
}

document.getElementById("modal-reboot").addEventListener("click", () => requestAction("reboot"));
document.getElementById("modal-shutdown").addEventListener("click", () => requestAction("shutdown"));

document.getElementById("modal-confirm-action").addEventListener("click", () => {
  const username = document.getElementById("modal-username").value;
  const password = document.getElementById("modal-password").value;
  sendAction(actionModal.dataset.machineId, pendingAction, username, password);
});

function sendAction(machineId, action, username, password) {
  fetch(`/api/machines/${machineId}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, username, password }),
  })
    .then((r) => r.json())
    .then((res) => {
      if (res.ok) {
        actionModalMessage.textContent = `Action "${action}" envoyée avec succès.`;
        actionCredsForm.style.display = "none";
      } else if (res.needs_credentials) {
        // vraiment un problème d'identifiants (aucun mémorisé, ou mot de
        // passe refusé): on (ré)affiche le formulaire
        actionCredsForm.style.display = "block";
        actionModalMessage.textContent = res.error;
      } else {
        // autre erreur (hôte injoignable, clé changée, action non
        // supportée...): inutile de redemander un mot de passe
        actionModalMessage.textContent = "Erreur: " + res.error;
      }
    })
    .catch(() => {
      actionModalMessage.textContent = "Erreur réseau lors de l'envoi de l'action.";
    });
}
