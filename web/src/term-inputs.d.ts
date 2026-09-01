export type TermEdit = { text: string; terms: string[] };

export function parseTermInput(value: string): string[];
export function editTermInput(value: string): TermEdit;
