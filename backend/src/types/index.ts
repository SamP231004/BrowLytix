export interface PageContext {
    title: string
    url: string
}

export interface AnalyzeRequestBody {
    // Single page visit
    title?: string
    url?: string

    // Optional browsing history
    history?: PageContext[]

    // User-provided Gemini API key
    apiKey: string
}

export interface NewsLink {
    title: string
    url: string
}

export interface AnalyzeResponse {
    keywords: string[]
    links: NewsLink[]
}