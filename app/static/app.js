// ─────────────────────────────────────────────
//  Состояние
// ─────────────────────────────────────────────
let mainMap = null;
let lastResult = null;                 // Последний результат /api/process (для дозагрузки)
let bottlenecks = [];                  // [{name, lat, lon, delay_min}]
let bottleneckMarkers = [];            // Leaflet-маркеры пробочных точек
let selectedStartLocationId = 'gorkogo25';
let brigadeStartPoints = {};           // {B1: {name,address,lat,lon}}
let selectedBrigadeRouteId = null;

const startLocations = [
  {
    id: 'gorkogo25',
    name: 'Горького 25',
    address: 'Иркутск, Горького, д.25',
    lat: 52.284742,
    lon: 104.285432,
  },
  {
    id: 'railway68',
    name: '2-я Железнодорожная 68',
    address: 'Иркутск, 2-я Железнодорожная, д.68',
    lat: 52.278821,
    lon: 104.24959,
  },
];

const brigadeColors = [
  '#e63946', '#2a9d8f', '#e9c46a', '#264653', '#f4a261',
  '#6a4c93', '#1982c4', '#8ac926', '#ff595e', '#6a994e',
];

// ─────────────────────────────────────────────
//  DOM refs
// ─────────────────────────────────────────────
const statusNode   = document.getElementById('status');
const resultRoot   = document.getElementById('resultRoot');
const processBtn   = document.getElementById('processBtn');
const validateBtn  = document.getElementById('validateBtn');
const appendBtn    = document.getElementById('appendBtn');
const recalcBtn    = document.getElementById('recalcBtn');

// ─────────────────────────────────────────────
//  Инициализация карты
// ─────────────────────────────────────────────
function initMap() {
  if (mainMap) return;
  mainMap = L.map('mainMap').setView([52.297, 104.297], 12);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OSM &copy; CartoDB',
    maxZoom: 19,
  }).addTo(mainMap);

  // Клик по карте — предлагаем добавить пробочную точку
  mainMap.on('click', (e) => {
    const { lat, lng } = e.latlng;
    document.getElementById('bnLat').value = lat.toFixed(5);
    document.getElementById('bnLon').value = lng.toFixed(5);
    document.getElementById('bnMapHint').textContent =
      `✅ Координаты взяты с карты: ${lat.toFixed(5)}, ${lng.toFixed(5)}. Заполните название и нажмите «Добавить точку».`;
  });
}

initMap();

// ─────────────────────────────────────────────
//  Стартовые точки бригад
// ─────────────────────────────────────────────
function brigadeIds() {
  const count = parseInt(document.getElementById('brigadeCount').value) || 0;
  return Array.from({ length: count }, (_, i) => `B${i + 1}`);
}

function getStartLocation(id) {
  return startLocations.find(item => item.id === id) || startLocations[0];
}

function cloneStartLocation(location) {
  return {
    name: location.name,
    address: location.address,
    lat: location.lat,
    lon: location.lon,
  };
}

function renderStartAddressButtons() {
  const root = document.getElementById('startAddressButtons');
  if (!root) return;
  root.innerHTML = startLocations.map(location => `
    <button
      type="button"
      class="btnPreset ${selectedStartLocationId === location.id ? 'btnPresetActive' : ''}"
      onclick="selectStartLocation('${location.id}')"
      title="${location.address}"
    >
      ${location.name}
    </button>
    <button
      type="button"
      class="btnPreset"
      onclick="assignStartToAll('${location.id}')"
      title="Назначить ${location.name} всем бригадам"
    >
      ${location.name} всем
    </button>
  `).join('');
}

function selectStartLocation(id) {
  selectedStartLocationId = id;
  renderStartAddressButtons();
}

function assignStartToAll(id = selectedStartLocationId) {
  const location = getStartLocation(id);
  brigadeIds().forEach(bid => {
    brigadeStartPoints[bid] = cloneStartLocation(location);
  });
  renderBrigadeStartList();
  setStatus(`Точка "${location.name}" назначена всем бригадам.`);
}

function assignSelectedBrigades() {
  const selected = selectedBrigadeIds();
  if (!selected.length) return setStatus('Выберите бригады галочками.');
  const location = getStartLocation(selectedStartLocationId);
  selected.forEach(bid => {
    brigadeStartPoints[bid] = cloneStartLocation(location);
  });
  renderBrigadeStartList();
  setStatus(`Точка "${location.name}" назначена выбранным бригадам: ${selected.join(', ')}.`);
}

function clearSelectedBrigades() {
  const selected = selectedBrigadeIds();
  if (!selected.length) return setStatus('Выберите бригады галочками.');
  selected.forEach(bid => {
    delete brigadeStartPoints[bid];
  });
  renderBrigadeStartList();
  setStatus(`Очищены стартовые точки: ${selected.join(', ')}.`);
}

function selectedBrigadeIds() {
  return Array.from(document.querySelectorAll('.brigadeStartCheck:checked')).map(el => el.value);
}

function setBrigadeStartFromSelect(brigadeId, locationId) {
  if (!locationId) {
    delete brigadeStartPoints[brigadeId];
  } else {
    brigadeStartPoints[brigadeId] = cloneStartLocation(getStartLocation(locationId));
  }
  renderBrigadeStartList();
}

function renderBrigadeStartList() {
  const root = document.getElementById('brigadeStartList');
  if (!root) return;

  const ids = brigadeIds();
  Object.keys(brigadeStartPoints).forEach(bid => {
    if (!ids.includes(bid)) delete brigadeStartPoints[bid];
  });

  root.innerHTML = `
    <table class="stickyTable">
      <thead>
        <tr><th></th><th>Бригада</th><th>Текущая точка</th><th>Адрес</th><th>Координаты</th></tr>
      </thead>
      <tbody>
        ${ids.map(bid => {
          const point = brigadeStartPoints[bid];
          const selectedId = startLocations.find(loc => point && loc.lat === point.lat && loc.lon === point.lon)?.id || '';
          return `
            <tr class="${point ? '' : 'rowMissingStart'}">
              <td><input type="checkbox" class="brigadeStartCheck" value="${bid}"></td>
              <td>${bid}</td>
              <td>
                <select onchange="setBrigadeStartFromSelect('${bid}', this.value)">
                  <option value="">Не выбрано</option>
                  ${startLocations.map(loc => `<option value="${loc.id}" ${selectedId === loc.id ? 'selected' : ''}>${loc.name}</option>`).join('')}
                </select>
              </td>
              <td>${point ? escapeHtml(point.address) : '<span class="missingText">Нужно выбрать</span>'}</td>
              <td>${point ? `${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}` : '—'}</td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>
  `;
}

function validateBrigadeStartPoints() {
  const missing = brigadeIds().filter(bid => !brigadeStartPoints[bid]);
  if (!missing.length) return true;
  setStatus(`❌ Укажите текущую стартовую точку для бригад: ${missing.join(', ')}.`);
  return false;
}

document.getElementById('brigadeCount').addEventListener('change', () => {
  renderBrigadeStartList();
});

renderStartAddressButtons();
renderBrigadeStartList();

// ─────────────────────────────────────────────
//  Пробочные точки
// ─────────────────────────────────────────────
function addPreset(name, lat, lon, delay_min) {
  addBottleneck({ name, lat, lon, delay_min });
}

function addBottleneckFromForm() {
  const name      = document.getElementById('bnName').value.trim();
  const lat       = parseFloat(document.getElementById('bnLat').value);
  const lon       = parseFloat(document.getElementById('bnLon').value);
  const delay_min = parseInt(document.getElementById('bnDelay').value) || 15;

  if (!name) { alert('Введите название точки'); return; }
  if (isNaN(lat) || isNaN(lon)) { alert('Введите координаты (можно кликнуть по карте)'); return; }

  addBottleneck({ name, lat, lon, delay_min });

  // Очищаем форму
  document.getElementById('bnName').value = '';
  document.getElementById('bnLat').value = '';
  document.getElementById('bnLon').value = '';
}

function addBottleneck(bn) {
  // Дедупликация по имени
  if (bottlenecks.find(b => b.name === bn.name)) return;

  bottlenecks.push(bn);

  // Маркер на карте
  const warningIcon = L.divIcon({
    className: '',
    html: `<div style="font-size:22px; line-height:1;">🚧</div>`,
    iconAnchor: [11, 11],
  });
  const marker = L.marker([bn.lat, bn.lon], { icon: warningIcon })
    .addTo(mainMap)
    .bindPopup(`<b>🚧 ${bn.name}</b><br>Задержка: +${bn.delay_min} мин`);
  bottleneckMarkers.push({ name: bn.name, marker });

  renderBottleneckList();
}

function removeBottleneck(name) {
  bottlenecks = bottlenecks.filter(b => b.name !== name);
  const idx = bottleneckMarkers.findIndex(bm => bm.name === name);
  if (idx !== -1) {
    mainMap.removeLayer(bottleneckMarkers[idx].marker);
    bottleneckMarkers.splice(idx, 1);
  }
  renderBottleneckList();
}

function renderBottleneckList() {
  const el = document.getElementById('bottleneckList');
  if (!bottlenecks.length) {
    el.innerHTML = '<p class="hint">Пробочные точки не добавлены.</p>';
    return;
  }
  el.innerHTML = `
    <table>
      <thead><tr><th>Название</th><th>Широта</th><th>Долгота</th><th>Задержка</th><th></th></tr></thead>
      <tbody>
        ${bottlenecks.map(b => `
          <tr>
            <td>${b.name}</td>
            <td>${b.lat.toFixed(4)}</td>
            <td>${b.lon.toFixed(4)}</td>
            <td>+${b.delay_min} мин</td>
            <td><button onclick="removeBottleneck('${b.name}')" style="padding:4px 8px; font-size:12px;">✕</button></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

renderBottleneckList();

// ─────────────────────────────────────────────
//  Кнопки
// ─────────────────────────────────────────────
validateBtn.addEventListener('click', async () => {
  const file = getFile();
  if (!file) return setStatus('Выберите файл.');
  const form = new FormData();
  form.append('file', file);

  setStatus('Проверяем файл...');
  const res  = await fetch('/api/validate', { method: 'POST', body: form });
  const data = await res.json();

  if (!data.ok) {
    processBtn.disabled = true;
    setStatus('❌ ' + data.errors.join('\n'));
    return;
  }
  processBtn.disabled = false;
  setStatus('✅ Проверка пройдена. Можно планировать.');
});

processBtn.addEventListener('click', async () => {
  await processCurrentFile('⏳ Строим маршруты (геокодирование + OSRM)...');
});

recalcBtn.addEventListener('click', async () => {
  await processCurrentFile('⏳ Пересчитываем маршрут с текущими настройками...');
});

async function processCurrentFile(message) {
  const file = getFile();
  if (!file) return setStatus('Выберите файл.');
  if (!validateBrigadeStartPoints()) return;
  setStatus(message);

  const form = buildProcessForm(file);
  const res  = await fetch('/api/process', { method: 'POST', body: form });
  const data = await res.json();

  if (!data.ok) {
    setStatus('❌ ' + (data.errors || ['Ошибка']).join('\n'));
    return;
  }

  lastResult = data.result;
  appendBtn.disabled = false;
  recalcBtn.disabled = false;
  renderResult(data.result);
}

appendBtn.addEventListener('click', async () => {
  const file = getFile();
  if (!file) return setStatus('Выберите файл с новыми задачами для дозагрузки.');
  if (!lastResult) return setStatus('Сначала выполните основное планирование.');
  if (!validateBrigadeStartPoints()) return;

  setStatus('⏳ Дозагружаем новые задачи...');

  const form = buildAppendForm(file);
  const res  = await fetch('/api/append', { method: 'POST', body: form });
  const data = await res.json();

  if (!data.ok) {
    setStatus('❌ ' + (data.errors || ['Ошибка']).join('\n'));
    return;
  }

  lastResult = data.result;
  renderResult(data.result);
});

// ─────────────────────────────────────────────
//  Формы
// ─────────────────────────────────────────────
function getFile() {
  return document.getElementById('fileInput').files[0];
}

function buildProcessForm(file) {
  const f = new FormData();
  f.append('file', file);
  f.append('brigade_count',        document.getElementById('brigadeCount').value);
  f.append('duty_brigade_id',      document.getElementById('dutyBrigadeId').value);
  f.append('traffic_level',        document.getElementById('trafficLevel').value);
  f.append('base_travel_hours',    document.getElementById('baseTravelHours').value);
  f.append('min_completion_ratio', document.getElementById('minCompletionRatio').value);
  f.append('max_overtime_minutes', document.getElementById('maxOvertimeMinutes').value);
  f.append('cluster_radius_km',    document.getElementById('clusterRadiusKm').value);
  f.append('status_filter',        document.getElementById('statusFilter').value);
  f.append('bottlenecks',          JSON.stringify(bottlenecks));
  f.append('brigade_start_points', JSON.stringify(brigadeStartPoints));
  return f;
}

function buildAppendForm(file) {
  const f = new FormData();
  f.append('file', file);
  f.append('existing_result',   JSON.stringify(lastResult));
  f.append('brigade_count',     document.getElementById('brigadeCount').value);
  f.append('duty_brigade_id',   document.getElementById('dutyBrigadeId').value);
  f.append('traffic_level',     document.getElementById('trafficLevel').value);
  f.append('base_travel_hours', document.getElementById('baseTravelHours').value);
  f.append('cluster_radius_km', document.getElementById('clusterRadiusKm').value);
  f.append('bottlenecks',       JSON.stringify(bottlenecks));
  f.append('brigade_start_points', JSON.stringify(brigadeStartPoints));
  return f;
}

function setStatus(text) {
  statusNode.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatTaskTime(t) {
  if (t.sla_deadline) return t.sla_deadline;
  if (t.visit_window_start && t.visit_window_end) {
    return `${t.visit_window_start} — ${t.visit_window_end}`;
  }
  return t.visit_window_start || '—';
}

function formatSlaStatus(t) {
  if (!t.sla_deadline) return '—';
  if (t.sla_status === 'breach') return '❌ SLA нарушен';
  if (t.sla_status === 'critical') return `❗ ${t.sla_status_text || 'срочно'}`;
  if (t.sla_status === 'warning') return `✅! ${t.sla_status_text || 'меньше 2 часов'}`;
  return `✅ ${t.sla_status_text || 'OK'}`;
}

function slaRowClass(t) {
  if (t.sla_status === 'breach') return 'rowBreach';
  if (t.sla_status === 'critical') return 'rowCritical';
  if (t.sla_status === 'warning') return 'rowWarning';
  return '';
}

function formatAddress(t) {
  const address = t.formatted_address || t.address || '—';
  return t.geocode_status === 'approximate' ? `${address} (поблизости)` : address;
}

function formatOriginalAddress(t) {
  const address = t.address || '—';
  return t.geocode_status === 'approximate' ? `${address} (поблизости)` : address;
}

// ─────────────────────────────────────────────
//  Рендер результата
// ─────────────────────────────────────────────
function renderResult(result) {
  const okText = result.target_achieved ? '✅ достигнута' : '❌ не достигнута';
  setStatus(`Выполнение: ${result.completion_ratio}% (цель ${result.target_ratio}%) — цель ${okText}`);

  renderMainMap(result.brigades);

  if (result.postponed_tasks?.length) {
    renderPostponedTasks(result.postponed_tasks);
  } else {
    const s = document.getElementById('postponedSection');
    if (s) s.style.display = 'none';
  }

  resultRoot.innerHTML = '';
  result.brigades.forEach((b, idx) => {
    const color = brigadeColors[idx % brigadeColors.length];

    const wrapper = document.createElement('div');
    wrapper.className = 'card';
    wrapper.innerHTML = `
      <h3 style="border-left: 4px solid ${color}; padding-left:10px;">
        ${b.brigade_id}${b.is_duty ? ' 🔔 дежурная' : ''} — ${b.tasks.length} задач
      </h3>
    `;

    if (b.route?.enabled) {
      const distText = b.route.total_distance_km != null ? `${b.route.total_distance_km} км` : '';
      const timeText = b.route.total_duration_min != null ? `${b.route.total_duration_min} мин` : '';
      const routeBox = document.createElement('div');
      routeBox.className = 'routeBox';
      routeBox.innerHTML = `
        <div class="routeMain">Маршрут: ${timeText}${distText ? ' · ' + distText : ''}</div>
        ${b.route.note ? `<div class="hint">${b.route.note}</div>` : ''}
      `;
      wrapper.appendChild(routeBox);
    }

    if (!b.tasks.length) {
      wrapper.insertAdjacentHTML('beforeend', '<p class="hint">Нет назначенных задач.</p>');
      resultRoot.appendChild(wrapper);
      return;
    }

    const table = document.createElement('table');
    table.innerHTML = `
      <thead>
        <tr>
          <th>№ задачи</th><th>Вид работ</th><th>Адрес</th><th>Рабочий адрес</th><th>Начало</th><th>Конец</th><th>SLA / интервал</th><th>Статус SLA</th>
        </tr>
      </thead>
      <tbody>
        ${b.tasks.map(t => `
          <tr class="${slaRowClass(t)}">
            <td>${escapeHtml(t.task_id)}</td>
            <td>${escapeHtml(t.task_type)}</td>
            <td>${escapeHtml(formatOriginalAddress(t))}</td>
            <td>${escapeHtml(formatAddress(t))}</td>
            <td>${escapeHtml(t.planned_start)}</td>
            <td>${escapeHtml(t.planned_finish)}</td>
            <td>${escapeHtml(formatTaskTime(t))}</td>
            <td>${escapeHtml(formatSlaStatus(t))}</td>
          </tr>
        `).join('')}
      </tbody>
    `;
    wrapper.appendChild(table);
    resultRoot.appendChild(wrapper);
  });
}

// ─────────────────────────────────────────────
//  Карта — реальные маршруты по дорогам
// ─────────────────────────────────────────────
let routeLayers = [];   // Полилинии маршрутов
let markerLayers = [];  // Маркеры задач

function clearRouteLayers() {
  routeLayers.forEach(item => mainMap.removeLayer(item.layer));
  markerLayers.forEach(item => mainMap.removeLayer(item.marker));
  routeLayers = [];
  markerLayers = [];
}

function selectBrigadeRoute(brigadeId) {
  selectedBrigadeRouteId = selectedBrigadeRouteId === brigadeId ? null : brigadeId;
  updateRouteHighlight();
  renderLegendFromLayers();
}

function updateRouteHighlight() {
  routeLayers.forEach(item => {
    const selected = selectedBrigadeRouteId && item.brigadeId === selectedBrigadeRouteId;
    const dimmed = selectedBrigadeRouteId && item.brigadeId !== selectedBrigadeRouteId;
    item.layer.setStyle({
      color: item.color,
      weight: selected ? 9 : 5,
      opacity: selected ? 1 : (dimmed ? 0.22 : 0.85),
    });
    if (selected) {
      item.layer.bringToFront();
    }
  });

  markerLayers.forEach(item => {
    const selected = selectedBrigadeRouteId && item.brigadeId === selectedBrigadeRouteId;
    const dimmed = selectedBrigadeRouteId && item.brigadeId !== selectedBrigadeRouteId;
    if (item.marker.setOpacity) {
      item.marker.setOpacity(selected ? 1 : (dimmed ? 0.35 : 1));
    }
    if (selected && item.marker.setZIndexOffset) {
      item.marker.setZIndexOffset(1000);
    } else if (item.marker.setZIndexOffset) {
      item.marker.setZIndexOffset(0);
    }
  });
}

function renderLegendFromLayers() {
  const items = routeLayers.map(item => ({
    brigadeId: item.brigadeId,
    taskCount: item.taskCount,
    color: item.color,
    distance: item.distance,
    duration: item.duration,
  }));
  renderLegend(items);
}

function renderMainMap(brigades) {
  clearRouteLayers();
  selectedBrigadeRouteId = null;

  const brigadesWithRoutes = brigades.filter(b => b.route?.enabled && b.route.geometry?.length >= 2);

  if (!brigadesWithRoutes.length) {
    document.getElementById('mainMap').insertAdjacentHTML(
      'afterend',
      '<p class="hint" style="text-align:center;">Нет маршрутов (нужны координаты у задач)</p>'
    );
    return;
  }

  const legendItems = [];
  const allLatLngs = [];

  brigadesWithRoutes.forEach((brigade, idx) => {
    const color = brigadeColors[idx % brigadeColors.length];

    // ── РЕАЛЬНАЯ ГЕОМЕТРИЯ МАРШРУТА (по дорогам, не по прямой!) ──
    const geometry = brigade.route.geometry; // [[lat,lon], [lat,lon], ...]
    if (geometry?.length >= 2) {
      const polyline = L.polyline(geometry, {
        color,
        weight: 5,
        opacity: 0.85,
      }).addTo(mainMap);
      polyline.on('click', () => selectBrigadeRoute(brigade.brigade_id));
      routeLayers.push({
        brigadeId: brigade.brigade_id,
        layer: polyline,
        color,
        taskCount: brigade.tasks.length,
        distance: brigade.route.total_distance_km,
        duration: brigade.route.total_duration_min,
      });
      allLatLngs.push(...geometry);
    }

    // ── МАРКЕРЫ ЗАДАЧ (только waypoints) ──
    const waypoints = brigade.route.points;
    const waypointTaskIds = brigade.route.point_task_ids || [];
    waypoints.forEach((point, pIdx) => {
      const tid = waypointTaskIds[pIdx];
      const isStart = tid === 'START';
      const task = !isStart && tid ? brigade.tasks.find(t => String(t.task_id) === String(tid)) : brigade.tasks[pIdx];
      const isFirst = pIdx === 0;
      const isLast  = pIdx === waypoints.length - 1;

      const dotIcon = L.divIcon({
        className: '',
        html: `
          <div style="
            width:${isFirst || isLast ? 18 : 12}px;
            height:${isFirst || isLast ? 18 : 12}px;
            background:${color};
            border:2px solid #fff;
            border-radius:${isStart ? '4px' : '50%'};
            box-shadow:0 2px 6px rgba(0,0,0,0.35);
          "></div>
        `,
        iconAnchor: [isFirst || isLast ? 9 : 6, isFirst || isLast ? 9 : 6],
      });

      const marker = L.marker(point, { icon: dotIcon }).addTo(mainMap);
      if (isStart && brigade.start_point) {
        marker.bindPopup(`
          <b>${brigade.brigade_id}</b><br>
          <b>Старт маршрута</b><br>
          ${escapeHtml(brigade.start_point.address || brigade.start_point.name)}
        `);
      } else if (task) {
        marker.bindPopup(`
          <b>${brigade.brigade_id}</b><br>
          <b>№ ${task.task_id}</b><br>
          ${escapeHtml(formatOriginalAddress(task))}<br>
          Вид работ: ${task.task_type}<br>
          Начало: ${task.planned_start}<br>
          ${task.sla_deadline ? `SLA: ${task.sla_deadline}<br>Статус: ${formatSlaStatus(task)}` : `Интервал: ${formatTaskTime(task)}`}
        `);
      }
      marker.on('click', () => selectBrigadeRoute(brigade.brigade_id));
      markerLayers.push({ brigadeId: brigade.brigade_id, marker });
    });

    legendItems.push({
      brigadeId: brigade.brigade_id,
      taskCount: brigade.tasks.length,
      color,
      distance: brigade.route.total_distance_km,
      duration: brigade.route.total_duration_min,
    });
  });

  // Центрирование
  if (allLatLngs.length) {
    mainMap.fitBounds(L.latLngBounds(allLatLngs), { padding: [40, 40] });
  }

  renderLegend(legendItems);
  updateRouteHighlight();
}

function renderLegend(items) {
  const legendDiv = document.getElementById('mapLegend');
  if (!legendDiv) return;
  legendDiv.innerHTML = `
    <table>
      <thead>
        <tr><th>Бригада</th><th>Задач</th><th>Дистанция</th><th>Время в пути</th></tr>
      </thead>
      <tbody>
        ${items.map(item => `
          <tr class="${selectedBrigadeRouteId === item.brigadeId ? 'selectedRouteRow' : ''}" onclick="selectBrigadeRoute('${item.brigadeId}')">
            <td>
              <span style="display:inline-block;width:14px;height:14px;background:${item.color};border-radius:3px;margin-right:6px;vertical-align:middle;"></span>
              ${item.brigadeId}
            </td>
            <td>${item.taskCount}</td>
            <td>${item.distance != null ? item.distance + ' км' : '—'}</td>
            <td>${item.duration != null ? item.duration + ' мин' : '—'}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function renderPostponedTasks(postponed) {
  const container = document.getElementById('postponedRoot');
  const section   = document.getElementById('postponedSection');
  container.innerHTML = `
    <table>
      <thead>
        <tr><th>№ задачи</th><th>Вид работ</th><th>Адрес</th><th>Рабочий адрес</th><th>SLA / интервал</th><th>Причина</th></tr>
      </thead>
      <tbody>
        ${postponed.map(t => `
          <tr>
            <td>${escapeHtml(t.task_id)}</td>
            <td>${escapeHtml(t.task_type)}</td>
            <td>${escapeHtml(formatOriginalAddress(t))}</td>
            <td>${escapeHtml(formatAddress(t))}</td>
            <td>${escapeHtml(formatTaskTime(t))}</td>
            <td>${escapeHtml(t.reason)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  section.style.display = 'block';
}
