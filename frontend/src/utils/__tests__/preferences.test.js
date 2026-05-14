// Tests pour preferences.useHashSearchParams — l'extraction/sérialisation
// query params dans le fragment URL (ajouté en V2.4 pour filtres bookmarkables).
//
// Pas besoin de testing-library : on test directement le hook via renderHook
// de vitest. jsdom fournit window/history/location.

import { renderHook, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useHashSearchParams } from '../preferences';

describe('useHashSearchParams', () => {
  beforeEach(() => {
    // Reset hash entre chaque test pour isolation.
    window.history.replaceState(null, '', '/');
  });
  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('retourne {} si pas de query string', () => {
    window.history.replaceState(null, '', '/');
    const { result } = renderHook(() => useHashSearchParams());
    const [params] = result.current;
    expect(params).toEqual({});
  });

  it('parse les query params depuis le hash', () => {
    window.history.replaceState(null, '', '#/universe?sector=tech&sort=drift');
    const { result } = renderHook(() => useHashSearchParams());
    const [params] = result.current;
    expect(params).toEqual({ sector: 'tech', sort: 'drift' });
  });

  it('setParams merge non-destructivement', () => {
    window.history.replaceState(null, '', '#/universe?sector=tech');
    const { result } = renderHook(() => useHashSearchParams());
    act(() => {
      result.current[1]({ sort: 'titan' });
    });
    expect(result.current[0]).toEqual({ sector: 'tech', sort: 'titan' });
    expect(window.location.hash).toBe('#/universe?sector=tech&sort=titan');
  });

  it('valeur falsy supprime la clé pour garder URL courte', () => {
    window.history.replaceState(null, '', '#/universe?sector=tech&sort=titan');
    const { result } = renderHook(() => useHashSearchParams());
    act(() => {
      result.current[1]({ sort: '' });
    });
    expect(result.current[0]).toEqual({ sector: 'tech' });
    expect(window.location.hash).toBe('#/universe?sector=tech');
  });

  it('toutes les clés supprimées → pas de ? dans l\'URL', () => {
    window.history.replaceState(null, '', '#/universe?sector=tech');
    const { result } = renderHook(() => useHashSearchParams());
    act(() => {
      result.current[1]({ sector: null });
    });
    expect(window.location.hash).toBe('#/universe');
  });

  it('utilise replaceState (pas push) — historique non pollué', () => {
    window.history.replaceState(null, '', '#/universe');
    const initialLength = window.history.length;
    const { result } = renderHook(() => useHashSearchParams());
    act(() => {
      result.current[1]({ sector: 'tech' });
    });
    act(() => {
      result.current[1]({ sort: 'titan' });
    });
    act(() => {
      result.current[1]({ buy: 'buy_strict' });
    });
    // history.length n'a pas grossi → replaceState confirmé.
    expect(window.history.length).toBe(initialLength);
  });
});
