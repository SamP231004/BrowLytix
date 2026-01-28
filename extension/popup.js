const keyInput = document.getElementById("key")
const saveBtn = document.getElementById("save")
const editBtn = document.getElementById("edit")
const toggle = document.getElementById("toggle")
const status = document.getElementById("status")

// Load existing API key
chrome.storage.sync.get("newsApiKey", (data) => {
    if (data.newsApiKey) {
        keyInput.value = data.newsApiKey
        status.textContent = "API key saved"
    }
})

editBtn.onclick = () => {
    keyInput.disabled = false
    saveBtn.disabled = false
    status.textContent = "Edit your Google News API key"
}

saveBtn.onclick = async () => {
    const key = keyInput.value.trim()
    if (!key) return

    await chrome.storage.sync.set({ newsApiKey: key })
    keyInput.disabled = true
    saveBtn.disabled = true
    status.textContent = "API key updated"
}

toggle.onclick = () => {
    keyInput.type = keyInput.type === "password" ? "text" : "password"
}

// History permission
document.getElementById("history").onclick = () => {
    chrome.permissions.request({ permissions: ["history"] }, granted => {
        if (granted) {
            chrome.runtime.sendMessage({ type: "READ_HISTORY" })
            status.textContent = "Browsing insights enabled"
        }
    })
}