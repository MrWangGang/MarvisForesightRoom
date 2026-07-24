const requiredProfileKeys = [
  "unique_id",
  "personality_description",
  "biography",
  "demographic_traits",
  "mbti",
  "professional_background",
  "risk_preference",
  "behavior_pattern",
  "social_relationships",
  "ideology",
];

let generatedPersonaKey = "";

const originalFetchForCreateGuard = window.fetch.bind(window);

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input?.url;
  const method = String(init.method ?? "GET").toUpperCase();
  const body = parseBody(init.body);

  if (method === "POST" && typeof url === "string" && url.endsWith("/api/tasks")) {
    if (!canCreateWithPayload(body)) {
      return new Response(
        JSON.stringify({ detail: "请先点击 AI 生成人设，确认 7 个完整人物卡生成后再创建讨论室" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      );
    }
  }

  const response = await originalFetchForCreateGuard(input, init);
  if (method === "POST" && typeof url === "string" && url.endsWith("/api/personas") && response.ok) {
    response
      .clone()
      .json()
      .then((data) => {
        if (hasRichPersonas(data.personas)) {
          generatedPersonaKey = keyForPayload(body);
          updateLaunchButton();
        }
      })
      .catch(() => {});
  }
  return response;
};

function parseBody(body) {
  if (!body || typeof body !== "string") {
    return {};
  }
  try {
    return JSON.parse(body);
  } catch {
    return {};
  }
}

function keyForPayload(payload) {
  return `${String(payload.topic ?? "").trim().slice(0, 12)}\n${String(payload.description ?? "").trim()}`;
}

function currentPayloadKey() {
  const topic = document.querySelector(".task-modal .topic-field input")?.value ?? "";
  const description = document.querySelector(".task-modal .task-desc textarea")?.value ?? "";
  return keyForPayload({ topic, description });
}

function hasRichPersonas(personas) {
  if (!personas || typeof personas !== "object") {
    return false;
  }
  return ["marvis", "jack", "brown", "ella", "noah", "luna", "ollie"].every((id) => {
    const profile = personas[id]?.profile;
    return profile && requiredProfileKeys.every((key) => Object.prototype.hasOwnProperty.call(profile, key));
  });
}

function canCreateWithPayload(payload) {
  return hasRichPersonas(payload.personas) && keyForPayload(payload) === generatedPersonaKey;
}

function updateLaunchButton() {
  const button = document.querySelector(".task-modal .launch-button");
  if (!button) {
    return;
  }
  const topic = document.querySelector(".task-modal .topic-field input")?.value?.trim() ?? "";
  const description = document.querySelector(".task-modal .task-desc textarea")?.value?.trim() ?? "";
  const personaReady = Boolean(generatedPersonaKey) && currentPayloadKey() === generatedPersonaKey;
  const disabled = !topic || !description || !personaReady;
  button.disabled = disabled;
  button.classList.toggle("persona-required", disabled && Boolean(topic && description));
  button.title = disabled && topic && description ? "请先点击 AI 生成人设" : "";
}

document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest?.(".task-modal .launch-button");
    if (!button) {
      return;
    }
    if (button.disabled || currentPayloadKey() !== generatedPersonaKey) {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.alert("请先点击 AI 生成人设，确认 7 个完整人物卡生成后再创建讨论室");
    }
  },
  true,
);

document.addEventListener(
  "input",
  (event) => {
    if (event.target.closest?.(".task-modal")) {
      window.setTimeout(updateLaunchButton, 0);
    }
  },
  true,
);

new MutationObserver(updateLaunchButton).observe(document.body, { childList: true, subtree: true });
