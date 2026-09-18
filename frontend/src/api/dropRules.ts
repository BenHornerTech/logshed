import { apiFetch } from './client.ts';
import {
  DropRule,
  DropRuleCreate,
  DropRuleUpdate,
  DropRuleTestRequest,
  DropRuleTestResponse,
} from '../types.ts';

export async function fetchDropRules(): Promise<DropRule[]> {
  return apiFetch<DropRule[]>('/api/drop-rules');
}

export async function createDropRule(data: DropRuleCreate): Promise<DropRule> {
  return apiFetch<DropRule>('/api/drop-rules', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateDropRule(
  id: number,
  data: DropRuleUpdate
): Promise<DropRule> {
  return apiFetch<DropRule>(`/api/drop-rules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteDropRule(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/drop-rules/${id}`, {
    method: 'DELETE',
  });
}

export async function testDropRule(
  data: DropRuleTestRequest
): Promise<DropRuleTestResponse> {
  return apiFetch<DropRuleTestResponse>('/api/drop-rules/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function resetDropRuleCounter(id: number): Promise<DropRule> {
  return apiFetch<DropRule>(`/api/drop-rules/${id}/reset`, {
    method: 'POST',
  });
}
