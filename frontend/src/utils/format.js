// Helpers de formatage null/NaN-safe (Sprint 3 — W5).
// Toutes ces fonctions tolèrent null, undefined, NaN, Infinity et retournent
// un placeholder lisible ("—", "N/A") au lieu de "undefined%" ou "NaN".

const DASH = "—";

// True ssi la valeur peut être formatée numériquement.
function isNumeric(v) {
  return typeof v === "number" && Number.isFinite(v);
}

// fmtNum(null, 2) → "—", fmtNum(1.234, 2) → "1.23"
export function fmtNum(v, digits = 2, fallback = DASH) {
  return isNumeric(v) ? v.toFixed(digits) : fallback;
}

// fmtPct(0.1234, 1) → "12.3%" — la valeur est déjà une fraction [0..1]
export function fmtPct(v, digits = 1, fallback = DASH) {
  return isNumeric(v) ? `${(v * 100).toFixed(digits)}%` : fallback;
}

// fmtPctRaw(12.34, 1) → "12.3%" — la valeur est déjà un pourcentage
export function fmtPctRaw(v, digits = 1, fallback = DASH) {
  return isNumeric(v) ? `${v.toFixed(digits)}%` : fallback;
}

// fmtSignedPct(+3.5) → "+3.5%", fmtSignedPct(-2.1) → "-2.1%"
export function fmtSignedPct(v, digits = 1, fallback = DASH) {
  if (!isNumeric(v)) return fallback;
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

// fmtMarketCap(3.2e12) → "$3.20T"
export function fmtMarketCap(v, fallback = DASH) {
  if (!isNumeric(v)) return fallback;
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

// fmtPrice(145.2) → "$145.20"
export function fmtPrice(v, digits = 2, fallback = DASH) {
  return isNumeric(v) ? `$${v.toFixed(digits)}` : fallback;
}

// fmtMoney(12_345.6) → "$12,345.60" — comme fmtPrice mais groupé en milliers
// pour les capitaux / P&L absolus. Pattern Seeking Alpha : toujours grouper.
export function fmtMoney(v, digits = 2, fallback = DASH) {
  if (!isNumeric(v)) return fallback;
  return `$${v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

// fmtSignedMoney(+1234.5) → "+$1,234.50", fmtSignedMoney(-50) → "-$50.00"
// Pratique pour le P&L réalisé / non-réalisé.
export function fmtSignedMoney(v, digits = 2, fallback = DASH) {
  if (!isNumeric(v)) return fallback;
  const sign = v >= 0 ? '+' : '-';
  const abs = Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${sign}$${abs}`;
}

// fmtCount(12345) → "12,345" — entiers groupés (n_trades, n_positions).
export function fmtCount(v, fallback = DASH) {
  if (!isNumeric(v)) return fallback;
  return Math.round(v).toLocaleString();
}

// safeCompare(null, ">=", 60) → false (évite de colorer du vert par défaut)
export function safeCompare(v, op, threshold) {
  if (!isNumeric(v)) return false;
  switch (op) {
    case ">":  return v >  threshold;
    case ">=": return v >= threshold;
    case "<":  return v <  threshold;
    case "<=": return v <= threshold;
    case "==": return v === threshold;
    default:   return false;
  }
}
