// Page /stats: dessine une frise de disponibilité par machine (une série
// de segments colorés, façon page de statut) à partir des données déjà
// agrégées côté serveur (voir /api/history/<id>, history.get_timeline) —
// aucune librairie de graphe, juste des <div> flexbox.
(() => {
  const PERIOD_HOURS = { "24h": 24, "7d": 24 * 7, "30d": 24 * 30 };

  function segmentColor(pct) {
    if (pct === null) return "#2a323c"; // pas de donnée sur ce segment
    if (pct >= 99.5) return "var(--up)";
    if (pct <= 0) return "var(--down)";
    return "#e5a83d"; // dispo partielle dans ce segment
  }

  async function renderTimeline(el, hours) {
    const machineId = el.dataset.machine;
    el.classList.add("loading");
    try {
      const res = await fetch(`/api/history/${encodeURIComponent(machineId)}?hours=${hours}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      el.innerHTML = "";
      for (const pct of data.timeline) {
        const seg = document.createElement("div");
        seg.className = "uptime-seg";
        seg.style.background = segmentColor(pct);
        seg.title = pct === null ? "Pas de données" : `${pct}% disponible`;
        el.appendChild(seg);
      }
    } catch (err) {
      el.innerHTML = "";
      const msg = document.createElement("span");
      msg.className = "text-dim";
      msg.textContent = "Erreur de chargement";
      el.appendChild(msg);
    } finally {
      el.classList.remove("loading");
    }
  }

  function renderAll(periodKey) {
    const hours = PERIOD_HOURS[periodKey] || 24;
    document.querySelectorAll(".uptime-timeline").forEach((el) => renderTimeline(el, hours));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const radios = document.querySelectorAll('input[name="period"]');
    radios.forEach((r) => r.addEventListener("change", () => {
      if (r.checked) renderAll(r.value);
    }));
    const checked = document.querySelector('input[name="period"]:checked');
    renderAll(checked ? checked.value : "30d");
  });
})();
