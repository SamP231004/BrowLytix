export function sanitizeText(input: string): string {
    return input
        .replace(/<[^>]*>?/gm, "")   // strip HTML tags
        .replace(/https?:\/\/\S+/g, "") // remove URLs
        .replace(/[^\w\s]/gi, "")    // remove special chars
        .replace(/\s+/g, " ")        // normalize whitespace
        .trim()
}