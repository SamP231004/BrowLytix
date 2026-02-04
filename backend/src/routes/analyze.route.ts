import { Router } from "express"
import { sanitizeText } from "../utils/sanitize.js"
import { textToVector } from "../ai/vectorizer.js"
import { assignCategory } from "../ai/category.engine.js"
import { fetchNewsLinks } from "../services/googleNews.js"

const router = Router()

router.post("/", async (req, res) => {
    try {
        const { userId, title, url, history, apiKey } = req.body

        if (!userId) {
            return res.status(400).json({ error: "Missing userId" })
        }

        if (!apiKey) {
            return res.status(400).json({ error: "Missing News API key" })
        }

        let combinedText = ""

        if (title) combinedText += " " + sanitizeText(title)
        if (url) combinedText += " " + sanitizeText(url)

        if (Array.isArray(history)) {
            combinedText += " " + history
                .map(h => sanitizeText(h.title || ""))
                .join(" ")
        }

        if (!combinedText.trim()) {
            return res.json({ category: null, links: [] })
        }

        const vector = textToVector(combinedText)

        // 🧠 PERSONAL AI MEMORY
        const { category, isNew } = await assignCategory(userId, vector)

        const keywords = combinedText
            .toLowerCase()
            .split(/\s+/)
            .slice(0, 5)

        const links = await fetchNewsLinks(keywords, apiKey)

        res.json({
            userId,
            categoryId: category.id,
            isNewCategory: isNew,
            links
        })

    } catch (err) {
        console.error("❌ Analyze error:", err)
        res.status(500).json({ error: "Server error" })
    }
})

export default router