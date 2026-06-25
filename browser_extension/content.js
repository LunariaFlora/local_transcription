(() => {
  const API_URL = "http://127.0.0.1:8765/api/youtube-import-links";
  const BUTTON_ID = "local-transcription-youtube-import";
  const WATCH_RE = /(?:youtube\.com\/watch\?[^#\s]*v=|youtu\.be\/|youtube\.com\/shorts\/)([A-Za-z0-9_-]{6,})/;
  const VIDEO_CARD_SELECTOR = [
    "ytd-playlist-video-renderer",
    "ytd-video-renderer",
    "ytd-rich-item-renderer",
    "ytd-compact-video-renderer",
    "ytd-grid-video-renderer",
    "ytd-reel-item-renderer",
    "yt-lockup-view-model",
  ].join(", ");

  function normalizeUrl(href) {
    try {
      const url = new URL(href, location.href);
      if (url.hostname.includes("youtu.be")) {
        const id = url.pathname.split("/").filter(Boolean)[0];
        return id ? `https://www.youtube.com/watch?v=${id}` : "";
      }
      if (url.pathname.startsWith("/shorts/")) {
        const id = url.pathname.split("/").filter(Boolean)[1];
        return id ? `https://www.youtube.com/watch?v=${id}` : "";
      }
      if (url.pathname === "/watch") {
        const id = url.searchParams.get("v");
        return id ? `https://www.youtube.com/watch?v=${id}` : "";
      }
      const match = String(href).match(WATCH_RE);
      return match ? `https://www.youtube.com/watch?v=${match[1]}` : "";
    } catch {
      return "";
    }
  }

  function cleanTitle(value) {
    const title = String(value || "")
      .replace(/\s+/g, " ")
      .replace(/\s+-\s+YouTube$/, "")
      .trim();
    if (!title) return "";
    if (/^https?:\/\//i.test(title)) return "";
    if (/^\d{1,2}:\d{2}(?::\d{2})?$/.test(title)) return "";
    if (/^(watch|观看|播放全部|play all|mix|shorts)$/i.test(title)) return "";
    return title;
  }

  function titleFromAria(value) {
    const aria = cleanTitle(value);
    if (!aria) return "";
    return cleanTitle(
      aria
        .replace(/\s+by\s+.+?\s+\d[\d,]*\s+views?.*$/i, "")
        .replace(/\s+\d[\d,]*\s+views?.*$/i, "")
        .replace(/\s+观看次数.*$/i, "")
    );
  }

  function titleForCard(card, fallbackAnchor) {
    const candidates = [
      card?.querySelector("a#video-title"),
      card?.querySelector("#video-title"),
      card?.querySelector("yt-formatted-string#video-title"),
      card?.querySelector("[id='video-title']"),
      card?.querySelector("h3 a"),
      fallbackAnchor,
    ].filter(Boolean);

    for (const element of candidates) {
      const fromTitle = cleanTitle(element.getAttribute?.("title"));
      if (fromTitle) return fromTitle;
      const fromAria = titleFromAria(element.getAttribute?.("aria-label"));
      if (fromAria) return fromAria;
      const fromText = cleanTitle(element.textContent);
      if (fromText) return fromText;
    }
    return cleanTitle(document.title);
  }

  function collectItems() {
    const seen = new Set();
    const items = [];

    document.querySelectorAll(VIDEO_CARD_SELECTOR).forEach((card) => {
      const anchor = card.querySelector('a#video-title[href*="/watch?v="], a[href*="/watch?v="], a[href*="/shorts/"], a[href*="youtu.be/"]');
      const url = normalizeUrl(anchor?.href || "");
      if (!url || seen.has(url)) return;
      seen.add(url);
      items.push({ url, title: titleForCard(card, anchor) || url });
    });

    document.querySelectorAll('a#video-title[href*="/watch?v="], a[href*="/watch?v="], a[href*="/shorts/"], a[href*="youtu.be/"]').forEach((anchor) => {
      const url = normalizeUrl(anchor.href);
      if (!url || seen.has(url)) return;
      const title = titleForCard(anchor.closest(VIDEO_CARD_SELECTOR), anchor);
      if (!title) return;
      seen.add(url);
      items.push({ url, title });
    });

    const currentUrl = normalizeUrl(location.href);
    if (currentUrl && !seen.has(currentUrl)) {
      items.unshift({ url: currentUrl, title: cleanTitle(document.title) || currentUrl });
    }
    return items;
  }

  async function importItems() {
    const button = document.getElementById(BUTTON_ID);
    const items = collectItems();
    if (!items.length) {
      showStatus("没有找到视频链接", true);
      return;
    }
    button.disabled = true;
    showStatus(`正在导入 ${items.length} 个视频...`);
    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "chrome-extension", items, open_edge: true }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "工作台没有响应");
      }
      showStatus(`已导入 ${data.count || items.length} 个视频`);
    } catch (error) {
      showStatus("先启动音频转写工作台", true);
    } finally {
      button.disabled = false;
    }
  }

  function showStatus(text, error = false) {
    let status = document.getElementById(`${BUTTON_ID}-status`);
    if (!status) {
      status = document.createElement("div");
      status.id = `${BUTTON_ID}-status`;
      document.documentElement.appendChild(status);
    }
    status.textContent = text;
    status.dataset.error = error ? "1" : "0";
    status.style.opacity = "1";
    clearTimeout(showStatus.timer);
    showStatus.timer = setTimeout(() => {
      status.style.opacity = "0";
    }, 3600);
  }

  function installStyles() {
    if (document.getElementById(`${BUTTON_ID}-styles`)) return;
    const style = document.createElement("style");
    style.id = `${BUTTON_ID}-styles`;
    style.textContent = `
      #${BUTTON_ID} {
        position: fixed;
        right: 18px;
        top: 112px;
        z-index: 2147483647;
        border: 1px solid rgba(96, 165, 250, .45);
        border-radius: 999px;
        padding: 10px 14px;
        color: #eff6ff;
        background: rgba(12, 18, 32, .86);
        box-shadow: 0 18px 48px rgba(0, 0, 0, .34), 0 0 24px rgba(59, 130, 246, .18);
        backdrop-filter: blur(14px);
        font: 600 13px/1.2 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        cursor: pointer;
      }
      #${BUTTON_ID}:hover {
        background: rgba(37, 99, 235, .9);
      }
      #${BUTTON_ID}:disabled {
        cursor: wait;
        opacity: .7;
      }
      #${BUTTON_ID}-status {
        position: fixed;
        right: 18px;
        top: 158px;
        z-index: 2147483647;
        max-width: 260px;
        border: 1px solid rgba(96, 165, 250, .32);
        border-radius: 14px;
        padding: 10px 12px;
        color: #dbeafe;
        background: rgba(12, 18, 32, .88);
        box-shadow: 0 18px 48px rgba(0, 0, 0, .3);
        opacity: 0;
        transition: opacity .28s ease;
        font: 500 12px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        pointer-events: none;
      }
      #${BUTTON_ID}-status[data-error="1"] {
        border-color: rgba(251, 113, 133, .5);
        color: #fecdd3;
      }
    `;
    document.documentElement.appendChild(style);
  }

  function installButton() {
    if (document.getElementById(BUTTON_ID)) return;
    installStyles();
    const button = document.createElement("button");
    button.id = BUTTON_ID;
    button.type = "button";
    button.textContent = "导入工作台";
    button.addEventListener("click", importItems);
    document.documentElement.appendChild(button);
  }

  installButton();
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      installButton();
    }
  }, 1200);
})();
