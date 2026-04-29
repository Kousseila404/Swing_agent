// toastBus — bus simple pour émettre/écouter des toasts depuis n'importe
// où dans l'app, et pour persister les 50 derniers en sessionStorage afin
// qu'un click sur l'icône cloche affiche l'historique.

const KEY = 'toast_history';
const MAX_HISTORY = 50;
const _listeners = new Set();

function _readHistory() {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function _writeHistory(items) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(items.slice(-MAX_HISTORY)));
  } catch { /* private mode */ }
}

export function pushToast(text, type = 'ok') {
  const item = {
    id: Date.now() + Math.random(),
    text,
    type,
    ts: new Date().toISOString(),
  };
  const history = _readHistory();
  history.push(item);
  _writeHistory(history);
  _listeners.forEach(fn => { try { fn(item); } catch {} });
  return item;
}

export function getHistory() {
  return _readHistory();
}

export function clearHistory() {
  _writeHistory([]);
  _listeners.forEach(fn => { try { fn(null); } catch {} });
}

export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
