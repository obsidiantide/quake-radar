// Quake Radar dashboard: reads the serve layer (events/daily/health/runs JSON) and renders it.
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
const BASE = (isLocal ? "data/" : (window.QUAKE_DATA_URL || "data/"));

const $ = (id) => document.getElementById(id);
const magColor = (m) => m >= 5 ? "var(--m5)" : m >= 4 ? "var(--m4)" : m >= 3 ? "var(--m3)" : "var(--m2)";
const magHex = (m) => m >= 5 ? "#f85149" : m >= 4 ? "#ff7a45" : m >= 3 ? "#e3b341" : "#58a6ff";
const fmtTime = (iso) => new Date(iso).toLocaleString("ru-RU", { timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) + " UTC";
const ago = (iso) => {
  if (!iso) return "никогда";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  return `${Math.round(s / 86400)} дн назад`;
};
const bytes = (n) => n > 1e6 ? (n / 1e6).toFixed(1) + " МБ" : (n / 1e3).toFixed(0) + " КБ";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function load(name) {
  const r = await fetch(BASE + name + "?t=" + Math.floor(Date.now() / 60000));
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
}

async function main() {
  let events, daily, health, runs;
  try {
    [events, daily, health, runs] = await Promise.all(["events.json", "daily.json", "health.json", "runs.json"].map(load));
  } catch (e) {
    document.querySelector("main").innerHTML = `<div class="card error">Не удалось загрузить данные: ${esc(e.message)}</div>`;
    return;
  }
  renderHealth(health);
  renderKpis(events);
  renderMap(events, health.home);
  renderList(events);
  renderDaily(daily);
  renderRuns(runs);
}

function renderHealth(h) {
  const last = h.last_run || {};
  const cls = { success: "ok", partial: "warn", failed: "bad" }[last.status] || "";
  $("run-dot").className = "dot " + cls;
  $("run-text").textContent = `последний запуск: ${last.status || "—"} · ${ago(last.finished_at)}`;
  $("fresh-pill").textContent = `данные обновлены ${ago(h.last_success_at)}`;

  const names = { emsc: "EMSC · REST + WebSocket", usgs: "USGS · REST API", geonames: "GeoNames · файл" };
  $("sources").innerHTML = Object.entries(h.sources).map(([k, s]) => `
    <div class="source"><span><span class="dot ${s.stale ? "bad" : "ok"}" style="display:inline-block;margin-right:8px"></span>${names[k] || k}</span>
    <span class="sub">${s.stale ? "устарел · " : ""}${ago(s.last_success)}</span></div>`).join("");
  const st = h.storage, d = h.last_14_days;
  $("storage").innerHTML = `
    <div class="source"><span>Событий в итоговом слое</span><b>${st.events_total}</b></div>
    <div class="source"><span>Сырой слой</span><span class="sub">${st.raw_files} файлов · ${bytes(st.raw_bytes)}</span></div>
    <div class="source"><span>Запусков / сбоев / частичных</span><span class="sub">${d.runs} / ${d.failed} / ${d.partial}</span></div>
    <div class="source"><span>Среднее время запуска</span><span class="sub">${d.avg_duration_s ?? "—"} с</span></div>
    <div class="source"><span>Загружено строк за 14 дней</span><span class="sub">${d.rows_loaded}</span></div>`;
}

function renderKpis(events) {
  const quakes = events.filter((e) => e.type === "earthquake");
  const now = Date.now();
  const within = (h) => quakes.filter((e) => now - new Date(e.t).getTime() < h * 3600e3);
  const d1 = within(24), d7 = within(168);
  $("k-24h").textContent = d1.length;
  $("k-24h-h").textContent = d1.length ? `макс. M${Math.max(...d1.map((e) => e.mag)).toFixed(1)}` : "тихо";
  $("k-7d").textContent = d7.length;
  $("k-7d-h").textContent = d7.length ? `макс. M${Math.max(...d7.map((e) => e.mag)).toFixed(1)}` : "";
  if (quakes.length) {
    const near = quakes.reduce((a, b) => (a.home_km < b.home_km ? a : b));
    $("k-near").textContent = `${Math.round(near.home_km)} км`;
    $("k-near-h").textContent = `M${near.mag.toFixed(1)} · ${near.city}`;
    const mx = quakes.reduce((a, b) => (a.mag > b.mag ? a : b));
    $("k-max").textContent = `M${mx.mag.toFixed(1)}`;
    $("k-max-h").textContent = `${mx.city}, ${mx.cc} · ${fmtTime(mx.t)}`;
  }
  $("k-both").textContent = events.filter((e) => e.sources === "emsc+usgs").length;
}

let map, markers = {};
function renderMap(events, home) {
  map = L.map("map", { zoomControl: true, attributionControl: true, scrollWheelZoom: false }).setView([41, 47.5], 5);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    attribution: "Tiles &copy; Esri", maxZoom: 12,
  }).addTo(map);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 12, pane: "overlayPane",
  }).addTo(map);
  L.circle([home.lat, home.lon], { radius: 300000, color: "#ff7a45", weight: 1, dashArray: "4 6", fill: false }).addTo(map);
  L.circleMarker([home.lat, home.lon], { radius: 5, color: "#fff", fillColor: "#fff", fillOpacity: 1 })
    .addTo(map).bindTooltip(`${home.name} · радиус алертов 300 км`);
  [...events].reverse().forEach((e) => {
    const m = L.circleMarker([e.lat, e.lon], {
      radius: Math.max(3, (e.mag - 1) * 3), color: magHex(e.mag), weight: 1,
      fillColor: magHex(e.mag), fillOpacity: 0.55,
    }).addTo(map).bindPopup(popup(e));
    markers[e.event_id] = m;
  });
}

function popup(e) {
  return `<b>M${e.mag.toFixed(1)} ${esc(e.mag_type || "")}</b> · ${esc(e.place || "")}<br>
    ${fmtTime(e.t)} · глубина ${e.depth ?? "?"} км<br>
    ${Math.round(e.city_km)} км от ${esc(e.city)} (${esc(e.cc)}) · <b>${Math.round(e.home_km)} км от дома</b><br>
    <span class="sub">источники: ${esc(e.sources)}${e.mag_emsc != null && e.mag_usgs != null ? ` · EMSC M${e.mag_emsc} / USGS M${e.mag_usgs}` : ""}</span>`;
}

function renderList(events) {
  $("ev-count").textContent = `${events.length} за 30 дней`;
  $("events").innerHTML = events.slice(0, 120).map((e) => `
    <div class="ev" data-id="${esc(e.event_id)}">
      <div class="mag" style="background:${magColor(e.mag)}">${e.mag.toFixed(1)}</div>
      <div><div>${esc(e.place || e.city)}</div>
        <div class="t">${fmtTime(e.t)} · ${Math.round(e.city_km)} км от ${esc(e.city)}
        <span class="src ${e.sources === "emsc+usgs" ? "both" : ""}">${esc(e.sources)}</span></div></div>
      <div class="km"><b>${Math.round(e.home_km)}</b><div class="t">км</div></div>
    </div>`).join("") || `<div class="ev">Нет событий</div>`;
  document.querySelectorAll(".ev[data-id]").forEach((el) => el.addEventListener("click", () => {
    const m = markers[el.dataset.id];
    if (m) { map.setView(m.getLatLng(), 7); m.openPopup(); }
  }));
}

function renderDaily(daily) {
  Chart.defaults.color = "#8b98a9";
  Chart.defaults.font.family = "Inter";
  new Chart($("daily"), {
    type: "bar",
    data: {
      labels: daily.map((d) => d.day),
      datasets: [{ label: "событий", data: daily.map((d) => d.n), backgroundColor: daily.map((d) => magHex(d.max_mag)), borderRadius: 2 }],
    },
    options: {
      maintainAspectRatio: false, plugins: { legend: { display: false },
        tooltip: { callbacks: { afterLabel: (c) => `макс. M${daily[c.dataIndex].max_mag}` } } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { grid: { color: "#243042" }, beginAtZero: true } },
    },
  });
}

function renderRuns(runs) {
  const last = [...runs].reverse().slice(0, 25);
  $("runs-sub").textContent = `последние ${last.length} из ${runs.length}`;
  $("runs").innerHTML = `<tr><th>Запуск</th><th>Режим</th><th>Начало</th><th>Статус</th><th>Строк (EMSC / USGS)</th><th>Объём</th><th>Время</th><th>Проблема</th></tr>` +
    last.map((r) => `<tr>
      <td class="mono">${esc(r.run_id)}</td><td>${esc(r.mode)}</td><td>${fmtTime(r.started_at)}</td>
      <td class="status ${esc(r.status)}">${esc(r.status)}</td>
      <td class="mono">${r.rows_raw?.emsc ?? "—"} / ${r.rows_raw?.usgs ?? "—"}</td>
      <td class="mono">${bytes(r.bytes_raw || 0)}</td><td class="mono">${r.duration_s}s</td>
      <td class="sub" style="white-space:normal;max-width:380px">${esc((r.checks_failed || []).length ? "проверки: " + r.checks_failed.join(", ") : (r.error || ""))}</td>
    </tr>`).join("");
}

main();
