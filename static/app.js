"use strict";

const $ = (selector) => document.querySelector(selector);
let messageTimer;
let networkInterfaces = [];
let networkAvailable = false;

function message(text, error = false) {
  const node = $("#message");
  node.textContent = text;
  node.className = error ? "show error" : "show";
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => { node.className = ""; }, 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? {"Content-Type": "application/json"} : {},
    cache: "no-store"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function duration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days ? `${days}d ${hours}h` : `${hours}h ${minutes}m`;
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    $("#connection").textContent = "Online";
    $("#connection").className = "badge";
    $("#player-name").textContent = s.player_name || "Unnamed player";
    $("#address").textContent = `http://${s.ip}:${location.port || 80}`;
    $("#state").textContent = s.state || "UNKNOWN";
    $("#state-detail").textContent = s.state_detail || "No player status yet";
    $("#chromium").textContent = s.chromium?.running ? "Running" : "Stopped";
    $("#saver-status").textContent = s.screensaver?.running ? "Playing" : (s.screensaver?.enabled ? "Armed" : "Disabled");
    $("#uptime").textContent = duration(s.uptime_seconds);
    $("#version").textContent = s.software_version || "—";
    $("#hostname").textContent = s.hostname || "—";
  } catch (error) {
    $("#connection").textContent = "Offline";
    $("#connection").className = "badge muted";
  }
}

async function loadConfig() {
  try {
    const c = await api("/api/config");
    $("#url").value = c.url;
    $("#saver-enabled").checked = c.screensaver.enabled;
    $("#timeout").value = c.screensaver.timeout_seconds;
    $("#media").value = c.screensaver.media;
  } catch (error) { message(error.message, true); }
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled || (button.id === "network-apply" && (!networkAvailable || !networkInterfaces.length));
  });
}

async function post(path, body) {
  setButtonsDisabled(true);
  try {
    await api(path, {method: "POST", body: body === undefined ? null : JSON.stringify(body)});
    message("Command accepted");
    setTimeout(refreshStatus, 800);
  } catch (error) { message(error.message, true); }
  finally { setButtonsDisabled(false); }
}

function populateNetworkFields() {
  const item = networkInterfaces.find((entry) => entry.interface === $("#network-interface").value);
  if (!item) return;
  $("#network-mode").value = item.mode || "dhcp";
  $("#static-fields").classList.toggle("hidden", $("#network-mode").value !== "static");
  $("#network-address").value = item.addresses?.[0] || "";
  $("#network-gateway").value = item.gateway || "";
  $("#network-dns").value = (item.dns || []).join(", ");
}

async function loadNetwork() {
  try {
    const network = await api("/api/network");
    networkAvailable = Boolean(network.available);
    networkInterfaces = network.interfaces || [];
    $("#network-backend").textContent = network.available ? network.backend : "Unavailable";
    $("#network-backend").className = network.available ? "badge" : "badge muted";
    $("#network-message").textContent = network.available
      ? "Select an interface. Current addresses are loaded for reference."
      : network.message;
    const select = $("#network-interface");
    select.replaceChildren();
    networkInterfaces.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.interface;
      option.textContent = `${item.interface} · ${item.type} · ${item.state}`;
      select.append(option);
    });
    $("#network-apply").disabled = !network.available || !networkInterfaces.length;
    populateNetworkFields();
  } catch (error) {
    networkAvailable = false;
    $("#network-backend").textContent = "Unavailable";
    $("#network-message").textContent = error.message;
    $("#network-apply").disabled = true;
  }
}

function uploadVideo(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/media");
    request.setRequestHeader("Content-Type", "video/mp4");
    request.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      $("#upload-progress").value = percent;
      $("#upload-label").textContent = `${percent}%`;
    });
    request.addEventListener("load", () => {
      let payload = {};
      try { payload = JSON.parse(request.responseText); } catch (_) { /* handled below */ }
      if (request.status >= 200 && request.status < 300) resolve(payload);
      else reject(new Error(payload.error || `Upload failed (${request.status})`));
    });
    request.addEventListener("error", () => reject(new Error("Upload connection failed")));
    request.send(file);
  });
}

$("#url-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await post("/api/url", {url: $("#url").value});
});
$("#saver-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await post("/api/config", {screensaver: {
    enabled: $("#saver-enabled").checked,
    timeout_seconds: Number($("#timeout").value),
    media: $("#media").value
  }});
});
$("#video-upload").addEventListener("click", async () => {
  const file = $("#video-file").files[0];
  if (!file) { message("Choose an MP4 file first", true); return; }
  if (!file.name.toLowerCase().endsWith(".mp4")) { message("The selected file must use the .mp4 extension", true); return; }
  setButtonsDisabled(true);
  $("#upload-progress").value = 0;
  $("#upload-label").textContent = "Starting upload…";
  try {
    const result = await uploadVideo(file);
    $("#media").value = result.media;
    $("#upload-progress").value = 100;
    $("#upload-label").textContent = `${result.filename} uploaded`;
    message("Video uploaded and selected");
    setTimeout(refreshStatus, 800);
  } catch (error) {
    $("#upload-label").textContent = "Upload failed";
    message(error.message, true);
  } finally { setButtonsDisabled(false); }
});
$("#reload").addEventListener("click", () => post("/api/reload"));
$("#restart").addEventListener("click", () => post("/api/restart-playback"));
$("#saver-start").addEventListener("click", () => post("/api/screensaver/start"));
$("#saver-stop").addEventListener("click", () => post("/api/screensaver/stop"));
$("#reboot").addEventListener("click", () => {
  if (window.confirm("Reboot this player now?")) post("/api/reboot");
});
$("#network-mode").addEventListener("change", () => {
  $("#static-fields").classList.toggle("hidden", $("#network-mode").value !== "static");
});
$("#network-interface").addEventListener("change", populateNetworkFields);
$("#network-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const mode = $("#network-mode").value;
  const payload = {
    interface: $("#network-interface").value,
    mode,
    address: mode === "static" ? $("#network-address").value.trim() : "",
    gateway: mode === "static" ? $("#network-gateway").value.trim() : "",
    dns: mode === "static" ? $("#network-dns").value.trim() : "",
    ssid: $("#network-ssid").value,
    password: $("#network-password").value
  };
  if (!window.confirm("Apply these network settings? This management page may disconnect.")) return;
  setButtonsDisabled(true);
  try {
    const result = await api("/api/network", {method: "POST", body: JSON.stringify(payload)});
    const reconnect = result.reconnect_address ? ` Reconnect at http://${result.reconnect_address}:${location.port || 8080}` : "";
    const notice = `Network change scheduled.${reconnect}`;
    $("#network-message").textContent = notice;
    message(notice);
    $("#network-password").value = "";
  } catch (error) { message(error.message, true); }
  finally { setButtonsDisabled(false); }
});

loadConfig();
loadNetwork();
refreshStatus();
setInterval(refreshStatus, 5000);
