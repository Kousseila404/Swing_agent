import { describe, it, expect } from 'vitest';
import { tzOffsetMinutes, zonedWallClockToUtc } from '../timezone';

describe('tzOffsetMinutes', () => {
  it('Europe/Paris été (CEST, UTC+2)', () => {
    expect(tzOffsetMinutes(new Date('2026-08-19T12:00:00Z'), 'Europe/Paris')).toBe(120);
  });
  it('Europe/Paris hiver (CET, UTC+1)', () => {
    expect(tzOffsetMinutes(new Date('2026-01-15T12:00:00Z'), 'Europe/Paris')).toBe(60);
  });
  it('America/New_York été (EDT, UTC-4)', () => {
    expect(tzOffsetMinutes(new Date('2026-08-19T12:00:00Z'), 'America/New_York')).toBe(-240);
  });
  it('America/New_York hiver (EST, UTC-5)', () => {
    expect(tzOffsetMinutes(new Date('2026-01-15T12:00:00Z'), 'America/New_York')).toBe(-300);
  });
  it('Asia/Hong_Kong — pas de DST, toujours UTC+8', () => {
    expect(tzOffsetMinutes(new Date('2026-08-19T12:00:00Z'), 'Asia/Hong_Kong')).toBe(480);
    expect(tzOffsetMinutes(new Date('2026-01-15T12:00:00Z'), 'Asia/Hong_Kong')).toBe(480);
  });
  it('UTC — offset nul', () => {
    expect(tzOffsetMinutes(new Date('2026-08-19T12:00:00Z'), 'UTC')).toBe(0);
  });
});

describe('zonedWallClockToUtc', () => {
  it('reproduit le cas CNC réel : 09h50 Paris été = 07h50 UTC', () => {
    const utc = zonedWallClockToUtc('2026-08-19', '09:50', 'Europe/Paris');
    expect(utc.toISOString()).toBe('2026-08-19T07:50:00.000Z');
  });

  it('09h50 Paris hiver (CET, +1h) = 08h50 UTC', () => {
    const utc = zonedWallClockToUtc('2026-01-15', '09:50', 'Europe/Paris');
    expect(utc.toISOString()).toBe('2026-01-15T08:50:00.000Z');
  });

  it('New York — 11h00 EDT été = 15h00 UTC', () => {
    const utc = zonedWallClockToUtc('2026-08-19', '11:00', 'America/New_York');
    expect(utc.toISOString()).toBe('2026-08-19T15:00:00.000Z');
  });

  it('Hong Kong — 12h15 HKT = 04h15 UTC (aucune transition DST à gérer)', () => {
    const utc = zonedWallClockToUtc('2026-08-19', '12:15', 'Asia/Hong_Kong');
    expect(utc.toISOString()).toBe('2026-08-19T04:15:00.000Z');
  });

  it('UTC direct — aucune conversion', () => {
    const utc = zonedWallClockToUtc('2026-08-19', '09:50', 'UTC');
    expect(utc.toISOString()).toBe('2026-08-19T09:50:00.000Z');
  });

  it('date ou heure absente -> null', () => {
    expect(zonedWallClockToUtc('', '09:50', 'UTC')).toBeNull();
    expect(zonedWallClockToUtc('2026-08-19', '', 'UTC')).toBeNull();
  });

  it('transition DST US (2e dimanche de mars) : reste cohérent juste après le changement', () => {
    // Le 8 mars 2026, 3h du matin ET passe de EST (UTC-5) à EDT (UTC-4).
    const utc = zonedWallClockToUtc('2026-03-08', '10:00', 'America/New_York');
    expect(utc.toISOString()).toBe('2026-03-08T14:00:00.000Z'); // EDT déjà actif à 10h
  });
});
