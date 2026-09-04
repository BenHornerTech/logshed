import { describe, it, expect } from 'vitest';
import {
  DEFAULT_SYSTEM_PROMPT,
  SYSTEM_INSTRUCTIONS_HEADER,
  USER_ANALYSIS_PROMPT_HEADER,
  buildFullEnvelope,
  parseFullEnvelope,
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
});
