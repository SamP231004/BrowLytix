export function sanitizeText(input) {
    if (!input) return "";

    return input
        .replace(/<[^>]*>/g, "")          // remove HTML
        .replace(/https?:\/\/\S+/g, "")   // remove URLs
        .replace(/[^\w\s]/g, "")          // remove symbols
        .replace(/\s+/g, " ")             // normalize spaces
        .trim();
}