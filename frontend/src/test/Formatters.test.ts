import { describe, it, expect } from 'vitest';
import { stripAnsi } from '../utils/formatters.ts';

describe('stripAnsi', () => {
  it('handles null, undefined, and empty string gracefully', () => {
    expect(stripAnsi(null)).toBe('');
    expect(stripAnsi(undefined)).toBe('');
    expect(stripAnsi('')).toBe('');
  });

  it('preserves clean text without any escape characters', () => {
    const text = '2026-09-11T19:40:00.041Z [info][Plex Scan]: Beginning to process';
    expect(stripAnsi(text)).toBe(text);
  });

  it('strips Overseerr/Jellyseerr style ANSI color codes like [\\x1b[32minfo\\x1b[39m]', () => {
    const input = '2026-09-11T19:40:00.041Z [\x1b[32minfo\x1b[39m][Plex Scan]: Beginning to process';
    const expected = '2026-09-11T19:40:00.041Z [info][Plex Scan]: Beginning to process';
    expect(stripAnsi(input)).toBe(expected);
  });

  it('strips multiline and complex ANSI color codes and resets', () => {
    const input = '\x1b[1;31mERROR:\x1b[0m Database connection lost\n\x1b[33mWARN:\x1b[0m Retrying in 5s';
    const expected = 'ERROR: Database connection lost\nWARN: Retrying in 5s';
    expect(stripAnsi(input)).toBe(expected);
  });

  it('strips stray non-printable control characters that trigger mobile glyph replacement', () => {
    // ASCII \x1b alone without trailing CSI code, plus other non-printable chars
    const input = 'Log with stray escape \x1b and bell \x07 and null \x00 character';
    expect(stripAnsi(input)).toBe('Log with stray escape  and bell  and null  character');
  });

  it('preserves tabs and newlines', () => {
    const input = 'Line 1\n\tIndented with tab\r\nLine 2';
    expect(stripAnsi(input)).toBe('Line 1\n\tIndented with tab\r\nLine 2');
  });
});
