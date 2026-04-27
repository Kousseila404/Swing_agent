import { describe, it, expect } from 'vitest';
import { histogramBins, computeUnderwater } from '../performance';

describe('histogramBins', () => {
  it('vide sur input vide', () => {
    expect(histogramBins([], 10)).toEqual([]);
    expect(histogramBins(null, 10)).toEqual([]);
  });

  it('valeur unique → 1 seul bin', () => {
    const out = histogramBins([50, 50, 50], 10);
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(3);
    expect(out[0].color).toBe('var(--success)');
  });

  it('valeur unique négative → couleur danger', () => {
    const out = histogramBins([-10, -10], 10);
    expect(out[0].color).toBe('var(--danger)');
  });

  it('distribue dans nBins et conserve le total', () => {
    const vals = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
    const bins = histogramBins(vals, 10);
    expect(bins).toHaveLength(10);
    const total = bins.reduce((s, b) => s + b.count, 0);
    expect(total).toBe(vals.length);
  });

  it('clamp l\'index de la borne max dans le dernier bin', () => {
    // 100 doit tomber dans le dernier bin (Math.min guard)
    const bins = histogramBins([0, 100], 10);
    expect(bins[9].count).toBeGreaterThanOrEqual(1);
    const total = bins.reduce((s, b) => s + b.count, 0);
    expect(total).toBe(2);
  });
});

describe('computeUnderwater', () => {
  it('drawdown 0 tant qu\'on fait de nouveaux peaks', () => {
    const eq = [
      ['2026-01-01', 100000],
      ['2026-01-02', 101000],
      ['2026-01-03', 102000],
    ];
    const out = computeUnderwater(eq);
    expect(out.every(p => p.dd === 0)).toBe(true);
  });

  it('calcule un DD négatif quand on repasse sous le peak', () => {
    const eq = [
      ['2026-01-01', 100000],
      ['2026-01-02', 110000],   // peak = 110k
      ['2026-01-03', 99000],    // dd = -10%
    ];
    const out = computeUnderwater(eq);
    expect(out[0].dd).toBe(0);
    expect(out[1].dd).toBe(0);
    expect(out[2].dd).toBe(-10);
  });

  it('slice la date à YYYY-MM-DD', () => {
    const out = computeUnderwater([['2026-01-01T12:34:56', 100000]]);
    expect(out[0].date).toBe('2026-01-01');
  });

  it('peak initialisé à INITIAL_CAPITAL (100k)', () => {
    // equity part à 95k → DD de -5% même sans history précédente
    const out = computeUnderwater([['2026-01-01', 95000]]);
    expect(out[0].dd).toBe(-5);
  });

  it('accepte un initialCapital custom', () => {
    const out = computeUnderwater([['2026-01-01', 47500]], 50000);
    expect(out[0].dd).toBe(-5);
  });
});
