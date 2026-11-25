// Quake Radar website. Reads the serve layer (JSON written by the pipeline) and renders
// five pages with hash routing: #/  #/alerts  #/event/<id>  #/pipeline  #/about
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
const BASE = isLocal ? "data/" : (window.QUAKE_DATA_URL || "data/");
const REPO = "https://github.com/obsidiantide/quake-radar";
const NEAR_KM = 300, NEAR_MAG = 3.5, STRONG_MAG = 5.0;

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const magHex = (m) => m >= 5 ? "#f85149" : m >= 4 ? "#ff7a45" : m >= 3 ? "#e3b341" : "#58a6ff";
const fmt = (iso, opts = {}) => new Date(iso).toLocaleString("ru-RU", { timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", ...opts }) + " UTC";
const fmtDay = (iso) => new Date(iso).toLocaleDateString("ru-RU", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" });
const ago = (iso) => {
  if (!iso) return "никогда";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  return `${Math.round(s / 86400)} дн назад`;
};
const bytes = (n) => n > 1e6 ? (n / 1e6).toFixed(1) + " МБ" : (n / 1e3).toFixed(0) + " КБ";
const evUrl = (e) => `#/event/${encodeURIComponent(e.event_id)}`;

// ------------------------------------------------------------------ data
const D = {};
async function load(name, fallback) {
  try {
    const r = await fetch(BASE + name + "?t=" + Math.floor(Date.now() / 60000));
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch (e) {
    if (fallback !== undefined) return fallback;
    throw new Error(`${name}: ${e.message}`);
  }
}
async function loadAll() {
  [D.events, D.daily, D.health, D.runs, D.alerts] = await Promise.all([
    load("events.json"), load("daily.json"), load("health.json"), load("runs.json"), load("alerts.json", null),
  ]);
  if (!D.alerts) { // older data branch without alerts.json: derive from events
    D.alerts = D.events.filter((e) => e.type === "earthquake" && ((e.home_km <= NEAR_KM && e.mag >= NEAR_MAG) || e.mag >= STRONG_MAG))
      .map((e) => ({ ...e, reason: e.home_km <= NEAR_KM && e.mag >= NEAR_MAG ? "near" : "strong" }));
  }
  D.byId = Object.fromEntries([...D.alerts, ...D.events].map((e) => [e.event_id, e]));
}

// ------------------------------------------------------------------ shared chrome
function renderChrome() {
  const last = D.health.last_run || {};
  const cls = { success: "ok", partial: "warn", failed: "bad" }[last.status] || "";
  $("#status-pill").innerHTML = `<span class="dot ${cls}"></span><span>данные обновлены ${ago(D.health.last_success_at)}</span>`;
  const recent = D.alerts.filter((a) => Date.now() - new Date(a.t) < 7 * 864e5);
  const badge = $("#nav-alerts");
  badge.hidden = !recent.length; badge.textContent = recent.length;
  const banner = $("#banner");
  const latest = D.alerts.find((a) => Date.now() - new Date(a.t) < 2 * 864e5);
  if (latest) {
    banner.hidden = false;
    banner.innerHTML = `<span class="dot bad"></span><span><b>M${latest.mag.toFixed(1)}</b> · ${esc(latest.place || latest.city)} · ${Math.round(latest.home_km)} км от Баку · ${ago(latest.t)}</span><a href="${evUrl(latest)}">подробнее →</a>`;
  } else if (last.status && last.status !== "success") {
    banner.hidden = false;
    banner.innerHTML = `<span class="dot warn"></span><span>Последний запуск пайплайна: <b>${esc(last.status)}</b>. Показаны последние проверенные данные.</span><a href="#/pipeline">мониторинг →</a>`;
  } else banner.hidden = true;
}

let maps = [];
function makeMap(el, center = [41, 47.5], zoom = 5) {
  maps.forEach((m) => m.remove()); maps = [];
  const map = L.map(el, { scrollWheelZoom: false }).setView(center, zoom);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri", maxZoom: 12 }).addTo(map);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 12, pane: "overlayPane" }).addTo(map);
  const h = D.health.home;
  L.circle([h.lat, h.lon], { radius: NEAR_KM * 1000, color: "#ff7a45", weight: 1, dashArray: "4 6", fill: false }).addTo(map);
  L.circleMarker([h.lat, h.lon], { radius: 5, color: "#fff", fillColor: "#fff", fillOpacity: 1 }).addTo(map).bindTooltip(`${h.name} · радиус тревог ${NEAR_KM} км`);
  maps.push(map);
  return map;
}
function addQuakes(map, list) {
  [...list].reverse().forEach((e) => {
    L.circleMarker([e.lat, e.lon], { radius: Math.max(3, (e.mag - 1) * 3), color: magHex(e.mag), weight: 1, fillColor: magHex(e.mag), fillOpacity: .55 })
      .addTo(map).bindPopup(`<b>M${e.mag.toFixed(1)}</b> · ${esc(e.place || e.city)}<br>${fmt(e.t)}<br>${Math.round(e.home_km)} км от Баку<br><a href="${evUrl(e)}">карточка события →</a>`);
  });
}
function evRow(e, extra = "") {
  return `<a class="ev" href="${evUrl(e)}">
    <div class="mag" style="background:${magHex(e.mag)}">${e.mag.toFixed(1)}</div>
    <div><div>${esc(e.place || e.city)} ${extra}</div>
      <div class="t">${fmt(e.t)} · ${Math.round(e.city_km)} км от ${esc(e.city)} <span class="src ${e.sources === "emsc+usgs" ? "both" : ""}">${esc(e.sources)}</span></div></div>
    <div class="km"><b>${Math.round(e.home_km)}</b><div class="t">км до Баку</div></div></a>`;
}
const legend = `<span class="legend"><span><i style="background:var(--m2)"></i>&lt;3</span><span><i style="background:var(--m3)"></i>3–4</span><span><i style="background:var(--m4)"></i>4–5</span><span><i style="background:var(--m5)"></i>5+</span></span>`;

// ------------------------------------------------------------------ pages
function pageHome(app) {
  const q = D.events.filter((e) => e.type === "earthquake");
  const within = (h) => q.filter((e) => Date.now() - new Date(e.t) < h * 3600e3);
  const d1 = within(24), d7 = within(168);
  const near = q.length ? q.reduce((a, b) => (a.home_km < b.home_km ? a : b)) : null;
  const mx = q.length ? q.reduce((a, b) => (a.mag > b.mag ? a : b)) : null;
  const maxOf = (l) => l.length ? `макс. M${Math.max(...l.map((e) => e.mag)).toFixed(1)}` : "тихо";
  app.innerHTML = `
    <div><h1 class="page">Землетрясения Кавказа и Каспия</h1>
    <p class="lede">Одна проверенная лента из двух сейсмологических агентств: дубли сведены, у каждого толчка есть ближайший город и расстояние до Баку. Обновляется автоматически.</p></div>
    <section class="kpis">
      <div class="kpi"><div class="label">За 24 часа</div><div class="value">${d1.length}</div><div class="hint">${maxOf(d1)}</div></div>
      <div class="kpi"><div class="label">За 7 дней</div><div class="value">${d7.length}</div><div class="hint">${maxOf(d7)}</div></div>
      <div class="kpi"><div class="label">Ближе всего к Баку, 30 дн</div><div class="value">${near ? Math.round(near.home_km) + " км" : "—"}</div><div class="hint">${near ? `M${near.mag.toFixed(1)} · ${esc(near.city)}` : ""}</div></div>
      <div class="kpi"><div class="label">Сильнейшее, 30 дн</div><div class="value">${mx ? "M" + mx.mag.toFixed(1) : "—"}</div><div class="hint">${mx ? `${esc(mx.city)}, ${esc(mx.cc)} · ${fmt(mx.t)}` : ""}</div></div>
      <div class="kpi"><div class="label">Тревог за 90 дней</div><div class="value">${D.alerts.length}</div><div class="hint"><a href="#/alerts">открыть список →</a></div></div>
    </section>
    <section class="grid">
      <div class="card"><h2>Карта, 30 дней ${legend}</h2><div class="map" id="map"></div></div>
      <div class="card"><h2>Последние события <span>${q.length} за 30 дней</span></h2><div class="list">${q.slice(0, 150).map((e) => evRow(e)).join("") || '<div class="empty">Нет событий</div>'}</div></div>
    </section>
    <section class="card"><h2>Активность по дням <span>землетрясения M2+, 180 дней · цвет = сильнейшее за день</span></h2><div class="chart-wrap"><canvas id="daily"></canvas></div></section>`;
  addQuakes(makeMap($("#map")), q);
  Chart.defaults.color = "#8b98a9"; Chart.defaults.font.family = "Inter";
  new Chart($("#daily"), {
    type: "bar",
    data: { labels: D.daily.map((d) => d.day), datasets: [{ data: D.daily.map((d) => d.n), backgroundColor: D.daily.map((d) => magHex(d.max_mag)), borderRadius: 2 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.raw} событий`, afterLabel: (c) => `макс. M${D.daily[c.dataIndex].max_mag}` } } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { grid: { color: "#243042" }, beginAtZero: true } } },
  });
}

function pageAlerts(app) {
  const a = D.alerts;
  app.innerHTML = `
    <div><h1 class="page">Тревоги</h1>
    <p class="lede">Толчки, которые стоит заметить: <b>M${NEAR_MAG}+ в радиусе ${NEAR_KM} км от Баку</b> или <b>M${STRONG_MAG}+ в любой точке региона</b>. Последние 90 дней. Свежая тревога (до 48 часов) показывается баннером на всех страницах.</p></div>
    <section class="grid">
      <div class="card"><h2>Где были тревоги ${legend}</h2><div class="map" id="map"></div></div>
      <div class="card"><h2>Список <span>${a.length} за 90 дней</span></h2><div class="list">${a.map((e) =>
        evRow(e, `<span class="tag ${e.reason}">${e.reason === "near" ? "рядом с Баку" : "сильное"}</span>`)).join("") || '<div class="empty">За 90 дней тревог не было</div>'}</div></div>
    </section>`;
  addQuakes(makeMap($("#map")), a);
}

function pageEvent(app, id) {
  const e = D.byId[decodeURIComponent(id)];
  if (!e) { app.innerHTML = `<div class="card empty">Событие не найдено: оно старше 30 дней или ещё не загружено. <a href="#/">На главную</a></div>`; return; }
  const both = e.mag_emsc != null && e.mag_usgs != null;
  const links = [
    e.emsc_id && `<a class="btn" target="_blank" rel="noopener" href="https://www.seismicportal.eu/eventdetails.html?unid=${encodeURIComponent(e.emsc_id)}">EMSC ↗</a>`,
    e.usgs_id && `<a class="btn" target="_blank" rel="noopener" href="https://earthquake.usgs.gov/earthquakes/eventpage/${encodeURIComponent(e.usgs_id)}">USGS ↗</a>`,
  ].filter(Boolean).join("");
  app.innerHTML = `
    <div class="actions"><a class="btn" href="javascript:history.back()">← Назад</a></div>
    <section class="card"><div class="body" style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
      <div class="mag lg" style="background:${magHex(e.mag)}">${e.mag.toFixed(1)}</div>
      <div style="flex:1;min-width:240px"><h1 class="page" style="margin:0">${esc(e.place || e.city)}</h1>
        <div class="sub">${fmt(e.t, { year: "numeric", second: "2-digit" })} · ${esc(e.event_id)}</div></div>
      <div class="actions">${links}</div></div></section>
    <section class="grid">
      <div class="card"><h2>На карте</h2><div class="map small" id="map"></div></div>
      <div class="card"><h2>Параметры</h2><div class="body facts">
        <div class="fact"><div class="label">Магнитуда</div><div class="value">M${e.mag.toFixed(1)} ${esc(e.mag_type || "")}</div></div>
        <div class="fact"><div class="label">Глубина</div><div class="value">${e.depth ?? "?"} км</div></div>
        <div class="fact"><div class="label">До Баку</div><div class="value">${Math.round(e.home_km)} км</div></div>
        <div class="fact"><div class="label">Ближайший город</div><div class="value">${esc(e.city)}, ${esc(e.cc)}</div><div class="sub">${Math.round(e.city_km)} км</div></div>
        <div class="fact"><div class="label">Координаты</div><div class="value mono">${e.lat}, ${e.lon}</div></div>
        <div class="fact"><div class="label">Источники</div><div class="value">${esc(e.sources)}</div></div>
      </div>
      <h2 style="border-top:1px solid var(--line)">Что говорят агентства</h2><div class="body">
        ${both ? `<table><tr><th>Агентство</th><th>Магнитуда</th></tr>
          <tr><td>EMSC</td><td>M${e.mag_emsc}</td></tr><tr><td>USGS</td><td>M${e.mag_usgs}</td></tr></table>
          <p class="sub" style="margin:10px 0 0">Оба агентства сообщили об этом толчке, пайплайн свёл их в одну запись. Итоговая магнитуда — решение USGS, если оно проверено (reviewed), иначе EMSC.</p>`
          : `<p class="sub" style="margin:0">Событие пока есть только в каталоге ${esc(e.sources.toUpperCase())}. USGS обычно публикует только толчки от ~M4.</p>`}
      </div></div>
    </section>`;
  const map = makeMap($("#map"), [e.lat, e.lon], 7);
  addQuakes(map, [e]);
}

function pagePipeline(app) {
  const h = D.health, last = h.last_run || {}, d = h.last_14_days, st = h.storage;
  const names = { emsc: "EMSC · REST + WebSocket", usgs: "USGS · REST API", geonames: "GeoNames · файл" };
  const runs = [...D.runs].reverse().slice(0, 40);
  app.innerHTML = `
    <div><h1 class="page">Пайплайн</h1>
    <p class="lede">Состояние дата-инженерного ядра: когда был последний запуск, сколько данных пришло, какие проверки прошли и когда что ломалось. При сбое автоматически открывается <a href="${REPO}/issues?q=label%3Apipeline-alert" target="_blank" rel="noopener">issue в GitHub</a>, при восстановлении оно закрывается.</p></div>
    <section class="kpis">
      <div class="kpi"><div class="label">Последний запуск</div><div class="value status ${esc(last.status)}">${esc(last.status || "—")}</div><div class="hint">${ago(last.finished_at)} · ${last.duration_s ?? "—"} с</div></div>
      <div class="kpi"><div class="label">Событий в итоговом слое</div><div class="value">${st.events_total}</div><div class="hint">raw ${st.raw_files} файлов · ${bytes(st.raw_bytes)}</div></div>
      <div class="kpi"><div class="label">Запусков за 14 дней</div><div class="value">${d.runs}</div><div class="hint">сбоев ${d.failed} · частичных ${d.partial}</div></div>
      <div class="kpi"><div class="label">Среднее время</div><div class="value">${d.avg_duration_s ?? "—"} с</div><div class="hint">загружено строк: ${d.rows_loaded}</div></div>
    </section>
    <section class="grid even">
      <div class="card"><h2>Источники <span>свежесть: устарел, если нет успешной загрузки ${3} ч</span></h2><div class="body rows">
        ${Object.entries(h.sources).map(([k, s]) => `<div class="row"><span><span class="dot ${s.stale ? "bad" : "ok"}" style="display:inline-block;margin-right:8px"></span>${names[k] || k}</span><span class="sub">${s.stale ? "устарел · " : ""}${ago(s.last_success)}</span></div>`).join("")}
      </div>
      <h2 style="border-top:1px solid var(--line)">Задачи последнего запуска <span class="mono">${esc(last.run_id || "")}</span></h2>
      <table>${Object.entries(last.tasks || {}).map(([n, t]) => `<tr><td class="mono">${esc(n)}</td><td class="${esc(t.status)}">${esc(t.status)}</td><td class="mono">${t.duration_s}s</td></tr>`).join("")}</table></div>
      <div class="card"><h2>Проверки качества <span>блокируют публикацию при провале</span></h2>
      <table>${(last.checks || []).map((c) => `<tr><td class="mono">${esc(c.name)}</td><td class="${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</td><td class="sub">${esc(c.detail)}</td></tr>`).join("") || '<tr><td class="sub">нет данных</td></tr>'}</table>
      <div class="body actions"><a class="btn" target="_blank" rel="noopener" href="${REPO}/actions">Запуски в GitHub Actions ↗</a><a class="btn" target="_blank" rel="noopener" href="${REPO}/tree/data">Хранилище (ветка data) ↗</a></div></div>
    </section>
    <section class="card"><h2>История запусков <span>последние ${runs.length} из ${D.runs.length}</span></h2><div style="overflow:auto"><table>
      <tr><th>Запуск</th><th>Режим</th><th>Начало</th><th>Статус</th><th>Строк EMSC / USGS</th><th>Объём</th><th>Время</th><th>Проблема</th></tr>
      ${runs.map((r) => `<tr><td class="mono">${esc(r.run_id)}</td><td>${esc(r.mode)}</td><td>${fmt(r.started_at)}</td><td class="status ${esc(r.status)}">${esc(r.status)}</td>
        <td class="mono">${r.rows_raw?.emsc ?? "—"} / ${r.rows_raw?.usgs ?? "—"}</td><td class="mono">${bytes(r.bytes_raw || 0)}</td><td class="mono">${r.duration_s}s</td>
        <td class="sub">${esc((r.checks_failed || []).length ? "проверки: " + r.checks_failed.join(", ") : (r.error || ""))}</td></tr>`).join("")}
    </table></div></section>`;
}

function pageAbout(app) {
  app.innerHTML = `
    <section class="card"><div class="body prose">
      <h1 class="page">О проекте</h1>
      <p class="lede">Семестровый проект по курсу Data Engineering, 2026. Команда: <b>Насиров Музаффар, Алимран Аскеров, Аширов Фарид</b>.</p>
      <h3>Зачем</h3>
      <p>После ощутимого толчка в Баку люди ищут ответ в чатах и новостях: где это было, какой силы, насколько далеко. Новости приходят через 20–60 минут, а сайты агентств EMSC и USGS дают разные цифры по одному событию. Quake Radar сводит их в одну проверенную ленту с расстоянием до дома.</p>
      <h3>Как это работает</h3>
      <ul>
        <li><b>Источники разных типов:</b> REST API EMSC и USGS (каждые 30 минут), поток EMSC по WebSocket и справочник городов GeoNames (файл, раз в 30 дней).</li>
        <li><b>Сырой слой</b> хранит ответы как есть, с контрольной суммой. Из него всегда можно всё пересобрать или догрузить прошлый период (backfill).</li>
        <li><b>Сведение агентств:</b> записи EMSC и USGS — одно землетрясение, если разница во времени до 60 секунд и в расстоянии до 100 км.</li>
        <li><b>Шесть проверок качества</b> на каждой загрузке. Если хоть одна провалена, сайт продолжает показывать прежние проверенные данные.</li>
        <li><b>Инкрементальность:</b> загружаются только новые и уточнённые записи. Повторный запуск не создаёт дублей.</li>
      </ul>
      <img class="arch" src="assets/arch.png" alt="Архитектура: источники, слои хранения, DAG и потребители">
      <h3>Технологии</h3>
      <p>Python, DuckDB и Parquet для трансформаций, собственный DAG-оркестратор, GitHub Actions для расписания, ветка git <code class="mono">data</code> как хранилище, Vercel для сайта, Leaflet и Chart.js для визуализации.</p>
      <div class="actions"><a class="btn primary" href="${REPO}" target="_blank" rel="noopener">Исходный код ↗</a><a class="btn" href="#/pipeline">Мониторинг пайплайна</a></div>
    </div></section>`;
}

// ------------------------------------------------------------------ router
const routes = { "": pageHome, alerts: pageAlerts, event: pageEvent, pipeline: pagePipeline, about: pageAbout };
function route() {
  const [, name = "", arg] = location.hash.replace(/^#/, "").split("/");
  const page = routes[name] || pageHome;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (name || "home")));
  maps.forEach((m) => m.remove()); maps = [];
  page($("#app"), arg);
  window.scrollTo(0, 0);
}

(async () => {
  try { await loadAll(); } catch (e) {
    $("#app").innerHTML = `<div class="card empty">Не удалось загрузить данные: ${esc(e.message)}</div>`; return;
  }
  renderChrome();
  window.addEventListener("hashchange", route);
  route();
})();
