// storage.js — wrapper localStorage / sessionStorage null-safe et typé.
//
// Élimine les ~10 try/catch dispersés dans le projet (preferences, presets,
// toastBus, App, PortfolioPage, SettingsPage, TickerAnalysisModal). Toute
// erreur (mode privé Safari, quota, SSR sans window) est silencieusement
// avalée et la valeur fallback est renvoyée.
//
// API minimale :
//   readJSON(key, fallback)        → JSON.parse-able, fallback sinon
//   writeJSON(key, value)          → silencieusement no-op si erreur
//   readString(key, fallback)
//   writeString(key, value)
//   readNumber(key, fallback)
//   readBoolean(key, fallback)
//   remove(key)
//   session = { ...mêmes méthodes mais sur sessionStorage }

function _ls() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; }
  catch { return null; }
}

function _ss() {
  try { return typeof sessionStorage !== 'undefined' ? sessionStorage : null; }
  catch { return null; }
}

function _readRaw(store, key) {
  if (!store) return null;
  try { return store.getItem(key); } catch { return null; }
}

function _writeRaw(store, key, value) {
  if (!store) return false;
  try { store.setItem(key, value); return true; } catch { return false; }
}

function _removeRaw(store, key) {
  if (!store) return;
  try { store.removeItem(key); } catch { /* private mode */ }
}

function _makeApi(getStore) {
  return {
    readJSON(key, fallback = null) {
      const raw = _readRaw(getStore(), key);
      if (raw == null) return fallback;
      try { return JSON.parse(raw); } catch { return fallback; }
    },

    writeJSON(key, value) {
      try {
        return _writeRaw(getStore(), key, JSON.stringify(value));
      } catch { return false; }
    },

    readString(key, fallback = '') {
      const v = _readRaw(getStore(), key);
      return v == null ? fallback : v;
    },

    writeString(key, value) {
      return _writeRaw(getStore(), key, String(value));
    },

    readNumber(key, fallback = 0) {
      const raw = _readRaw(getStore(), key);
      if (raw == null) return fallback;
      const n = parseFloat(raw);
      return Number.isFinite(n) ? n : fallback;
    },

    readBoolean(key, fallback = false) {
      const raw = _readRaw(getStore(), key);
      if (raw == null) return fallback;
      return raw === 'true';
    },

    remove(key) {
      _removeRaw(getStore(), key);
    },
  };
}

// API par défaut = localStorage.
const local = _makeApi(_ls);
export const session = _makeApi(_ss);

export const readJSON     = local.readJSON;
export const writeJSON    = local.writeJSON;
export const readString   = local.readString;
export const writeString  = local.writeString;
export const readNumber   = local.readNumber;
export const readBoolean  = local.readBoolean;
export const remove       = local.remove;

export default local;
