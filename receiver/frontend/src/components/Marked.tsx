/** Text with <mark> over [start, end) ranges (search hits). Ranges are UTF-16 offsets into `text`. */
export function Marked({ text, ranges }: { text: string; ranges: [number, number][] }) {
  if (!ranges.length) return <>{text}</>;
  const out = [];
  let at = 0;
  ranges.forEach(([a, b], i) => {
    if (a > at) out.push(<span key={`t${i}`}>{text.slice(at, a)}</span>);
    out.push(<mark key={`m${i}`}>{text.slice(Math.max(a, at), b)}</mark>);
    at = Math.max(at, b);
  });
  if (at < text.length) out.push(<span key="end">{text.slice(at)}</span>);
  return <>{out}</>;
}
