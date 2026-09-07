import type { AskEvent, AttributionResult } from './types'

/**
 * 问数走 SSE。EventSource 只支持 GET，而问题体可能较长且含多轮上下文，
 * 因此用 fetch + ReadableStream 手工解析 SSE 帧。
 */
export async function askStream(
  body: { question: string; history?: { role: string; content: string }[]; session_id?: string },
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`服务返回 ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // SSE 允许 \r\n / \n / \r 三种换行，sse-starlette 发的是 \r\n，
    // 只按 '\n\n' 切会永远切不出帧。这里按整段缓冲区正则分帧，
    // 半个换行符（如结尾停在 '\r'）会留在 buf 里等下一片，不会误判。
    const frames = buf.split(/\r\n\r\n|\n\n|\r\r/)
    buf = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split(/\r\n|\n|\r/).find((l) => l.startsWith('data:'))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as AskEvent)
      } catch {
        /* 忽略半帧 */
      }
    }
  }
}

export async function runAttribution(body: {
  metric: string; t0: string; t1: string
  filters?: Record<string, string>; narrate?: boolean
}): Promise<AttributionResult> {
  const res = await fetch('/api/attribute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.json()).detail ?? `服务返回 ${res.status}`)
  return res.json()
}

export async function getOptions(): Promise<{
  metrics: { id: string; label: string; unit: string }[]
  dimensions: { id: string; label: string }[]
}> {
  return (await fetch('/api/attribute/options')).json()
}

export async function getMetrics(): Promise<
  { id: string; name: string; aliases: string[]; unit: string; description: string; expr: string; caveat: string }[]
> {
  return (await fetch('/api/metrics')).json()
}
