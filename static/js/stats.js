// Page /stats: dessine, par machine, une frise de disponibilité (segments
// colorés façon page de statut) et une courbe de latence (sparkline SVG
// tracée à la main), à partir des données déjà agrégées côté serveur
// (voir /api/history/<id>, history.get_timeline/get_latency_timeline) —
// aucune librairie de graphe, juste des <div>/<svg>.
(() => {
  const PERIOD_HOURS = { "24h": 24, "7d": 24 * 7, "30d": 24 * 30 };

  function segmentColor(pct) {
    if (pct === null) return "#2a323c"; // pas de donnée sur ce segment
    if (pct >= 99.5) return "var(--up)";
    if (pct <= 0) return "var(--down)";
    return "#e5a83d"; // dispo partielle dans ce segment
  }

  function renderUptimeSegments(el, timeline) {
    el.innerHTML = "";
    for (const pct of timeline) {
      const seg = document.createElement("div");
      seg.className = "uptime-seg";
      seg.style.background = segmentColor(pct);
      seg.title = pct === null ? "Pas de données" : `${pct}% disponible`;
      el.appendChild(seg);
    }
  }

  // Sparkline dessinée à la main (pas de librairie): une valeur par
  // bucket devient un point, les buckets sans donnée (null) coupent la
  // ligne plutôt que d'être interpolés — un trou visible vaut mieux
  // qu'une fausse continuité.
  function renderLatencyChart(el, latency) {
    const nums = latency.filter((v) => v !== null);
    if (nums.length === 0) {
      el.innerHTML = '<span class="text-dim">Pas de données de latence</span>';
      return;
    }

    const width = 300;
    const height = 36;
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const range = max - min || 1;
    const stepX = width / Math.max(latency.length - 1, 1);

    const points = latency.map((v, i) => (
      v === null ? null : [i * stepX, height - ((v - min) / range) * (height - 6) - 3]
    ));

    const segments = [];
    let current = [];
    for (const p of points) {
      if (p === null) {
        if (current.length > 1) segments.push(current);
        current = [];
      } else {
        current.push(p);
      }
    }
    if (current.length > 1) segments.push(current);

    const polylines = segments.map((seg) => {
      const coords = seg.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
      return `<polyline points="${coords}" fill="none" stroke="var(--accent)" `
        + `stroke-width="1.5" vector-effect="non-scaling-stroke" />`;
    }).join("");

    el.innerHTML = (
      `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${polylines}</svg>`
      + `<span class="latency-range">${min.toFixed(0)}–${max.toFixed(0)} ms</span>`
    );
  }

  async function renderMachine(row, hours) {
    const machineId = row.dataset.machine;
    const uptimeEl = row.querySelector(".uptime-timeline");
    const latencyEl = row.querySelector(".latency-chart");
    uptimeEl.classList.add("loading");
    try {
      const res = await fetch(`/api/history/${encodeURIComponent(machineId)}?hours=${hours}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderUptimeSegments(uptimeEl, data.timeline);
      renderLatencyChart(latencyEl, data.latency);
    } catch (err) {
      uptimeEl.innerHTML = '<span class="text-dim">Erreur de chargement</span>';
      latencyEl.innerHTML = "";
    } finally {
      uptimeEl.classList.remove("loading");
    }
  }

  function renderAll(periodKey) {
    const hours = PERIOD_HOURS[periodKey] || 24;
    document.querySelectorAll(".uptime-row").forEach((row) => renderMachine(row, hours));
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
