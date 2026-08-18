const mapWrap = document.getElementById("map-wrap");
let editMode = false;

// --- Alignement position <-> image ---------------------------------
//
// Les positions des machines sont en %, relatives au conteneur
// #map-wrap. Pour que ce % corresponde toujours au même point visuel
// sur l'image (quelle que soit la taille/orientation d'écran), le
// conteneur doit avoir exactement le même ratio largeur/hauteur que
// l'image — sinon "object-fit: contain" ajoute des bandes vides dont
// la taille varie selon l'écran, et le repère en % se décale.
const mapBg = document.getElementById("map-bg");
if (mapBg) {
  const applyAspectRatio = () => {
    if (mapBg.naturalWidth && mapBg.naturalHeight) {
      mapWrap.style.aspectRatio = `${mapBg.naturalWidth} / ${mapBg.naturalHeight}`;
      mapWrap.style.height = "auto";
    }
  };
  if (mapBg.complete) applyAspectRatio();
  mapBg.addEventListener("load", applyAspectRatio);
}

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
