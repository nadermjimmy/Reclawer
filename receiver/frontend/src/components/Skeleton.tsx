/** Static tint skeletons (no shimmer). Render only after a 300 ms delay (see useDelayed). */
export function Skeleton({ width = "100%", height = 14, radius = 999 }: { width?: number | string; height?: number; radius?: number }) {
  return <span className="skeleton" style={{ width, height, borderRadius: radius }} aria-hidden="true" />;
}

export function SkeletonRows({ rows = 8 }: { rows?: number }) {
  return (
    <div className="skeleton-rows" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton-row" key={i}>
          <Skeleton width={18} height={18} radius={6} />
          <Skeleton width={`${30 + ((i * 17) % 30)}%`} />
          <Skeleton width={`${20 + ((i * 11) % 25)}%`} />
        </div>
      ))}
    </div>
  );
}
