chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== "SHOW_NEWS") return

  document.getElementById("browlytix-panel")?.remove()

  const panel = document.createElement("div")
  panel.id = "browlytix-panel"

  let body = ""

  if (msg.payload === "OVERLOADED") {
    body = `<p class="error">⚠️ AI is currently overloaded.<br/>Please try again later.</p>`
  } else {
    body = `
      <ul>
        ${msg.payload.map(l => `
          <li><a href="${l.url}" target="_blank">${l.title}</a></li>
        `).join("")}
      </ul>
    `
  }

  panel.innerHTML = `
    <div class="header">
      <span>📰 Related News</span>
      <button id="close">✕</button>
    </div>
    ${body}
  `

  document.body.appendChild(panel)

  document.getElementById("close").onclick = () => {
    panel.classList.add("hide")
    setTimeout(() => panel.remove(), 200)
  }
})

// Styles
const style = document.createElement("style")
style.textContent = `
#browlytix-panel {
  position: fixed;
  top: 16px;
  right: 16px;
  width: 320px;
  background: #121212;
  color: white;
  border-radius: 12px;
  padding: 12px;
  z-index: 999999;
  animation: slideIn 0.3s ease;
  box-shadow: 0 20px 40px rgba(0,0,0,0.4);
}

#browlytix-panel.hide {
  animation: slideOut 0.2s ease forwards;
}

#browlytix-panel .header {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
}

#browlytix-panel ul {
  padding-left: 16px;
}

#browlytix-panel li {
  margin: 6px 0;
}

#browlytix-panel a {
  color: #4da3ff;
  text-decoration: none;
}

.error {
  color: #ff8a8a;
  font-size: 13px;
}

@keyframes slideIn {
  from { transform: translateX(120%); }
  to { transform: translateX(0); }
}

@keyframes slideOut {
  to { transform: translateX(120%); opacity: 0; }
}
`
document.head.appendChild(style)
