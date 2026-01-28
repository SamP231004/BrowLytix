import fetch from "node-fetch";

type NewsApiArticle = {
    title: string;
    url: string;
    publishedAt: string;
};

type NewsApiResponse = {
    status: "ok" | "error";
    totalResults?: number;
    articles?: NewsApiArticle[];
    message?: string;
};

/**
 * Normalize URL to avoid duplicates caused by tracking params
 */
function normalizeUrl(rawUrl: string): string {
    try {
        const url = new URL(rawUrl);
        url.search = ""; // remove query params
        url.hash = "";
        return url.toString();
    } catch {
        return rawUrl;
    }
}

/**
 * Check if article title strongly matches keywords
 */
function matchesKeywords(title: string, keywords: string[]): boolean {
    const lowerTitle = title.toLowerCase();
    return keywords.some(keyword =>
        lowerTitle.includes(keyword.toLowerCase())
    );
}

/**
 * Fetch REAL, LIVE, CURRENT news articles using Google News (via NewsAPI)
 */
export async function fetchNewsLinks(
    keywords: string[],
    apiKey: string
): Promise<{ title: string; url: string }[]> {

    const query = keywords.join(" OR ");

    const fromDate = new Date();
    fromDate.setDate(fromDate.getDate() - 7);

    const url = new URL("https://newsapi.org/v2/everything");
    url.searchParams.set("q", query);
    url.searchParams.set("from", fromDate.toISOString());
    url.searchParams.set("sortBy", "publishedAt");
    url.searchParams.set("language", "en");
    url.searchParams.set("pageSize", "20");

    const res = await fetch(url.toString(), {
        headers: {
            "X-Api-Key": apiKey
        }
    });

    if (!res.ok) {
        console.error("❌ News API request failed:", res.statusText);
        return [];
    }

    const data = (await res.json()) as NewsApiResponse;

    if (data.status !== "ok" || !data.articles) {
        console.error("❌ News API error:", data.message);
        return [];
    }

    const seenUrls = new Set<string>();
    const results: { title: string; url: string }[] = [];

    for (const article of data.articles) {
        if (!article.title || !article.url) continue;

        // 🔒 Enforce keyword relevance
        if (!matchesKeywords(article.title, keywords)) continue;

        // 🔁 Enforce unique links
        const normalizedUrl = normalizeUrl(article.url);
        if (seenUrls.has(normalizedUrl)) continue;

        seenUrls.add(normalizedUrl);
        results.push({
            title: article.title,
            url: article.url
        });

        // 🎯 Stop once we have exactly 3
        if (results.length === 3) break;
    }

    return results;
}