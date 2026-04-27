// Pagination stricte — utilisée par les grandes tables (Universe, SectorsDrill).
// Affiche "X-Y sur Z · page P/T" avec contrôles ‹ › + saut début/fin.

export default function Pagination({ page, totalPages, total, pageSize, onChange }) {
  const from = (page - 1) * pageSize + 1;
  const to   = Math.min(page * pageSize, total);

  const go = (n) => onChange(Math.max(1, Math.min(totalPages, n)));

  return (
    <div className="pagination-bar" style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      gap: '1rem', padding: '0.5rem 0.25rem', flexWrap: 'wrap',
      fontSize: '0.8rem', color: 'var(--text-muted)',
    }}>
      <span>
        <strong style={{ color: 'var(--text-main)' }}>{from}-{to}</strong>
        {' '}sur <strong style={{ color: 'var(--text-main)' }}>{total}</strong>
      </span>
      <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
        <button className="scan-filter-btn" onClick={() => go(1)} disabled={page <= 1}
                title="Première page" style={{ padding: '0.35rem 0.55rem' }}>«</button>
        <button className="scan-filter-btn" onClick={() => go(page - 1)} disabled={page <= 1}
                style={{ padding: '0.35rem 0.55rem' }}>‹ Préc.</button>
        <span style={{ padding: '0 0.5rem', fontFamily: 'monospace' }}>
          {page} / {totalPages}
        </span>
        <button className="scan-filter-btn" onClick={() => go(page + 1)} disabled={page >= totalPages}
                style={{ padding: '0.35rem 0.55rem' }}>Suiv. ›</button>
        <button className="scan-filter-btn" onClick={() => go(totalPages)} disabled={page >= totalPages}
                title="Dernière page" style={{ padding: '0.35rem 0.55rem' }}>»</button>
      </div>
    </div>
  );
}
