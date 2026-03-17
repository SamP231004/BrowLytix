export function cosineSimilarity(a, b) {
    let dot = 0;
    let magA = 0;
    let magB = 0;

    const len = Math.min(a.length, b.length);

    for (let i = 0; i < len; i++) {
        dot += a[i] * b[i];
        magA += a[i] * a[i];
        magB += b[i] * b[i];
    }

    return magA && magB
        ? dot / (Math.sqrt(magA) * Math.sqrt(magB))
        : 0;
}