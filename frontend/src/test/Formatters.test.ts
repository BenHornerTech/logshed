import { describe, it, expect } from 'vitest';
import { stripAnsi, cleanLogMessageForDisplay } from '../utils/formatters.ts';

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

describe('cleanLogMessageForDisplay', () => {
  it('handles null, undefined, and empty string gracefully', () => {
    expect(cleanLogMessageForDisplay(null)).toBe('');
    expect(cleanLogMessageForDisplay(undefined)).toBe('');
    expect(cleanLogMessageForDisplay('')).toBe('');
  });

  it('trims leading ISO timestamp and level while preserving message body (Example 1)', () => {
    const input = '2026-09-12 06:35:02  INFO      All scopes processed';
    expect(cleanLogMessageForDisplay(input)).toBe('All scopes processed');
  });

  it('trims leading timestamp and level while preserving bracketed subsystem tag (Example 2)', () => {
    const input =
      "2026-09-12T05:03:48+01:00 [MONITOR] WARN: Monitor #16 'SABnzbd': Pending: connect EHOSTUNREACH 172.22.2.33:8080 | Max retries: 2 | Retry: 2 | Retry Interval: 60 seconds | Type: http";
    const expected =
      "[MONITOR] Monitor #16 'SABnzbd': Pending: connect EHOSTUNREACH 172.22.2.33:8080 | Max retries: 2 | Retry: 2 | Retry Interval: 60 seconds | Type: http";
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims leading ISO timestamp and bracketed level while preserving metadata tag and JSON (Example 3)', () => {
    const input =
      '2026-09-12T05:33:55.369Z [error][Plex.TV Metadata API]: Failed to retrieve watchlist items {"errorMessage":"Request failed with status code 504"}';
    const expected =
      '[Plex.TV Metadata API]: Failed to retrieve watchlist items {"errorMessage":"Request failed with status code 504"}';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('preserves message with application tag but no timestamp or level (Example 4)', () => {
    const input =
      'logshed: Could not download icon https://raw.githubusercontent.com/BenHornerTech/logshed/main/assets/logshed-logo.png';
    expect(cleanLogMessageForDisplay(input)).toBe(input);
  });

  it('trims slash datetime and bracketed level from nginx error log (Example 5)', () => {
    const input =
      '2026/09/12 07:43:00 [error] 2360609#2360609: *268403 open() "/usr/local/emhttp/plugins/dynamix.docker.manager/images/question.png" failed';
    const expected =
      '2360609#2360609: *268403 open() "/usr/local/emhttp/plugins/dynamix.docker.manager/images/question.png" failed';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims bracketed UTC timestamp from Technitium DNS server log', () => {
    const input =
      '[2026-09-12 07:48:57 UTC] [172.22.2.3:40424] [TCP] DNS Server received zone transfer request for zone: cluster-catalog.cluster.lan.benhorner.co.uk';
    const expected =
      '[172.22.2.3:40424] [TCP] DNS Server received zone transfer request for zone: cluster-catalog.cluster.lan.benhorner.co.uk';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims comma-millis timestamp and bracketed INFO from paperless-ngx log while preserving celery subsystem tag', () => {
    const input =
      '[2026-09-12 08:47:00,011] [INFO] [celery.worker.strategy] Task paperless_mail.tasks.process_mail_accounts[ed70640c-c25c-42b0-96a8-a6c765de24b4] received';
    const expected =
      '[celery.worker.strategy] Task paperless_mail.tasks.process_mail_accounts[ed70640c-c25c-42b0-96a8-a6c765de24b4] received';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims named timezone timestamp and LOG: from PostgreSQL log while preserving session PID', () => {
    const input = '2026-09-12 08:50:39.344 BST [27] LOG:  checkpoint starting: time';
    const expected = '[27] checkpoint starting: time';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims named timezone timestamp and unbracketed LOG: from PostgreSQL log', () => {
    const input = '2026-09-12 08:50:39.344 BST LOG:  checkpoint starting: time';
    const expected = 'checkpoint starting: time';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('preserves plain log message starting with the word Log when not followed by a colon', () => {
    const input = 'Log file rotated successfully';
    expect(cleanLogMessageForDisplay(input)).toBe(input);
  });
});

