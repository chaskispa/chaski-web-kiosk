"use strict";

async function refreshWelcomeAddress() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    if (!response.ok) return;
    const status = await response.json();
    if (!status.ip || status.ip === "unavailable") return;
    document.querySelector("#welcome-ip").textContent = status.ip;
    document.querySelector("#welcome-management").textContent = `http://${status.ip}:${location.port || 80}`;
  } catch (_) {
    // The local control server may be restarting; the static address remains visible.
  }
}

refreshWelcomeAddress();
setInterval(refreshWelcomeAddress, 5000);
