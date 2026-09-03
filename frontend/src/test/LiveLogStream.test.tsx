import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LiveLogStream, formatLocalTimestamp } from '../components/logs/LiveLogStream.tsx';
import * as logsApi from '../api/logs.ts';
import { LogEntry } from '../types.ts';

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize?: () => number }) => {
    const size = estimateSize ? estimateSize() : 28;
    return {
      getTotalSize: () => count * size,
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({
          key: index,
          index,
          start: index * size,
          size,
        })),
      scrollToIndex: vi.fn(),
    };
  },
}));

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  listeners: Record<string, ((event: any) => void)[]> = {};
  onerror: ((e: any) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (event: any) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(cb);
  }

  removeEventListener(type: string, cb: (event: any) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== cb);
    }
  }

  close() {}

  emit(type: string, data: any) {
    if (this.listeners[type]) {
      this.listeners[type].forEach((cb) => cb({ data: JSON.stringify(data) }));
    }
  }
}

(globalThis as any).EventSource = MockEventSource;

const sampleLogs: LogEntry[] = [
  {
    id: 101,
    timestamp: '2026-09-03T14:30:15.123Z',
    received_at: '2026-09-03T14:30:15.150Z',
    source_ip: '192.168.1.50',
    source_alias: 'tower-unraid',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: 'Nginx upstream connection timeout',
    raw: '<11>1 2026-09-03T14:30:15.123Z tower-unraid nginx - - - Nginx upstream connection timeout',
  },
  {
    id: 102,
    timestamp: '2026-09-03T14:30:10.456Z',
    received_at: '2026-09-03T14:30:10.460Z',
    source_ip: '192.168.1.50',
    source_alias: 'tower-unraid',
    app_name: 'postgres',
    facility: 1,
    severity: 4,
    message: 'Postgres slow query detected',
    raw: '<12>1 2026-09-03T14:30:10.456Z tower-unraid postgres - - - Postgres slow query detected',
  },
  {
    id: 103,
    timestamp: '2026-09-03T14:30:05.789Z',
    received_at: '2026-09-03T14:30:05.800Z',
    source_ip: '192.168.1.1',
    source_alias: 'opnsense-router',
    app_name: 'filterlog',
    facility: 4,
    severity: 6,
    message: 'Default deny rule matched WAN block',
    raw: '<134>1 2026-09-03T14:30:05.789Z opnsense-router filterlog - - - Default deny rule matched WAN block',
  },
  {
    id: 104,
    timestamp: '2026-09-03T14:30:00.000Z',
    received_at: '2026-09-03T14:30:00.020Z',
    source_ip: '192.168.1.50',
    source_alias: 'tower-unraid',
    app_name: 'docker',
    facility: 1,
    severity: 6,
    message: 'Container started cleanly',
    raw: '<14>1 2026-09-03T14:30:00.000Z tower-unraid docker - - - Container started cleanly',
  },
];

describe('formatLocalTimestamp Helper (Item #5)', () => {
  it('formats UTC ISO timestamp into local HH:mm:ss.SSS', () => {
    const utcString = '2026-09-03T14:30:15.123Z';
    const formatted = formatLocalTimestamp(utcString);
    const date = new Date(utcString);
    const expected = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}.123`;
    expect(formatted).toBe(expected);
  });

  it('handles empty and invalid timestamp strings gracefully', () => {
    expect(formatLocalTimestamp('')).toBe('');
    expect(formatLocalTimestamp('invalid-date')).toBe('invalid-date');
  });
});

describe('LiveLogStream Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [...sampleLogs],
      total: sampleLogs.length,
      limit: 500,
      offset: 0,
    });
  });

  it('displays Screen Buffer label and clear screen buffer tooltip (Item #14)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/Screen Buffer:/)).toBeInTheDocument();
    });

    const trashBtn = screen.getByTitle('Clear screen buffer (clears browser view only; does not delete logs from disk)');
    expect(trashBtn).toBeInTheDocument();
  });

  it('does not render the redundant Maximize2 row action button (Item #17)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Ensure Maximize2 "Inspect Detail & Context" button is absent
    expect(screen.queryByTitle('Inspect Detail & Context')).toBeNull();
    // Verify "Explain with AI" is present
    expect(screen.getAllByTitle('Explain with AI').length).toBeGreaterThan(0);
  });

  it('formats timestamps and renders UTC + received_at tooltip (Item #5)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Check tooltip on the timestamp cell
    const expectedTime = formatLocalTimestamp(sampleLogs[0].timestamp);
    const timestampEl = screen.getByText(expectedTime);
    expect(timestampEl).toBeInTheDocument();
    expect(timestampEl.getAttribute('title')).toContain(sampleLogs[0].timestamp);
    expect(timestampEl.getAttribute('title')).toContain(sampleLogs[0].received_at);
  });

  it('maintains top-to-bottom stream direction and prepends SSE logs to the top (Item #4)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Verify initial order is DESC (newest log id 101 appears before id 104)
    const logRows = document.querySelectorAll('.log-row');
    expect(logRows.length).toBe(4);
    expect(logRows[0]).toHaveTextContent('Nginx upstream connection timeout');
    expect(logRows[3]).toHaveTextContent('Container started cleanly');

    // Simulate new SSE event arrival
    const newEntry: LogEntry = {
      id: 105,
      timestamp: '2026-09-03T14:35:00.000Z',
      received_at: '2026-09-03T14:35:00.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'tower-unraid',
      app_name: 'kernel',
      facility: 0,
      severity: 2,
      message: 'Hardware error corrected by ECC',
      raw: '<10>1 2026-09-03T14:35:00.000Z tower-unraid kernel - - - Hardware error corrected by ECC',
    };

    expect(MockEventSource.instances.length).toBeGreaterThan(0);
    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('log', newEntry);
    });

    // Newest log should be prepended to the top (index 0)
    await waitFor(() => {
      const updatedRows = document.querySelectorAll('.log-row');
      expect(updatedRows[0]).toHaveTextContent('Hardware error corrected by ECC');
    });
  });

  it('handles checkbox clicking without bubbling to row detail modal (Item #15)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const firstRow = logRows[0];
    const checkboxContainer = firstRow.firstElementChild as HTMLElement;

    // Click checkbox container
    fireEvent.click(checkboxContainer);

    // Should select log without opening LogDetailModal
    await waitFor(() => {
      expect(screen.getByText('1 log selected')).toBeInTheDocument();
    });
    expect(screen.queryByText(/Log Record #101/i)).toBeNull();

    // Clicking anywhere else on row (e.g. message cell) opens detail modal
    const messageCell = screen.getByText('Nginx upstream connection timeout');
    fireEvent.click(messageCell);

    await waitFor(() => {
      expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();
    });
  });

  it('performs Shift-Click range selection with single-host validation (Item #16)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const row0Checkbox = logRows[0].firstElementChild as HTMLElement; // id 101, host: tower-unraid
    const row1Checkbox = logRows[1].firstElementChild as HTMLElement; // id 102, host: tower-unraid

    // Click row 0 (index 0)
    fireEvent.click(row0Checkbox);
    expect(screen.getByText('1 log selected')).toBeInTheDocument();

    // Shift-click row 1 (index 1) - same host
    fireEvent.click(row1Checkbox, { shiftKey: true });
    expect(screen.getByText('2 logs selected')).toBeInTheDocument();

    // Now shift-click row 3 (index 3) - row 2 is opnsense-router (disparate host)
    const row3Checkbox = logRows[3].firstElementChild as HTMLElement; // id 104, host: tower-unraid
    fireEvent.click(row3Checkbox, { shiftKey: true });

    // Should select row 3 as well (total 3 logs: 101, 102, 104) while skipping row 2 (103)
    await waitFor(() => {
      expect(screen.getByText('3 logs selected')).toBeInTheDocument();
      expect(screen.getByText(/Range selection contained logs from multiple hosts/i)).toBeInTheDocument();
    });
  });

  it('displays auto-scroll pause banner and jumps to top on click (Item #4)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Pause auto-scroll by clicking the auto-scroll toggle button
    const autoScrollBtn = screen.getByText('Auto-Scroll ON');
    fireEvent.click(autoScrollBtn);

    expect(screen.getByText('Paused')).toBeInTheDocument();

    // Emit a new log event while paused
    const newEntry: LogEntry = {
      id: 106,
      timestamp: '2026-09-03T14:40:00.000Z',
      received_at: '2026-09-03T14:40:00.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'tower-unraid',
      app_name: 'test',
      facility: 1,
      severity: 6,
      message: 'New incoming background log',
      raw: '<14>1 2026-09-03T14:40:00.000Z tower-unraid test - - - New incoming background log',
    };

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('log', newEntry);
    });

    // Floating banner should appear with "Auto-scroll paused (1 new log at top) — Click to jump to top"
    await waitFor(() => {
      expect(screen.getByText(/Auto-scroll paused \(1 new log at top\) — Click to jump to top/i)).toBeInTheDocument();
    });

    // Click floating banner to jump to top and resume
    const jumpBtn = screen.getByText(/Click to jump to top/i);
    fireEvent.click(jumpBtn);

    await waitFor(() => {
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
      expect(screen.queryByText(/Auto-scroll paused/i)).toBeNull();
    });
  });
});
