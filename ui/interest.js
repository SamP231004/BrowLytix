export function extractKeywords(text) {
  if (!text) return ["technology"];

  return text
    .toLowerCase()
    .replace(/[^\w\s]/g, "")
    .split(" ")
    .filter(word => word.length > 3)
    .slice(0, 5);
}