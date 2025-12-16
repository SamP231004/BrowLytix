import fetch from "node-fetch";

type GeminiResponse = {
    candidates?: {
        content?: {
            parts?: { text?: string }[];
        };
    }[];
};

export async function fetchNewsLinks(
    keywords: string[],
    apiKey: string
): Promise<{ title: string; url: string }[]> {

    console.log("🚀 [fetchNewsLinks] Keywords:", keywords);

    const prompt = `
Return exactly 3 recent news articles related to:
${keywords.join(", ")}

Rules:
- Output ONLY valid JSON
- No markdown
- No explanation
- Format:
[
  { "title": "string", "url": "string" }
]
`;

    const res = await fetch(
        "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key=" +
        apiKey,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                contents: [
                    {
                        role: "user",
                        parts: [{ text: prompt }]
                    }
                ]
            })
        }
    );

    const data = (await res.json()) as GeminiResponse;

    console.log("🔮 Gemini raw response:", JSON.stringify(data, null, 2));

    const text =
        data?.candidates?.[0]?.content?.parts?.[0]?.text;

    if (!text) {
        console.error("❌ Gemini returned no text");
        return [];
    }

    try {
        const clean = text.replace(/```json|```/g, "").trim();
        return JSON.parse(clean);
    } catch {
        console.error("❌ JSON parse failed:", text);
        return [];
    }
}
