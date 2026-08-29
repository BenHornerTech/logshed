import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { LogDetailModal } from '../components/logs/LogDetailModal.tsx';
import { LogEntry } from '../types.ts';

describe('XSS Prevention in Log Rendering', () => {
  it('renders malicious scripts safely as raw text without executing or injecting HTML elements', () => {
    const maliciousPayload = '<script>window.XSS_TRIGGERED=true; alert("PWNED");</script><img src="x" onerror="evil()" /><b>BOLD</b>';

    const mockLog: LogEntry = {
      id: 999,
      timestamp: '2026-08-29T12:00:00Z',
      received_at: '2026-08-29T12:00:01Z',
      source_ip: '192.168.1.100',
      source_alias: 'attacker-host',
      app_name: 'evil-service',
      facility: 1,
      severity: 3,
      message: maliciousPayload,
      raw: `<11>1 2026-08-29T12:00:00Z attacker-host evil-service - - - ${maliciousPayload}`,
    };

    const { container } = render(
      <LogDetailModal
        log={mockLog}
        isOpen={true}
        onClose={() => {}}
        onExplainWithAi={() => {}}
      />
    );

    // Verify script tags were NOT injected as HTML elements
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect(container.querySelector('b')).toBeNull();

    // Verify raw text is safely present in DOM
    expect(screen.getByText(maliciousPayload)).toBeInTheDocument();
  });
});
