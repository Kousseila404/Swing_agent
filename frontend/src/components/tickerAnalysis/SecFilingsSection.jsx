// SecFilingsSection — onglet Filings du TickerAnalysisModal.
//
// Liste les dépôts SEC EDGAR (10-K, 10-Q, 8-K, 4, 13F-HR…) d'un ticker
// avec filtre par catégorie. Les filings sont publics et arrivent sous
// 2 jours ouvrés ; cluster = 3+ filings dans une fenêtre 7j (signal
// smart-money fort).

import { useMemo, useState } from 'react';

import { useSecFilings } from '../../hooks/useApi';
import { FORM_TONE } from './constants';
import { Section } from './Layout';

const FILTERS = [
  { id: 'all',     label: 'Tout' },
  { id: 'reports', label: '10-K/Q' },
  { id: 'events',  label: '8-K' },
  { id: 'insider', label: 'Insider' },
];

export default function SecFilingsSection({ ticker }) {
  const [formFilter, setFormFilter] = useState('all');
  const filingsQ = useSecFilings(ticker, 50);
  const data = filingsQ.data || {};

  // On lit `filings` à l'intérieur du useMemo : `data.filings || []` créerait
  // une nouvelle ref à chaque render et invaliderait le memo.
  const filtered = useMemo(() => {
    const list = filingsQ.data?.filings || [];
    if (formFilter === 'all') return list;
    if (formFilter === 'reports') return list.filter((f) => /^10-[KQ]/.test(f.form));
    if (formFilter === 'events')  return list.filter((f) => /^8-K/.test(f.form));
    if (formFilter === 'insider') return list.filter((f) => /^4/.test(f.form));
    return list;
  }, [filingsQ.data, formFilter]);

  return (
    <Section title={`📂 Filings SEC EDGAR (${data.n_filings ?? 0})`}>
      <div style={{
        display: 'flex', gap: 4, flexWrap: 'wrap',
        marginBottom: 8, alignItems: 'center',
      }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          Filtre :
        </span>
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFormFilter(f.id)}
            className={`scan-filter-btn ${formFilter === f.id ? 'active' : ''}`}
            style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}
          >
            {f.label}
          </button>
        ))}
        {data.cik && (
          <span style={{
            fontSize: '0.65rem', color: 'var(--text-muted)',
            marginLeft: 'auto', fontFamily: 'monospace',
          }}>
            CIK {data.cik}
          </span>
        )}
      </div>

      {filingsQ.isLoading && (
        <div style={{
          fontSize: '0.78rem', color: 'var(--text-muted)', padding: '0.5rem',
        }}>
          Chargement filings…
        </div>
      )}

      {data.error && (
        <div style={{
          padding: '0.6rem', borderRadius: 6, fontSize: '0.78rem',
          background: 'rgba(248,113,113,0.08)', color: '#fb7185',
          border: '1px solid rgba(248,113,113,0.3)',
        }}>
          ⚠️ {data.error === 'cik_unknown'
            ? "Ticker introuvable dans l'index SEC EDGAR."
            : data.error}
        </div>
      )}

      {!filingsQ.isLoading && filtered.length === 0 && !data.error && (
        <div style={{
          fontSize: '0.78rem', color: 'var(--text-muted)', padding: '0.5rem',
        }}>
          📭 Aucun filing pour ce filtre.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {filtered.map((f) => {
          const meta = FORM_TONE[f.form] || {
            bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)', icon: '📄',
          };
          return (
            <a
              key={f.accession + f.form}
              href={f.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '0.4rem 0.7rem',
                background: 'var(--bg-tertiary)',
                borderRadius: 5,
                border: '1px solid var(--border)',
                textDecoration: 'none', color: 'inherit',
                transition: 'border-color 0.15s, background 0.15s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-primary)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; }}
            >
              <span style={{
                fontSize: '0.65rem', fontWeight: 800, letterSpacing: '0.04em',
                padding: '0.2rem 0.5rem', borderRadius: 4,
                background: meta.bg, color: meta.fg,
                minWidth: 78, textAlign: 'center', fontFamily: 'monospace',
              }}>
                {meta.icon} {f.form}
              </span>
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                {f.form_label}
              </span>
              <span style={{
                marginLeft: 'auto', display: 'flex', gap: 12,
                alignItems: 'center', fontSize: '0.74rem',
              }}>
                <span style={{ fontFamily: 'monospace', color: 'var(--text-main)' }}>
                  {f.date}
                </span>
                <span style={{
                  color: 'var(--text-muted)', minWidth: 60, textAlign: 'right',
                }}>
                  {f.days_ago === 0 ? 'auj.' : `il y a ${f.days_ago}j`}
                </span>
                <span style={{ color: 'var(--accent-primary)', fontSize: '0.85rem' }}>
                  ↗
                </span>
              </span>
            </a>
          );
        })}
      </div>
    </Section>
  );
}
