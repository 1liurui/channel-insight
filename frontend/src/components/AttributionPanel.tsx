import { useEffect, useState } from 'react'
import { getOptions, runAttribution } from '../api'
import type { AttrNode, AttributionResult } from '../types'
import { Waterfall } from './Waterfall'

function fmtVal(v: number, unit: string) {
  if (unit === '元') {
    return Math.abs(v) >= 1e4
      ? `${(v / 1e4).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} 万元`
      : `${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 元`
  }
  return `${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} ${unit}`
}

function TreeNodes({ nodes, unit }: { nodes: AttrNode[]; unit: string }) {
  return (
    <ul className={nodes.length ? '' : 'empty'}>
      {nodes.map((n, i) => (
        <li key={i}>
          <div className="node">
            <span className={`contrib ${n.contribution >= 0 ? 'pos' : 'neg'}`}>
              {(n.contribution * 100).toFixed(1)}%
            </span>
            <span className="name">{n.dim_label}·{n.value}</span>
            <span className="vals">
              {fmtVal(n.v0, unit)} → {fmtVal(n.v1, unit)}（{n.delta >= 0 ? '+' : ''}
              {fmtVal(n.delta, unit)}）
            </span>
          </div>
          {n.children.length > 0 && <TreeNodes nodes={n.children} unit={unit} />}
        </li>
      ))}
    </ul>
  )
}

export function AttributionPanel() {
  const [opts, setOpts] = useState<Awaited<ReturnType<typeof getOptions>>>()
  const [metric, setMetric] = useState('net_sales')
  const [t0, setT0] = useState('2026-02')
  const [t1, setT1] = useState('2026-06')
  const [dim, setDim] = useState('')
  const [dimVal, setDimVal] = useState('')
  const [narrate, setNarrate] = useState(true)
  const [res, setRes] = useState<AttributionResult>()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { getOptions().then(setOpts).catch(() => {}) }, [])

  async function run() {
    setBusy(true); setErr(''); setRes(undefined)
    try {
      const filters = dim && dimVal ? { [dim]: dimVal } : {}
      setRes(await runAttribution({ metric, t0, t1, filters, narrate }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const a = res?.attribution
  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <h2>指标异动归因</h2>
          <span className="sub">贡献度 = 该取值变动量 ÷ 总变动量，累计超 80% 判主因，下钻三层</span>
        </div>
        <div className="panel-body">
          <div className="form-row">
            <div className="field">
              <label>指标（仅可加指标）</label>
              <select value={metric} onChange={(e) => setMetric(e.target.value)}>
                {opts?.metrics.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label>基期</label>
              <input value={t0} onChange={(e) => setT0(e.target.value)} placeholder="2026-02" size={9} />
            </div>
            <div className="field">
              <label>对比期</label>
              <input value={t1} onChange={(e) => setT1(e.target.value)} placeholder="2026-06" size={9} />
            </div>
            <div className="field">
              <label>限定维度（可选）</label>
              <select value={dim} onChange={(e) => setDim(e.target.value)}>
                <option value="">不限定</option>
                {opts?.dimensions.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
              </select>
            </div>
            {dim && (
              <div className="field">
                <label>取值</label>
                <input value={dimVal} onChange={(e) => setDimVal(e.target.value)} placeholder="如 华东" size={10} />
              </div>
            )}
            <label className="check">
              <input type="checkbox" checked={narrate} onChange={(e) => setNarrate(e.target.checked)} />
              生成业务结论
            </label>
            <button className="btn" onClick={run} disabled={busy}>{busy ? '计算中…' : '开始归因'}</button>
          </div>
          {err && <div className="alert"><b>{err}</b></div>}
        </div>
      </div>

      {a && !a.significant && (
        <div className="panel"><div className="panel-body">
          <div className="alert warn">
            {a.metric_label} 变动 {a.delta_pct.toFixed(2)}%，未达 2% 的显著异动门槛，不做归因。
            <br />总变动趋零时贡献度的分母趋零，任何拆解结果都只是噪声放大。
          </div>
        </div></div>
      )}

      {a?.significant && res && (
        <>
          <div className="panel">
            <div className="panel-head"><h2>{a.metric_label} {a.t0} → {a.t1}</h2></div>
            <div className="panel-body">
              <div className="kpi">
                <div><div className="lbl">基期</div><div className="big">{fmtVal(a.v0, a.unit)}</div></div>
                <div><div className="lbl">对比期</div><div className="big">{fmtVal(a.v1, a.unit)}</div></div>
                <div>
                  <div className="lbl">变动</div>
                  <div className={`big ${a.delta >= 0 ? 'pos' : 'neg'}`}>
                    {a.delta >= 0 ? '+' : ''}{fmtVal(a.delta, a.unit)}（{a.delta_pct >= 0 ? '+' : ''}{a.delta_pct.toFixed(2)}%）
                  </div>
                </div>
                <div>
                  <div className="lbl">主拆解维度</div>
                  <div className="big">{a.dimension_ranking[0]?.label}</div>
                </div>
              </div>
              {a.offsetting && (
                <div className="alert warn">
                  净额仅为各取值变动绝对值之和的 {(a.dimension_ranking[0].offset_ratio * 100).toFixed(0)}%，
                  存在明显内部反向抵消，结论需谨慎。
                </div>
              )}
              <Waterfall steps={res.waterfall.steps} unit={a.unit} />
            </div>
          </div>

          <div className="grid2">
            <div className="panel">
              <div className="panel-head">
                <h2>拆解路径</h2>
                <span className="sub">每层重新选解释力最强的维度</span>
              </div>
              <div className="panel-body">
                <div className="tree"><TreeNodes nodes={a.tree} unit={a.unit} /></div>
                {res.narrative && (
                  <div className="narrative">
                    <h3>业务结论</h3>{res.narrative}
                  </div>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <h2>候选维度解释力</h2>
                <span className="sub">归一化 HHI</span>
              </div>
              <div className="panel-body">
                <div className="tablewrap">
                  <table>
                    <thead><tr><th>维度</th><th>取值</th><th>集中度</th><th>净/毛</th></tr></thead>
                    <tbody>
                      {a.dimension_ranking.map((d) => (
                        <tr key={d.dimension}>
                          <td>{d.label}</td>
                          <td className="num">{d.n_values}</td>
                          <td className="num">{d.concentration.toFixed(3)}</td>
                          <td className="num">{d.offset_ratio.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="rowcount">
                  集中度越高说明变动越集中在少数取值；净/毛越低说明取值之间互相对冲越严重。
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  )
}
