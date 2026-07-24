const agentNameToId = {
  "马维斯": "marvis",
  Aiden: "jack",
  Bennett: "brown",
  Serena: "ella",
  Orion: "noah",
  Luna: "luna",
  Elliot: "ollie",
};

let latestPersonas = {};
let tasks = [];
let activePersonaTaskId = null;
let lastOverlayKey = "";

const originalFetchForPersonaProfile = window.fetch.bind(window);

window.fetch = async (input, init) => {
  const response = await originalFetchForPersonaProfile(input, init);
  const url = typeof input === "string" ? input : input?.url;
  if (response.ok && typeof url === "string") {
    response
      .clone()
      .json()
      .then((data) => cachePayload(url, data, init?.method ?? "GET"))
      .catch(() => {});
  }
  return response;
};

function cachePayload(url, data, method) {
  if (url.endsWith("/api/bootstrap")) {
    tasks = data.tasks ?? tasks;
    latestPersonas = data.defaultPersonas ?? latestPersonas;
  }
  if (url.endsWith("/api/personas")) {
    latestPersonas = data.personas ?? latestPersonas;
  }
  if (url.endsWith("/api/tasks")) {
    tasks = data.tasks ?? tasks;
  }
  if (url.includes("/api/tasks/") && data.task) {
    upsertTask(data.task);
  }
  if (url.endsWith("/api/tasks") && method.toUpperCase() === "POST" && data.task) {
    upsertTask(data.task);
    activePersonaTaskId = data.task.id;
  }
  window.setTimeout(renderPersonaProfileOverlay, 0);
}

function upsertTask(task) {
  tasks = tasks.some((item) => item.id === task.id)
    ? tasks.map((item) => (item.id === task.id ? { ...item, ...task } : item))
    : [task, ...tasks];
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function flattenValue(value) {
  if (Array.isArray(value)) {
    return value.map(flattenValue).join("、");
  }
  if (value && typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${key}: ${flattenValue(item)}`)
      .join("；");
  }
  return String(value ?? "");
}

function profileSections(profile) {
  return [
    {
      title: "身份底色",
      rows: [
        ["唯一 ID", profile.unique_id],
        ["MBTI", profile.mbti],
        ["人口统计", profile.demographic_traits],
      ],
    },
    {
      title: "个人经历",
      rows: [
        ["人格描述", profile.personality_description],
        ["个人传记", profile.biography],
        ["职业背景", profile.professional_background],
      ],
    },
    {
      title: "讨论行为",
      rows: [
        ["风险偏好", profile.risk_preference],
        ["行为模式", profile.behavior_pattern],
        ["说话方式", profile.speaking_style],
      ],
    },
    {
      title: "关系立场",
      rows: [
        ["社交关系", profile.social_relationships],
        ["意识形态", profile.ideology],
      ],
    },
  ]
    .map((section) => ({
      ...section,
      rows: section.rows.filter(([, value]) => flattenValue(value).trim()),
    }))
    .filter((section) => section.rows.length);
}

function detailAgentId(detail) {
  const name = detail.querySelector(".persona-detail-head strong")?.textContent?.trim();
  return agentNameToId[name] ?? null;
}

function taskForLibrary() {
  if (activePersonaTaskId) {
    const active = tasks.find((task) => task.id === activePersonaTaskId);
    if (active) {
      return active;
    }
  }
  const title = document.querySelector(".persona-library-card > h2")?.textContent?.trim();
  return tasks.find((task) => task.title === title) ?? null;
}

function profileForDetail(detail) {
  const agentId = detailAgentId(detail);
  if (!agentId) {
    return null;
  }
  if (detail.classList.contains("persona-library-detail")) {
    return taskForLibrary()?.personaDrafts?.[agentId]?.profile ?? null;
  }
  return latestPersonas?.[agentId]?.profile ?? latestPersonas?.[agentId] ?? null;
}

function overlayElement() {
  let overlay = document.getElementById("persona-profile-overlay");
  if (!overlay) {
    overlay = document.createElement("aside");
    overlay.id = "persona-profile-overlay";
    overlay.className = "persona-profile-overlay";
    overlay.setAttribute("aria-label", "完整人物卡");
    document.body.appendChild(overlay);
  }
  return overlay;
}

function hideOverlay() {
  const overlay = document.getElementById("persona-profile-overlay");
  if (overlay) {
    overlay.hidden = true;
  }
  lastOverlayKey = "";
}

function positionOverlay(overlay, detail) {
  const rect = detail.getBoundingClientRect();
  const viewportGap = 18;
  const width = Math.min(460, Math.max(340, rect.width * 1.08));
  const height = Math.max(360, rect.height);
  const leftSide = rect.left - width - viewportGap;
  const rightSide = rect.right + viewportGap;
  const fitsLeft = leftSide >= viewportGap;
  const fitsRight = rightSide + width <= window.innerWidth - viewportGap;

  overlay.classList.toggle("dock-left", fitsLeft);
  overlay.classList.toggle("dock-right", !fitsLeft && fitsRight);
  overlay.classList.toggle("dock-bottom", !fitsLeft && !fitsRight);

  if (fitsLeft) {
    overlay.style.left = `${leftSide}px`;
    overlay.style.top = `${rect.top}px`;
  } else if (fitsRight) {
    overlay.style.left = `${rightSide}px`;
    overlay.style.top = `${rect.top}px`;
  } else {
    overlay.style.left = `${viewportGap}px`;
    overlay.style.top = `${Math.min(rect.bottom + viewportGap, window.innerHeight * 0.52)}px`;
  }
  overlay.style.width = `${width}px`;
  overlay.style.maxHeight = `${Math.min(height, window.innerHeight - rect.top - viewportGap)}px`;
}

function renderPersonaProfileOverlay() {
  const detail = document.querySelector(".persona-detail");
  if (!detail) {
    hideOverlay();
    return;
  }

  const profile = profileForDetail(detail);
  if (!profile || typeof profile !== "object") {
    hideOverlay();
    return;
  }

  const sections = profileSections(profile);
  if (!sections.length) {
    hideOverlay();
    return;
  }

  const overlay = overlayElement();
  const agentId = detailAgentId(detail) ?? "unknown";
  const agentName = detail.querySelector(".persona-detail-head strong")?.textContent?.trim() ?? "Agent";
  const key = `${agentId}:${JSON.stringify(profile)}`;
  if (key !== lastOverlayKey) {
    overlay.innerHTML = `
      <div class="persona-profile-head">
        <span>完整人物卡</span>
        <strong>${escapeHtml(agentName)}</strong>
      </div>
      <div class="persona-profile-body">
        ${sections
          .map(
            (section) => `
              <section>
                <b>${escapeHtml(section.title)}</b>
                ${section.rows
                  .map(
                    ([label, value]) => `
                      <dl>
                        <dt>${escapeHtml(label)}</dt>
                        <dd>${escapeHtml(flattenValue(value))}</dd>
                      </dl>
                    `,
                  )
                  .join("")}
              </section>
            `,
          )
          .join("")}
      </div>
    `;
    lastOverlayKey = key;
  }
  positionOverlay(overlay, detail);
  overlay.hidden = false;
}

document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest?.("button");
    if (button?.textContent.trim() === "查看人设") {
      const card = button.closest(".history-task");
      const index = [...document.querySelectorAll(".history-task")].indexOf(card);
      activePersonaTaskId = tasks[index]?.id ?? activePersonaTaskId;
    }
    window.setTimeout(renderPersonaProfileOverlay, 0);
  },
  true,
);

window.addEventListener("resize", renderPersonaProfileOverlay);
window.addEventListener("scroll", renderPersonaProfileOverlay, true);

const root = document.getElementById("root") ?? document.body;
new MutationObserver(renderPersonaProfileOverlay).observe(root, { childList: true, subtree: true });
