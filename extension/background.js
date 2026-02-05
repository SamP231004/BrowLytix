const readyTabs = new Set()
const urlCache = new Map()

const API_BASE_URL = "https://brow-lytix.vercel.app"
// const API_BASE_URL = "http://localhost:4000"

/* =======================
   USER ID (PERSONALIZATION)
======================= */
async function getUserId() {
    const { userId } = await chrome.storage.local.get("userId")
    if (userId) return userId

    const newId = "user_" + crypto.randomUUID()
    await chrome.storage.local.set({ userId: newId })
    console.log("🧠 Generated new userId:", newId)
    return newId
}

/* =======================
   Startup & Diagnostics
======================= */
chrome.storage.sync.get("newsApiKey", (data) => {
    console.log(
        "🗞️ Google News API key on startup:",
        data.newsApiKey ? "FOUND" : "NOT FOUND"
    )
})

chrome.runtime.onInstalled.addListener(() => {
    console.log("🚀 BrowLytix installed")
})

/* =======================
   Message Router
======================= */
chrome.runtime.onMessage.addListener(async (msg, sender) => {
    switch (msg.type) {
        case "READ_HISTORY":
            readHistory()
            break

        case "PAGE_VISIT":
            await handlePageVisit(msg, sender)
            break

        case "CONTENT_READY":
            if (sender.tab?.id) {
                readyTabs.add(sender.tab.id)
            }
            break

        default:
            break
    }
})

/* =======================
   Page Visit Handler
======================= */
async function handlePageVisit(msg, sender) {
    const { title, url } = msg
    if (!sender.tab || !sender.tab.id) return

    const tabId = sender.tab.id

    // 🔹 URL cache
    if (urlCache.has(url)) {
        chrome.tabs.sendMessage(tabId, {
            type: "SHOW_NEWS",
            payload: {
                type: "links",
                data: urlCache.get(url)
            }
        })
        return
    }

    const { newsApiKey } = await chrome.storage.sync.get("newsApiKey")
    if (!newsApiKey) return

    try {
        const userId = await getUserId()

        const res = await fetch(`${API_BASE_URL}/api/analyze`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                userId,
                title,
                url,
                apiKey: newsApiKey
            })
        })

        const data = await res.json()
        console.log("📩 FULL backend response:", JSON.stringify(data, null, 2))

        if (!data.links || !data.links.length) return

        urlCache.set(url, data.links)

        const message = {
            type: "SHOW_NEWS",
            payload: {
                type: "links",
                data: data.links
            }
        }

        if (!readyTabs.has(tabId)) {
            setTimeout(() => {
                chrome.tabs.sendMessage(tabId, message)
            }, 300)
        } else {
            chrome.tabs.sendMessage(tabId, message)
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
    if (!hasPermission) return

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

            const { newsApiKey } = await chrome.storage.sync.get("newsApiKey")
            if (!newsApiKey) return

            try {
                const userId = await getUserId()

                await fetch(`${API_BASE_URL}/api/analyze`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        userId,
                        history: pages,
                        apiKey: newsApiKey
                    })
                })
            } catch (err) {
                console.error("❌ Failed to send history:", err)
            }
        }
    )
}