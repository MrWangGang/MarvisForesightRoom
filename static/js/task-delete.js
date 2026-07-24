const historyCardSelector = ".history-task";
const actionRowSelector = ".history-task > div";

async function loadTasks() {
  try {
    const response = await fetch("/api/bootstrap", { cache: "no-store" });
    if (!response.ok) {
      return [];
    }
    const data = await response.json();
    return data.tasks ?? [];
  } catch {
    return [];
  }
}

function addDeleteButtons() {
  document.querySelectorAll(actionRowSelector).forEach((row) => {
    if (row.querySelector(".task-delete-button")) {
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "task-delete-button";
    button.textContent = "删除";
    row.appendChild(button);
  });
}

function taskIdForCard(card, tasks) {
  const cards = [...document.querySelectorAll(historyCardSelector)];
  const index = cards.indexOf(card);
  return tasks[index]?.id;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest?.(".task-delete-button");
  if (!button) {
    return;
  }

  event.preventDefault();
  event.stopPropagation();

  const card = button.closest(historyCardSelector);
  const tasks = await loadTasks();
  const taskId = taskIdForCard(card, tasks);
  if (!taskId) {
    window.alert("删除失败：找不到对应任务");
    return;
  }

  button.disabled = true;
  button.textContent = "删除中";
  try {
    const response = await fetch(`/api/tasks/${taskId}`, { method: "DELETE" });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || "删除失败");
    }
        window.location.href = "/";
  } catch (error) {
    button.disabled = false;
    button.textContent = "删除";
    window.alert(`删除失败：${error.message || error}`);
  }
});

addDeleteButtons();
window.setInterval(addDeleteButtons, 1000);
