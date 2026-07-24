const observedFeeds = new WeakSet();

function scrollFeedToBottom(feed) {
  window.requestAnimationFrame(() => {
    feed.scrollTop = feed.scrollHeight;
  });
}

function watchFeed(feed) {
  if (!feed || observedFeeds.has(feed)) {
    return;
  }
  observedFeeds.add(feed);
  scrollFeedToBottom(feed);
  new MutationObserver(() => scrollFeedToBottom(feed)).observe(feed, {
    childList: true,
    subtree: true,
    characterData: true,
  });
}

function watchChatFeeds() {
  document.querySelectorAll(".chat-feed").forEach(watchFeed);
}

new MutationObserver(watchChatFeeds).observe(document.body, { childList: true, subtree: true });
window.addEventListener("resize", watchChatFeeds);
watchChatFeeds();
