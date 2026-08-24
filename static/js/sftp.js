// Navigateur de fichiers SFTP dans la colonne latérale du terminal.
// Utilise le même socket que terminal.js (variable globale `socket`) et
// se branche sur la session SSH déjà ouverte côté serveur.

const MAX_TRANSFER_BYTES = 15 * 1024 * 1024; // doit rester cohérent avec sftp_ws.py

let fbCurrentPath = null;

const fbList = document.getElementById("fb-list");
const fbPath = document.getElementById("fb-path");
const fbMessage = document.getElementById("fb-message");
const fbDropzone = document.getElementById("fb-dropzone");

function fbSetMessage(text, isError) {
  fbMessage.textContent = text || "";
  fbMessage.classList.toggle("error", !!isError);
}

function fbJoinPath(base, name) {
  if (!base || base === "/") return "/" + name;
  return base.replace(/\/+$/, "") + "/" + name;
}

function fbParentPath(path) {
  if (!path || path === "/") return "/";
  const trimmed = path.replace(/\/+$/, "");
  const idx = trimmed.lastIndexOf("/");
  if (idx <= 0) return "/";
  return trimmed.slice(0, idx);
}

function fbFormatSize(bytes) {
  if (bytes === null || bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

function fbListDir(path) {
  socket.emit("sftp_list", { path });
}

function fbRenderEntries(path, entries) {
  fbCurrentPath = path;
  fbPath.textContent = path;
  fbPath.title = path;
  fbList.innerHTML = "";

  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "fb-empty";
    empty.textContent = "Dossier vide";
    fbList.appendChild(empty);
    return;
  }

  entries.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "fb-row";

    const icon = document.createElement("span");
    icon.className = "fb-icon";
    icon.textContent = entry.is_dir ? "📁" : "📄";

    const name = document.createElement("span");
    name.className = "fb-name";
    name.textContent = entry.name;

    const size = document.createElement("span");
    size.className = "fb-size";
    size.textContent = entry.is_dir ? "" : fbFormatSize(entry.size);

    row.appendChild(icon);
    row.appendChild(name);
    row.appendChild(size);

    if (entry.is_dir) {
      row.classList.add("fb-dir");
      row.addEventListener("dblclick", () => fbListDir(fbJoinPath(path, entry.name)));
    } else {
      name.classList.add("fb-file-name");
      name.title = "Cliquer pour éditer";
      name.addEventListener("click", (e) => {
        e.stopPropagation();
        fbOpenEditor(fbJoinPath(path, entry.name), entry.name);
      });

      const dl = document.createElement("button");
      dl.className = "fb-btn-icon";
      dl.title = "Télécharger";
      dl.textContent = "⬇";
      dl.addEventListener("click", (e) => {
        e.stopPropagation();
        fbStartDownload(fbJoinPath(path, entry.name));
      });
      row.appendChild(dl);
    }

    const del = document.createElement("button");
    del.className = "fb-btn-icon fb-btn-danger";
    del.title = "Supprimer";
    del.textContent = "✕";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      const label = entry.is_dir ? "ce dossier (doit être vide)" : "ce fichier";
      if (!confirm(`Supprimer définitivement ${label} : ${entry.name} ?`)) return;
      socket.emit("sftp_delete", { path: fbJoinPath(path, entry.name), is_dir: entry.is_dir });
    });
    row.appendChild(del);

    fbList.appendChild(row);
  });
}

// --- Navigation ---------------------------------------------------------

document.getElementById("fb-up").addEventListener("click", () => {
  if (fbCurrentPath) fbListDir(fbParentPath(fbCurrentPath));
});

document.getElementById("fb-refresh").addEventListener("click", () => {
  fbListDir(fbCurrentPath);
});

// --- Suivi du répertoire courant du terminal -----------------------------
//
// Tant que le suivi est actif, chaque changement de répertoire détecté
// dans le terminal (via la séquence OSC 7, voir terminal.js) navigue
// automatiquement la colonne fichiers vers ce même répertoire. Reste
// actif jusqu'à ce qu'on le désactive explicitement.

let fbFollowEnabled = false;
const fbFollowBtn = document.getElementById("fb-follow");

function fbUpdateFollowButton() {
  fbFollowBtn.classList.toggle("active", fbFollowEnabled);
  fbFollowBtn.textContent = fbFollowEnabled ? "⚓ Suivi actif" : "⚓ Suivre";
}

fbFollowBtn.addEventListener("click", () => {
  fbFollowEnabled = !fbFollowEnabled;
  fbUpdateFollowButton();
  if (fbFollowEnabled) {
    // On saute immédiatement à la dernière position connue du terminal
    // si on en a déjà une (ex: l'utilisateur avait déjà navigué avant
    // d'activer le suivi) — pas la peine d'attendre le prochain
    // changement de répertoire.
    if (window.fbLastKnownCwd) {
      fbListDir(window.fbLastKnownCwd);
    }
    fbSetMessage(
      "Suivi activé — devrait fonctionner directement avec un prompt " +
      "standard. Pour un suivi plus fiable (prompt personnalisé, chemins " +
      "avec espaces), voir la config OSC 7 optionnelle dans le README.",
      false
    );
  }
});

window.addEventListener("terminal-cwd", (e) => {
  if (!fbFollowEnabled) return;
  fbListDir(e.detail.path);
});

// --- Nouveau dossier -----------------------------------------------------

document.getElementById("fb-mkdir").addEventListener("click", () => {
  const name = prompt("Nom du nouveau dossier :");
  if (!name) return;
  socket.emit("sftp_mkdir", { path: fbJoinPath(fbCurrentPath, name) });
});

// --- Upload: sélection de fichier + glisser-déposer ----------------------
//
// Le fichier est découpé côté client en morceaux de CHUNK_SIZE, envoyés
// séquentiellement avec accusé de réception (callback d'ack Socket.IO)
// avant d'envoyer le suivant — ça évite d'envoyer un fichier entier en
// un seul (gros) message, qui dépasserait la limite par défaut de
// Socket.IO et bloquerait la connexion plutôt que d'échouer proprement.

const CHUNK_SIZE = 256 * 1024; // doit rester cohérent avec sftp_ws.py

function fbReadChunkAsBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function fbUploadFile(file) {
  if (file.size > MAX_TRANSFER_BYTES) {
    fbSetMessage(
      `${file.name} dépasse la taille max pour ce mode d'envoi ` +
      `(${Math.round(MAX_TRANSFER_BYTES / (1024 * 1024))} Mo). Utilisez scp/rsync pour les gros fichiers.`,
      true
    );
    return;
  }

  const uploadId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const targetPath = fbJoinPath(fbCurrentPath, file.name);
  const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));

  try {
    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
      const start = chunkIndex * CHUNK_SIZE;
      const slice = file.slice(start, start + CHUNK_SIZE);
      const base64 = await fbReadChunkAsBase64(slice);

      const pct = Math.round(((chunkIndex + 1) / totalChunks) * 100);
      fbSetMessage(`Envoi de ${file.name}... (${pct}%)`, false);

      const response = await new Promise((resolve) => {
        socket.emit("sftp_upload_chunk", {
          upload_id: uploadId,
          path: targetPath,
          chunk_index: chunkIndex,
          content_base64: base64,
        }, resolve);
      });

      if (!response || !response.ok) {
        fbSetMessage((response && response.error) || `Erreur pendant l'envoi de ${file.name}.`, true);
        return;
      }
    }

    const endResponse = await new Promise((resolve) => {
      socket.emit("sftp_upload_end", { upload_id: uploadId, path: targetPath }, resolve);
    });
    if (!endResponse || !endResponse.ok) {
      fbSetMessage((endResponse && endResponse.error) || `Erreur à la finalisation de l'envoi de ${file.name}.`, true);
    }
    // sftp_uploaded (émis par le serveur) rafraîchit la liste et confirme le message.
  } catch (err) {
    fbSetMessage(`Erreur de lecture pour ${file.name}.`, true);
  }
}

document.getElementById("fb-upload-input").addEventListener("change", (e) => {
  Array.from(e.target.files).forEach(fbUploadFile);
  e.target.value = "";
});

["dragenter", "dragover"].forEach((evt) => {
  fbDropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    fbDropzone.classList.add("drag-over");
  });
});
["dragleave", "drop"].forEach((evt) => {
  fbDropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    fbDropzone.classList.remove("drag-over");
  });
});
fbDropzone.addEventListener("drop", (e) => {
  const files = e.dataTransfer.files;
  if (files && files.length) {
    Array.from(files).forEach(fbUploadFile);
  }
});

// --- Téléchargement: réassemble les morceaux reçus puis déclenche le
//     download navigateur ------------------------------------------------

const fbDownloadChunks = {}; // download_id -> tableau de Uint8Array

function fbStartDownload(path) {
  const downloadId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  fbDownloadChunks[downloadId] = [];
  fbSetMessage(`Téléchargement en cours...`, false);
  socket.emit("sftp_download", { path, download_id: downloadId });
}

function fbBase64ToBytes(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// --- Édition de fichier en ligne ------------------------------------------
//
// Clic sur le nom d'un fichier: ouvre son contenu dans CodeMirror (greffé
// sur le <textarea> existant via CodeMirror.fromTextArea), avec coloration
// syntaxique. Le mode est déterminé depuis l'extension du fichier
// (mode/meta.js) et chargé à la demande depuis le CDN (CodeMirror.autoLoadMode)
// — une extension non reconnue retombe simplement sur du texte brut, sans
// erreur. Limité aux fichiers texte UTF-8 de moins de 5 Mo (voir sftp_ws.py).

const editorModal = document.getElementById("file-editor-modal");
const editorFilename = document.getElementById("editor-filename");
const editorPathEl = document.getElementById("editor-path");
const editorCursorPosEl = document.getElementById("editor-cursor-pos");
const editorTextarea = document.getElementById("editor-textarea");
const editorMessage = document.getElementById("editor-message");
const editorSaveBtn = document.getElementById("editor-save-btn");
let editorCurrentPath = null;
let editorDirty = false;

// Le clic sur "Fermer" prévient déjà d'une perte de modifications non
// enregistrées (voir fbCloseEditor), mais fermer l'onglet ou recharger la
// page directement contournerait ça sans rien demander — au tout dernier
// moment où le navigateur donne la main avant de décharger la page.
window.addEventListener("beforeunload", (e) => {
  if (!editorDirty) return;
  e.preventDefault();
  e.returnValue = ""; // requis par certains navigateurs (Chrome), le texte affiché n'est plus personnalisable depuis longtemps
});

CodeMirror.modeURL = "https://cdn.jsdelivr.net/npm/codemirror@5.65.16/mode/%N/%N.min.js";

const editorCM = CodeMirror.fromTextArea(editorTextarea, {
  lineNumbers: true,
  theme: "bastion",
  matchBrackets: true,
  styleActiveLine: true,
  indentUnit: 4,
  tabSize: 4,
});

editorCM.on("change", (instance, changeObj) => {
  // "setValue" = chargement programmatique du contenu (fbOpenEditor /
  // sftp_file_text), pas une vraie frappe utilisateur.
  if (changeObj.origin === "setValue") return;
  editorDirty = true;
});

editorCM.on("cursorActivity", (instance) => {
  const pos = instance.getCursor();
  const selection = instance.getSelection();
  const suffix = selection ? ` (${selection.length})` : "";
  editorCursorPosEl.textContent = `L${pos.line + 1}:C${pos.ch + 1}${suffix}`;
});

function fbSetEditorMode(name) {
  const info = CodeMirror.findModeByFileName(name);
  if (!info) {
    editorCM.setOption("mode", null);
    return;
  }
  editorCM.setOption("mode", info.mime || info.mode);
  CodeMirror.autoLoadMode(editorCM, info.mode);
}

function fbOpenEditor(path, name) {
  editorCurrentPath = path;
  editorDirty = false;
  editorFilename.textContent = name;
  editorPathEl.textContent = path;
  editorCursorPosEl.textContent = "";
  editorCM.setValue("");
  editorCM.setOption("readOnly", true);
  editorCM.getWrapperElement().classList.add("cm-loading");
  editorMessage.textContent = "Chargement...";
  editorMessage.classList.remove("error");
  editorModal.style.display = "flex";
  document.body.classList.add("modal-open");
  fbSetEditorMode(name);
  syncEditorModalViewport();
  // CodeMirror mesure mal son contenu tant qu'il est caché (display:none) —
  // on force un recalcul une fois la modale affichée.
  editorCM.refresh();
  socket.emit("sftp_read_file", { path });
}

function fbCloseEditor() {
  if (editorDirty && !confirm("Des modifications non enregistrées seront perdues. Fermer quand même ?")) {
    return;
  }
  editorModal.style.display = "none";
  editorModal.style.height = "";
  editorModal.style.top = "";
  document.body.classList.remove("modal-open");
  editorCurrentPath = null;
  editorDirty = false;
}

// --- Adapter la modale au clavier virtuel (mobile) -------------------------
//
// .modal-overlay est en position: fixed; inset: 0, dimensionné sur le
// viewport "layout" — mais sur mobile, l'apparition du clavier virtuel ne
// redimensionne pas toujours ce viewport layout (notamment Chrome Android):
// la modale garde alors sa taille d'avant clavier, et son bas (barre de
// touches, boutons Enregistrer/Fermer) se retrouve caché sous le clavier
// plutôt que réellement scrollable — avec une compensation différente
// encore d'un navigateur à l'autre (d'où le clavier qui semble "avaler"
// les boutons sur l'un, et un défilement erratique sur l'autre). L'API
// VisualViewport donne la taille RÉELLEMENT visible en temps réel: on
// force la modale à toujours correspondre exactement à cette zone.
function syncEditorModalViewport() {
  if (!window.visualViewport || editorModal.style.display === "none") return;
  const vv = window.visualViewport;
  editorModal.style.height = `${vv.height}px`;
  editorModal.style.top = `${vv.offsetTop}px`;
  editorCM.refresh();
}

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncEditorModalViewport);
  window.visualViewport.addEventListener("scroll", syncEditorModalViewport);
}

document.getElementById("editor-close-btn").addEventListener("click", fbCloseEditor);

function fbSaveEditor() {
  if (!editorCurrentPath || editorSaveBtn.disabled) return;
  editorMessage.textContent = "Enregistrement...";
  editorMessage.classList.remove("error");
  editorSaveBtn.disabled = true;
  socket.emit("sftp_write_file", { path: editorCurrentPath, content: editorCM.getValue() });
}

editorSaveBtn.addEventListener("click", fbSaveEditor);

// Ctrl+S / Cmd+S: le keymap par défaut de CodeMirror mappe déjà cette
// combinaison sur la commande "save" (voir keyMap.pcDefault/macDefault
// dans lib/codemirror.js) — mais tant qu'aucune commande "save" n'est
// définie, CodeMirror ne bloque PAS l'événement clavier (vérifié dans son
// code: preventDefault n'est appelé que si un handler existe réellement),
// donc le navigateur ouvrirait sa boîte de dialogue native "Enregistrer
// la page" à la place. La définir ici couvre les deux à la fois: elle
// intercepte l'appui ET déclenche notre sauvegarde.
CodeMirror.commands.save = fbSaveEditor;

// --- Touches spéciales (mobile), éditeur -----------------------------------
//
// Même besoin que la barre du terminal (voir terminal.js): un clavier
// virtuel n'a pas de touche Tab, indispensable ici pour l'indentation.
// CodeMirror n'est pas un flux d'octets comme le terminal SSH — pas de
// séquence d'échappement à envoyer, on appelle directement ses commandes
// natives (execCommand) plutôt que de réémettre une frappe clavier.

// Toast de debug: confirme qu'un appui a bien été reçu par le navigateur
// (le fix précédent, basé sur touchend, n'a pas suffi — ce toast sert à
// voir si le tap est détecté du tout, ou si l'action derrière plante).
// À retirer une fois le bug identifié.
function showToast(message, isError) {
  let el = document.getElementById("debug-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "debug-toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.toggle("error", !!isError);
  el.classList.add("visible");
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => el.classList.remove("visible"), 3000);
}

// preventDefault() sur touchstart (pour ne pas voler le focus de l'éditeur
// au tap) supprime aussi, sur la plupart des navigateurs mobiles, le click
// synthétique qui suit normalement un tap — se reposer uniquement sur
// "click" laissait ces boutons sans aucun effet au toucher (jamais repéré
// en test desktop, où mousedown/click suffisent). Fix: déclencher l'action
// sur touchend (avec son propre preventDefault, qui annule bien le click
// fantôme qui suivrait) pour le tactile, et garder "click" pour la souris.
function runTap(label, action) {
  showToast(`Appui détecté: ${label}`);
  try {
    action();
  } catch (err) {
    showToast(`BUG (${label}): ${err.message}`, true);
  }
}

function bindTap(el, label, action) {
  el.addEventListener("mousedown", (e) => e.preventDefault());
  el.addEventListener("touchstart", (e) => e.preventDefault(), { passive: false });
  el.addEventListener("touchend", (e) => {
    e.preventDefault();
    runTap(label, action);
  });
  el.addEventListener("click", () => runTap(label, action));
}

const editorKeysBar = document.getElementById("editor-mobile-keys");

if (editorKeysBar) {
  editorKeysBar.querySelectorAll("button[data-cmd]").forEach((btn) => {
    const cmd = btn.dataset.cmd;
    if (cmd === "dismissKeyboard") {
      bindTap(btn, cmd, () => editorCM.getInputField().blur());
      return;
    }
    bindTap(btn, cmd, () => {
      editorCM.execCommand(cmd);
      editorCM.focus();
    });
  });
}

// --- Évènements serveur ---------------------------------------------------

socket.on("ssh_ready", () => {
  // Une fois le terminal connecté, on charge le dossier de départ
  // (répertoire personnel de l'utilisateur SSH) dans la colonne fichiers.
  fbListDir(null);
});

socket.on("sftp_listing", (data) => {
  fbSetMessage("", false);
  // La toute première liste reçue (au tout début de la session, avant
  // toute navigation) correspond au répertoire personnel — on la garde
  // en mémoire pour pouvoir résoudre un "~" détecté par le scraping de
  // prompt de terminal.js (voir scrapePromptPath()).
  if (window.fbHomePath === undefined) {
    window.fbHomePath = data.path;
  }
  fbRenderEntries(data.path, data.entries);
});

socket.on("sftp_error", (data) => {
  fbSetMessage(data.message, true);
  // Si l'éditeur est ouvert, l'erreur y est aussi affichée: le message
  // de la colonne latérale peut être masqué par le modal.
  if (editorModal.style.display !== "none") {
    editorMessage.textContent = data.message;
    editorMessage.classList.add("error");
    editorSaveBtn.disabled = false;
  }
});

socket.on("sftp_created", () => {
  fbSetMessage("Dossier créé.", false);
  fbListDir(fbCurrentPath);
});

socket.on("sftp_deleted", () => {
  fbSetMessage("Supprimé.", false);
  fbListDir(fbCurrentPath);
});

socket.on("sftp_uploaded", () => {
  fbSetMessage("Envoi terminé.", false);
  fbListDir(fbCurrentPath);
});

socket.on("sftp_download_chunk", (data) => {
  const buffer = fbDownloadChunks[data.download_id];
  if (!buffer) return; // téléchargement inconnu (page rechargée entre-temps ?)
  buffer[data.chunk_index] = fbBase64ToBytes(data.content_base64);
});

socket.on("sftp_download_end", (data) => {
  const parts = fbDownloadChunks[data.download_id];
  delete fbDownloadChunks[data.download_id];
  if (!parts) return;

  const blob = new Blob(parts, { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = data.name;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);

  fbSetMessage(`${data.name} téléchargé.`, false);
});

socket.on("sftp_file_text", (data) => {
  if (data.path !== editorCurrentPath) return;
  editorCM.setValue(data.content);
  editorCM.clearHistory(); // pas d'"annuler" jusqu'au contenu vide initial
  editorCM.setOption("readOnly", false);
  editorCM.getWrapperElement().classList.remove("cm-loading");
  editorDirty = false;
  editorMessage.textContent = "";
  editorMessage.classList.remove("error");
  editorCM.refresh();
});

socket.on("sftp_file_saved", (data) => {
  if (data.path !== editorCurrentPath) return;
  editorDirty = false;
  editorMessage.textContent = "Enregistré.";
  editorMessage.classList.remove("error");
  editorSaveBtn.disabled = false;
  // Rafraîchit la colonne (taille/date affichées) sans fermer l'éditeur.
  fbListDir(fbCurrentPath);
});
