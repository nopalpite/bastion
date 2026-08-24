const mapWrap = document.getElementById("map-wrap");
let editMode = false;

// --- Alignement position <-> image ---------------------------------
//
// Les positions des machines sont en %, relatives au conteneur
// #map-wrap. Pour que ce % corresponde toujours au même point visuel sur
// l'image (quelle que soit la taille/orientation d'écran), le conteneur
// doit avoir exactement le même ratio largeur/hauteur que l'image —
// sinon "object-fit: contain" ajoute des bandes vides dont la taille
// varie selon l'écran, et le repère en % se décale.
//
// Le serveur injecte déjà ce ratio (variables CSS --map-w/--map-h, voir
// templates/map.html) pour un affichage correct dès le premier rendu,
// sans attendre le JS. Ce bloc ne sert donc que de filet de sécurité pour
// les cas où le serveur n'a pas pu déterminer le ratio réel — le SVG
// (vectoriel, aucune résolution fixe à lire côté serveur, voir
// map_image.py) en particulier, mais aussi tout format dont la lecture
// aurait échoué côté serveur. Pour une image dont le ratio est déjà
// correctement injecté, ce code se contente de reconfirmer les mêmes
// valeurs — inoffensif.
const mapBg = document.getElementById("map-bg");
if (mapBg) {
  const applyAspectRatio = () => {
    if (mapBg.naturalWidth && mapBg.naturalHeight) {
      mapWrap.style.setProperty("--map-w", mapBg.naturalWidth);
      mapWrap.style.setProperty("--map-h", mapBg.naturalHeight);
    }
  };
  if (mapBg.complete) applyAspectRatio();
  mapBg.addEventListener("load", applyAspectRatio);
}

// --- Utiliser toute la largeur disponible sur un grand écran ------------
//
// .map-wrap limite sa hauteur via --map-max-h (voir style.css) pour ne
// jamais dépasser la fenêtre verticalement — un "75vh" fixe ignorerait la
// hauteur réelle de tout ce qui est affiché au-dessus du plan (en-tête,
// barre "non placées" si elle est présente...), qui varie d'une page à
// l'autre : sur un écran large avec un en-tête proportionnellement petit,
// ça sous-utilisait la largeur dispo (le plan restait inutilement étroit).
// On mesure donc l'espace vertical réellement restant sous le plan.
function updateMapMaxHeight() {
  const bottomMargin = 24; // un peu d'air en bas de page
  const available = window.innerHeight - mapWrap.getBoundingClientRect().top - bottomMargin;
  mapWrap.style.setProperty("--map-max-h", `${Math.max(280, available)}px`);
}
updateMapMaxHeight();
window.addEventListener("resize", () => requestAnimationFrame(updateMapMaxHeight));

// --- Afficher/masquer les noms des machines -----------------------------
//
// Préférence globale (pas par salle) mémorisée en local, façon largeur du
// panneau SFTP dans terminal.js: pratique sur un plan chargé, pas de raison
// de la redemander à chaque visite.

const LABELS_STORAGE_KEY = "bastion-map-hide-labels";
const toggleLabelsBtn = document.getElementById("toggle-labels");

function applyLabelsVisibility(hidden) {
  mapWrap.classList.toggle("hide-labels", hidden);
  toggleLabelsBtn.textContent = hidden ? "Afficher les noms" : "Masquer les noms";
}

applyLabelsVisibility(localStorage.getItem(LABELS_STORAGE_KEY) === "1");

toggleLabelsBtn.addEventListener("click", () => {
  const hidden = !mapWrap.classList.contains("hide-labels");
  applyLabelsVisibility(hidden);
  localStorage.setItem(LABELS_STORAGE_KEY, hidden ? "1" : "0");
});

// --- Mode édition (déplacer les marqueurs) ------------------------------

document.getElementById("toggle-edit").addEventListener("click", (e) => {
  editMode = !editMode;
  mapWrap.classList.toggle("edit-mode", editMode);
  e.target.textContent = editMode ? "Terminer l'édition" : "Activer le mode édition";
});

function savePosition(machineId, x, y) {
  return fetch(`/api/machines/${machineId}/position`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y }),
  });
}

// Pointer Events unifie souris/tactile/stylet en une seule API, avec
// setPointerCapture pour un drag fiable même si le doigt/curseur sort
// brièvement du marqueur pendant le déplacement.
function makeDraggable(marker) {
  let dragging = false;

  marker.addEventListener("pointerdown", (e) => {
    if (!editMode) return;
    dragging = true;
    marker.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  marker.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = mapWrap.getBoundingClientRect();
    let x = ((e.clientX - rect.left) / rect.width) * 100;
    let y = ((e.clientY - rect.top) / rect.height) * 100;
    x = Math.max(0, Math.min(100, x));
    y = Math.max(0, Math.min(100, y));
    marker.style.left = x + "%";
    marker.style.top = y + "%";
  });

  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    try { marker.releasePointerCapture(e.pointerId); } catch (err) { /* déjà relâché */ }
    const x = parseFloat(marker.style.left);
    const y = parseFloat(marker.style.top);
    savePosition(marker.dataset.id, x, y);
  }

  marker.addEventListener("pointerup", endDrag);
  marker.addEventListener("pointercancel", endDrag);

  marker.addEventListener("click", () => {
    if (editMode) return; // en édition, le clic sert juste à déplacer
    openActionModal(marker.dataset.id, marker.dataset.name);
  });
}

document.querySelectorAll(".map-marker").forEach(makeDraggable);

// --- Placer une machine non positionnée au centre du plan ---------------

document.querySelectorAll("[data-place-id]").forEach((btn) => {
  btn.addEventListener("click", () => {
    btn.disabled = true;
    savePosition(btn.dataset.placeId, 50, 50)
      .then(() => location.reload())
      .catch(() => {
        btn.disabled = false;
        alert("Erreur réseau: le placement n'a pas pu être enregistré.");
      });
  });
});
