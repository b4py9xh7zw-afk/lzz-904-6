/* 课程录屏画质优化器 - 前端逻辑 */
const $ = (id) => document.getElementById(id);
let fileId = null;

/* ---------- ffmpeg 健康检查 ---------- */
async function checkHealth() {
  const badge = $("ff-status");
  badge.className = "badge badge-loading";
  badge.textContent = "检测 ffmpeg 中…";
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.ok) {
      badge.className = "badge badge-ok";
      badge.textContent = `✅ ffmpeg 就绪`;
      badge.title = `${data.ffmpeg_path}\n${data.ffmpeg_version || ""}`;
      $("ff-alert").classList.add("hidden");
    } else {
      showFFAlert(data);
    }
  } catch (e) {
    showFFAlert({ error: "无法连接后端服务: " + e.message, instructions: [
      "确认后端已启动: python3 app.py",
      "确认浏览器访问的地址与端口正确（默认 http://127.0.0.1:5000）",
    ]});
  }
}

function showFFAlert(data) {
  const badge = $("ff-status");
  badge.className = "badge badge-fail";
  badge.textContent = "❌ ffmpeg 不可用";
  $("ff-error").textContent = " " + (data.error || "未知错误");
  const ol = $("ff-instructions");
  ol.innerHTML = "";
  (data.instructions || []).forEach((t) => {
    const li = document.createElement("li");
    li.textContent = t;
    ol.appendChild(li);
  });
  $("ff-alert").classList.remove("hidden");
}

$("btn-recheck").addEventListener("click", checkHealth);

/* ---------- 上传 ---------- */
const dz = $("dropzone");
dz.addEventListener("click", () => $("file-input").click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("dragover");
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
$("file-input").addEventListener("change", (e) => {
  if (e.target.files.length) uploadFile(e.target.files[0]);
});

async function uploadFile(file) {
  hideError();
  dz.querySelector("p").textContent = `⏳ 正在上传 ${file.name} …`;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
      if (data.instructions) showFFAlert(data);
      throw new Error(data.error || "上传失败");
    }
    fileId = data.file_id;
    dz.querySelector("p").textContent = `✅ 已上传：${data.filename}`;
    renderFileInfo(data.info);
    $("btn-process").disabled = false;
    // 默认预览长度: 整段短于 10s 则取整段
    if (data.info.duration && data.info.duration < 10) $("duration").value = 0;
  } catch (e) {
    dz.querySelector("p").textContent = "点击选择或拖拽视频文件到此处";
    showError(e.message);
  }
}

function renderFileInfo(info) {
  const items = [
    ["分辨率", `${info.width} × ${info.height}`],
    ["时长", info.duration != null ? `${info.duration} 秒` : "未知"],
    ["大小", info.size != null ? `${(info.size / 1048576).toFixed(1)} MB` : "未知"],
    ["视频编码", info.video_codec || "未知"],
    ["音频", info.audio_codec || "无音轨"],
  ];
  $("file-info").innerHTML = items
    .map(([k, v]) => `<div class="item"><b>${k}</b>${v}</div>`).join("");
  $("file-info").classList.remove("hidden");
}

/* ---------- 参数联动显示 ---------- */
[["sharpen", "sharpen-val", 1], ["denoise", "denoise-val", 1]].forEach(([src, out]) => {
  $(src).addEventListener("input", () => { $(out).textContent = $(src).value; });
});

/* ---------- 生成预览 ---------- */
$("btn-process").addEventListener("click", async () => {
  if (!fileId) return;
  hideError();
  $("progress").classList.remove("hidden");
  $("btn-process").disabled = true;
  const payload = {
    file_id: fileId,
    sharpen: parseFloat($("sharpen").value),
    denoise: parseFloat($("denoise").value),
    duration: parseFloat($("duration").value) || 0,
    resolution: $("resolution").value,
  };
  try {
    const res = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      if (data.instructions) showFFAlert(data);
      throw new Error(data.error + (data.detail ? "\n" + data.detail : ""));
    }
    showResult(data);
  } catch (e) {
    showError(e.message);
  } finally {
    $("progress").classList.add("hidden");
    $("btn-process").disabled = false;
  }
});

function showResult(data) {
  $("preview").src = data.preview_url + "?t=" + Date.now();
  $("output-meta").textContent =
    `输出大小 ${(data.output_size / 1024).toFixed(1)} KB · 滤镜链: ${data.filter_chain || "(无)"}`;
  $("command-text").textContent = data.command_text;
  $("dl-command").href = data.command_url;
  $("dl-report").href = data.report_url;
  $("dl-video").href = data.preview_url;
  $("result-card").classList.remove("hidden");
  $("result-card").scrollIntoView({ behavior: "smooth" });
}

/* ---------- 工具 ---------- */
function showError(msg) {
  $("error-box").textContent = "❌ " + msg;
  $("error-box").classList.remove("hidden");
}
function hideError() { $("error-box").classList.add("hidden"); }

checkHealth();
