const keyInput = document.getElementById("key")
const saveBtn = document.getElementById("save")
const editBtn = document.getElementById("edit")
const toggle = document.getElementById("toggle")
const status = document.getElementById("status")

// Load existing key
chrome.storage.sync.get("geminiKey", (data) => {
    if (data.geminiKey) {
        keyInput.value = data.geminiKey
        status.textContent = "API key saved"
    }
})

editBtn.onclick = () => {
    keyInput.disabled = false
    saveBtn.disabled = false
    status.textContent = "Edit your API key"
}

saveBtn.onclick = async () => {
    const key = keyInput.value.trim()
    if (!key) return

    await chrome.storage.sync.set({ geminiKey: key })
    keyInput.disabled = true
    saveBtn.disabled = true
    status.textContent = "API key updated"
}

toggle.onclick = () => {
    keyInput.type = keyInput.type === "password" ? "text" : "password"
}

// History
document.getElementById("history").onclick = () => {
    chrome.permissions.request({ permissions: ["history"] }, granted => {
        if (granted) {
            chrome.runtime.sendMessage({ type: "READ_HISTORY" })
            status.textContent = "Browsing insights enabled"
        }
    })
}