const agentMeta = {
  marvis: { name: "马维斯", role: "主理人", color: "#2ba7e8", avatar: "/assets/avatars/marvis.webp" },
  jack: { name: "Aiden", role: "产品", color: "#168cff", avatar: "/assets/avatars/jack.webp" },
  brown: { name: "Bennett", role: "工程", color: "#ef3027", avatar: "/assets/avatars/brown.webp" },
  ella: { name: "Serena", role: "设计", color: "#7b22ff", avatar: "/assets/avatars/ella.webp" },
  noah: { name: "Orion", role: "运营", color: "#24d8c9", avatar: "/assets/avatars/noah.webp" },
  luna: { name: "Luna", role: "数据", color: "#ffd200", avatar: "/assets/avatars/luna.webp" },
  ollie: { name: "Elliot", role: "支持", color: "#1fd7e6", avatar: "/assets/avatars/ollie.webp" },
};

const originalFetchForInfoView = window.fetch.bind(window);
let feedMirrorObserver = null;

window.fetch = async (input, init) => {
  const response = await originalFetchForInfoView(input, init);
  const url = typeof input === "string" ? input : input?.url;
  if (typeof url === "string" && /\/api\/tasks\/[^/]+\/mind-map$/.test(url) && response.ok) {
    response
      .clone()
      .json()
      .then((data) => window.setTimeout(() => renderInfoView(data.task), 0))
      .catch(() => {});
  }
  return response;
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function messageMeta(message) {
  if (message.kind === "system") {
    return { ...agentMeta.marvis, name: "系统", role: "" };
  }
  return agentMeta[message.speaker_id] ?? { name: "系统", role: "", color: "#6f8ca4", avatar: "/assets/avatars/marvis.webp" };
}

function renderMessage(message) {
  const meta = messageMeta(message);
  return `
    <div class="info-chat-message ${escapeHtml(message.kind || "agent")}" style="--agent-color:${escapeHtml(meta.color)}">
      <img src="${escapeHtml(meta.avatar)}" alt="">
      <div>
        <strong>${escapeHtml(meta.role ? `${meta.name}：${meta.role}` : meta.name)}</strong>
        <p>${escapeHtml(message.content || "").replaceAll("\n", "<br>")}</p>
      </div>
    </div>
  `;
}

function renderInfoView(task) {
  const modal = document.querySelector(".mind-map-modal .mind-map-card");
  const canvas = modal?.querySelector(".mind-canvas");
  if (!modal || !canvas || !task) {
    return;
  }

  modal.classList.add("info-chat-card");
  canvas.classList.add("info-chat-canvas");

  const eyebrow = modal.querySelector(".eyebrow");
  const title = modal.querySelector("h2");
  const description = modal.querySelector("p");
  if (eyebrow) eyebrow.textContent = "CHAT INFO";
  if (title) title.textContent = `${task.title || task.topic || "任务"} · 聊天信息`;
  if (description) description.textContent = `${task.phase || "讨论中"} · ${(task.messages ?? []).length} 条消息`;

  renderFromTask(canvas, task);
  mirrorVisibleChatFeed(canvas);
}

function renderFromTask(canvas, task) {
  canvas.innerHTML = `
    <div class="info-chat-feed fallback-render">
      ${(task.messages ?? []).map(renderMessage).join("") || '<div class="info-empty">还没有聊天消息。</div>'}
    </div>
  `;
  scrollCanvasBottom(canvas);
}

function mirrorVisibleChatFeed(canvas) {
  const source = document.querySelector(".discussion-panel .chat-feed");
  if (!source) {
    return;
  }
  stopMirroring();
  copyChatFeed(source, canvas);
  feedMirrorObserver = new MutationObserver(() => copyChatFeed(source, canvas));
  feedMirrorObserver.observe(source, {
    childList: true,
    subtree: true,
    characterData: true,
  });
}

function copyChatFeed(source, canvas) {
  if (!document.querySelector(".mind-map-modal")) {
    stopMirroring();
    return;
  }
  canvas.innerHTML = `<div class="info-chat-feed mirrored-render">${source.innerHTML}</div>`;
  scrollCanvasBottom(canvas);
}

function scrollCanvasBottom(canvas) {
  window.requestAnimationFrame(() => {
    canvas.scrollTop = canvas.scrollHeight;
  });
}

function stopMirroring() {
  if (feedMirrorObserver) {
    feedMirrorObserver.disconnect();
    feedMirrorObserver = null;
  }
}

function renameMapButtons() {
  document.querySelectorAll(".map-button").forEach((button) => {
    if (button.textContent.trim() === "导图") {
      button.textContent = "信息";
    }
  });
}

new MutationObserver(() => {
  renameMapButtons();
  if (!document.querySelector(".mind-map-modal")) {
    stopMirroring();
  }
}).observe(document.body, { childList: true, subtree: true });
renameMapButtons();
