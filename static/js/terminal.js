const socket = io();
let term = null;
let fitAddon = null;

// Extrait le chemin d'une charge utile OSC 7 ("file://hostname/chemin").
// Convention standard utilisée par de nombreux terminaux (iTerm2, VS
// Code, gnome-terminal...) pour signaler le répertoire courant du shell.
function parseOsc7(payload) {
  const match = payload.match(/^file:\/\/[^/]*(\/.*)$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch (e) {
    return match[1];
  }
}

// Heuristique "zéro config" façon MobaXterm: lit le texte déjà affiché
// de la ligne courante du terminal (xterm.js le fournit sans les codes
// ANSI de couleur, donc pas besoin de les filtrer nous-mêmes) et essaie
// d'y reconnaître un prompt shell classique contenant le répertoire
// courant. Moins fiable qu'OSC 7 (casse avec un prompt très personnalisé,
// ou un chemin contenant des espaces), mais fonctionne sans rien
// configurer côté machine cible pour les prompts par défaut (Debian/
// Ubuntu, RHEL/CentOS...).
function scrapePromptPath(term) {
  const buf = term.buffer.active;
  const line = buf.getLine(buf.baseY + buf.cursorY);
  if (!line) return null;
  const text = line.translateToString(true).trim();

  // "user@host:/chemin$ " ou "user@host:/chemin# " (défaut Debian/Ubuntu)
  let match = text.match(/^\S+@\S+:(\S+)\s*[$#]\s*$/);
  // "[user@host chemin]$ " (RHEL/CentOS/Fedora, si personnalisé pour
  // afficher le chemin complet plutôt que le nom de dossier seul)
  if (!match) match = text.match(/^\[\S+@\S+ (\S+)\]\s*[$#]\s*$/);
  if (!match) return null;

  let path = match[1];
  // Le prompt RHEL/CentOS par défaut n'affiche que le NOM du dossier
  // courant (pas le chemin complet) — impossible à résoudre en chemin
  // absolu fiable à partir de ça seul, donc on l'ignore plutôt que de
  // deviner un chemin potentiellement faux.
  if (path !== "~" && !path.startsWith("~/") && !path.startsWith("/")) {
    return null;
  }

  if (path === "~") {
    path = window.fbHomePath || null;
  } else if (path.startsWith("~/") && window.fbHomePath) {
    path = window.fbHomePath.replace(/\/+$/, "") + "/" + path.slice(2);
  }
  return path;
}

function reportCwd(path) {
  if (path) {
    // Mémorisé en permanence, même si le suivi est désactivé — permet
    // à sftp.js de sauter immédiatement à la bonne position dès
    // l'activation du bouton "Suivre", sans attendre le prochain
    // changement de répertoire dans le terminal.
    window.fbLastKnownCwd = path;
    window.dispatchEvent(new CustomEvent("terminal-cwd", { detail: { path } }));
  }
}

function openTerminal() {
  document.getElementById("connect-form").style.display = "none";
  document.getElementById("session-layout").style.display = "flex";
  const termEl = document.getElementById("terminal");

  term = new Terminal({
    theme: {
      background: "#0d1117",
      foreground: "#d7dee5",
      cursor: "#3ddcd6",
    },
    fontFamily: "IBM Plex Mono, monospace",
    fontSize: 14,
  });
  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(termEl);
  fitAddon.fit();

  term.onData((data) => socket.emit("ssh_input", { data }));

  // Le shell distant doit être configuré pour émettre cette séquence à
  // chaque prompt (voir README, section "Suivi du répertoire courant")
  // pour que le bouton "Suivre" de la colonne fichiers fonctionne.
  term.parser.registerOscHandler(7, (data) => {
    reportCwd(parseOsc7(data));
    return true;
  });

  window.addEventListener("resize", () => {
    fitAddon.fit();
    socket.emit("ssh_resize", { cols: term.cols, rows: term.rows });
  });
}

document.getElementById("connect-btn").addEventListener("click", () => {
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  document.getElementById("connect-error").textContent = "";
  socket.emit("ssh_connect", { machine_id: machineId, username, password });
});

// Si des identifiants sont mémorisés côté serveur pour cette machine, on
// tente une connexion automatique sans montrer le formulaire.
if (hasStoredCreds) {
  socket.emit("ssh_connect", { machine_id: machineId });
}

socket.on("ssh_ready", () => {
  openTerminal();
  socket.emit("ssh_resize", { cols: 80, rows: 24 });
});

socket.on("ssh_output", (msg) => {
  if (!term) return;
  term.write(msg.data, () => {
    reportCwd(scrapePromptPath(term));
  });
});

socket.on("ssh_error", (msg) => {
  document.getElementById("connect-error").textContent = msg.message;
  document.getElementById("connect-form").style.display = "block";
});

socket.on("ssh_key_mismatch", (msg) => {
  document.getElementById("connect-form").style.display = "none";
  document.getElementById("session-layout").style.display = "none";
  const banner = document.getElementById("key-mismatch-banner");
  document.getElementById("key-mismatch-message").textContent = msg.message;
  document.getElementById("key-mismatch-type").textContent = msg.key_type;
  document.getElementById("key-mismatch-fingerprint").textContent = msg.fingerprint;
  banner.style.display = "block";
});

document.getElementById("trust-new-key-btn").addEventListener("click", () => {
  socket.emit("ssh_trust_new_key", {});
  document.getElementById("key-mismatch-banner").style.display = "none";
});

document.getElementById("cancel-key-btn").addEventListener("click", () => {
  document.getElementById("key-mismatch-banner").style.display = "none";
  document.getElementById("connect-form").style.display = "block";
});

socket.on("ssh_closed", () => {
  if (term) term.write("\r\n\x1b[31m[connexion fermée]\x1b[0m\r\n");
});

// --- Redimensionnement de la colonne fichiers ---------------------------

(function setupResizeHandle() {
  const handle = document.getElementById("resize-handle");
  const fileBrowser = document.getElementById("file-browser");
  const layout = document.getElementById("session-layout");
  if (!handle || !fileBrowser || !layout) return;

  const STORAGE_KEY = "bastion-filebrowser-width";
  const MIN_WIDTH = 180;
  const MAX_WIDTH = 600;

  const savedWidth = localStorage.getItem(STORAGE_KEY);
  if (savedWidth) {
    fileBrowser.style.width = savedWidth + "px";
  }

  let dragging = false;

  handle.addEventListener("pointerdown", (e) => {
    dragging = true;
    handle.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = layout.getBoundingClientRect();
    let newWidth = e.clientX - rect.left;
    newWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, newWidth));
    fileBrowser.style.width = newWidth + "px";
    if (fitAddon) fitAddon.fit();
  });

  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    try { handle.releasePointerCapture(e.pointerId); } catch (err) { /* déjà relâché */ }
    localStorage.setItem(STORAGE_KEY, parseInt(fileBrowser.style.width, 10));
  }

  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);
})();
