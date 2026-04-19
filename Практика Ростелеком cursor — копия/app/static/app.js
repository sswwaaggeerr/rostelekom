const statusNode = document.getElementById("status");
const resultRoot = document.getElementById("resultRoot");
const processBtn = document.getElementById("processBtn");

document.getElementById("validateBtn").addEventListener("click", async () => {
  const file = getFile();
  if (!file) return setStatus("Выберите файл.");
  const form = new FormData();
  form.append("file", file);

  const res = await fetch("/api/validate", { method: "POST", body: form });
  const data = await res.json();
  if (!data.ok) {
    processBtn.disabled = true;
    setStatus(data.errors.join("\n"));
    return;
  }
  processBtn.disabled = false;
  setStatus("Проверка прошла успешно. Можно обрабатывать.");
});

processBtn.addEventListener("click", async () => {
  const file = getFile();
  if (!file) return setStatus("Выберите файл.");
  const form = buildProcessForm(file);

  const res = await fetch("/api/process", { method: "POST", body: form });
  const data = await res.json();
  if (!data.ok) {
    setStatus((data.errors || ["Ошибка обработки"]).join("\n"));
    return;
  }
  renderResult(data.result);
});

function getFile() {
  return document.getElementById("fileInput").files[0];
}

function buildProcessForm(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("brigade_count", document.getElementById("brigadeCount").value);
  form.append("duty_brigade_id", document.getElementById("dutyBrigadeId").value);
  form.append("traffic_level", document.getElementById("trafficLevel").value);
  form.append("base_travel_hours", document.getElementById("baseTravelHours").value);
  form.append("min_completion_ratio", document.getElementById("minCompletionRatio").value);
  form.append("max_overtime_minutes", document.getElementById("maxOvertimeMinutes").value);
  return form;
}

function setStatus(text) {
  statusNode.textContent = text;
}

function renderResult(result) {
  const okText = result.target_achieved ? "достигнута" : "не достигнута";
  setStatus(
    `Выполнение: ${result.completion_ratio}% (цель ${result.target_ratio}%) - цель ${okText}.`
  );

  resultRoot.innerHTML = "";
  result.brigades.forEach((b) => {
    const wrapper = document.createElement("div");
    wrapper.className = "card";
    const title = document.createElement("h3");
    title.textContent = `${b.brigade_id}${b.is_duty ? " (дежурная)" : ""}`;
    wrapper.appendChild(title);

    if (b.route) {
      const routeBox = document.createElement("div");
      routeBox.className = "routeBox";
      const enabled = !!b.route.enabled;
      const mainLine = document.createElement("div");
      mainLine.className = "routeMain";
      const timeText =
        b.route.total_duration_min != null
          ? `${b.route.total_duration_min} мин`
          : "время не рассчитано";
      const distText =
        b.route.total_distance_km != null
          ? `${b.route.total_distance_km} км`
          : null;
      mainLine.textContent = `Маршрут: ${timeText}${distText ? " · " + distText : ""}`;
      routeBox.appendChild(mainLine);

      if (b.route.reason) {
        const reason = document.createElement("div");
        reason.className = "hint";
        reason.textContent = b.route.reason;
        routeBox.appendChild(reason);
      }

      wrapper.appendChild(routeBox);

      // Рендерим карту, если есть точки
      if (b.route && b.route.points && b.route.points.length >= 2) {
        const mapDiv = document.createElement('div');
        mapDiv.style.height = '300px';
        mapDiv.style.marginTop = '12px';
        mapDiv.style.borderRadius = '12px';
        wrapper.appendChild(mapDiv);

        // Инициализируем карту после того, как DOM обновится
        setTimeout(() => {
          const map = L.map(mapDiv).setView(b.route.points[0], 12);
          L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; CartoDB'
          }).addTo(map);

          // Добавляем маркеры для каждой точки
          b.route.points.forEach((point, idx) => {
            const marker = L.marker(point).addTo(map);
            const task = b.tasks[idx];
            marker.bindPopup(`<b>${task.task_id}</b><br>${task.address}<br>${task.planned_start}`);
          });

          // Рисуем линию маршрута
          const latlngs = b.route.points.map(p => [p[0], p[1]]);
          L.polyline(latlngs, { color: '#ff2f92', weight: 4 }).addTo(map);

          // Адаптируем зум под все точки
          map.fitBounds(latlngs);
        }, 50);
      }
    }

    if (!b.tasks.length) {
      const empty = document.createElement("p");
      empty.textContent = "Нет назначенных задач.";
      wrapper.appendChild(empty);
      resultRoot.appendChild(wrapper);
      return;
    }

    const table = document.createElement("table");
    table.innerHTML = `
      <thead>
        <tr>
          <th>task_id</th><th>address</th><th>start</th><th>finish</th><th>SLA</th><th>status</th><th>коорд.</th>
        </tr>
      </thead>
      <tbody>
        ${b.tasks
          .map(
            (t) => `<tr>
              <td>${t.task_id}</td>
              <td>${t.address}</td>
              <td>${t.planned_start}</td>
              <td>${t.planned_finish}</td>
              <td>${t.sla_deadline}</td>
              <td>${t.sla_status}</td>
              <td>${t.lat != null && t.lon != null ? "есть" : "—"}</td>
            </tr>`
          )
          .join("")}
      </tbody>
    `;
    wrapper.appendChild(table);
    resultRoot.appendChild(wrapper);
  });
}
