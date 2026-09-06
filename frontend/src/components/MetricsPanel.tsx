import { useEffect, useState } from 'react'
import { getMetrics } from '../api'

type Metric = Awaited<ReturnType<typeof getMetrics>>[number]

export function MetricsPanel() {
  const [metrics, setMetrics] = useState<Metric[]>([])
  useEffect(() => { getMetrics().then(setMetrics).catch(() => {}) }, [])
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>指标口径</h2>
        <span className="sub">
          semantic layer · {metrics.length} 项 · 检索与 SQL 生成共用同一份定义
        </span>
      </div>
      <div className="panel-body">
        {metrics.map((m) => (
          <div className="metric-card" key={m.id}>
            <h3>{m.name} <span className="alias">{m.unit}</span></h3>
            <div className="alias">别名：{m.aliases.join('、')}</div>
            <div style={{ marginTop: 4 }}>{m.description}</div>
            <pre>{m.expr}</pre>
            {m.caveat && <div className="caveat">{m.caveat}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
