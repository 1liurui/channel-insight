import type { WaterfallStep } from '../types'

/**
 * 贡献度瀑布图。首柱期初、末柱期末，中间每根柱是一个主因的增减，
 * 未进入主因集合的合并为「其他」，因此首尾必然对得上——
 * 这是瀑布图相较并列柱状图的唯一价值，对不上就没有意义。
 */
export function Waterfall({ steps, unit }: { steps: WaterfallStep[]; unit: string }) {
  const W = 660, H = 268, PAD_L = 10, PAD_R = 10, PAD_T = 28, PAD_B = 58
  const n = steps.length
  const gap = 14
  const bw = (W - PAD_L - PAD_R - gap * (n - 1)) / n

  let running = 0
  const bars = steps.map((s) => {
    if (s.type === 'total') {
      running = s.value
      return { s, lo: 0, hi: s.value, connectAt: s.value }
    }
    const lo = Math.min(running, running + s.value)
    const hi = Math.max(running, running + s.value)
    running += s.value
    return { s, lo, hi, connectAt: running }
  })

  const maxV = Math.max(...bars.map((b) => b.hi), 1)
  const y = (v: number) => PAD_T + (H - PAD_T - PAD_B) * (1 - v / maxV)
  const fmtV = (v: number) =>
    unit === '元'
      ? `${(v / 1e4).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}万`
      : v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="贡献度瀑布图">
      <line x1={PAD_L} y1={y(0)} x2={W - PAD_R} y2={y(0)} stroke="var(--border-strong)" />
      {bars.map((b, i) => {
        const x = PAD_L + i * (bw + gap)
        const top = y(b.hi)
        const h = Math.max(1.5, y(b.lo) - top)
        const isTotal = b.s.type === 'total'
        const fill = isTotal ? 'var(--accent)' : b.s.value >= 0 ? 'var(--pos)' : 'var(--neg)'
        const label = b.s.label.length > 9 ? `${b.s.label.slice(0, 9)}…` : b.s.label
        return (
          <g key={i}>
            {i > 0 && (
              <line x1={x - gap} y1={y(bars[i - 1].connectAt)} x2={x} y2={y(bars[i - 1].connectAt)}
                    stroke="var(--border-strong)" strokeDasharray="3 3" />
            )}
            <rect x={x} y={top} width={bw} height={h} rx={2} fill={fill}
                  opacity={isTotal ? 1 : 0.88} />
            <text x={x + bw / 2} y={top - 7} textAnchor="middle" fontSize="10"
                  fontFamily="var(--mono)" fill="var(--muted)">
              {isTotal ? fmtV(b.s.value) : `${b.s.value >= 0 ? '+' : ''}${fmtV(b.s.value)}`}
            </text>
            <title>{`${b.s.label}：${fmtV(b.s.value)}`}</title>
            <text x={x + bw / 2} y={H - PAD_B + 18} textAnchor="middle" fontSize="10"
                  fill="var(--text)">{label}</text>
            {b.s.contribution !== undefined && (
              <text x={x + bw / 2} y={H - PAD_B + 33} textAnchor="middle" fontSize="9.5"
                    fontFamily="var(--mono)" fill="var(--faint)">
                {`${(b.s.contribution * 100).toFixed(0)}%`}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
