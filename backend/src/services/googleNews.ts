import fetch from "node-fetch"

type NewsApiArticle = {
    title: string
    url: string
}

type NewsApiResponse = {
    status: "ok" | "error"
    articles?: NewsApiArticle[]
}

export async function fetchNewsLinks(
    keywords: string[],
    apiKey: string
): Promise<{ title: string; url: string }[]> {

    if (!keywords.length) return []

    const url = new URL("https://newsapi.org/v2/everything")
    url.searchParams.set("q", keywords.join(" OR "))
    url.searchParams.set("language", "en")
    url.searchParams.set("sortBy", "publishedAt")
    url.searchParams.set("pageSize", "10")

    const res = await fetch(url.toString(), {
        headers: { "X-Api-Key": apiKey }
    })

    if (!res.ok) return []

    const data = (await res.json()) as NewsApiResponse
    if (data.status !== "ok" || !data.articles) return []

    return data.articles
        .filter(a => a.title && a.url)
        .slice(0, 3)
        .map(a => ({ title: a.title, url: a.url }))
}