// NewsSection — onglet News du TickerAnalysisModal.
//
// Affiche les news Finnhub d'un ticker sur 7/14/30 jours, lazy-loaded
// (cache backend 1h). Lien direct sur l'article sur clic.

import { useState } from 'react';

import { useNews } from '../../hooks/useApi';
import { Section } from './Layout';
import { relTime } from './helpers';

export default function NewsSection({ ticker }) {
  const [days, setDays] = useState(14);
  const newsQ = useNews(ticker, days);
  const data = newsQ.data || {};
  const articles = data.articles || [];

  return (
    <Section title={`📰 News (${data.n_articles ?? 0})`}>
      <div style={{
        display: 'flex', gap: 6, marginBottom: 10,
        justifyContent: 'flex-end', alignItems: 'center',
      }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          Fenêtre :
        </span>
        {[7, 14, 30].map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => setDays(d)}
            className={`scan-filter-btn ${days === d ? 'active' : ''}`}
            style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}
          >
            {d}j
          </button>
        ))}
        {data.cached && (
          <span
            style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginLeft: 6 }}
            title="Servi depuis le cache 1h"
          >
            ⚡ cache
          </span>
        )}
      </div>

      {newsQ.isLoading && (
        <div style={{
          fontSize: '0.78rem', color: 'var(--text-muted)',
          padding: '1rem', textAlign: 'center',
        }}>
          Chargement news…
        </div>
      )}

      {data.error && (
        <div style={{
          padding: '0.7rem', borderRadius: 6, fontSize: '0.78rem',
          background: 'rgba(248,113,113,0.08)', color: '#fb7185',
          border: '1px solid rgba(248,113,113,0.3)',
        }}>
          ⚠️ {data.error}
          {data.error.includes('FINNHUB_API_KEY') && (
            <div style={{
              marginTop: 4, fontSize: '0.7rem', color: 'var(--text-muted)',
            }}>
              Configure <code>FINNHUB_API_KEY</code> dans <code>backend/.env</code> puis
              redémarre <code>swing-api.service</code>.
            </div>
          )}
        </div>
      )}

      {!newsQ.isLoading && articles.length === 0 && !data.error && (
        <div style={{
          fontSize: '0.78rem', color: 'var(--text-muted)',
          padding: '1rem', textAlign: 'center',
        }}>
          📭 Aucune news sur les {days} derniers jours.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {articles.map((a) => (
          <a
            key={a.id || a.url}
            href={a.url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'flex', gap: 10, padding: '0.65rem 0.8rem',
              background: 'var(--bg-tertiary)', borderRadius: 6,
              border: '1px solid var(--border)',
              textDecoration: 'none', color: 'inherit',
              transition: 'background 0.15s, border-color 0.15s',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-primary)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; }}
          >
            {a.image && (
              <img
                src={a.image}
                alt=""
                loading="lazy"
                onError={(e) => { e.currentTarget.style.display = 'none'; }}
                style={{
                  width: 64, height: 48, objectFit: 'cover',
                  borderRadius: 4, flexShrink: 0,
                }}
              />
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: '0.85rem', fontWeight: 600, lineHeight: 1.35,
                color: 'var(--text-main)',
              }}>
                {a.headline}
              </div>
              {a.summary && (
                <div style={{
                  fontSize: '0.74rem', color: 'var(--text-muted)',
                  marginTop: 3, lineHeight: 1.4,
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}>
                  {a.summary}
                </div>
              )}
              <div style={{
                fontSize: '0.66rem', color: 'var(--text-muted)',
                marginTop: 4, display: 'flex', gap: 8, alignItems: 'center',
              }}>
                {a.source && (
                  <span style={{ fontWeight: 700, color: 'var(--accent-primary)' }}>
                    {a.source}
                  </span>
                )}
                {a.datetime && <span>· {relTime(a.datetime)}</span>}
                {a.category && (
                  <span style={{
                    border: '1px solid var(--border)', borderRadius: 3,
                    padding: '0 5px', textTransform: 'capitalize',
                  }}>
                    {a.category}
                  </span>
                )}
              </div>
            </div>
          </a>
        ))}
      </div>
    </Section>
  );
}
