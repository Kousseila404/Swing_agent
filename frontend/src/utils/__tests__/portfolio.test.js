import { describe, it, expect } from 'vitest';
import { parseNum, tradePnL, mergeLivePositions, toCsv } from '../portfolio';

describe('parseNum', () => {
  it('passe les nombres finis', () => {
    expect(parseNum(42)).toBe(42);
    expect(parseNum(-3.14)).toBe(-3.14);
  });
  it('parse les strings', () => {
    expect(parseNum('12.5')).toBe(12.5);
    expect(parseNum('-0.1')).toBe(-0.1);
  });
  it('retourne 0 sur invalide (NaN/Infinity/null/undefined/garbage)', () => {
    expect(parseNum('abc')).toBe(0);
    expect(parseNum(null)).toBe(0);
    expect(parseNum(undefined)).toBe(0);
    expect(parseNum(Infinity)).toBe(0);
    expect(parseNum(NaN)).toBe(0);
  });
});

describe('tradePnL', () => {
  it('LONG : (exit - entry) * size', () => {
    expect(tradePnL({ Direction: 'LONG', Entry: 100, Exit_Price: 110, Size: 5 })).toBe(50);
  });
  it('SHORT : (entry - exit) * size', () => {
    expect(tradePnL({ Direction: 'SHORT', Entry: 100, Exit_Price: 90, Size: 5 })).toBe(50);
  });
  it('perte LONG', () => {
    expect(tradePnL({ Direction: 'LONG', Entry: 100, Exit_Price: 95, Size: 2 })).toBe(-10);
  });
  it('champs strings acceptés via parseNum', () => {
    expect(tradePnL({ Direction: 'LONG', Entry: '100', Exit_Price: '105', Size: '3' })).toBe(15);
  });
  it('null si un champ manque', () => {
    expect(tradePnL({ Direction: 'LONG', Entry: 100, Exit_Price: null, Size: 1 })).toBeNull();
    expect(tradePnL({ Direction: 'LONG', Entry: 100, Size: 1 })).toBeNull();
    expect(tradePnL({ Direction: 'LONG', Entry: 'abc', Exit_Price: 100, Size: 1 })).toBeNull();
  });
});

describe('mergeLivePositions', () => {
  it('live snapshot prioritaire, enrichi depuis CSV (RR, Signal, Date)', () => {
    const equity = [{
      ticker: 'NVDA', direction: 'LONG', entry: 100, stop_loss: 90, take_profit: 120,
      size: 5, current_price: 105, unrealized_pnl: 25, pct_from_entry: 5,
      pct_to_sl: -10, pct_to_tp: 15, entry_date: '2026-04-01',
    }];
    const csv = [{ Ticker: 'NVDA', RR: '1:2', Signal: 'CHANDELIER', Date: '2026-04-01' }];
    const out = mergeLivePositions(equity, csv);
    expect(out).toHaveLength(1);
    expect(out[0].Ticker).toBe('NVDA');
    expect(out[0].live).toBe(true);
    expect(out[0].RR).toBe('1:2');
    expect(out[0].Signal).toBe('CHANDELIER');
    expect(out[0].unrealized_pnl).toBe(25);
  });

  it('match case-insensitive sur ticker', () => {
    const out = mergeLivePositions(
      [{ ticker: 'aapl', direction: 'LONG', entry: 150, size: 1 }],
      [{ Ticker: 'AAPL', RR: '1:3', Signal: 'SIG' }],
    );
    expect(out[0].RR).toBe('1:3');
    expect(out[0].Signal).toBe('SIG');
  });

  it('CSV manquant → live sans enrichissement', () => {
    const out = mergeLivePositions(
      [{ ticker: 'TSLA', direction: 'LONG' }],
      null,
    );
    expect(out[0].Ticker).toBe('TSLA');
    expect(out[0].RR).toBe('');
  });

  it('pas de snapshot live → fallback CSV avec live=false', () => {
    const csv = [{ Ticker: 'MSFT', Direction: 'LONG', Entry: 300 }];
    const out = mergeLivePositions([], csv);
    expect(out).toHaveLength(1);
    expect(out[0].live).toBe(false);
    expect(out[0].Entry).toBe(300);
  });

  it('tout vide → []', () => {
    expect(mergeLivePositions(null, null)).toEqual([]);
    expect(mergeLivePositions([], [])).toEqual([]);
  });
});

describe('toCsv', () => {
  it('header + body simples', () => {
    const out = toCsv(
      [{ a: 1, b: 2 }, { a: 3, b: 4 }],
      ['a', 'b'],
    );
    expect(out).toBe('a,b\n1,2\n3,4');
  });

  it('quote les champs avec virgule/newline/guillemet', () => {
    const out = toCsv(
      [{ x: 'hello, world', y: 'line1\nline2', z: 'say "hi"' }],
      ['x', 'y', 'z'],
    );
    expect(out).toBe('x,y,z\n"hello, world","line1\nline2","say ""hi"""');
  });

  it('null/undefined → string vide', () => {
    const out = toCsv([{ a: null, b: undefined, c: 0 }], ['a', 'b', 'c']);
    expect(out).toBe('a,b,c\n,,0');
  });

  it('0 ligne → juste le header', () => {
    expect(toCsv([], ['x', 'y'])).toBe('x,y\n');
  });
});
