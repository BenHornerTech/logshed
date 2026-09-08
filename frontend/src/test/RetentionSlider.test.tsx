import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RetentionSlider } from '../components/settings/RetentionSlider.tsx';
import * as systemApi from '../api/system.ts';

describe('RetentionSlider Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders slider supporting default 30 days with presets [3, 7, 14, 30]', () => {
    render(<RetentionSlider retentionDays={14} onSaveRetention={vi.fn()} />);

    expect(screen.getByText('Log Retention Policy')).toBeInTheDocument();
    // Header and Preset button both display 14 Days
    expect(screen.getAllByText('14 Days').length).toBe(2);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider).toBeInTheDocument();
    expect(slider.min).toBe('1');
    expect(slider.max).toBe('30');
    expect(slider.value).toBe('14');

    // Verify preset quick-select buttons exist
    const presets = [3, 7, 14, 30];
    presets.forEach((val) => {
      expect(screen.getByRole('button', { name: `${val} Days` })).toBeInTheDocument();
    });
  });

  it('supports custom maxRetentionDays with expanded presets and slider max', () => {
    render(
      <RetentionSlider
        retentionDays={30}
        maxRetentionDays={90}
        onSaveRetention={vi.fn()}
      />
    );

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider.max).toBe('90');

    // Presets include higher presets up to 90
    const expectedPresets = [3, 7, 14, 30, 60, 90];
    expectedPresets.forEach((val) => {
      expect(screen.getByRole('button', { name: `${val} Days` })).toBeInTheDocument();
    });
  });

  it('positions ticks with percentage-based left styling matching slider thumbs', () => {
    render(<RetentionSlider retentionDays={14} onSaveRetention={vi.fn()} />);

    const min = 1;
    const max = 30;
    const presets = [3, 7, 14, 30];

    presets.forEach((val) => {
      const expectedPercent = ((val - min) / (max - min)) * 100;
      const tickContainer = screen.getByTitle(`Set retention to ${val} days`);
      expect(tickContainer).toBeInTheDocument();
      expect(tickContainer.style.left).toBe(`${expectedPercent}%`);
    });
  });

  it('updates selected days when clicking a preset button or dragging slider', () => {
    render(<RetentionSlider retentionDays={14} onSaveRetention={vi.fn()} />);

    // Click 7 Days preset
    const preset7Btn = screen.getByRole('button', { name: '7 Days' });
    fireEvent.click(preset7Btn);

    expect(screen.getAllByText('7 Days').length).toBe(2);
    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider.value).toBe('7');

    // Click tick for 30 days
    const tick30 = screen.getByTitle('Set retention to 30 days');
    fireEvent.click(tick30);

    expect(screen.getAllByText('30 Days').length).toBe(2);
    expect(slider.value).toBe('30');
  });

  it('disables save button when unchanged and enables it when modified', async () => {
    const onSaveMock = vi.fn().mockResolvedValue(undefined);
    render(<RetentionSlider retentionDays={14} onSaveRetention={onSaveMock} />);

    const saveBtn = screen.getByRole('button', { name: /Save Retention Policy/i });
    expect(saveBtn).toBeDisabled();
    expect(saveBtn.className).toContain('opacity-40');

    // Change to 7 days
    const preset7Btn = screen.getByRole('button', { name: '7 Days' });
    fireEvent.click(preset7Btn);

    expect(saveBtn).not.toBeDisabled();
    expect(saveBtn.className).toContain('bg-accent-600');

    fireEvent.click(saveBtn);
    expect(onSaveMock).toHaveBeenCalledWith(7);

    await waitFor(() => {
      expect(screen.getByText('Saved!')).toBeInTheDocument();
    });
  });

  it('triggers manual purge on demand with Purge Expired Logs Now button', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const pruneSpy = vi.spyOn(systemApi, 'triggerManualPrune').mockResolvedValue({
      status: 'ok',
      deleted_logs: 120,
      deleted_metrics: 5,
      metrics: {
        recorded_at: '2026-09-04T12:00:00Z',
        db_size_bytes: 4096000,
        disk_free_bytes: 10000000,
        disk_total_bytes: 20000000,
        total_logs_count: 500,
      },
    });

    const onPruneCompleted = vi.fn();
    render(
      <RetentionSlider
        retentionDays={14}
        onSaveRetention={vi.fn()}
        onPruneCompleted={onPruneCompleted}
      />
    );

    const purgeBtn = screen.getByRole('button', { name: /Purge Expired Logs Now/i });
    fireEvent.click(purgeBtn);

    await waitFor(() => {
      expect(pruneSpy).toHaveBeenCalled();
      expect(onPruneCompleted).toHaveBeenCalled();
      expect(screen.getByText('Prune Completed Successfully:')).toBeInTheDocument();
      expect(screen.getByText('120')).toBeInTheDocument();
    });
  });

  it('renders explanatory caption and Info icon with tooltip about automated schedule and page reuse', () => {
    render(<RetentionSlider retentionDays={14} onSaveRetention={vi.fn()} />);

    // Explanatory footnote caption
    expect(
      screen.getByText(
        'Old logs are automatically cleaned up daily. Purging deletes them immediately and frees space for new logs.'
      )
    ).toBeInTheDocument();

    // Info icon tooltip
    expect(
      screen.getByTitle(/SQLite automatically reuses free database pages/i)
    ).toBeInTheDocument();
  });

  it('handles boundary condition maxRetentionDays=1 without NaN styles and formats singular 1 Day', () => {
    render(<RetentionSlider retentionDays={1} maxRetentionDays={1} onSaveRetention={vi.fn()} />);

    // Header displays 1 Day
    expect(screen.getAllByText('1 Day').length).toBe(2);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider.min).toBe('1');
    expect(slider.max).toBe('1');
    expect(slider.value).toBe('1');

    // Preset button displays singular 1 Day
    const presetBtn = screen.getByRole('button', { name: '1 Day' });
    expect(presetBtn).toBeInTheDocument();

    // Tick does not have NaN in left style
    const tick = screen.getByTitle('Set retention to 1 day');
    expect(tick).toBeInTheDocument();
    expect(tick.style.left).toBe('0%');
    expect(tick.style.left).not.toContain('NaN');
  });

  it('clamps retentionDays state when prop exceeds maxRetentionDays', () => {
    render(<RetentionSlider retentionDays={30} maxRetentionDays={7} onSaveRetention={vi.fn()} />);

    // Header displays clamped 7 Days
    expect(screen.getAllByText('7 Days').length).toBe(2);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider.max).toBe('7');
    expect(slider.value).toBe('7');
  });

  it('supports Enter and Space keyboard navigation on step markers', () => {
    render(<RetentionSlider retentionDays={14} onSaveRetention={vi.fn()} />);

    const tick30 = screen.getByTitle('Set retention to 30 days');
    expect(tick30).toHaveAttribute('role', 'button');
    expect(tick30).toHaveAttribute('tabIndex', '0');

    // Press Enter on 30d tick
    fireEvent.keyDown(tick30, { key: 'Enter' });
    expect(screen.getAllByText('30 Days').length).toBe(2);

    // Press Space on 7d tick
    const tick7 = screen.getByTitle('Set retention to 7 days');
    fireEvent.keyDown(tick7, { key: ' ' });
    expect(screen.getAllByText('7 Days').length).toBe(2);
  });
});
