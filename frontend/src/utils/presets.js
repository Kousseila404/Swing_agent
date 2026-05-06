// Filter presets — sets nommés persistés en localStorage par "scope"
// (universe / sectors / proposals). Chaque preset est un objet libre :
// le caller décide quelle shape stocker.

import { readJSON, remove, writeJSON } from './storage';

const KEY  = (scope) => `presets_${scope}`;
const LAST = (scope) => `presets_last_${scope}`;

export function listPresets(scope) {
  return readJSON(KEY(scope), []);
}

export function savePreset(scope, name, payload) {
  const list = listPresets(scope);
  const i = list.findIndex((p) => p.name === name);
  const entry = { name, payload, updated_at: new Date().toISOString() };
  if (i >= 0) list[i] = entry;
  else        list.push(entry);
  writeJSON(KEY(scope), list);
  writeJSON(LAST(scope), name);
  return list;
}

export function deletePreset(scope, name) {
  const list = listPresets(scope).filter((p) => p.name !== name);
  writeJSON(KEY(scope), list);
  if (loadLastApplied(scope) === name) {
    remove(LAST(scope));
  }
  return list;
}

export function getPreset(scope, name) {
  return listPresets(scope).find((p) => p.name === name) || null;
}

export function loadLastApplied(scope) {
  return readJSON(LAST(scope), null);
}

export function markApplied(scope, name) {
  if (name) writeJSON(LAST(scope), name);
}
