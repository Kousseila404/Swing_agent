// Filter presets — sets nommés persistés en localStorage par "scope"
// (universe / sectors / proposals). Chaque preset est un objet libre :
// le caller décide quelle shape stocker.

const KEY = (scope) => `presets_${scope}`;
const LAST = (scope) => `presets_last_${scope}`;

function _read(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    if (v == null) return fallback;
    return JSON.parse(v);
  } catch { return fallback; }
}

function _write(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

export function listPresets(scope) {
  return _read(KEY(scope), []);
}

export function savePreset(scope, name, payload) {
  const list = listPresets(scope);
  const i = list.findIndex(p => p.name === name);
  const entry = { name, payload, updated_at: new Date().toISOString() };
  if (i >= 0) list[i] = entry;
  else        list.push(entry);
  _write(KEY(scope), list);
  _write(LAST(scope), name);
  return list;
}

export function deletePreset(scope, name) {
  const list = listPresets(scope).filter(p => p.name !== name);
  _write(KEY(scope), list);
  if (loadLastApplied(scope) === name) {
    try { localStorage.removeItem(LAST(scope)); } catch { /* private mode */ }
  }
  return list;
}

export function getPreset(scope, name) {
  return listPresets(scope).find(p => p.name === name) || null;
}

export function loadLastApplied(scope) {
  return _read(LAST(scope), null);
}

export function markApplied(scope, name) {
  if (name) _write(LAST(scope), name);
}
