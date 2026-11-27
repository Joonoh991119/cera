// Math dialog: a preview image is tied to the version of the input that produced it (tex, size, color).
// It is inserted only when it matches the current input (this prevents an old image going in alongside a new formula that was typed before typesetting finished).
export const mathKey = (tex, size, color) => `${tex}\u0000${size}\u0000${color}`;
// an empty formula (whitespace only) is not inserted (a new dialog opens empty)
export function canInsert(prev, tex, size, color, busy, err) {
  return !!String(tex ?? "").trim() && !!prev && prev.key === mathKey(tex, size, color) && !busy && !err;
}
