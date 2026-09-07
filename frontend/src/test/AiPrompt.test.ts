import { describe, it, expect } from 'vitest';
import {
  DEFAULT_SYSTEM_PROMPT,
  SYSTEM_INSTRUCTIONS_HEADER,
  USER_ANALYSIS_PROMPT_HEADER,
  buildFullEnvelope,
  parseFullEnvelope,
  normalizePrompt,
} from '../utils/aiPrompt.ts';

describe('aiPrompt utilities', () => {
  it('buildFullEnvelope joins system instructions and user analysis prompt with headers', () => {
    const sys = 'You are a test assistant.';
    const user = 'Here are the logs.';
    const envelope = buildFullEnvelope(sys, user);

    expect(envelope).toContain(SYSTEM_INSTRUCTIONS_HEADER);
    expect(envelope).toContain('You are a test assistant.');
    expect(envelope).toContain(USER_ANALYSIS_PROMPT_HEADER);
    expect(envelope).toContain('Here are the logs.');
  });

  it('buildFullEnvelope falls back to DEFAULT_SYSTEM_PROMPT when system prompt is empty', () => {
    const envelope = buildFullEnvelope('', 'User logs');
    expect(envelope).toContain(DEFAULT_SYSTEM_PROMPT.trim());
    expect(envelope).toContain('User logs');
  });

  it('parseFullEnvelope correctly parses envelope with both headers', () => {
    const customSys = 'Custom instructions for debugging.';
    const customUser = 'Custom logs payload.';
    const envelope = `${SYSTEM_INSTRUCTIONS_HEADER}\n${customSys}\n\n${USER_ANALYSIS_PROMPT_HEADER}\n${customUser}`;

    const parsed = parseFullEnvelope(envelope, DEFAULT_SYSTEM_PROMPT);
    expect(parsed.systemPrompt).toBe(customSys);
    expect(parsed.userPrompt).toBe(customUser);
  });

  it('parseFullEnvelope gracefully handles edited text missing standard headers', () => {
    const rawEdited = 'Just raw user prompt text without delimiter headers';
    const parsed = parseFullEnvelope(rawEdited, 'Fallback System Prompt');
    expect(parsed.systemPrompt).toBe('Fallback System Prompt');
    expect(parsed.userPrompt).toBe(rawEdited);
  });

  it('DEFAULT_SYSTEM_PROMPT uses universal terminology without Americanisms', () => {
    expect(DEFAULT_SYSTEM_PROMPT).toContain('redacted server/container logs');
    expect(DEFAULT_SYSTEM_PROMPT).not.toContain('sanitized');
    expect(DEFAULT_SYSTEM_PROMPT).not.toContain('analyze');
  });

  it('normalizePrompt trims whitespace and normalizes CRLF to LF', () => {
    expect(normalizePrompt('  hello\r\nworld  \n')).toBe('hello\nworld');
    expect(normalizePrompt(null)).toBe('');
    expect(normalizePrompt(undefined)).toBe('');
    expect(normalizePrompt('line1\nline2')).toBe('line1\nline2');
    expect(normalizePrompt('line1\r\nline2')).toBe('line1\nline2');
    expect(normalizePrompt(DEFAULT_SYSTEM_PROMPT.replace(/\n/g, '\r\n'))).toBe(
      normalizePrompt(DEFAULT_SYSTEM_PROMPT)
    );
  });
});
