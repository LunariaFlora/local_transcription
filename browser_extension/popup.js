const statusEl = document.getElementById("status");

async function importCurrentTab() {
  statusEl.textContent = "正在读取当前标签页...";
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url || !tab.url.includes("youtube.com")) {
    statusEl.textContent = "请先切到 YouTube 页面。";
    return;
  }

  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const seen = new Set();
      const items = [];
      const cardSelector = [
        "ytd-playlist-video-renderer",
        "ytd-video-renderer",
        "ytd-rich-item-renderer",
        "ytd-compact-video-renderer",
        "ytd-grid-video-renderer",
        "ytd-reel-item-renderer",
        "yt-lockup-view-model",
      ].join(", ");
      const normalize = (href) => {
        try {
          const url = new URL(href, location.href);
          if (url.pathname === "/watch") {
            const id = url.searchParams.get("v");
            return id ? `https://www.youtube.com/watch?v=${id}` : "";
          }
          if (url.pathname.startsWith("/shorts/")) {
            const id = url.pathname.split("/").filter(Boolean)[1];
            return id ? `https://www.youtube.com/watch?v=${id}` : "";
          }
          return "";
        } catch {
          return "";
        }
      };
      const cleanTitle = (value) => {
        const title = String(value || "").replace(/\s+/g, " ").replace(/\s+-\s+YouTube$/, "").trim();
        if (!title || /^https?:\/\//i.test(title)) return "";
        if (/^\d{1,2}:\d{2}(?::\d{2})?$/.test(title)) return "";
        if (/^(watch|观看|播放全部|play all|mix|shorts)$/i.test(title)) return "";
        return title;
      };
      const titleFromAria = (value) => cleanTitle(
        cleanTitle(value)
          .replace(/\s+by\s+.+?\s+\d[\d,]*\s+views?.*$/i, "")
          .replace(/\s+\d[\d,]*\s+views?.*$/i, "")
          .replace(/\s+观看次数.*$/i, "")
      );
      const titleFor = (card, anchor) => {
        const candidates = [
          card?.querySelector("a#video-title"),
          card?.querySelector("#video-title"),
          card?.querySelector("yt-formatted-string#video-title"),
          card?.querySelector("[id='video-title']"),
          card?.querySelector("h3 a"),
          anchor,
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
      };

      document.querySelectorAll(cardSelector).forEach((card) => {
        const anchor = card.querySelector('a#video-title[href*="/watch?v="], a[href*="/watch?v="], a[href*="/shorts/"]');
        const url = normalize(anchor?.href || "");
        if (!url || seen.has(url)) return;
        seen.add(url);
        items.push({ url, title: titleFor(card, anchor) || url });
      });
      document.querySelectorAll('a#video-title[href*="/watch?v="], a[href*="/watch?v="], a[href*="/shorts/"]').forEach((anchor) => {
        const url = normalize(anchor.href);
        if (!url || seen.has(url)) return;
        const title = titleFor(anchor.closest(cardSelector), anchor);
        if (!title) return;
        seen.add(url);
        items.push({ url, title });
      });
      const current = normalize(location.href);
      if (current && !seen.has(current)) {
        items.unshift({ url: current, title: cleanTitle(document.title) || current });
      }
      return items;
    },
  });

  if (!result?.length) {
    statusEl.textContent = "没有找到视频链接。";
    return;
  }

  const response = await fetch("http://127.0.0.1:8765/api/youtube-import-links", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: "chrome-extension-popup", items: result, open_edge: true }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    statusEl.textContent = data.error || "请先启动音频转写工作台。";
    return;
  }
  statusEl.textContent = `已导入 ${data.count || result.length} 个视频。`;
}

document.getElementById("importCurrent").addEventListener("click", () => {
  importCurrentTab().catch(() => {
    statusEl.textContent = "请先启动音频转写工作台。";
  });
});
