const replacements = new Map([
  ["ACTION MAP", "CONVERSATION FLOW"],
  ["行动导图", "推演节点图"],
  ["行动推演", "推演链"],
  ["从这里分叉验证", "每次对话形成一个节点"],
  ["成功路径", "推演节点"],
  ["失败路径", ""],
  ["下一步动作", ""],
  ["这里不是总结报告，而是把 Agent 的推演拆成用户能执行、能止损、能放大的路径。", "这里由专门 Agent 异步读取完整聊天记录，把每一次对话转成一个推演节点，并按发生顺序连成走向。"],
  ["马维斯正在整理成功/失败路径 ...", "ConversationTraceAgent 正在生成推演节点 ..."],
]);

function rewriteMindMapLabels(root = document.body) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const next = replacements.get(node.nodeValue);
    if (next) {
      node.nodeValue = next;
    }
  }
}

rewriteMindMapLabels();

new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        rewriteMindMapLabels(node);
      } else if (node.nodeType === Node.TEXT_NODE) {
        const next = replacements.get(node.nodeValue);
        if (next) {
          node.nodeValue = next;
        }
      }
    }
  }
}).observe(document.body, { childList: true, subtree: true });
