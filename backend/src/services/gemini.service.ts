import fetch from "node-fetch";

type GeminiResponse = {
    candidates?: {
        content?: {
            parts?: { text?: string }[];
        };
    }[];
    error?: {
        code: number;
        message: string;
    };
};

const MODELS = [
    "models/gemini-3-flash",        // Newest, fastest, and most capable "Flash"
    "models/gemini-2.5-flash",      // Reliable 2.5 series
    "models/gemini-2.5-flash-lite", // Optimized for ultra-low latency
    "models/gemini-2.0-flash",      // Previous generation (often less traffic now)
    "models/gemini-1.5-flash",      // "Legacy" but extremely stable
];

async function sleep(ms: number) {
    return new Promise(res => setTimeout(res, ms));
}

async function callGemini(
    model: string,
    prompt: string,
    apiKey: string
): Promise<string | null> {

    const res = await fetch(
        `https://generativelanguage.googleapis.com/v1/${model}:generateContent?key=${apiKey}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                contents: [{ role: "user", parts: [{ text: prompt }] }]
            })
        }
    );

    const data = (await res.json()) as GeminiResponse;

    if (data.error) {
        console.warn(`⚠️ ${model} failed:`, data.error.message);
        return null;
    }

    return data.candidates?.[0]?.content?.parts?.[0]?.text ?? null;
}

async function sleepWithJitter(attempt: number) {
    const baseDelay = Math.pow(2, attempt) * 1000; // 2s, 4s, 8s...
    const jitter = Math.random() * 1000;
    return new Promise(res => setTimeout(res, baseDelay + jitter));
}

export async function fetchNewsLinks(
    keywords: string[],
    apiKey: string
): Promise<{ title: string; url: string }[]> {

    const prompt = `
        You are a news retrieval assistant.

        Return EXACTLY 3 REAL, EXISTING, and CLICKABLE news article links
        from ONLY the following trusted news websites:

        - https://www.thehindu.com
        - https://timesofindia.indiatimes.com
        - https://indianexpress.com
        - https://www.hindustantimes.com
        - https://www.bbc.com/news

        Rules (VERY IMPORTANT):
        - DO NOT invent or guess URLs
        - ONLY return articles that actually exist on the website
        - URLs must open successfully in a browser
        - Articles must be relevant to: ${keywords.join(", ")}

        Respond ONLY in valid JSON array format:
        [
        { "title": "string", "url": "string" }
        ]

        No explanation. No extra text.
    `;
    const MAX_TOTAL_ATTEMPTS = 5;

    for (let attempt = 1; attempt <= MAX_TOTAL_ATTEMPTS; attempt++) {
        // Interleave models: Attempt 1 uses Model A, Attempt 2 uses Model B...
        const model = MODELS[attempt % MODELS.length];

        console.log(`🔁 Attempt ${attempt} using ${model}...`);
        const text = await callGemini(model, prompt, apiKey);

        if (text) {
            try {
                // Improved JSON extraction in case model adds markdown blocks
                const jsonMatch = text.match(/\[[\s\S]*\]/);
                const cleanJson = jsonMatch ? jsonMatch[0] : text;
                return JSON.parse(cleanJson);
            } catch (e) {
                console.error("❌ JSON parse failed, retrying...");
            }
        }

        if (attempt < MAX_TOTAL_ATTEMPTS) {
            console.warn(`⚠️ ${model} overloaded. Backing off...`);
            await sleepWithJitter(attempt);
        }
    }

    console.error("❌ All attempts failed. Service remains overloaded.");
    return [];
}