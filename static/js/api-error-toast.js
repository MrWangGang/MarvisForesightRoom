function parseApiError(message) {
  const raw = String(message ?? "").trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
  } catch {
    return raw;
  }
  return raw;
}

window.addEventListener("unhandledrejection", (event) => {
  const message = parseApiError(event.reason?.message ?? event.reason);
  if (!message || !message.includes("LLM 调用失败")) {
    return;
  }
  event.preventDefault();
  window.alert(message);
});
