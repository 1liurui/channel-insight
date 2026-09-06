import { useRef, useState } from 'react'
import { askStream } from '../api'
import type { Turn } from '../types'
import { NodeTrace } from './NodeTrace'
import { ResultTable } from './ResultTable'

const SAMPLES = [
  '华东区上个月的净销额是多少',
  '各渠道的费效比排名',
  '临期库存占比最高的十个经销商',
  '上个月碳酸饮料的动销率',
  '各大区业务代表的拜访达成率',
]

export function AskPanel() {
  const [q, setQ] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)
  // 多轮上下文存改写后的完整问句而非原句：原句可能不含时间与筛选条件，
  // 几轮之后上下文会逐轮衰减，结果看不出错但量级已经偏了。
  const history = useRef<{ role: string; content: string }[]>([])

  async function submit(question: string) {
    if (!question.trim() || busy) return
    setQ('')
    setBusy(true)
    const id = ++seq.current
    setTurns((t) => [{ id, question, nodes: [], running: true }, ...t])
    const patch = (f: (t: Turn) => Turn) =>
      setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)))

    try {
      await askStream({ question, history: history.current.slice(-6) }, (e) => {
        switch (e.type) {
          case 'node':
            patch((t) => ({
              ...t,
              nodes: [...t.nodes, { node: e.node, elapsed_ms: e.elapsed_ms }],
              rewritten: e.rewritten ?? t.rewritten,
              tables: e.tables ?? t.tables,
              sql: e.sql ?? t.sql,
            }))
            break
          case 'cache_hit':
            patch((t) => ({ ...t, cacheHit: true, cacheScore: e.score }))
            break
          case 'sql':
            patch((t) => ({ ...t, sql: e.sql, tables: e.tables }))
            break
          case 'rows':
            patch((t) => ({ ...t, columns: e.columns, rows: e.rows, rowCount: e.row_count }))
            break
          case 'error':
            patch((t) => ({ ...t, error: e.error, errorType: e.error_type }))
            break
          case 'done':
            patch((t) => ({
              ...t, running: false, latencyMs: e.latency_ms, cacheHit: e.cache_hit,
              llmCalls: e.llm_calls, retry: e.retry,
              rewritten: e.rewritten && e.rewritten !== question ? e.rewritten : t.rewritten,
            }))
            break
        }
      })
    } catch (err) {
      patch((t) => ({ ...t, running: false, error: String(err), errorType: '连接失败' }))
    } finally {
      setBusy(false)
      setTurns((ts) => {
        const t = ts.find((x) => x.id === id)
        if (t) {
          history.current.push({ role: '用户', content: t.rewritten || t.question })
          history.current.push({ role: '助手', content: `查询结果共 ${t.rowCount ?? 0} 行` })
        }
        return ts
      })
    }
  }

  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <h2>自助取数</h2>
          <span className="sub">自然语言 → SQL，支持多轮追问</span>
        </div>
        <div className="panel-body">
          <form className="askbar" onSubmit={(e) => { e.preventDefault(); submit(q) }}>
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="例如：华东区上个月各渠道的净销额" disabled={busy} />
            <button className="btn" type="submit" disabled={busy || !q.trim()}>
              {busy ? '查询中…' : '查询'}
            </button>
          </form>
          <div className="samples">
            {SAMPLES.map((s) => (
              <button key={s} className="chip" onClick={() => submit(s)} disabled={busy}>{s}</button>
            ))}
          </div>
        </div>
      </div>

      <div className="panel">
        {turns.length === 0 && <div className="empty">还没有查询。点上面的示例问题试一条。</div>}
        {turns.map((t) => (
          <div className="turn" key={t.id}>
            <div className="turn-q">
              {t.question}
              <span className="meta">
                {t.cacheHit && `缓存命中${t.cacheScore ? ` ${t.cacheScore.toFixed(3)}` : ''} · `}
                {t.retry ? `纠错 ${t.retry} 轮 · ` : ''}
                {t.llmCalls ? `${t.llmCalls} 次模型调用 · ` : ''}
                {t.latencyMs !== undefined ? `${Math.round(t.latencyMs)} ms` : '执行中…'}
              </span>
            </div>
            {t.rewritten && (
              <p className="rewritten">指代消解后：<b>{t.rewritten}</b></p>
            )}
            {!t.cacheHit && <NodeTrace nodes={t.nodes} running={t.running} />}
            {t.sql && <pre className="sql">{t.sql}</pre>}
            {t.error && (
              <div className="alert"><b>{t.errorType}</b>：{t.error}</div>
            )}
            {t.columns && t.rows && (
              <ResultTable columns={t.columns} rows={t.rows} rowCount={t.rowCount ?? 0} />
            )}
          </div>
        ))}
      </div>
    </>
  )
}
