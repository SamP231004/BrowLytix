import fetch from "node-fetch";

// 🔹 Build clean query (NO internal category IDs)
function buildSmartQuery(keywords) {
    if (!keywords || keywords.length === 0) {
        return "technology OR AI OR startups";
    }

    // take top keywords only
    return keywords.slice(0, 5).join(" OR ");
}

export async function fetchNewsLinks(keywords) {
    try {
        const query = buildSmartQuery(keywords);

        console.log("📰 Query:", query);

        const url = new URL("https://newsapi.org/v2/everything");

        url.searchParams.set("q", query);
        url.searchParams.set("language", "en");
        url.searchParams.set("sortBy", "publishedAt"); // 🔥 more reliable
        url.searchParams.set("pageSize", "10");

        const res = await fetch(url.toString(), {
            headers: {
                "X-Api-Key": process.env.NEWS_API_KEY
            }
        });

        if (!res.ok) {
            console.error("❌ News API error:", res.status);
            return fallbackNews();
        }

        const data = await res.json();

        console.log("📰 Raw Articles Count:", data.articles?.length || 0);

        // ✅ Filter valid articles
        const articles = (data.articles || [])
            .filter(a => a.title && a.url)
            .slice(0, 5)
            .map(a => ({
                title: a.title,
                url: a.url
            }));

        // 🔥 Fallback if empty
        if (articles.length === 0) {
            console.log("⚠️ No relevant news, using fallback...");
            return fallbackNews();
        }

        return articles;

    } catch (err) {
        console.error("❌ News fetch error:", err);
        return fallbackNews();
    }
}

// 🔥 Always guarantee results
async function fallbackNews() {
    try {
        const url = new URL("https://newsapi.org/v2/everything");

        url.searchParams.set("q", "technology OR AI OR startups");
        url.searchParams.set("language", "en");
        url.searchParams.set("sortBy", "publishedAt");
        url.searchParams.set("pageSize", "5");

        const res = await fetch(url.toString(), {
            headers: {
                "X-Api-Key": process.env.NEWS_API_KEY
            }
        });

        const data = await res.json();

        return (data.articles || [])
            .slice(0, 5)
            .map(a => ({
                title: a.title,
                url: a.url
            }));

    } catch {
        // 🔥 absolute fallback (never empty UI)
        return [
            {
                title: "Latest technology news",
                url: "https://news.google.com"
            }
        ];
    }
}