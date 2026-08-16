import { useMemo, useState } from "react";
import type { NavPoint } from "./types";

const RANGES = [
  ["1月", 31], ["3月", 93], ["6月", 186], ["1年", 366], ["3年", 1096], ["全部", null],
] as const;

export function splitNavSeries(points: NavPoint[]): NavPoint[][] {
  const valid = points
    .filter((point) => Number.isFinite(point.unit_nav) && !Number.isNaN(new Date(point.date).getTime()))
    .sort((a, b) => a.date.localeCompare(b.date));
  const series: NavPoint[][] = [];
  for (const point of valid) {
    const current = series[series.length - 1];
    if (!current) {
      series.push([point]);
      continue;
    }
    const previous = current[current.length - 1];
    const gap = (new Date(point.date).getTime() - new Date(previous.date).getTime()) / 86_400_000;
    if (gap > 7) series.push([point]);
    else current.push(point);
  }
  return series;
}

function filterRange(points: NavPoint[], days: number | null) {
  if (!days || points.length === 0) return points;
  const latest = Math.max(...points.map((point) => new Date(point.date).getTime()));
  return points.filter((point) => new Date(point.date).getTime() >= latest - days * 86_400_000);
}

export function NavChart({ points }: { points: NavPoint[] }) {
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("1年");
  const shown = useMemo(() => filterRange(points, RANGES.find(([label]) => label === range)?.[1] ?? null), [points, range]);
  const series = useMemo(() => splitNavSeries(shown), [shown]);

  if (!shown.length) {
    return <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">历史净值曲线暂不可用</div>;
  }

  const width = 760, height = 240, pad = 28;
  const values = shown.map((point) => point.unit_nav);
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || Math.max(max * 0.02, 0.01);
  const dates = shown.map((point) => new Date(point.date).getTime());
  const first = Math.min(...dates), last = Math.max(...dates), dateSpan = last - first || 1;
  const xy = (point: NavPoint) => ({
    x: pad + ((new Date(point.date).getTime() - first) / dateSpan) * (width - pad * 2),
    y: pad + ((max - point.unit_nav) / span) * (height - pad * 2),
  });

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5" aria-label="净值曲线时间范围">
        {RANGES.map(([label]) => (
          <button key={label} onClick={() => setRange(label)} aria-pressed={range === label}
            className={`rounded-full border px-3 py-1 text-xs ${range === label ? "border-primary bg-primary/15 text-primary" : "border-border text-muted-foreground hover:text-foreground"}`}>
            {label}
          </button>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${range}官方单位净值曲线`} className="w-full rounded-xl border border-border/60 bg-black/10">
        <defs>
          <linearGradient id="nav-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="hsl(var(--primary))" stopOpacity="0.25" />
            <stop offset="1" stopColor="hsl(var(--primary))" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((ratio) => <line key={ratio} x1={pad} x2={width - pad} y1={height * ratio} y2={height * ratio} stroke="hsl(var(--border))" strokeDasharray="4 6" />)}
        {series.map((line, index) => {
          const path = line.map((point, pointIndex) => {
            const pos = xy(point);
            return `${pointIndex ? "L" : "M"}${pos.x.toFixed(2)},${pos.y.toFixed(2)}`;
          }).join(" ");
          return line.length > 1
            ? <path key={index} d={path} fill="none" stroke="hsl(var(--primary))" strokeWidth="2.5" strokeLinejoin="round" />
            : <circle key={index} cx={xy(line[0]).x} cy={xy(line[0]).y} r="3" fill="hsl(var(--primary))" />;
        })}
        <text x={pad} y={18} fill="hsl(var(--muted-foreground))" fontSize="11">{max.toFixed(4)}</text>
        <text x={pad} y={height - 8} fill="hsl(var(--muted-foreground))" fontSize="11">{min.toFixed(4)}</text>
      </svg>
      <p className="mt-2 text-[11px] text-muted-foreground">仅连接相隔不超过 7 天的净值点；较长数据缺口会断开显示。</p>
    </div>
  );
}
