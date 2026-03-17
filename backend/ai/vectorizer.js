const vocabulary = new Map();

export function textToVector(text) {
    const tokens = text.toLowerCase().split(/\s+/);

    // build vocab
    for (const token of tokens) {
        if (!vocabulary.has(token)) {
            vocabulary.set(token, vocabulary.size);
        }
    }

    const vector = new Array(vocabulary.size).fill(0);

    for (const token of tokens) {
        const index = vocabulary.get(token);
        if (index !== undefined) {
            vector[index]++;
        }
    }

    return vector;
}