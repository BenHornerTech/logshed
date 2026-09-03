import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
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
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['tower-unraid', 'opnsense-router'],
      apps: ['nginx', 'postgres', 'docker', 'filterlog'],
      host_to_apps: {
        'tower-unraid': ['docker', 'nginx', 'postgres'],
        'opnsense-router': ['filterlog'],
      },
      app_to_hosts: {
        docker: ['tower-unraid'],
        nginx: ['tower-unraid'],
        postgres: ['tower-unraid'],
        filterlog: ['opnsense-router'],
      },
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

  it('maintains quick filter buttons even after a filter is applied (Item #32)', async () => {
    // Initially returns sampleLogs with tower-unraid and opnsense-router
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Quick filter pills for both tower-unraid and opnsense-router exist
    expect(screen.getByRole('button', { name: 'tower-unraid' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'opnsense-router' })).toBeInTheDocument();

    // Mock fetchLogs to return only tower-unraid logs when filtered
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [sampleLogs[0]],
      total: 1,
      limit: 500,
      offset: 0,
    });

    // Click quick filter pill for tower-unraid
    fireEvent.click(screen.getByRole('button', { name: 'tower-unraid' }));

    // Verify opnsense-router quick filter pill DOES NOT disappear!
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'tower-unraid' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'opnsense-router' })).toBeInTheDocument();
    });

    // The tower-unraid pill should now be active (highlighted)
    const towerBtn = screen.getByRole('button', { name: 'tower-unraid' });
    expect(towerBtn.className).toContain('text-accent-300');
  });

  it('scopes app/container dropdown options to only apps belonging to the chosen host', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // 1. Initially without host filter, open App/Container dropdown
    const appTrigger = screen.getByText('App / Container:').closest('[role="button"]') as HTMLElement;
    const appDropdown = appTrigger.parentElement as HTMLElement;
    fireEvent.click(appTrigger);

    // All apps from both tower-unraid (nginx, postgres, docker) and opnsense-router (filterlog) are present in the dropdown
    expect(within(appDropdown).getByText('filterlog')).toBeInTheDocument();
    expect(within(appDropdown).getByText('nginx')).toBeInTheDocument();
    expect(within(appDropdown).getByText('postgres')).toBeInTheDocument();

    // Close app dropdown
    fireEvent.click(appTrigger);

    // 2. Select host 'tower-unraid'
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });
    act(() => {
      fireEvent.click(within(hostDropdown).getByText('tower-unraid'));
      fireEvent.click(hostTrigger); // close host dropdown
    });

    // 3. Open App/Container dropdown again
    act(() => {
      fireEvent.click(appTrigger);
    });

    // Should contain tower-unraid apps, but NOT opnsense-router's filterlog
    await waitFor(() => {
      expect(within(appDropdown).getByText('nginx')).toBeInTheDocument();
      expect(within(appDropdown).getByText('postgres')).toBeInTheDocument();
      expect(within(appDropdown).queryByText('filterlog')).toBeNull();
    });
  });

  it('scopes host dropdown options when an app is selected first (bidirectional scoping)', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const appTrigger = screen.getByText('App / Container:').closest('[role="button"]') as HTMLElement;
    const appDropdown = appTrigger.parentElement as HTMLElement;
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;

    // 1. Select app 'filterlog' (which only belongs to opnsense-router)
    act(() => {
      fireEvent.click(appTrigger);
    });
    act(() => {
      fireEvent.click(within(appDropdown).getByText('filterlog'));
      fireEvent.click(appTrigger); // close app dropdown
    });

    // 2. Open Host dropdown
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // Should contain opnsense-router, but NOT tower-unraid
    await waitFor(() => {
      expect(within(hostDropdown).getByText('opnsense-router')).toBeInTheDocument();
      expect(within(hostDropdown).queryByText('tower-unraid')).toBeNull();
    });
  });

  it('captures click away on backdrop so clicking outside closes dropdown without opening log drawer', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // 1. Open Host dropdown
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // Dropdown is open and backdrop interceptor exists
    const backdrop = within(hostDropdown).getByTestId('dropdown-backdrop');
    expect(backdrop).toBeInTheDocument();

    // 2. Click the backdrop
    act(() => {
      fireEvent.click(backdrop);
    });

    // 3. Dropdown closes, and log modal drawer did NOT open
    expect(within(hostDropdown).queryByTestId('dropdown-backdrop')).toBeNull();
    expect(screen.queryByText(/Log Record #/i)).toBeNull();
  });

  it('preserves full database hosts and apps in dropdowns even after clearing the screen buffer', async () => {
    render(<LiveLogStream onAnalyzeAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Clear the screen buffer
    const clearBtn = screen.getByTitle(/Clear screen buffer/i);
    act(() => {
      fireEvent.click(clearBtn);
    });

    // Buffer is empty: "0 lines"
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText(/No logs in stream/i)).toBeInTheDocument();

    // Now open the Host/IP dropdown
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // Dropdown still contains tower-unraid and opnsense-router from full database facets!
    expect(within(hostDropdown).getByText('tower-unraid')).toBeInTheDocument();
    expect(within(hostDropdown).getByText('opnsense-router')).toBeInTheDocument();
  });

  it('deduplicates aliased hosts and never shows the raw IP when an alias is set', async () => {
    // Provide knownAliases with 172.22.2.4 -> NPM and 192.168.1.50 -> tower-unraid
    render(
      <LiveLogStream
        onAnalyzeAi={vi.fn()}
        knownAliases={{
          '172.22.2.4': 'NPM',
          '192.168.1.50': 'tower-unraid',
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Open Host dropdown
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // NPM and tower-unraid appear in the host dropdown
    expect(within(hostDropdown).getByText('tower-unraid')).toBeInTheDocument();
    expect(within(hostDropdown).getByText('NPM')).toBeInTheDocument();

    // Raw IPs that are aliased (172.22.2.4 and 192.168.1.50) MUST NOT appear in the dropdown!
    expect(within(hostDropdown).queryByText('172.22.2.4')).toBeNull();
    expect(within(hostDropdown).queryByText('192.168.1.50')).toBeNull();
  });
});
