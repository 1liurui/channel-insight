export type Cell = string | number | boolean | null

export interface NodeEvent {
  type: 'node'
  node: string
  elapsed_ms: number
  tables?: string[]
  sql?: string
  rewritten?: string
  guard_reason?: string
}
export interface SqlEvent { type: 'sql'; sql: string; tables: string[] }
export interface RowsEvent { type: 'rows'; columns: string[]; rows: Cell[][]; row_count: number }
export interface ErrorEvent { type: 'error'; error: string; error_type: string }
export interface CacheEvent { type: 'cache_hit'; score: number; latency_ms: number }
export interface DoneEvent {
  type: 'done'; cache_hit: boolean; latency_ms: number
  retry?: number; llm_calls?: number; rewritten?: string
}
export type AskEvent = NodeEvent | SqlEvent | RowsEvent | ErrorEvent | CacheEvent | DoneEvent

export interface Turn {
  id: number
  question: string
  rewritten?: string
  nodes: { node: string; elapsed_ms: number }[]
  sql?: string
  tables?: string[]
  columns?: string[]
  rows?: Cell[][]
  rowCount?: number
  error?: string
  errorType?: string
  cacheHit?: boolean
  cacheScore?: number
  latencyMs?: number
  llmCalls?: number
  retry?: number
  running: boolean
}

export interface WaterfallStep {
  label: string
  type: 'total' | 'change'
  value: number
  contribution?: number
}
export interface AttrNode {
  dimension: string; dim_label: string; value: string
  v0: number; v1: number; delta: number; contribution: number
  children: AttrNode[]
}
export interface DimRank {
  dimension: string; label: string; n_values: number; n_for_80: number
  top_contribution: number; concentration: number; offset_ratio: number
}
export interface AttributionResult {
  attribution: {
    metric_label: string; unit: string; t0: string; t1: string
    v0: number; v1: number; delta: number; delta_pct: number
    significant: boolean; offsetting: boolean
    dimension_ranking: DimRank[]
    tree: AttrNode[]
  }
  waterfall: { metric: string; unit: string; steps: WaterfallStep[]; delta: number; delta_pct: number }
  narrative?: string
}
