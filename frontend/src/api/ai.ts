import { apiFetch } from './client.ts';
import { AiDiagnosisRequest, AiDiagnosisResponse, AiAuditEntry, AiPreviewRequest, AiPreviewResponse } from '../types.ts';

export async function previewAiPrompt(req: AiPreviewRequest): Promise<AiPreviewResponse> {
  return apiFetch<AiPreviewResponse>('/api/ai/preview', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function diagnoseLogs(req: AiDiagnosisRequest): Promise<AiDiagnosisResponse> {
  return apiFetch<AiDiagnosisResponse>('/api/ai/diagnose', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function fetchAiAudit(limit: number = 50, offset: number = 0): Promise<{ items: AiAuditEntry[]; total: number }> {
  return apiFetch<{ items: AiAuditEntry[]; total: number }>(`/api/ai/audit?limit=${limit}&offset=${offset}`);
}

export async function deleteAiAuditItem(auditId: number): Promise<{ status: string; deleted_id?: number }> {
  return apiFetch<{ status: string; deleted_id?: number }>(`/api/ai/audit/${auditId}`, {
    method: 'DELETE',
  });
}

export async function clearAiAuditLog(): Promise<{ status: string; deleted_count?: number }> {
  return apiFetch<{ status: string; deleted_count?: number }>('/api/ai/audit', {
    method: 'DELETE',
  });
}
