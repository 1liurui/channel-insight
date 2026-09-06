/**
 * 链路执行轨迹。业务人员等待时看到的不该是一个转圈，
 * 而是链路走到哪一步、每步花了多久——耗时超过 300ms 的节点高亮，
 * 一眼能看出瓶颈在模型调用还是在检索。
 */
const LABEL: Record<string, string> = {
  rewrite_question: '指代消解',
  extract_keywords: '关键词抽取',
  recall_column: '字段召回',
  recall_metric: '指标召回',
  recall_value: '取值召回',
  fuse_candidates: 'RRF 融合',
  filter_schema: '选表',
  build_context: '口径注入',
  generate_sql: 'SQL 生成',
  guardrail: '安全校验',
  execute_sql: '执行',
  correct_sql: '自纠错',
}
const ORDER = Object.keys(LABEL)

export function NodeTrace({ nodes, running }: {
  nodes: { node: string; elapsed_ms: number }[]; running: boolean
}) {
  const done = new Map(nodes.map((n) => [n.node, n.elapsed_ms]))
  const visible = running ? ORDER.filter((n) => done.has(n) || n !== 'correct_sql') : nodes.map((n) => n.node)
  return (
    <div className="trace">
      {visible.map((n) => {
        const ms = done.get(n)
        const cls = ms === undefined ? 'step pending'
          : n === 'correct_sql' ? 'step bad'
          : ms > 300 ? 'step slow' : 'step'
        return (
          <span key={n} className={cls}>
            {LABEL[n] ?? n}
            <em>{ms === undefined ? '…' : `${Math.round(ms)}ms`}</em>
          </span>
        )
      })}
    </div>
  )
}
