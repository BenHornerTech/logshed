import { apiFetch } from './client.ts';
import { AiAnalysisRequest, AiAnalysisResponse, AiAuditEntry, AiPreviewRequest, AiPreviewResponse } from '../types.ts';

export async function previewAiPrompt(req: AiPreviewRequest): Promise<AiPreviewResponse> {
  return apiFetch<AiPreviewResponse>('/api/ai/preview', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function analyzeLogs(req: AiAnalysisRequest): Promise<AiAnalysisResponse> {
  return apiFetch<AiAnalysisResponse>('/api/ai/analyze', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function fetchAiAudit(limit: number = 50, offset: number = 0): Promise<{ items: AiAuditEntry[]; total: number }> {
  return apiFetch<{ items: AiAuditEntry[]; total: number }>(`/api/ai/audit?limit=${limit}&offset=${offset}`);
}
