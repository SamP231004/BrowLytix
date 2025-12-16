const readyTabs = new Set();
const lastRequestTime = new Map()
const urlCache = new Map()
const REQUEST_COOLDOWN = 15_000 // 15 seconds per tab

/* =======================
   Startup & Diagnostics
======================= */

chrome.storage.sync.get("geminiKey", (data) => {
    console.log(
        "🔑 Gemini key on startup:",
        data.geminiKey ? "FOUND" : "NOT FOUND"
    )
})

chrome.runtime.onInstalled.addListener(() => {
    console.log("🚀 BrowLytix installed")
})

/* =======================
   Message Router (SINGLE)
======================= */

chrome.runtime.onMessage.addListener(async (msg, sender) => {
    switch (msg.type) {
        case "READ_HISTORY":
            console.log("📚 READ_HISTORY triggered from popup")
            readHistory()
            break

        case "PAGE_VISIT":
            await handlePageVisit(msg, sender)
            break

        case "CONTENT_READY":
            if (sender.tab?.id) {
                console.log("🧩 Content ready for tab", sender.tab.id);
                readyTabs.add(sender.tab.id);
            }
            break;

        default:
            // ignore
            break
    }
})

/* =======================
   Page Visit Handler
======================= */

async function handlePageVisit(msg, sender) {
    const { title, url } = msg

    if (!sender.tab || !sender.tab.id) {
        console.warn("⚠️ No tab info, cannot show UI")
        return
    }

    const tabId = sender.tab.id
    const now = Date.now()

    // ============================
    // 1️⃣ DEBOUNCE (per tab)
    // ============================
    const last = lastRequestTime.get(tabId) || 0
    if (now - last < REQUEST_COOLDOWN) {
        console.warn("⏳ Skipping request (cooldown active)")
        return
    }
    lastRequestTime.set(tabId, now)

    // ============================
    // 2️⃣ CACHE (per URL)
    // ============================
    if (urlCache.has(url)) {
        console.log("📦 Using cached news for URL")

        chrome.tabs.sendMessage(tabId, {
            type: "SHOW_NEWS",
            payload: urlCache.get(url)
        })

        return
    }

    // ============================
    // 3️⃣ API KEY CHECK
    // ============================
    const { geminiKey } = await chrome.storage.sync.get("geminiKey")
    if (!geminiKey) {
        console.warn("❌ Gemini key missing")
        return
    }

    try {
        // ============================
        // 4️⃣ CALL BACKEND
        // ============================
        const res = await fetch("http://localhost:4000/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title,
                url,
                apiKey: geminiKey
            })
        })

        const data = await res.json()
        console.log("📩 Backend response:", data)

        if (!data.links || !data.links.length) {
            console.warn("📰 No links returned from backend")
            return
        }

        // Cache result
        urlCache.set(url, data.links)

        // ============================
        // 5️⃣ SEND TO CONTENT SCRIPT
        // ============================
        if (!readyTabs.has(tabId)) {
            console.warn("⚠️ Content not ready yet, retrying...")
            setTimeout(() => {
                chrome.tabs.sendMessage(tabId, {
                    type: "SHOW_NEWS",
                    payload: data.links
                })
            }, 500)
        } else {
            chrome.tabs.sendMessage(tabId, {
                type: "SHOW_NEWS",
                payload: data.links
            })
        }

    } catch (err) {
        console.error("❌ Page analyze failed:", err)
    }
}


/* =======================
   History Reader
======================= */

async function readHistory() {
    const hasPermission = await chrome.permissions.contains({
        permissions: ["history"]
    })

    if (!hasPermission) {
        console.warn("❌ History permission not granted")
        return
    }

    chrome.history.search(
        {
            text: "",
            startTime: Date.now() - 1000 * 60 * 60 * 24,
            maxResults: 20
        },
        async (results) => {
            const pages = results.map(item => ({
                title: item.title || "",
                url: item.url || ""
            }))

            console.log("📚 Sending history to backend:", pages.length)

            const { geminiKey } = await chrome.storage.sync.get("geminiKey")
            if (!geminiKey) return

            try {
                await fetch("http://localhost:4000/api/analyze", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        history: pages,
                        apiKey: geminiKey
                    })
                })
            } catch (err) {
                console.error("❌ Failed to send history:", err)
            }
        }
    )
}
