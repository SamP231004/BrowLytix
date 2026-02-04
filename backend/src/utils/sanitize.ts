export function sanitizeText(input: string): string {
    return input
        .replace(/<[^>]*>/g, "")
        .replace(/https?:\/\/\S+/g, "")
        .replace(/[^\w\s]/g, "")
        .replace(/\s+/g, " ")
        .trim()
}