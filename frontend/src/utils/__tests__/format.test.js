import { describe, it, expect } from 'vitest';
import {
  fmtNum,
  fmtPct,
  fmtPctRaw,
  fmtSignedPct,
  fmtMarketCap,
  fmtPrice,
  fmtMoney,
  fmtSignedMoney,
  fmtCount,
  safeCompare,
} from '../format';

describe('fmtNum', () => {
  it('formate un nombre fini', () => {
    expect(fmtNum(1.234, 2)).toBe('1.23');
    expect(fmtNum(0, 0)).toBe('0');
  });
  it('retourne "—" sur null/undefined/NaN/Infinity', () => {
    expect(fmtNum(null)).toBe('—');
    expect(fmtNum(undefined)).toBe('—');
    expect(fmtNum(NaN)).toBe('—');
    expect(fmtNum(Infinity)).toBe('—');
  });
  it('accepte un fallback custom', () => {
    expect(fmtNum(null, 2, 'N/A')).toBe('N/A');
  });
});

describe('fmtPct (fraction → %)', () => {
  it('multiplie par 100 et suffixe %', () => {
    expect(fmtPct(0.1234, 1)).toBe('12.3%');
    expect(fmtPct(1, 0)).toBe('100%');
  });
  it('gère null/NaN', () => {
    expect(fmtPct(null)).toBe('—');
    expect(fmtPct(NaN)).toBe('—');
  });
});

describe('fmtPctRaw (déjà en %)', () => {
  it('ne multiplie pas', () => {
    expect(fmtPctRaw(12.34, 1)).toBe('12.3%');
  });
});

describe('fmtSignedPct', () => {
  it('préfixe + sur positif, - par toFixed sur négatif', () => {
    expect(fmtSignedPct(3.5)).toBe('+3.5%');
    expect(fmtSignedPct(-2.1)).toBe('-2.1%');
    expect(fmtSignedPct(0)).toBe('+0.0%');
  });
  it('retourne "—" sur invalide', () => {
    expect(fmtSignedPct(null)).toBe('—');
  });
});

describe('fmtMarketCap', () => {
  it('échelle T/B/M', () => {
    expect(fmtMarketCap(3.2e12)).toBe('$3.20T');
    expect(fmtMarketCap(4.5e9)).toBe('$4.5B');
    expect(fmtMarketCap(500e6)).toBe('$500M');
    expect(fmtMarketCap(1234)).toBe('$1234');
  });
  it('null → "—"', () => {
    expect(fmtMarketCap(null)).toBe('—');
    expect(fmtMarketCap(Infinity)).toBe('—');
  });
});

describe('fmtPrice', () => {
  it('ajoute $ + 2 décimales par défaut', () => {
    expect(fmtPrice(145.2)).toBe('$145.20');
    expect(fmtPrice(0)).toBe('$0.00');
  });
});

describe('fmtMoney (groupé)', () => {
  it('groupe les milliers', () => {
    // Note: Intl.NumberFormat utilise séparateurs locale-dépendants.
    // En "en-US" ce serait "$12,345.60". En "fr" ce serait "$12 345,60".
    // On vérifie la présence du $ et des 2 décimales.
    const result = fmtMoney(12_345.6, 2);
    expect(result.startsWith('$')).toBe(true);
    expect(result).toMatch(/12.345/);
    expect(result.endsWith('60')).toBe(true);
  });
  it('null → "—"', () => {
    expect(fmtMoney(null)).toBe('—');
  });
});

describe('fmtSignedMoney', () => {
  it('préfixe + sur positif, - sur négatif', () => {
    expect(fmtSignedMoney(1234.5, 2)).toMatch(/^\+\$/);
    expect(fmtSignedMoney(-50, 2)).toMatch(/^-\$/);
  });
  it('null → "—"', () => {
    expect(fmtSignedMoney(null)).toBe('—');
  });
});

describe('fmtCount', () => {
  it('arrondi + groupé', () => {
    const r = fmtCount(12345);
    expect(r).toMatch(/12.345/);
  });
  it('null → "—"', () => {
    expect(fmtCount(null)).toBe('—');
  });
});

describe('safeCompare', () => {
  it('renvoie false pour null/NaN peu importe l\'op', () => {
    expect(safeCompare(null, '>=', 60)).toBe(false);
    expect(safeCompare(NaN, '>', 0)).toBe(false);
  });
  it('applique les comparateurs numériques', () => {
    expect(safeCompare(65, '>=', 60)).toBe(true);
    expect(safeCompare(59, '>=', 60)).toBe(false);
    expect(safeCompare(10, '<', 20)).toBe(true);
    expect(safeCompare(5, '==', 5)).toBe(true);
  });
  it('op inconnu → false', () => {
    expect(safeCompare(5, '!!', 5)).toBe(false);
  });
});
