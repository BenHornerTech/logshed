/**
 * Shared AI prompt constants and helper functions.
 */

export const DEFAULT_SYSTEM_PROMPT = `You are an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator.
Analyze the following sanitized server/container logs and provide a structured diagnosis in Markdown format.

Your response MUST include the following three sections with exact headers:
## Summary
A concise 1-2 sentence overview of the issue.

## Root Cause
A detailed explanation of why the event or failure occurred based on the log evidence.

## Actionable Remediation
Step-by-step commands, configuration fixes, or debugging steps to resolve the issue.`;

export const SYSTEM_INSTRUCTIONS_HEADER = '=== SYSTEM INSTRUCTIONS ===';
export const USER_ANALYSIS_PROMPT_HEADER = '=== USER ANALYSIS PROMPT ===';

/**
 * Assemble the complete prompt envelope for full LLM dispatch or copying.
 */
export function buildFullEnvelope(systemPrompt: string, userPrompt: string): string {
  const cleanSys = (systemPrompt || DEFAULT_SYSTEM_PROMPT).trim();
  const cleanUser = (userPrompt || '').trim();
  return `${SYSTEM_INSTRUCTIONS_HEADER}\n${cleanSys}\n\n${USER_ANALYSIS_PROMPT_HEADER}\n${cleanUser}`;
}

/**
 * Parse an envelope back into system instructions and user analysis prompt.
 */
export function parseFullEnvelope(
  fullText: string,
  fallbackSystemPrompt: string
): { systemPrompt: string; userPrompt: string } {
  const sysIdx = fullText.indexOf(SYSTEM_INSTRUCTIONS_HEADER);
  const userIdx = fullText.indexOf(USER_ANALYSIS_PROMPT_HEADER);

  if (sysIdx !== -1 && userIdx !== -1 && userIdx > sysIdx) {
    const sys = fullText.slice(sysIdx + SYSTEM_INSTRUCTIONS_HEADER.length, userIdx).trim();
    const user = fullText.slice(userIdx + USER_ANALYSIS_PROMPT_HEADER.length).trim();
    return {
      systemPrompt: sys || fallbackSystemPrompt,
      userPrompt: user,
    };
  }

  // If user stripped the delimiter headers, treat the entire string as the analysis prompt
  return {
    systemPrompt: fallbackSystemPrompt,
    userPrompt: fullText.trim(),
  };
}
