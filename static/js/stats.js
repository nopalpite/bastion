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

  // Les segments vont du plus ancien (gauche) au plus récent (droite),
  // dans le même ordre que history.get_timeline() — sans repère de date,
  // impossible de savoir à quoi correspond la frise (24h ? la semaine
  // dernière ?). Plusieurs graduations réparties le long de l'axe
  // (façon panneau Grafana "state timeline") plutôt que juste les deux
  // extrémités, pour repérer un point précis sans calculer soi-même.
  // Calculé côté client: dépend de l'instant où la page est affichée,
  // pas d'une valeur figée côté serveur au moment du rendu.
  const TICK_COUNT = 6;

  function formatTick(date, hours) {
    // En dessous de 48h, l'heure seule suffit à situer un point ; au-delà
    // (7j/30j), la date compte plus que la minute près.
    if (hours <= 48) {
      return date.toLocaleString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit" });
  }

  function renderTimelineLabels(row, hours) {
    const container = row.querySelector(".uptime-timeline-labels");
    if (!container) return;
    container.innerHTML = "";
    const now = Date.now();
    const startMs = now - hours * 3600 * 1000;
    for (let i = 0; i < TICK_COUNT; i++) {
      const span = document.createElement("span");
      if (i === TICK_COUNT - 1) {
        span.textContent = "maintenant";
      } else {
        const t = startMs + (i / (TICK_COUNT - 1)) * (now - startMs);
        span.textContent = formatTick(new Date(t), hours);
      }
      container.appendChild(span);
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
    renderTimelineLabels(row, hours);
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
