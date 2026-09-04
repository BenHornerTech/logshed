import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RetentionSlider } from '../components/settings/RetentionSlider.tsx';
import * as systemApi from '../api/system.ts';

describe('RetentionSlider Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders slider supporting up to 365 days with presets', () => {
    render(<RetentionSlider retentionDays={30} onSaveRetention={vi.fn()} />);

    expect(screen.getByText('Log Retention & Vacuum Policy')).toBeInTheDocument();
    // Header and Preset button both display 30 Days
    expect(screen.getAllByText('30 Days').length).toBe(2);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider).toBeInTheDocument();
    expect(slider.min).toBe('1');
    expect(slider.max).toBe('365');
    expect(slider.value).toBe('30');

    // Verify preset quick-select buttons exist
    const presets = [7, 14, 30, 90, 180, 365];
    presets.forEach((val) => {
      expect(screen.getByRole('button', { name: `${val} Days` })).toBeInTheDocument();
    });
  });

  it('positions ticks with percentage-based left styling matching slider thumbs', () => {
    render(<RetentionSlider retentionDays={30} onSaveRetention={vi.fn()} />);

    const min = 1;
    const max = 365;
    const presets = [7, 14, 30, 90, 180, 365];

    presets.forEach((val) => {
      const expectedPercent = ((val - min) / (max - min)) * 100;
      const tickContainer = screen.getByTitle(`Set retention to ${val} days`);
      expect(tickContainer).toBeInTheDocument();
      expect(tickContainer.style.left).toBe(`${expectedPercent}%`);
    });
  });

  it('updates selected days when clicking a preset button or dragging slider', () => {
    render(<RetentionSlider retentionDays={30} onSaveRetention={vi.fn()} />);

    // Click 90 Days preset
    const preset90Btn = screen.getByRole('button', { name: '90 Days' });
    fireEvent.click(preset90Btn);

    expect(screen.getAllByText('90 Days').length).toBe(2);
    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider.value).toBe('90');

    // Click tick for 180 days
    const tick180 = screen.getByTitle('Set retention to 180 days');
    fireEvent.click(tick180);

    expect(screen.getAllByText('180 Days').length).toBe(2);
    expect(slider.value).toBe('180');
  });

  it('disables save button when unchanged and enables it when modified', async () => {
    const onSaveMock = vi.fn().mockResolvedValue(undefined);
    render(<RetentionSlider retentionDays={30} onSaveRetention={onSaveMock} />);

    const saveBtn = screen.getByRole('button', { name: /Save Retention Policy/i });
    expect(saveBtn).toBeDisabled();
    expect(saveBtn.className).toContain('opacity-40');

    // Change to 14 days
    const preset14Btn = screen.getByRole('button', { name: '14 Days' });
    fireEvent.click(preset14Btn);

    expect(saveBtn).not.toBeDisabled();
    expect(saveBtn.className).toContain('bg-accent-600');

    fireEvent.click(saveBtn);
    expect(onSaveMock).toHaveBeenCalledWith(14);

    await waitFor(() => {
      expect(screen.getByText('Saved!')).toBeInTheDocument();
    });
  });

  it('triggers manual prune and vacuum on demand', async () => {
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
        retentionDays={30}
        onSaveRetention={vi.fn()}
        onPruneCompleted={onPruneCompleted}
      />
    );

    const pruneBtn = screen.getByRole('button', { name: /Prune & Vacuum Now/i });
    fireEvent.click(pruneBtn);

    await waitFor(() => {
      expect(pruneSpy).toHaveBeenCalled();
      expect(onPruneCompleted).toHaveBeenCalled();
      expect(screen.getByText('Prune Completed Successfully:')).toBeInTheDocument();
      expect(screen.getByText('120')).toBeInTheDocument();
    });
  });

  it('renders explanatory caption and Info icon with tooltip about automated schedule and manual prune', () => {
    render(<RetentionSlider retentionDays={30} onSaveRetention={vi.fn()} />);

    // Explanatory footnote caption
    expect(
      screen.getByText(
        'Automated retention pruning runs daily every 24 hours. Manual prune purges logs older than the saved policy and reclaims disk space immediately.'
      )
    ).toBeInTheDocument();

    // Info icon tooltip
    expect(
      screen.getByTitle(/Pruning runs automatically once every 24 hours/i)
    ).toBeInTheDocument();
  });
});
