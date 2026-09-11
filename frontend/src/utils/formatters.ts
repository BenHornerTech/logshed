/**
 * Log message formatting and sanitization utilities.
 */

// Matches ANSI CSI sequences (e.g. \x1b[32m, \x1b[0m, \x1b[1;31m), OSC sequences, and two-character ESC codes
const ANSI_REGEX = /\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\)|[@-Z\\-_])/g;

// Matches non-printable ASCII control characters (ASCII 0-8, 11-12, 14-31, 127), preserving \t (9), \n (10), \r (13)
const CONTROL_CHARS_REGEX = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g;

/**
 * Strips ANSI escape sequences and non-printable control characters from text.
 * Prevents mobile browser glyph replacement artifacts (like square boxes with an X: ☒)
 * and terminal color code artifacts (like [32minfo[39m).
 */
export function stripAnsi(text: string | null | undefined): string {
  if (!text) return '';
  return text
    .replace(ANSI_REGEX, '')
    .replace(CONTROL_CHARS_REGEX, '');
}
