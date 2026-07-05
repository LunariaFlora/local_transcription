const $ = (id) => document.getElementById(id);

let lastRunning = false;
let manualInputSelection = false;
let manualOutputSelection = false;
const selectedInputs = new Set();
const selectedOutputs = new Set();
let youtubeItems = [];
const selectedYoutubeUrls = new Set();
let youtubeHelpPage = 0;
let youtubeHelpSeenThisSession = false;
let lastImportedAt = 0;
const youtubeHelpStorageKey = "youtubeImportHelpDismissed";

const taskLabels = {
  api: "在线 API 转写",
  "local-small": "本地 small 转写",
  "local-medium": "本地 medium 转写",
  title: "自动命名",
  summary: "总结概要",
  "high-quality-mp3": "导出高质量 MP3",
  "youtube-download": "YouTube 下载",
};

const backendTaskLabels = {
  "Groq API Transcribe": "在线转写",
  "Local Transcribe small": "本地 small 转写",
  "Local Transcribe medium": "本地 medium 转写",
  "Title and Rename": "自动命名",
  Summary: "总结概要",
  "High-quality MP3": "导出 MP3",
  "YouTube Download": "YouTube 下载",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try {
      const json = JSON.parse(text);
      if (json.error) message = json.error;
    } catch {
    }
    try {
      const doc = new DOMParser().parseFromString(text, "text/html");
      const body = doc.body?.textContent?.trim();
      if (body && body !== text) message = body.replace(/\s+/g, " ");
    } catch {
    }
    throw new Error(message);
  }
  return response.json();
}

function showToast(message) {
  let toast = $("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.remove("show");
  requestAnimationFrame(() => toast.classList.add("show"));
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 1800);
}

function showProcess(message, tone = "info") {
  let toast = $("processToast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "processToast";
    toast.className = "process-toast";
    document.body.appendChild(toast);
  }
  toast.dataset.tone = tone;
  toast.textContent = message;
  toast.classList.remove("show");
  requestAnimationFrame(() => toast.classList.add("show"));
  clearTimeout(showProcess.timer);
  showProcess.timer = setTimeout(() => toast.classList.remove("show"), 1600);
}

function closeFloatingToasts(event) {
  const toast = $("toast");
  const processToast = $("processToast");
  const target = event.target;
  if (toast?.classList.contains("show") && !toast.contains(target)) {
    toast.classList.remove("show");
  }
  if (processToast?.classList.contains("show") && !processToast.contains(target)) {
    processToast.classList.remove("show");
  }
}

function latestUsefulLine(logs = []) {
  return logs
    .filter((line) => line && !line.startsWith("[task exited"))
    .filter((line) => !line.includes("ffmpeg version"))
    .filter((line) => !line.startsWith("  built with"))
    .slice(-1)[0] || "";
}

function taskHint(task) {
  if (task.running) {
    if (task.name === "YouTube Download") return "正在下载视频到 inputs。";
    if (task.name === "Title and Rename") return "正在处理文字稿。";
    if (task.name === "High-quality MP3") return "正在导出 MP3。";
    return "正在转写文件。";
  }
  if (task.returncode === 0) return "任务已完成。";
  if (task.returncode && task.returncode !== 0) return "任务失败，请检查设置。";
  return "等待开始。";
}

function cleanSelection(set, validNames) {
  for (const name of [...set]) {
    if (!validNames.has(name)) set.delete(name);
  }
}

function syncDefaultSelections(data) {
  const inputNames = new Set(data.inputs.map((file) => file.name));
  cleanSelection(selectedInputs, inputNames);
  if (!manualInputSelection) {
    selectedInputs.clear();
    data.inputs.filter((file) => !file.transcribed).forEach((file) => selectedInputs.add(file.name));
  }

  const outputNames = new Set(data.outputs.map((folder) => folder.name));
  cleanSelection(selectedOutputs, outputNames);
  if (!manualOutputSelection && selectedOutputs.size === 0) {
    data.outputs
      .filter((folder) => folder.has_transcript)
      .sort((a, b) => b.mtime - a.mtime)
      .slice(0, 1)
      .forEach((folder) => selectedOutputs.add(folder.name));
  }
}

function renderInputItem(file, target) {
  const selected = selectedInputs.has(file.name);
  const status = file.transcribed ? "已转写" : "未转写";
  return `
    <li class="selectable ${selected ? "selected" : ""}" data-kind="${target}" data-name="${file.name}">
      <span class="item-main">
        <strong>${file.name}</strong>
        <small>${formatBytes(file.size)}</small>
      </span>
      <span class="item-tags">
        <em class="${file.transcribed ? "done" : "pending"}">${status}</em>
        ${selected ? "<em class=\"picked\">已选中</em>" : ""}
      </span>
    </li>`;
}

function renderOutputItem(folder, target) {
  const selected = selectedOutputs.has(folder.name);
  const status = folder.has_transcript ? "有文字稿" : `${folder.files.length} 个文件`;
  return `
    <li class="selectable ${selected ? "selected" : ""}" data-kind="${target}" data-name="${folder.name}">
      <span class="item-main">
        <strong>${folder.name}</strong>
        <small>${status}</small>
      </span>
      <span class="item-tags">
        ${folder.has_polished ? "<em class=\"done\">已润色</em>" : ""}
        ${folder.has_title ? "<em class=\"done\">已命名</em>" : ""}
        ${folder.has_summary ? "<em class=\"done\">已总结</em>" : ""}
        ${selected ? "<em class=\"picked\">已选中</em>" : ""}
      </span>
    </li>`;
}

function renderYoutubeItems() {
  $("youtubeCount").textContent = `${youtubeItems.length} 个视频，已选 ${selectedYoutubeUrls.size}`;
  $("youtubeList").innerHTML = youtubeItems
    .map((item) => {
      const selected = selectedYoutubeUrls.has(item.url);
      const meta = [item.channel, item.duration ? `${Math.round(Number(item.duration) / 60)} 分钟` : ""]
        .filter(Boolean)
        .join(" / ");
      return `
        <li class="selectable youtube-item ${selected ? "selected" : ""}" data-url="${escapeHtml(item.url)}">
          <span class="item-main">
            <strong>${escapeHtml(item.title || item.url)}</strong>
            <small>${escapeHtml(meta || item.url)}</small>
          </span>
          <span class="item-tags">
            ${selected ? "<em class=\"picked\">已选中</em>" : ""}
          </span>
        </li>`;
    })
    .join("") || "<li><span>还没有视频</span><span></span></li>";

  document.querySelectorAll(".youtube-item").forEach((item) => {
    item.addEventListener("click", () => {
      const url = item.dataset.url;
      selectedYoutubeUrls.has(url) ? selectedYoutubeUrls.delete(url) : selectedYoutubeUrls.add(url);
      renderYoutubeItems();
    });
  });
}

async function refreshYoutubeCookieStatus() {
  try {
    const config = await api("/api/youtube-config");
    $("youtubeCookieStatus").textContent = config.cookie_file_exists
      ? "已启用 cookies.txt"
      : "未检测 cookies.txt";
  } catch {
    $("youtubeCookieStatus").textContent = "cookies 状态未知";
  }
}

function youtubeHelpDismissed() {
  return localStorage.getItem(youtubeHelpStorageKey) === "1";
}

function updateYoutubeHelpBubble() {
  const button = $("youtubeHelpBtn");
  if (!button) return;
  button.classList.toggle("attention", youtubeHelpDismissed());
}

function renderYoutubeHelpPage() {
  document.querySelectorAll(".tutorial-page").forEach((page, index) => {
    page.classList.toggle("active", index === youtubeHelpPage);
  });
  $("youtubeHelpPageText").textContent = `${youtubeHelpPage + 1} / 3`;
  $("prevYoutubeHelp").disabled = youtubeHelpPage === 0;
  $("nextYoutubeHelp").textContent = youtubeHelpPage === 2 ? "完成" : "下一页";
}

function openYoutubeHelp(page = 0) {
  youtubeHelpPage = Math.max(0, Math.min(2, page));
  $("youtubeHelpDontShow").checked = youtubeHelpDismissed();
  renderYoutubeHelpPage();
  $("youtubeHelpModal").classList.add("open");
  $("youtubeHelpModal").setAttribute("aria-hidden", "false");
}

function closeYoutubeHelp() {
  if ($("youtubeHelpDontShow").checked) {
    localStorage.setItem(youtubeHelpStorageKey, "1");
  } else {
    localStorage.removeItem(youtubeHelpStorageKey);
  }
  $("youtubeHelpModal").classList.remove("open");
  $("youtubeHelpModal").setAttribute("aria-hidden", "true");
  updateYoutubeHelpBubble();
}

function maybeOpenYoutubeHelp() {
  if (youtubeHelpSeenThisSession || youtubeHelpDismissed()) {
    updateYoutubeHelpBubble();
    return;
  }
  youtubeHelpSeenThisSession = true;
  openYoutubeHelp(0);
}

async function loadImportedYoutubeItems({ quiet = false } = {}) {
  const data = await api("/api/youtube-imported");
  if (!data.items?.length) return;
  if (data.imported_at && data.imported_at === lastImportedAt && quiet) return;
  lastImportedAt = data.imported_at || Date.now();
  setYoutubeItems(data.items || []);
  if (!quiet) showProcess(`已从扩展导入 ${youtubeItems.length} 个视频`, "success");
}

function bindSelectionClicks() {
  document.querySelectorAll("[data-kind='input'], [data-kind='post-input']").forEach((item) => {
    item.addEventListener("click", () => {
      manualInputSelection = true;
      const name = item.dataset.name;
      selectedInputs.has(name) ? selectedInputs.delete(name) : selectedInputs.add(name);
      refresh();
    });
  });
  document.querySelectorAll("[data-kind='output'], [data-kind='post-output']").forEach((item) => {
    item.addEventListener("click", () => {
      manualOutputSelection = true;
      const name = item.dataset.name;
      selectedOutputs.has(name) ? selectedOutputs.delete(name) : selectedOutputs.add(name);
      refresh();
    });
  });
}

function renderFiles(data) {
  syncDefaultSelections(data);

  $("inputCount").textContent = `${data.inputs.length} 个文件，已选 ${selectedInputs.size}`;
  $("postInputCount").textContent = `${data.inputs.length} 个文件，已选 ${selectedInputs.size}`;
  $("outputCount").textContent = `${data.outputs.length} 个结果`;
  $("postOutputCount").textContent = `${data.outputs.length} 个结果，已选 ${selectedOutputs.size}`;

  const sortedInputs = data.inputs.slice().sort((a, b) => b.mtime - a.mtime);
  const sortedOutputs = data.outputs.slice().sort((a, b) => b.mtime - a.mtime);

  $("inputsList").innerHTML = sortedInputs
    .map((file) => renderInputItem(file, "input"))
    .join("") || "<li><span>还没有输入文件</span><span></span></li>";

  $("postInputsList").innerHTML = sortedInputs
    .map((file) => renderInputItem(file, "post-input"))
    .join("") || "<li><span>还没有输入文件</span><span></span></li>";

  $("outputsList").innerHTML = sortedOutputs
    .map((folder) => renderOutputItem(folder, "output"))
    .join("") || "<li><span>还没有结果</span><span></span></li>";

  $("postOutputsList").innerHTML = sortedOutputs
    .map((folder) => renderOutputItem(folder, "post-output"))
    .join("") || "<li><span>还没有结果</span><span></span></li>";

  bindSelectionClicks();
}

function renderSettings(settings) {
  $("watchDownloads").checked = Boolean(settings.watch_downloads);
  $("autoTitleRename").checked = Boolean(settings.auto_title_rename);
  $("autoClose").checked = Boolean(settings.auto_close_after_task);
  $("beamSize").value = settings.local_beam_size ?? 2;
  $("autoCloseMinutes").value = settings.auto_close_minutes ?? 30;
}

function renderTask(task) {
  const running = Boolean(task.running);
  $("taskState").textContent = running ? "运行中" : "";
  $("activeTask").textContent = backendTaskLabels[task.name] || task.name || "准备转写";
  $("taskHint").textContent = taskHint(task);
  $("workbenchCard").classList.toggle("running", running);
  $("taskStatusPanel").classList.toggle("running", running);
  $("progressGlow").parentElement.classList.toggle("running", running);

  if (lastRunning && !running) {
    showProcess(task.returncode === 0 ? "任务已完成" : "任务失败，请查看提示", task.returncode === 0 ? "success" : "error");
  }

  const done = !running && task.returncode === 0;
  $("doneActions").hidden = !done;
  lastRunning = running;
}

async function refresh() {
  const data = await api("/api/state");
  renderFiles(data);
  renderSettings(data.settings);
  renderTask(data.task);
}

function selectedRouteTask() {
  const route = document.querySelector('input[name="route"]:checked')?.value || "api";
  if (route === "api") return "api";
  const model = document.querySelector(".model-chip.active")?.dataset.model || "small";
  return model === "medium" ? "local-medium" : "local-small";
}

function selectedPayload(task) {
  const inputTasks = new Set(["api", "local-small", "local-medium", "high-quality-mp3"]);
  const outputTasks = new Set(["title", "summary"]);
  const payload = { task };
  if (inputTasks.has(task)) payload.files = [...selectedInputs];
  if (outputTasks.has(task)) payload.folders = [...selectedOutputs];
  if (task === "youtube-download") {
    payload.urls = [...selectedYoutubeUrls];
    payload.audio_only = $("youtubeAudioOnly").checked;
    payload.use_cookies = $("youtubeUseCookies").checked;
  }
  return payload;
}

function validateSelection(task) {
  if (["api", "local-small", "local-medium", "high-quality-mp3"].includes(task) && selectedInputs.size === 0) {
    showProcess("请先选择源文件", "error");
    return false;
  }
  if (["title", "summary"].includes(task) && selectedOutputs.size === 0) {
    showProcess("请先选择文字稿结果", "error");
    return false;
  }
  if (task === "youtube-download" && selectedYoutubeUrls.size === 0) {
    showProcess("请先选择 YouTube 视频", "error");
    return false;
  }
  return true;
}

async function runTask(task) {
  if (!validateSelection(task)) return;
  await api("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(selectedPayload(task)),
  });
  $("doneActions").hidden = true;
  showProcess(`已开始：${taskLabels[task] || task}`);
  await refresh();
}

async function saveSettings() {
  await api("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      watch_downloads: $("watchDownloads").checked,
      auto_title_rename: $("autoTitleRename").checked,
      auto_close_after_task: $("autoClose").checked,
      local_beam_size: Number($("beamSize").value || 2),
      auto_close_minutes: Number($("autoCloseMinutes").value || 30),
    }),
  });
  await refresh();
  closeSettings();
  showProcess("设置已保存", "success");
}

async function upload(files) {
  if (!files.length) return;
  const form = new FormData();
  [...files].forEach((file) => form.append("files", file));
  const response = await fetch("/api/upload", { method: "POST", body: form });
  if (!response.ok) {
    showProcess("文件导入失败", "error");
    return;
  }
  const payload = await response.json();
  manualInputSelection = true;
  selectedInputs.clear();
  (payload.uploaded || []).forEach((name) => selectedInputs.add(name));
  await refresh();
  showProcess(`已导入并选中 ${selectedInputs.size} 个文件`, "success");
}

async function openFolder(folder) {
  try {
    await api("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    showToast("已打开导出文件夹");
  } catch (error) {
    alert(`打开文件夹失败：${error.message}\n请关闭旧的前端窗口，然后重新运行 launch_frontend.ps1`);
  }
}

function openSettings() {
  $("settingsSaved").textContent = "";
  $("settingsModal").classList.add("open");
  $("settingsModal").setAttribute("aria-hidden", "false");
}

function closeSettings() {
  $("settingsModal").classList.remove("open");
  $("settingsModal").setAttribute("aria-hidden", "true");
}

function switchView(name) {
  const youtube = name === "youtube";
  const post = name === "postprocess";
  $("transcribeView").classList.toggle("active", !post && !youtube);
  $("youtubeView").classList.toggle("active", youtube);
  $("postprocessView").classList.toggle("active", post);
  $("showTranscribe").classList.toggle("active", !post && !youtube);
  $("showYoutube").classList.toggle("active", youtube);
  $("showPostprocess").classList.toggle("active", post);
  moveStatusPanel(post ? "post" : youtube ? "youtube" : "transcribe");
  if (youtube) {
    maybeOpenYoutubeHelp();
    loadImportedYoutubeItems({ quiet: true }).catch(() => {});
  }
}

function moveStatusPanel(target) {
  const panel = $("taskStatusPanel");
  const slot = target === "post" ? $("postStatusSlot") : target === "youtube" ? $("youtubeStatusSlot") : $("transcribeStatusSlot");
  if (panel.parentElement !== slot) {
    slot.appendChild(panel);
    panel.animate(
      [
        { opacity: 0, transform: "translateY(10px) scale(0.985)" },
        { opacity: 1, transform: "translateY(0) scale(1)" },
      ],
      { duration: 300, easing: "cubic-bezier(0.16, 1, 0.3, 1)" },
    );
  }
}

function setYoutubeItems(items) {
  youtubeItems = items || [];
  selectedYoutubeUrls.clear();
  youtubeItems.forEach((item) => {
    if (item.url) selectedYoutubeUrls.add(item.url);
  });
  renderYoutubeItems();
}

async function scanYoutube(source) {
  showProcess("正在读取 YouTube 列表");
  const payload = {
    source,
    playlist_url: $("playlistUrl").value.trim(),
    limit: Number($("youtubeLimit").value || 80),
  };
  if (source !== "custom") payload.playlist_url = "";
  const data = await api("/api/youtube-scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setYoutubeItems(data.items || []);
  showProcess(`已读取 ${youtubeItems.length} 个视频`, "success");
}

async function parseYoutubeLinks() {
  const data = await api("/api/youtube-parse-links", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: $("youtubeLinks").value }),
  });
  setYoutubeItems(data.items || []);
  showProcess(`已解析 ${youtubeItems.length} 个链接`, "success");
}

async function uploadYoutubeCookies(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  await api("/api/youtube-cookies", { method: "POST", body: form });
  await refreshYoutubeCookieStatus();
  showProcess("cookies.txt 已保存，后续扫描会优先使用它", "success");
}

function updateRouteSelection() {
  const route = document.querySelector('input[name="route"]:checked')?.value || "api";
  document.querySelectorAll(".route-option").forEach((option) => {
    option.classList.toggle("selected", option.dataset.route === route);
  });
  $("modelPicker").classList.toggle("disabled", route !== "local");
}

document.querySelectorAll(".model-chip").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    const localInput = document.querySelector('input[name="route"][value="local"]');
    if (localInput && !localInput.checked) {
      localInput.checked = true;
      updateRouteSelection();
    }
    document.querySelectorAll(".model-chip").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

document.querySelectorAll('input[name="route"]').forEach((input) => {
  input.addEventListener("change", updateRouteSelection);
});

document.querySelectorAll("[data-task]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await runTask(button.dataset.task);
    } catch (error) {
      showProcess(error.message, "error");
    }
  });
});

document.querySelectorAll(".card, .post-card").forEach((card) => {
  card.addEventListener("pointermove", (event) => {
    const rect = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${event.clientX - rect.left}px`);
    card.style.setProperty("--my", `${event.clientY - rect.top}px`);
  });
});

$("startTranscribe").addEventListener("click", async () => {
  try {
    await runTask(selectedRouteTask());
  } catch (error) {
    showProcess(error.message, "error");
  }
});
$("continueTranscribe").addEventListener("click", () => {
  $("doneActions").hidden = true;
  $("taskHint").textContent = "可以继续导入文件，或直接再次开始转写。";
});
$("goPostprocess").addEventListener("click", (event) => {
  event.preventDefault();
  switchView("postprocess");
  window.scrollTo({ top: 0, behavior: "auto" });
});
$("showTranscribe").addEventListener("click", () => switchView("transcribe"));
$("showYoutube").addEventListener("click", () => switchView("youtube"));
$("showPostprocess").addEventListener("click", () => switchView("postprocess"));
$("returnToTranscribe").addEventListener("click", () => {
  switchView("transcribe");
  window.scrollTo({ top: 0, behavior: "auto" });
});
$("settingsBtn").addEventListener("click", openSettings);
$("closeSettings").addEventListener("click", closeSettings);
$("settingsModal").addEventListener("click", (event) => {
  if (event.target.classList.contains("modal-backdrop")) closeSettings();
});
$("youtubeHelpBtn").addEventListener("click", () => openYoutubeHelp(0));
$("closeYoutubeHelp").addEventListener("click", closeYoutubeHelp);
$("prevYoutubeHelp").addEventListener("click", () => {
  youtubeHelpPage = Math.max(0, youtubeHelpPage - 1);
  renderYoutubeHelpPage();
});
$("nextYoutubeHelp").addEventListener("click", () => {
  if (youtubeHelpPage >= 2) {
    closeYoutubeHelp();
    return;
  }
  youtubeHelpPage += 1;
  renderYoutubeHelpPage();
});
$("youtubeHelpDontShow").addEventListener("change", updateYoutubeHelpBubble);
$("refreshBtn").addEventListener("click", refresh);
$("stopBtn").addEventListener("click", async () => {
  await api("/api/stop", { method: "POST" });
  showProcess("已请求停止当前任务");
  await refresh();
});
$("saveSettings").addEventListener("click", saveSettings);
$("fileInput").addEventListener("change", (event) => upload(event.target.files));
$("openOutputsBtn").addEventListener("click", () => openFolder("outputs"));
$("openOutputsBtn2").addEventListener("click", () => openFolder("outputs"));
$("scanWatchLater").addEventListener("click", () => scanYoutube("watchlater").catch((error) => showProcess(error.message, "error")));
$("scanLiked").addEventListener("click", () => scanYoutube("liked").catch((error) => showProcess(error.message, "error")));
$("scanFavorites").addEventListener("click", () => scanYoutube("favorites").catch((error) => showProcess(error.message, "error")));
$("scanCustomPlaylist").addEventListener("click", () => scanYoutube("custom").catch((error) => showProcess(error.message, "error")));
$("parseYoutubeLinks").addEventListener("click", () => parseYoutubeLinks().catch((error) => showProcess(error.message, "error")));
$("youtubeCookieFile").addEventListener("change", (event) => {
  uploadYoutubeCookies(event.target.files?.[0]).catch((error) => showProcess(error.message, "error"));
});
$("selectAllYoutube").addEventListener("click", () => {
  if (selectedYoutubeUrls.size === youtubeItems.length) {
    selectedYoutubeUrls.clear();
  } else {
    youtubeItems.forEach((item) => {
      if (item.url) selectedYoutubeUrls.add(item.url);
    });
  }
  renderYoutubeItems();
});
$("clearYoutube").addEventListener("click", () => setYoutubeItems([]));
$("downloadYoutube").addEventListener("click", async () => {
  try {
    await runTask("youtube-download");
    showProcess("已开始下载 YouTube 视频");
  } catch (error) {
    showProcess(error.message, "error");
  }
});
document.addEventListener("pointerdown", closeFloatingToasts);

const dropZone = $("dropZone");
dropZone.addEventListener("dragover", (event) => event.preventDefault());
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  upload(event.dataTransfer.files);
});

updateRouteSelection();
const initialParams = new URLSearchParams(window.location.search);
if (initialParams.get("view") === "youtube") {
  switchView("youtube");
  loadImportedYoutubeItems({ quiet: !initialParams.has("imported") }).catch((error) => showProcess(error.message, "error"));
} else {
  moveStatusPanel("transcribe");
}
renderYoutubeItems();
refreshYoutubeCookieStatus();
updateYoutubeHelpBubble();
refresh();
setInterval(refresh, 2000);
