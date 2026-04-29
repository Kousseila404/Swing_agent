// Portfolio helpers — extraits de PortfolioPage.jsx pour permettre des tests unitaires.

export function parseNum(x) {
  const n = typeof x === 'number' ? x : parseFloat(x);
  return Number.isFinite(n) ? n : 0;
}

// LTCG seuil US : 365 jours de détention pour passer en long-term
// capital gains tax rate (15% vs short-term taxé au revenu).
export const LTCG_THRESHOLD_DAYS = 365;

// Retourne {days_held, days_to_ltcg, ltcg_eligible} pour une position.
// asOf = "now" par défaut, surchargé pour les tests.
export function holdingPeriod(entryDate, asOf = new Date()) {
  if (!entryDate) return null;
  try {
    const d0 = new Date(String(entryDate).slice(0, 10));
    if (Number.isNaN(d0.getTime())) return null;
    const days = Math.floor((asOf.getTime() - d0.getTime()) / 86_400_000);
    return {
      days_held:     Math.max(0, days),
      days_to_ltcg:  Math.max(0, LTCG_THRESHOLD_DAYS - days),
      ltcg_eligible: days >= LTCG_THRESHOLD_DAYS,
    };
  } catch { return null; }
}

export function tradePnL(t) {
  const entry  = parseNum(t.Entry);
  const exit_p = parseNum(t.Exit_Price);
  const size   = parseNum(t.Size);
  if (!entry || !exit_p || !size) return null;
  return t.Direction === 'LONG' ? (exit_p - entry) * size : (entry - exit_p) * size;
}

// Live snapshot a priorité sur le CSV brut ; fallback CSV si tracker pas démarré.
export function mergeLivePositions(equityPositions, csvPositions) {
  if (Array.isArray(equityPositions) && equityPositions.length > 0) {
    const byTicker = new Map(
      (csvPositions || []).map(t => [String(t.Ticker || '').toUpperCase(), t]),
    );
    return equityPositions.map(p => {
      const csv = byTicker.get(String(p.ticker || '').toUpperCase()) || {};
      return {
        Ticker:        p.ticker,
        Direction:     p.direction,
        Entry:         p.entry,
        Stop_Loss:     p.stop_loss,
        Take_Profit:   p.take_profit,
        Size:          p.size,
        RR:            csv.RR ?? '',
        Date:          p.entry_date || csv.Date || '',
        Signal:        csv.Signal || '',
        current_price: p.current_price,
        unrealized_pnl: p.unrealized_pnl,
        pct_from_entry: p.pct_from_entry,
        pct_to_sl:     p.pct_to_sl,
        pct_to_tp:     p.pct_to_tp,
        live:          true,
      };
    });
  }
  return (csvPositions || []).map(t => ({ ...t, live: false }));
}

// CSV RFC 4180-ish : échappe les guillemets, quote les champs avec virgule/newline.
export function toCsv(rows, cols) {
  const esc = v => {
    if (v == null) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = cols.join(',');
  const body = rows.map(r => cols.map(c => esc(r[c])).join(',')).join('\n');
  return `${header}\n${body}`;
}
