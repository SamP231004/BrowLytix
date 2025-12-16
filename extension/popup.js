document.getElementById("save").onclick = async () => {
    const key = document.getElementById("key").value.trim()

    if (!key) {
        alert("Please enter a Gemini API key")
        return
    }

    await chrome.storage.sync.set({ geminiKey: key })

    console.log("✅ Gemini key saved:", key.slice(0, 6) + "****")

    alert("Gemini API key saved locally")
}

document.getElementById("history").onclick = async () => {
    chrome.permissions.request(
        { permissions: ["history"] },
        (granted) => {
            if (granted) {
                console.log("✅ History permission granted")

                // 🔥 Explicitly tell background to read history
                chrome.runtime.sendMessage({ type: "READ_HISTORY" })
            } else {
                alert("Permission denied")
            }
        }
    )
}

