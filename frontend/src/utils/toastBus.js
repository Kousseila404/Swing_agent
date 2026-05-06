// toastBus — bus simple pour émettre/écouter des toasts depuis n'importe
// où dans l'app, et pour persister les 50 derniers en sessionStorage afin
// qu'un click sur l'icône cloche affiche l'historique.

import { session } from './storage';

const KEY = 'toast_history';
const MAX_HISTORY = 50;
const _listeners = new Set();

function _readHistory() {
  return session.readJSON(KEY, []);
}

function _writeHistory(items) {
  session.writeJSON(KEY, items.slice(-MAX_HISTORY));
}

let _idCounter = 0;
function _nextId() {
  // Time-based ID pour rester unique entre rechargements + monotonic
  // dans une session (suffixe counter pour éviter les collisions à la
  // milliseconde près). Date.now ici n'est PAS dans le render donc OK.
  _idCounter += 1;
  return `${Date.now()}-${_idCounter}`;
}

export function pushToast(text, type = 'ok') {
  const item = {
    id: _nextId(),
    text,
    type,
    ts: new Date().toISOString(),
  };
  const history = _readHistory();
  history.push(item);
  _writeHistory(history);
  _listeners.forEach((fn) => { try { fn(item); } catch { /* listener fault */ } });
  return item;
}

export function getHistory() {
  return _readHistory();
}

export function clearHistory() {
  _writeHistory([]);
  _listeners.forEach((fn) => { try { fn(null); } catch { /* listener fault */ } });
}

export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
