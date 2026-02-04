import redis from "../db/redis.js"
import { cosineSimilarity } from "./math.js"

export type CategoryProfile = {
    id: string
    centroid: number[]
    count: number
    lastUpdated: number
}

function redisKey(userId: string) {
    return `ai:user:${userId}:categories`
}

async function loadCategories(userId: string): Promise<CategoryProfile[]> {
    const raw = await redis.get(redisKey(userId))
    return raw ? JSON.parse(raw) : []
}

async function saveCategories(userId: string, categories: CategoryProfile[]) {
    await redis.set(redisKey(userId), JSON.stringify(categories))
}

export async function assignCategory(
    userId: string,
    vector: number[],
    similarityThreshold = 0.75
): Promise<{ category: CategoryProfile; isNew: boolean }> {

    const categories = await loadCategories(userId)

    let bestMatch: CategoryProfile | null = null
    let bestScore = 0

    for (const cat of categories) {
        const score = cosineSimilarity(vector, cat.centroid)
        if (score > bestScore) {
            bestScore = score
            bestMatch = cat
        }
    }

    // 🆕 Create new personal category
    if (!bestMatch || bestScore < similarityThreshold) {
        const newCategory: CategoryProfile = {
            id: `cat_${Date.now()}`,
            centroid: vector,
            count: 1,
            lastUpdated: Date.now()
        }

        categories.push(newCategory)
        await saveCategories(userId, categories)

        return { category: newCategory, isNew: true }
    }

    // 🔄 Online learning (per user)
    bestMatch.centroid = bestMatch.centroid.map(
        (v, i) => (v * bestMatch.count + vector[i]) / (bestMatch.count + 1)
    )

    bestMatch.count++
    bestMatch.lastUpdated = Date.now()

    await saveCategories(userId, categories)

    return { category: bestMatch, isNew: false }
}