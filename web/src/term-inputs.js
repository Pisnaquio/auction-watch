export function parseTermInput(value) {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function editTermInput(value) {
  return { text: value, terms: parseTermInput(value) };
}
