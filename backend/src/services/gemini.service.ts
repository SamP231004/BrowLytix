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

export async function fetchNewsLinks(
    keywords: string[],
    apiKey: string
): Promise<{ title: string; url: string }[]> {

    const prompt = `
        Return exactly 3 recent news articles related to:
        ${keywords.join(", ")}

        Respond ONLY in valid JSON:
        [
        { "title": "string", "url": "string" }
        ]
    `;

    for (const model of MODELS) {
        for (let attempt = 1; attempt <= 3; attempt++) {
            console.log(`🔁 Trying ${model} (attempt ${attempt})`);

            const text = await callGemini(model, prompt, apiKey);

            if (text) {
                try {
                    return JSON.parse(
                        text.replace(/```json|```/g, "").trim()
                    );
                } catch {
                    console.error("❌ JSON parse failed");
                    return [];
                }
            }

            await sleep(1000 * attempt);
        }
    }

    console.error("❌ All Gemini models overloaded");
    return [];
}
