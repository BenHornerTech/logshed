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

const LEVEL_WORDS =
  'emerg|emergency|alert|crit|critical|fatal|panic|err|error|warn|warning|notice|log|info|informational|debug|trace|verbose';

/**
 * Regex patterns for leading timestamps in log messages:
 * - ISO 8601 / RFC 3339: [2026-09-12T05:03:48+01:00] or 2026-09-12T05:33:55.369Z
 * - With named timezone: [2026-09-12 07:48:57 UTC] or 2026-09-12 08:50:39.344 BST
 * - Standard datetime: 2026-09-12 06:35:02
 * - With comma millis (Python logging): [2026-09-12 08:47:00,011]
 * - Date with slashes: 2026/09/12 07:43:00
 * - BSD syslog timestamp: Sep 12 06:35:02
 */
const LEADING_TIMESTAMP_REGEX =
  /^(?:\[\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?\]|\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?|[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s*/;

const SUBSYSTEM_WITH_LEVEL_REGEX = new RegExp(
  `^(\\[[^\\]]+\\])\\s+(?:${LEVEL_WORDS})\\s*:?\\s+`,
  'i'
);

const BRACKET_LEVEL_REGEX = new RegExp(`^\\[(?:${LEVEL_WORDS})\\]\\s*:?\\s*`, 'i');

const WORD_LEVEL_REGEX =
  /^(?:(?:emerg|emergency|alert|crit|critical|fatal|panic|err|error|warn|warning|notice|info|informational|debug|trace|verbose)\s*:?\s+|log:\s+)/i;

/**
 * Trims redundant leading timestamps and repeated severity prefixes from message text
 * for clean display in the live log stream table rows.
 * Preserves the original message content in full if no redundant prefix matches.
 */
export function cleanLogMessageForDisplay(text: string | null | undefined): string {
  if (!text) return '';
  const stripped = stripAnsi(text);
  let clean = stripped.trim();

  // 1. Strip leading redundant timestamps
  clean = clean.replace(LEADING_TIMESTAMP_REGEX, '');

  // 2. If preceded by a bracketed subsystem tag like [MONITOR] before the level, preserve the subsystem:
  clean = clean.replace(SUBSYSTEM_WITH_LEVEL_REGEX, '$1 ');

  // 3. Match leading bracketed level: [error], [warn], [info], etc.
  clean = clean.replace(BRACKET_LEVEL_REGEX, '');

  // 4. Match leading unbracketed level: "WARN: ...", "INFO   ..."
  clean = clean.replace(WORD_LEVEL_REGEX, '');

  const result = clean.trim();
  return result.length > 0 ? result : stripped;
}

