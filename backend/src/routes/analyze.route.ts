import { Router } from "express"
import { extractKeywords } from "../services/keyword.service.js"
import { fetchNewsLinks } from "../services/gemini.service.js"
import { sanitizeText } from "../utils/sanitize.js"

const router = Router()

router.post("/", async (req, res) => {
    try {
        const { title, history, apiKey } = req.body

        if (!apiKey) {
            return res.status(400).json({ error: "Missing Gemini API key" })
        }

        let keywords: string[] = []

        // 🔹 Case 1: Single page visit
        if (title) {
            const cleanTitle = sanitizeText(title)
            keywords = extractKeywords(cleanTitle)
        }

        // 🔹 Case 2: Browsing history
        if (Array.isArray(history)) {
            const titles = history
                .map((p) => sanitizeText(p.title || ""))
                .join(" ")

            keywords = extractKeywords(titles)
        }

        if (!keywords.length) {
            return res.json({ keywords: [], links: [] })
        }

        console.log("🧠 Extracted keywords:", keywords)

        const links = await fetchNewsLinks(keywords, apiKey)

        console.log("📰 Final links:", links)

        res.json({ keywords, links })
    } catch (err) {
        console.error("❌ Analyze error:", err)
        res.status(500).json({ error: "Failed to process request" })
    }
})

export default router