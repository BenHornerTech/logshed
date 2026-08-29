import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { SeverityBadge } from '../components/common/SeverityBadge.tsx';

describe('SeverityBadge RFC 5424 Mapping & Styling', () => {
  it('maps severity 0 (Emergency) to red badge', () => {
    render(<SeverityBadge severity={0} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('EMERG');
    expect(badge).toHaveClass('text-red-400');
    expect(badge).toHaveClass('bg-red-950/80');
  });

  it('maps severity 1 (Alert) to red badge', () => {
    render(<SeverityBadge severity={1} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('ALERT');
    expect(badge).toHaveClass('text-red-400');
  });

  it('maps severity 2 (Critical) to red badge', () => {
    render(<SeverityBadge severity={2} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('CRIT');
    expect(badge).toHaveClass('text-red-400');
  });

  it('maps severity 3 (Error) to red badge', () => {
    render(<SeverityBadge severity={3} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('ERROR');
    expect(badge).toHaveClass('text-red-300');
  });

  it('maps severity 4 (Warning) to amber/yellow badge', () => {
    render(<SeverityBadge severity={4} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('WARN');
    expect(badge).toHaveClass('text-amber-300');
    expect(badge).toHaveClass('bg-amber-950/80');
  });

  it('maps severity 5 (Notice) to blue badge', () => {
    render(<SeverityBadge severity={5} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('NOTICE');
    expect(badge).toHaveClass('text-blue-300');
    expect(badge).toHaveClass('bg-blue-950/80');
  });

  it('maps severity 6 (Info) to slate badge', () => {
    render(<SeverityBadge severity={6} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('INFO');
    expect(badge).toHaveClass('text-slate-300');
  });

  it('maps severity 7 (Debug) to slate badge', () => {
    render(<SeverityBadge severity={7} />);
    const badge = screen.getByTestId('severity-badge');
    expect(badge).toHaveTextContent('DEBUG');
    expect(badge).toHaveClass('text-slate-400');
  });
});
