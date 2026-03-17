import redis from "../db/redis.js";
import { cosineSimilarity } from "./math.js";

function redisKey(userId) {
    return `ai:user:${userId}:categories`;
}

async function loadCategories(userId) {
    const raw = await redis.get(redisKey(userId));
    return raw ? JSON.parse(raw) : [];
}

async function saveCategories(userId, categories) {
    await redis.set(redisKey(userId), JSON.stringify(categories));
}

export async function assignCategory(userId, vector, threshold = 0.7) {
    let categories = [];

    try {
        categories = await loadCategories(userId);
    } catch (err) {
        console.log("⚠️ Redis not available, using fallback memory");
    }

    let bestMatch = null;
    let bestScore = 0;

    for (const cat of categories) {
        const score = cosineSimilarity(vector, cat.centroid);
        if (score > bestScore) {
            bestScore = score;
            bestMatch = cat;
        }
    }

    // 🆕 create new category
    if (!bestMatch || bestScore < threshold) {
        const newCategory = {
            id: `cat_${Date.now()}`,
            centroid: vector,
            count: 1,
            lastUpdated: Date.now()
        };

        categories.push(newCategory);
        try {
            await saveCategories(userId, categories);
        } catch { };

        return { category: newCategory, isNew: true };
    }

    // 🔄 update existing
    bestMatch.centroid = bestMatch.centroid.map(
        (v, i) => (v * bestMatch.count + vector[i]) / (bestMatch.count + 1)
    );

    bestMatch.count++;
    bestMatch.lastUpdated = Date.now();

    try {
        await saveCategories(userId, categories);
    } catch { }

    return { category: bestMatch, isNew: false };
}