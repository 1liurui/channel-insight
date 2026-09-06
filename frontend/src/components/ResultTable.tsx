import type { Cell } from '../types'

const isNum = (v: Cell) => typeof v === 'number'

function fmt(v: Cell): string {
  if (v === null) return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toLocaleString('zh-CN')
    return Math.abs(v) < 1 ? v.toFixed(4) : v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  return v
}

export function ResultTable({ columns, rows, rowCount }: {
  columns: string[]; rows: Cell[][]; rowCount: number
}) {
  if (!columns.length) return null
  const shown = rows.slice(0, 50)
  return (
    <>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>{columns.map((c, i) => <th key={i}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>
                {r.map((v, j) => (
                  <td key={j} className={isNum(v) ? 'num' : undefined}>{fmt(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="rowcount">
        共 {rowCount.toLocaleString('zh-CN')} 行
        {rowCount > shown.length && `，显示前 ${shown.length} 行`}
      </div>
    </>
  )
}
