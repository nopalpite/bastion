// Onglets de sessions SSH/VNC sur le dashboard (voir templates/dashboard.html).
//
// Chaque onglet = une <iframe> vers /terminal/<id> ou /vnc/<id> (avec
// ?embedded=1 pour masquer leur lien "Retour", inutile ici). Ces deux
// pages n'utilisent déjà aucun chrome partagé (base.html: elles
// définissent {% block body %}, pas {% block content %}), et leurs
// connexions (Socket.IO pour SSH, WebSocket brut pour VNC) sont déjà
// scopées par connexion côté serveur (voir ssh_ws.py/vnc_tls_bridge.py)
// -- changer d'onglet ne touche donc jamais iframe.src (la session
// sous-jacente resterait sinon coupée/relancée), et fermer un onglet se
// contente de retirer son iframe du DOM: le navigateur ferme la
// connexion sous-jacente tout seul, ce que le serveur détecte déjà
// (ssh_ws.py: handle_disconnect) sans rien de plus à faire ici.
const tabStrip = document.getElementById("tab-strip");
const gridView = document.getElementById("grid-view");
const sessionView = document.getElementById("session-view");
const tabGridBtn = document.getElementById("tab-grid");

let tabs = []; // [{ id, machineId, machineName, protocol, tabEl, iframeEl }]
let nextTabId = 1;

function openSessionTab(machineId, machineName, protocol) {
  const id = nextTabId++;
  const path = protocol === "vnc" ? "vnc" : "terminal";

  const tabEl = document.createElement("button");
  tabEl.type = "button";
  tabEl.className = "tab-strip-item";
  tabEl.dataset.tabId = id;
  tabEl.innerHTML =
    `<span>${protocol.toUpperCase()} · ${machineName}</span>` +
    `<span class="tab-strip-close" data-close-tab="${id}">✕</span>`;
  tabEl.addEventListener("click", (e) => {
    if (e.target.closest("[data-close-tab]")) return; // géré séparément ci-dessous
    activateTab(id);
  });
  tabEl.querySelector("[data-close-tab]").addEventListener("click", (e) => {
    e.stopPropagation();
    closeTab(id);
  });
  tabStrip.appendChild(tabEl);

  const iframeEl = document.createElement("iframe");
  iframeEl.className = "session-frame";
  iframeEl.dataset.tabId = id;
  iframeEl.src = `/${path}/${machineId}?embedded=1`;
  sessionView.appendChild(iframeEl);

  tabs.push({ id, machineId, machineName, protocol, tabEl, iframeEl });

  tabStrip.hidden = false;
  document.body.classList.add("session-active");

  activateTab(id);
}

function activateTab(tabId) {
  tabGridBtn.classList.toggle("active", tabId === "__grid__");
  gridView.hidden = tabId !== "__grid__";
  sessionView.hidden = tabId === "__grid__";

  tabs.forEach((t) => {
    const active = t.id === tabId;
    t.tabEl.classList.toggle("active", active);
    t.iframeEl.classList.toggle("active", active);
  });
}

function closeTab(tabId) {
  const idx = tabs.findIndex((t) => t.id === tabId);
  if (idx === -1) return;
  const [closed] = tabs.splice(idx, 1);
  const wasActive = closed.tabEl.classList.contains("active");
  closed.tabEl.remove();
  closed.iframeEl.remove(); // ferme la connexion sous-jacente (voir en-tête du fichier)

  if (!wasActive) return;

  if (tabs.length > 0) {
    activateTab(tabs[Math.max(0, idx - 1)].id);
    return;
  }

  activateTab("__grid__");
  tabStrip.hidden = true;
  sessionView.hidden = true;
  document.body.classList.remove("session-active");
}

tabGridBtn.addEventListener("click", () => activateTab("__grid__"));
