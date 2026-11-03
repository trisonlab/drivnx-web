const DELIMITER_NAMES: Record<string, string> = {
  ",": "Comma",
  ";": "Semicolon",
  "\t": "Tab",
  "|": "Pipe",
};

export function delimiterName(delimiter: string): string {
  return DELIMITER_NAMES[delimiter] ?? delimiter;
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} bytes`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = -1;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

/** Pasted paths often bring spaces, and Windows' "Copy as path" adds quotes. */
export function cleanPastedPath(text: string): string {
  const trimmed = text.trim();
  const quoted = /^(["'])(.*)\1$/.exec(trimmed);
  return quoted ? quoted[2] : trimmed;
}
