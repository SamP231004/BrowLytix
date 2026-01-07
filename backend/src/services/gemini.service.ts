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
    "models/gemini-2.5-flash",
    "models/gemini-2.0-flash-lite"
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

    const prompt = `Return exactly 3 recent news articles related to: ${keywords.join(", ")}. 
                    Respond ONLY in JSON format: [{"title": "string", "url": "string"}]`;

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