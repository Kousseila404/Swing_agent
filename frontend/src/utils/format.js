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
