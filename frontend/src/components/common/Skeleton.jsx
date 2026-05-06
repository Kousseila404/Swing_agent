// Skeleton — primitive de chargement (shimmer).
//
// Variants :
//   - <Skeleton w={120} h={16} />           : barre simple
//   - <SkeletonLine count={3} />             : N lignes empilées
//   - <SkeletonCircle size={32} />           : avatar / dot
//   - <SkeletonBlock h={120} />              : carte
//   - <PageSkeleton tiles={4} rows={3} />   : page complète, fallback Suspense
//
// Anim CSS gérée dans index.css → respecte prefers-reduced-motion auto.

export function Skeleton({ w = '100%', h = 12, radius, style, className = '', ...rest }) {
  return (
    <span
      className={`skeleton ${className}`}
      style={{
        width: typeof w === 'number' ? `${w}px` : w,
        height: typeof h === 'number' ? `${h}px` : h,
        borderRadius: radius,
        ...style,
      }}
      aria-hidden="true"
      {...rest}
    />
  );
}

export function SkeletonLine({ count = 1, last = '60%' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton
          key={i}
          h={12}
          w={i === count - 1 && count > 1 ? last : '100%'}
        />
      ))}
    </div>
  );
}

export function SkeletonCircle({ size = 32 }) {
  return <Skeleton w={size} h={size} radius="50%" />;
}

export function SkeletonBlock({ h = 120 }) {
  return <Skeleton w="100%" h={h} radius={14} />;
}

// Fallback Suspense par défaut : 4 KPI tiles + 1 grand bloc + 3 lignes.
export function PageSkeleton({ tiles = 4, blockHeight = 240, rows = 3 }) {
  return (
    <div className="page-skeleton" role="status" aria-live="polite" aria-label="Chargement…">
      <div className="page-skeleton-row">
        {Array.from({ length: tiles }).map((_, i) => (
          <div key={i} className="page-skeleton-tile">
            <Skeleton w="55%" h={10} />
            <Skeleton w="80%" h={22} />
            <Skeleton w="40%" h={9} />
          </div>
        ))}
      </div>
      <SkeletonBlock h={blockHeight} />
      {rows > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <SkeletonCircle size={28} />
              <Skeleton h={12} w={`${50 + (i * 9) % 35}%`} />
              <Skeleton h={12} w={80} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default Skeleton;
