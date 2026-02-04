const CATEGORY_KEYWORDS: Record<string, string[]> = {
    food: ["zomato", "swiggy", "restaurant", "food", "cafe"],
    travel: ["flight", "hotel", "travel", "trip", "booking"],
    tech: ["ai", "software", "startup", "technology", "coding"],
    finance: ["stock", "crypto", "market", "investment"]
}

export function detectCategory(text: string): string | null {
    const lower = text.toLowerCase()

    for (const category in CATEGORY_KEYWORDS) {
        if (CATEGORY_KEYWORDS[category].some(k => lower.includes(k))) {
            return category
        }
    }

    return null
}

export function buildCategoryQuery(category: string): string[] {
    return CATEGORY_KEYWORDS[category] || []
}