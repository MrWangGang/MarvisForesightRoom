const STORAGE_KEY = "marvis-max-turns";
const DEFAULT_MAX_TURNS = 200;
const MIN_MAX_TURNS = 14;
const MAX_MAX_TURNS = 240;

function normalizeMaxTurns(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_MAX_TURNS;
  }
  return Math.min(MAX_MAX_TURNS, Math.max(MIN_MAX_TURNS, parsed));
}

function currentMaxTurns() {
  const select = document.querySelector("[data-max-turns-select]");
  return normalizeMaxTurns(select?.value ?? window.localStorage.getItem(STORAGE_KEY));
}

function installFetchPatch() {
  if (window.__marvisMaxTurnsFetchPatched) {
    return;
  }
  window.__marvisMaxTurnsFetchPatched = true;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url ?? "";
    const method = String(init?.method ?? "GET").toUpperCase();
    if (url === "/api/tasks" && method === "POST" && typeof init.body === "string") {
      try {
        const payload = JSON.parse(init.body);
        payload.max_turns = currentMaxTurns();
        init = { ...init, body: JSON.stringify(payload) };
      } catch {
        // Keep the original request body if it is not JSON.
      }
    }
    return originalFetch(input, init);
  };
}

function renderMaxTurnsField() {
  const taskDesc = document.querySelector(".task-desc");
  if (!taskDesc || document.querySelector("[data-max-turns-field]")) {
    return;
  }

  const field = document.createElement("label");
  field.className = "max-turns-field";
  field.dataset.maxTurnsField = "true";
  field.innerHTML = `
    <span>最大轮数 <b>一个人发言算 1 轮</b></span>
    <div class="max-turns-control">
      <select data-max-turns-select aria-label="最大轮数">
        <option value="30">30 轮</option>
        <option value="50">50 轮</option>
        <option value="80">80 轮</option>
        <option value="120">120 轮</option>
        <option value="160">160 轮</option>
        <option value="200">200 轮</option>
        <option value="240">240 轮</option>
      </select>
      <em>接近上限强制投票</em>
    </div>
  `;

  const select = field.querySelector("select");
  select.value = String(currentMaxTurns());
  select.addEventListener("change", () => {
    window.localStorage.setItem(STORAGE_KEY, String(normalizeMaxTurns(select.value)));
  });

  taskDesc.insertAdjacentElement("afterend", field);
}

installFetchPatch();
renderMaxTurnsField();
new MutationObserver(renderMaxTurnsField).observe(document.documentElement, {
  childList: true,
  subtree: true,
});
