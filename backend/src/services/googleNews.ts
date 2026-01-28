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
    url.searchParams.set("pageSize", "10"); // fetch extra, trim later

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

    // Deduplicate by URL
    const uniqueArticles = Array.from(
        new Map(data.articles.map(article => [article.url, article])).values()
    );

    // Take EXACTLY 3 most recent
    return uniqueArticles.slice(0, 3).map(article => ({
        title: article.title,
        url: article.url
    }));
}