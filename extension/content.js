console.log("🧩 BrowLytix content script LOADED");

// 🔥 Tell background we are ready
chrome.runtime.sendMessage({ type: "CONTENT_READY" });

// 🔥 SEND PAGE VISIT (THIS WAS MISSING)
chrome.runtime.sendMessage({
  type: "PAGE_VISIT",
  title: document.title,
  url: location.href
});

// Tell background we are ready
chrome.runtime.sendMessage({ type: "CONTENT_READY" });

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== "SHOW_NEWS") return;

  console.log("📰 SHOW_NEWS received in content.js", msg.payload);

  const links = msg.payload;
  if (!links || !links.length) return;

  document.getElementById("browlytix-panel")?.remove();

  const panel = document.createElement("div");
  panel.id = "browlytix-panel";

  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;">
      <strong>📰 Related News</strong>
      <span id="browlytix-close" style="cursor:pointer">✕</span>
    </div>
    <ul style="margin-top:8px;padding-left:16px;">
      ${links.map(l => `
        <li style="margin-bottom:6px">
          <a href="${l.url}" target="_blank" style="color:#4da3ff">
            ${l.title}
          </a>
        </li>
      `).join("")}
    </ul>
  `;

  Object.assign(panel.style, {
    position: "fixed",
    top: "16px",
    right: "16px",
    width: "320px",
    background: "#111",
    color: "#fff",
    padding: "12px",
    borderRadius: "10px",
    zIndex: 999999,
    boxShadow: "0 10px 30px rgba(0,0,0,0.35)"
  });

  document.body.appendChild(panel);

  document.getElementById("browlytix-close").onclick = () => panel.remove();
});
