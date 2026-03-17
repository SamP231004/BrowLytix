import { Router } from "express"
import { sanitizeText } from "../utils/sanitize.js"
import { textToVector } from "../ai/vectorizer.js"
import { assignCategory } from "../ai/category.engine.js"
import { fetchNewsLinks } from "../services/news.service.js"

const router = Router()

router.post("/", async (req, res) => {
    try {
        const { userId, title, url, history } = req.body

        if (!userId) {
            return res.status(400).json({ error: "Missing userId" })
        }

        // 🔹 Combine context
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

        // 🔹 Vectorize
        const vector = textToVector(combinedText)

        // 🧠 PERSONAL MEMORY
        const { category, isNew } = await assignCategory(userId, vector)

        // 🔥 SMART KEYWORDS (cleaner)
        const keywords = combinedText
            .toLowerCase()
            .split(/\s+/)
            .filter(w =>
                w.length > 3 &&
                !["https", "www", "com", "html"].includes(w)
            )
            .slice(0, 10)

        // 📰 FETCH BETTER NEWS
        const links = await fetchNewsLinks(keywords, category.id)
        console.log("🧠 Keywords:", keywords);

        res.json({
            userId,
            categoryId: category.id,
            isNewCategory: isNew,
            keywords,
            links
        })

    } catch (err) {
        console.error("❌ Analyze error:", err)
        res.status(500).json({ error: "Server error" })
    }
})

export default router