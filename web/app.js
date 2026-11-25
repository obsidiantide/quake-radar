// Quake Radar website. Reads the serve layer written by the pipeline and renders five pages
// with hash routing: #/  #/alerts  #/event/<id>  #/pipeline  #/about
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
const BASE = isLocal ? "data/" : (window.QUAKE_DATA_URL || "data/");
const REPO = "https://github.com/obsidiantide/quake-radar";
const NEAR_KM = 300, NEAR_MAG = 3.5, STRONG_MAG = 5.0;
const COUNTRIES = { AZ: "Азербайджан", TR: "Турция", IR: "Иран", GE: "Грузия", AM: "Армения", RU: "Россия", IQ: "Ирак",
  TM: "Туркменистан", KZ: "Казахстан", SY: "Сирия", UZ: "Узбекистан" };

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const magHex = (m) => m >= 5 ? "#f85149" : m >= 4 ? "#ff7a45" : m >= 3 ? "#e3b341" : "#58a6ff";
const fmt = (iso, opts = {}) => new Date(iso).toLocaleString("ru-RU", { timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", ...opts }) + " UTC";
const ago = (iso) => {
  if (!iso) return "никогда";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  const d = Math.round(s / 86400);
  return `${d} ${d % 10 === 1 && d % 100 !== 11 ? "день" : d % 10 >= 2 && d % 10 <= 4 && (d % 100 < 10 || d % 100 >= 20) ? "дня" : "дней"} назад`;
};
const bytes = (n) => n > 1e6 ? (n / 1e6).toFixed(1) + " МБ" : (n / 1e3).toFixed(0) + " КБ";
const evUrl = (e) => `#/event/${encodeURIComponent(e.event_id)}`;
const country = (cc) => COUNTRIES[cc] || cc || "—";
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

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
  if (!D.alerts) {
    D.alerts = D.events.filter((e) => e.type === "earthquake" && ((e.home_km <= NEAR_KM && e.mag >= NEAR_MAG) || e.mag >= STRONG_MAG))
      .map((e) => ({ ...e, reason: e.home_km <= NEAR_KM && e.mag >= NEAR_MAG ? "near" : "strong" }));
  }
  D.quakes = D.events.filter((e) => e.type === "earthquake");
  D.byId = Object.fromEntries([...D.alerts, ...D.events].map((e) => [e.event_id, e]));
  // continuous day series for sparklines and the activity chart
  const byDay = Object.fromEntries(D.daily.map((d) => [d.day, d]));
  const days = [];
  const end = new Date(); end.setUTCHours(0, 0, 0, 0);
  const start = D.daily.length ? new Date(D.daily[0].day + "T00:00:00Z") : new Date(end - 30 * 864e5);
  for (let t = start.getTime(); t <= end.getTime(); t += 864e5) {
    const k = new Date(t).toISOString().slice(0, 10);
    days.push({ day: k, n: byDay[k]?.n || 0, max_mag: byDay[k]?.max_mag || 0 });
  }
  D.days = days;
}

// ------------------------------------------------------------------ small visual helpers
function spark(values, color = "#ff7a45") {
  if (!values.length) return "";
  const w = 200, h = 34, mx = Math.max(...values, 1);
  const pts = values.map((v, i) => [i * (w / Math.max(values.length - 1, 1)), h - 3 - (v / mx) * (h - 8)]);
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const id = "g" + Math.random().toString(36).slice(2, 7);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${line} L${w} ${h} L0 ${h} Z" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>`;
}
function bars(rows, colorFn) {
  const mx = Math.max(...rows.map((r) => r.v), 1);
  return `<div class="bars">${rows.map((r, i) => `<div class="bar-row"><span class="lbl">${esc(r.label)}</span>
    <span class="trk"><i style="width:${(r.v / mx * 100).toFixed(1)}%;background:${colorFn(r, i)};animation-delay:${i * 60}ms"></i></span>
    <span class="val">${r.v}</span></div>`).join("")}</div>`;
}
const nearBar = (km) => `<div class="near"><i style="width:${Math.max(4, 100 - Math.min(km, 1000) / 10).toFixed(0)}%"></i></div>`;

// ------------------------------------------------------------------ shared chrome
function renderChrome() {
  const last = D.health.last_run || {};
  const cls = { success: "ok", partial: "warn", failed: "bad" }[last.status] || "";
  $("#status-pill").innerHTML = `<span class="dot ${cls}"></span><span>обновлено ${ago(D.health.last_success_at)}</span>`;
  const recent = D.alerts.filter((a) => Date.now() - new Date(a.t) < 7 * 864e5);
  const badge = $("#nav-alerts");
  badge.hidden = !recent.length; badge.textContent = recent.length;
  const banner = $("#banner");
  const latest = D.alerts.find((a) => Date.now() - new Date(a.t) < 2 * 864e5);
  if (latest) {
    banner.hidden = false;
    banner.innerHTML = `<span class="dot bad"></span><span><b>M${latest.mag.toFixed(1)}</b> · ${esc(latest.place || latest.city)} · ${Math.round(latest.home_km)} км от Баку · ${ago(latest.t)}</span><a href="${evUrl(latest)}">Открыть событие →</a>`;
  } else if (last.status && last.status !== "success") {
    banner.hidden = false;
    banner.innerHTML = `<span class="dot warn"></span><span>Последний запуск пайплайна: <b>${esc(last.status)}</b>. На сайте последние проверенные данные.</span><a href="#/pipeline">Открыть мониторинг →</a>`;
  } else banner.hidden = true;
}

let maps = [];
function makeMap(el, center = [41, 47.5], zoom = 5) {
  maps.forEach((m) => m.remove()); maps = [];
  const map = L.map(el, { scrollWheelZoom: false, zoomControl: true }).setView(center, zoom);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri", maxZoom: 12 }).addTo(map);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 12, pane: "overlayPane" }).addTo(map);
  const h = D.health.home;
  L.circle([h.lat, h.lon], { radius: NEAR_KM * 1000, color: "#ff7a45", weight: 1.2, dashArray: "4 7", fillColor: "#ff7a45", fillOpacity: .04 }).addTo(map);
  L.circleMarker([h.lat, h.lon], { radius: 5, color: "#fff", weight: 2, fillColor: "#07090d", fillOpacity: 1 }).addTo(map).bindTooltip(`${h.name} · радиус тревог ${NEAR_KM} км`);
  maps.push(map);
  return map;
}
function addQuakes(map, list, { pulseLatest = true } = {}) {
  [...list].reverse().forEach((e) => {
    L.circleMarker([e.lat, e.lon], { radius: Math.max(3.5, (e.mag - 1) * 3.2), color: magHex(e.mag), weight: 1.2, fillColor: magHex(e.mag), fillOpacity: .5 })
      .addTo(map).bindPopup(`<b>M${e.mag.toFixed(1)}</b> · ${esc(e.place || e.city)}<br>${fmt(e.t)} · ${ago(e.t)}<br>${Math.round(e.home_km)} км от Баку<br><a href="${evUrl(e)}">Карточка события →</a>`);
  });
  if (pulseLatest && list.length && !reduceMotion) {
    const e = list[0];
    L.marker([e.lat, e.lon], { icon: L.divIcon({ className: "pulse-icon", html: "<span></span>", iconSize: [14, 14] }), interactive: false, keyboard: false }).addTo(map);
  }
}
function evRow(e, extra = "") {
  return `<a class="ev" href="${evUrl(e)}">
    <div class="mag" style="background:${magHex(e.mag)}">${e.mag.toFixed(1)}</div>
    <div><div class="ttl">${esc(e.place || e.city)} ${extra}</div>
      <div class="t">${ago(e.t)} · ${Math.round(e.city_km)} км от ${esc(e.city)} <span class="src ${e.sources === "emsc+usgs" ? "both" : ""}">${esc(e.sources)}</span></div></div>
    <div class="km"><b>${Math.round(e.home_km)}</b> <span class="t">км</span>${nearBar(e.home_km)}</div></a>`;
}
const legend = `<span class="legend"><span><i style="background:var(--m2)"></i>&lt;3</span><span><i style="background:var(--m3)"></i>3–4</span><span><i style="background:var(--m4)"></i>4–5</span><span><i style="background:var(--m5)"></i>5+</span></span>`;

// ------------------------------------------------------------------ pages
function pageHome(app) {
  const q = D.quakes;
  const within = (h) => q.filter((e) => Date.now() - new Date(e.t) < h * 3600e3);
  const d1 = within(24), d7 = within(168);
  const near = q.length ? q.reduce((a, b) => (a.home_km < b.home_km ? a : b)) : null;
  const mx = q.length ? q.reduce((a, b) => (a.mag > b.mag ? a : b)) : null;
  const maxOf = (l) => l.length ? `сильнейшее M${Math.max(...l.map((e) => e.mag)).toFixed(1)}` : "тихо";
  const last30 = D.days.slice(-30).map((d) => d.n), last7 = D.days.slice(-7).map((d) => d.n);
  const bins = [[2, 2.5], [2.5, 3], [3, 3.5], [3.5, 4], [4, 4.5], [4.5, 10]];
  const magRows = bins.map(([a, b]) => ({ label: b === 10 ? `M${a}+` : `M${a}–${b}`, v: q.filter((e) => e.mag >= a && e.mag < b).length, m: a }));
  const byCc = {}; q.forEach((e) => { byCc[e.cc] = (byCc[e.cc] || 0) + 1; });
  const ccRows = Object.entries(byCc).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([cc, v]) => ({ label: country(cc), v }));
  const closest = [...q].sort((a, b) => a.home_km - b.home_km).slice(0, 5);

  app.innerHTML = `
    <section class="hero" data-enter>
      <div><div class="eyebrow"><span class="dot ok"></span>Обновляется каждые 30 минут · EMSC + USGS</div>
        <h1 class="page">Землетрясения <em>Кавказа и Каспия</em></h1>
        <p class="lede">Одна проверенная лента из двух сейсмологических агентств: дубли сведены, у каждого толчка есть ближайший город и расстояние до Баку.</p></div>
      <div class="fact">За 30 дней — <b>${q.length} толчков M2+</b>. Ближе всего к Баку: <b>${near ? Math.round(near.home_km) + " км" : "—"}</b>${near ? `, M${near.mag.toFixed(1)} у ${esc(near.city)}, ${ago(near.t)}` : ""}.</div>
    </section>
    <section class="kpis five" data-enter>
      <div class="kpi"><div class="label">За 24 часа</div><div class="value">${d1.length}</div><div class="hint">${maxOf(d1)}</div>${spark(last7)}</div>
      <div class="kpi"><div class="label">За 7 дней</div><div class="value">${d7.length}</div><div class="hint">${maxOf(d7)}</div>${spark(last30)}</div>
      <a class="kpi" href="${near ? evUrl(near) : "#/"}"><div class="label">Ближе всего к Баку</div><div class="value">${near ? Math.round(near.home_km) + " км" : "—"}</div><div class="hint">${near ? `M${near.mag.toFixed(1)} · ${esc(near.city)}` : ""}</div>${nearBar(near ? near.home_km : 1000)}</a>
      <a class="kpi" href="${mx ? evUrl(mx) : "#/"}"><div class="label">Сильнейшее, 30 дней</div><div class="value">M${mx ? mx.mag.toFixed(1) : "—"}</div><div class="hint">${mx ? `${esc(mx.city)}, ${esc(country(mx.cc))}` : ""}</div></a>
      <a class="kpi" href="#/alerts"><div class="label">Тревоги, 90 дней</div><div class="value">${D.alerts.length}</div><div class="hint"><span style="color:#ffb08a">Открыть список →</span></div></a>
    </section>
    <section class="grid">
      <div class="card"><h2>Карта, 30 дней ${legend}</h2><div class="map" id="map" role="region" aria-label="Карта землетрясений"></div></div>
      <div class="card"><h2>Последние события <span>${q.length} за 30 дней</span></h2><div class="list">${q.slice(0, 150).map((e) => evRow(e)).join("") || '<div class="empty">Нет событий</div>'}</div></div>
    </section>
    <section class="grid">
      <div class="card"><h2>Активность по дням <span>столбцы — события M2+, линия — среднее за 7 дней</span></h2><div class="chart-wrap"><canvas id="daily" aria-label="График активности"></canvas></div></div>
      <div class="card"><h2>Сила толчков <span>30 дней</span></h2><div class="body">${bars(magRows, (r) => magHex(r.m + .01))}</div></div>
    </section>
    <section class="grid even">
      <div class="card"><h2>Где трясёт <span>по ближайшему городу, 30 дней</span></h2><div class="body">${bars(ccRows, (r, i) => i === 0 ? "linear-gradient(90deg,#ff4d4d,#ff7a45)" : "#3a4b61")}</div></div>
      <div class="card"><h2>Ближе всего к Баку <span>30 дней</span></h2>${closest.map((e) => evRow(e)).join("")}</div>
    </section>`;

  addQuakes(makeMap($("#map")), q);
  Chart.defaults.color = "#7d8a9c"; Chart.defaults.font.family = "Inter";
  const days = D.days.slice(-120);
  const avg = days.map((_, i) => { const w = days.slice(Math.max(0, i - 6), i + 1); return +(w.reduce((s, d) => s + d.n, 0) / w.length).toFixed(2); });
  new Chart($("#daily"), {
    data: {
      labels: days.map((d) => d.day),
      datasets: [
        { type: "line", data: avg, borderColor: "#e8eef5", borderWidth: 1.6, pointRadius: 0, tension: .35, order: 0 },
        { type: "bar", data: days.map((d) => d.n), backgroundColor: days.map((d) => d.n ? magHex(d.max_mag) + "cc" : "transparent"), borderRadius: 3, barPercentage: .9, categoryPercentage: .9, order: 1 },
      ],
    },
    options: { maintainAspectRatio: false, animation: reduceMotion ? false : { duration: 700 },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (c) => new Date(c[0].label + "T00:00:00Z").toLocaleDateString("ru-RU", { day: "numeric", month: "long" }),
        label: (c) => c.datasetIndex === 1 ? `${c.raw} событий, сильнейшее M${days[c.dataIndex].max_mag}` : `среднее за 7 дней: ${c.raw}` } } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 6, callback: (v, i) => new Date(days[i].day + "T00:00:00Z").toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) } },
        y: { grid: { color: "#1a2431" }, beginAtZero: true, ticks: { precision: 0 } } } },
  });
}

function pageAlerts(app) {
  const a = D.alerts;
  app.innerHTML = `
    <section class="hero" data-enter>
      <div><div class="eyebrow">Тревоги · 90 дней</div><h1 class="page">Толчки, которые <em>стоит заметить</em></h1>
      <p class="lede"><b>M${NEAR_MAG}+ в радиусе ${NEAR_KM} км от Баку</b> или <b>M${STRONG_MAG}+ в любой точке региона</b>. Тревога моложе 48 часов показывается баннером на всех страницах.</p></div>
      <div class="fact">Тревог за 90 дней: <b>${a.length}</b>. ${a.length ? `Последняя — <b>${ago(a[0].t)}</b>, M${a[0].mag.toFixed(1)}, ${Math.round(a[0].home_km)} км от Баку.` : "Было спокойно."}</div>
    </section>
    <section class="grid">
      <div class="card"><h2>Где были тревоги ${legend}</h2><div class="map" id="map" role="region" aria-label="Карта тревог"></div></div>
      <div class="card"><h2>Список <span>${a.length} за 90 дней</span></h2><div class="list">${a.map((e) =>
        evRow(e, `<span class="tag ${e.reason === "near" ? "near-t" : "strong"}">${e.reason === "near" ? "рядом с Баку" : "сильное"}</span>`)).join("") || '<div class="empty">За 90 дней тревог не было</div>'}</div></div>
    </section>`;
  addQuakes(makeMap($("#map")), a);
}

function pageEvent(app, id) {
  const e = D.byId[decodeURIComponent(id || "")];
  if (!e) { app.innerHTML = `<div class="card empty">Событие не найдено: оно старше 30 дней или ещё не загружено. <a href="#/">На главную</a></div>`; return; }
  const both = e.mag_emsc != null && e.mag_usgs != null;
  const links = [
    e.emsc_id && `<a class="btn" target="_blank" rel="noopener" href="https://www.seismicportal.eu/eventdetails.html?unid=${encodeURIComponent(e.emsc_id)}">Открыть в EMSC ↗</a>`,
    e.usgs_id && `<a class="btn" target="_blank" rel="noopener" href="https://earthquake.usgs.gov/earthquakes/eventpage/${encodeURIComponent(e.usgs_id)}">Открыть в USGS ↗</a>`,
  ].filter(Boolean).join("");
  const cmpRows = both ? [{ label: "EMSC", v: e.mag_emsc, m: e.mag_emsc }, { label: "USGS", v: e.mag_usgs, m: e.mag_usgs }] : [];
  app.innerHTML = `
    <div class="actions"><a class="btn" href="javascript:history.back()">← Назад</a></div>
    <section class="card" data-enter><div class="body" style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
      <div class="mag lg" style="background:${magHex(e.mag)}">${e.mag.toFixed(1)}</div>
      <div style="flex:1;min-width:240px"><div class="eyebrow" style="margin-bottom:4px">${ago(e.t)}</div><h1 class="page" style="font-size:clamp(24px,3vw,36px)">${esc(e.place || e.city)}</h1>
        <div class="sub">${fmt(e.t, { year: "numeric", second: "2-digit" })} · ${esc(e.event_id)}</div></div>
      <div class="actions">${links}</div></div></section>
    <section class="grid">
      <div class="card"><h2>На карте <span>пунктир — ${NEAR_KM} км от Баку</span></h2><div class="map small" id="map" role="region" aria-label="Карта события"></div></div>
      <div class="card"><h2>Параметры</h2><div class="body facts">
        <div class="fct"><div class="label">До Баку</div><div class="value">${Math.round(e.home_km)} км</div>${nearBar(e.home_km)}</div>
        <div class="fct"><div class="label">Магнитуда</div><div class="value">M${e.mag.toFixed(1)} <span class="sub">${esc(e.mag_type || "")}</span></div></div>
        <div class="fct"><div class="label">Глубина</div><div class="value">${e.depth ?? "?"} км</div></div>
        <div class="fct"><div class="label">Ближайший город</div><div class="value">${esc(e.city)}</div><div class="sub">${Math.round(e.city_km)} км · ${esc(country(e.cc))}</div></div>
        <div class="fct"><div class="label">Координаты</div><div class="value mono" style="font-size:15px">${e.lat}, ${e.lon}</div></div>
        <div class="fct"><div class="label">Источники</div><div class="value"><span class="src ${both ? "both" : ""}" style="font-size:13px">${esc(e.sources)}</span></div></div>
      </div>
      <h2 style="border-top:1px solid var(--line)">Что говорят агентства</h2><div class="body">
        ${both ? `<div class="compare">${bars(cmpRows, (r) => magHex(r.m))}</div>
          <p class="sub" style="margin:12px 0 0">Оба агентства сообщили об этом толчке, пайплайн свёл их в одну запись. Итог — решение USGS, если оно проверено (reviewed), иначе EMSC.</p>`
          : `<p class="sub" style="margin:0">Пока есть только в каталоге ${esc(e.sources.toUpperCase())}. USGS обычно публикует толчки от ~M4.</p>`}
      </div></div>
    </section>`;
  addQuakes(makeMap($("#map"), [e.lat, e.lon], 7), [e]);
}

function dagSvg(tasks) {
  const N = {
    extract_usgs: [10, 14], extract_emsc: [10, 74], extract_geonames: [10, 134], stage_cities: [196, 134],
    transform: [382, 74], quality_checks: [568, 74], publish: [754, 74], export_events: [940, 30], export_alerts: [940, 118],
  };
  const E = [["extract_usgs", "transform"], ["extract_emsc", "transform"], ["extract_geonames", "stage_cities"], ["stage_cities", "transform"],
    ["transform", "quality_checks"], ["quality_checks", "publish"], ["publish", "export_events"], ["publish", "export_alerts"]];
  const W = 160, H = 46;
  const path = ([a, b]) => { const [x1, y1] = N[a], [x2, y2] = N[b]; const sx = x1 + W, sy = y1 + H / 2, ex = x2, ey = y2 + H / 2, mx = (sx + ex) / 2;
    return `M${sx} ${sy} C${mx} ${sy} ${mx} ${ey} ${ex} ${ey}`; };
  const st = (n) => tasks[n]?.status || "pending";
  return `<svg class="dag" viewBox="0 0 1110 196" role="img" aria-label="Граф задач последнего запуска">
    ${E.map((e) => `<path class="edge" d="${path(e)}"/>`).join("")}
    ${E.map((e, i) => `<path class="flow ${st(e[0]) === "success" && st(e[1]) === "success" ? "" : "off"}" d="${path(e)}" style="animation-delay:${i * .25}s"/>`).join("")}
    ${Object.entries(N).map(([n, [x, y]]) => `<g class="node ${st(n)}" transform="translate(${x},${y})">
      <rect width="${W}" height="${H}" rx="10"/><circle cx="14" cy="${H / 2}"/>
      <text x="26" y="20">${n}</text><text class="s" x="26" y="36">${esc(st(n))}${tasks[n] ? " · " + tasks[n].duration_s + " с" : ""}</text></g>`).join("")}
  </svg>`;
}

function pagePipeline(app) {
  const h = D.health, last = h.last_run || {}, d = h.last_14_days, st = h.storage;
  const names = { emsc: "EMSC · REST + WebSocket", usgs: "USGS · REST API", geonames: "GeoNames · файл" };
  const runs = [...D.runs].reverse().slice(0, 40);
  const durations = D.runs.slice(-30).map((r) => r.duration_s);
  app.innerHTML = `
    <section class="hero" data-enter>
      <div><div class="eyebrow">Дата-инженерное ядро</div><h1 class="page">Пайплайн <em>под капотом</em></h1>
      <p class="lede">Когда был последний запуск, сколько данных пришло, какие проверки прошли и когда что ломалось. При сбое открывается <a href="${REPO}/issues?q=label%3Apipeline-alert" target="_blank" rel="noopener">issue в GitHub</a>, при восстановлении оно закрывается.</p></div>
      <div class="fact">Последний запуск <span class="chip ${esc(last.status)}">${esc(last.status || "—")}</span> ${ago(last.finished_at)}, за <b>${last.duration_s ?? "—"} с</b>. Проверок пройдено: <b>${(last.checks || []).filter((c) => c.passed).length} из ${(last.checks || []).length}</b>.</div>
    </section>
    <section class="kpis four" data-enter>
      <div class="kpi"><div class="label">Событий в итоговом слое</div><div class="value">${st.events_total}</div><div class="hint">raw: ${st.raw_files} файлов · ${bytes(st.raw_bytes)}</div></div>
      <div class="kpi"><div class="label">Запусков за 14 дней</div><div class="value">${d.runs}</div><div class="hint">сбоев ${d.failed} · частичных ${d.partial}</div></div>
      <div class="kpi"><div class="label">Время запуска</div><div class="value">${d.avg_duration_s ?? "—"} с</div><div class="hint">в среднем; график — последние 30</div>${spark(durations, "#58a6ff")}</div>
      <div class="kpi"><div class="label">Строк загружено</div><div class="value">${d.rows_loaded}</div><div class="hint">за 14 дней</div></div>
    </section>
    <section class="card"><h2>Граф последнего запуска <span class="mono">${esc(last.run_id || "")}</span></h2><div class="body">${dagSvg(last.tasks || {})}</div></section>
    <section class="grid even">
      <div class="card"><h2>Источники <span>устарел, если нет успешной загрузки 3 ч</span></h2><div class="body rows">
        ${Object.entries(h.sources).map(([k, s]) => `<div class="row"><span style="display:flex;align-items:center;gap:10px"><span class="dot ${s.stale ? "bad" : "ok"}"></span>${names[k] || k}</span><span class="sub">${s.stale ? "устарел · " : ""}${ago(s.last_success)}</span></div>`).join("")}
      </div>
      <div class="body actions" style="border-top:1px solid var(--line)"><a class="btn" target="_blank" rel="noopener" href="${REPO}/actions">Запуски в GitHub Actions ↗</a><a class="btn" target="_blank" rel="noopener" href="${REPO}/tree/data">Хранилище, ветка data ↗</a></div></div>
      <div class="card"><h2>Проверки качества <span>блокируют публикацию при провале</span></h2>
      <table>${(last.checks || []).map((c) => `<tr><td class="mono">${esc(c.name)}</td><td><span class="chip ${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</span></td><td class="sub">${esc(c.detail)}</td></tr>`).join("") || '<tr><td class="sub">нет данных</td></tr>'}</table></div>
    </section>
    <section class="card"><h2>История запусков <span>последние ${runs.length} из ${D.runs.length}</span></h2><div style="overflow:auto"><table>
      <tr><th>Запуск</th><th>Режим</th><th>Начало</th><th>Статус</th><th>Строк EMSC / USGS</th><th>Объём</th><th>Время</th><th>Проблема</th></tr>
      ${runs.map((r) => `<tr><td class="mono">${esc(r.run_id)}</td><td>${esc(r.mode)}</td><td>${fmt(r.started_at)}</td><td><span class="chip ${esc(r.status)}">${esc(r.status)}</span></td>
        <td class="mono">${r.rows_raw?.emsc ?? "—"} / ${r.rows_raw?.usgs ?? "—"}</td><td class="mono">${bytes(r.bytes_raw || 0)}</td><td class="mono">${r.duration_s} с</td>
        <td class="sub">${esc((r.checks_failed || []).length ? "проверки: " + r.checks_failed.join(", ") : (r.error || ""))}</td></tr>`).join("")}
    </table></div></section>`;
}

function pageAbout(app) {
  app.innerHTML = `
    <section class="hero" data-enter>
      <div><div class="eyebrow">О проекте</div><h1 class="page">Сделано командой <em>за семестр</em></h1>
      <p class="lede">Проект по курсу Data Engineering, 2026. Насиров Музаффар, Алимран Аскеров, Аширов Фарид.</p></div>
    </section>
    <section class="card"><div class="body prose">
      <h3>Зачем</h3>
      <p>После ощутимого толчка в Баку люди ищут ответ в чатах и новостях: где это было, какой силы, насколько далеко. Новости приходят через 20–60 минут, а сайты EMSC и USGS дают разные цифры по одному событию. Quake Radar сводит их в одну проверенную ленту с расстоянием до дома.</p>
      <h3>Как это работает</h3>
      <ul>
        <li><b>Источники разных типов:</b> REST API EMSC и USGS (каждые 30 минут), поток EMSC по WebSocket, справочник городов GeoNames (файл, раз в 30 дней).</li>
        <li><b>Сырой слой</b> хранит ответы как есть, с контрольной суммой. Из него всегда можно всё пересобрать или догрузить прошлый период.</li>
        <li><b>Сведение агентств:</b> записи EMSC и USGS — одно землетрясение, если разница во времени до 60 секунд и в расстоянии до 100 км.</li>
        <li><b>Шесть проверок качества</b> на каждой загрузке. Если хоть одна провалена, сайт показывает прежние проверенные данные.</li>
        <li><b>Инкрементальность:</b> загружаются только новые и уточнённые записи; повторный запуск не создаёт дублей.</li>
      </ul>
      <img class="arch" src="assets/arch.png" alt="Архитектура: источники, слои хранения, граф задач и потребители">
      <h3>Технологии</h3>
      <p>Python, DuckDB и Parquet для трансформаций, собственный DAG-оркестратор, GitHub Actions для расписания, ветка git <span class="mono">data</span> как хранилище, Vercel для сайта, Leaflet и Chart.js для визуализации.</p>
      <div class="actions"><a class="btn primary" href="${REPO}" target="_blank" rel="noopener">Открыть исходный код ↗</a><a class="btn" href="#/pipeline">Открыть мониторинг</a></div>
    </div></section>`;
}

// ------------------------------------------------------------------ router
const routes = { "": pageHome, alerts: pageAlerts, event: pageEvent, pipeline: pagePipeline, about: pageAbout };
function route() {
  const [, name = "", arg] = location.hash.replace(/^#/, "").split("/");
  const page = routes[name] || pageHome;
  document.querySelectorAll("#nav a").forEach((a) => { const on = a.dataset.route === (name || "home"); a.classList.toggle("active", on); on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"); });
  maps.forEach((m) => m.remove()); maps = [];
  page($("#app"), arg);
  window.scrollTo(0, 0);
}

$(".skip").addEventListener("click", (e) => { e.preventDefault(); $("#app").focus(); });
(async () => {
  try { await loadAll(); } catch (e) {
    $("#app").innerHTML = `<div class="card empty">Не удалось загрузить данные: ${esc(e.message)}</div>`; return;
  }
  renderChrome();
  window.addEventListener("hashchange", route);
  route();
})();
