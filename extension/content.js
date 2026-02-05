console.log("🧠 BrowLytix content script loaded")

chrome.runtime.sendMessage({ type: "CONTENT_READY" })

chrome.runtime.sendMessage({
  type: "PAGE_VISIT",
  title: document.title,
  url: location.href
})

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "SHOW_NEWS") {
    if (msg.payload.type === "links") {
      injectNews(msg.payload.data)
    }
  }
})

function injectNews(links) {
  if (document.getElementById("browlytix-panel")) return

  const panel = document.createElement("div")
  panel.id = "browlytix-panel"

  panel.innerHTML = `
    <div class="bl-header">
      <span class="bl-title">📰 Related Reads</span>
      <button id="bl-close">✕</button>
    </div>

    <div class="bl-body">
      ${links.map((l, i) => `
        <a href="${l.url}" target="_blank" class="bl-link">
          <span class="bl-bullet"></span>
          <span class="bl-text">${l.title}</span>
        </a>
        ${i < links.length - 1 ? `<div class="bl-divider"></div>` : ""}
      `).join("")}
    </div>
  `

  const style = document.createElement("style")
  style.textContent = `
    #browlytix-panel {
      position: fixed;
      top: 80px;
      right: 24px;
      width: 280px;
      background: rgba(18, 18, 18, 0.88);
      backdrop-filter: blur(14px);
      border-radius: 14px;
      color: #fff;
      font-family: Inter, system-ui, sans-serif;
      z-index: 2147483647;
      box-shadow: 0 16px 40px rgba(0,0,0,0.45);
      animation: bl-fade-in 0.25s ease;
    }

    @keyframes bl-fade-in {
      from { opacity: 0; transform: translateY(-6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .bl-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 14px;
      font-size: 13px;
      font-weight: 600;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }

    .bl-header button {
      background: none;
      border: none;
      color: #aaa;
      cursor: pointer;
    }

    .bl-header button:hover {
      color: #fff;
    }

    .bl-body {
      padding: 10px 14px 12px;
      max-height: 220px;
      overflow-y: auto;
    }

    .bl-link {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      color: #7ecbff;
      text-decoration: none;
      font-size: 13px;
      line-height: 1.45;
      margin-bottom: 8px;
    }

    .bl-link:hover {
      color: #fff;
      transform: translateX(2px);
    }

    .bl-bullet {
      margin-top: 6px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #7ecbff;
      flex-shrink: 0;
    }

    .bl-divider {
      height: 1px;
      background: rgba(255,255,255,0.08);
      margin: 8px 0;
    }
  `

  document.head.appendChild(style)
  document.body.appendChild(panel)

  document.getElementById("bl-close").onclick = () => panel.remove()
}