import { describe, it, expect } from 'vitest';
import { factorColor } from '../colors';

describe('factorColor', () => {
  it('> 75 → var(--success)', () => {
    expect(factorColor(76)).toBe('var(--success)');
    expect(factorColor(100)).toBe('var(--success)');
  });
  it('[40, 75] → neutre slate', () => {
    expect(factorColor(40)).toBe('#94a3b8');
    expect(factorColor(60)).toBe('#94a3b8');
    expect(factorColor(75)).toBe('#94a3b8');
  });
  it('< 40 → var(--danger)', () => {
    expect(factorColor(39.9)).toBe('var(--danger)');
    expect(factorColor(0)).toBe('var(--danger)');
  });
  it('null/NaN/Infinity → muted', () => {
    expect(factorColor(null)).toBe('var(--text-muted)');
    expect(factorColor(undefined)).toBe('var(--text-muted)');
    expect(factorColor(NaN)).toBe('var(--text-muted)');
    expect(factorColor(Infinity)).toBe('var(--text-muted)');
  });
});
