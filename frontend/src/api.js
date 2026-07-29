const qs = (p = {}) => {
  const s = new URLSearchParams()
  Object.entries(p).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return
    Array.isArray(v) ? v.forEach((x) => s.append(k, x)) : s.append(k, v)
  })
  const str = s.toString()
  return str ? `?${str}` : ''
}

async function j(url, opts) {
  const r = await fetch(url, opts)
  const text = await r.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { error: text } }
  if (!r.ok) throw new Error(data?.error || `HTTP ${r.status}`)
  return data
}

const post = (url, body) =>
  j(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })

export const api = {
  health: () => j('/api/health'),
  overview: () => j('/api/overview'),
  quality: (limit = 200) => j(`/api/data/quality${qs({ limit })}`),
  synclog: () => j('/api/data/synclog'),
  searchStocks: (q) => j(`/api/stocks/search${qs({ q })}`),
  kline: (code, start, end) => j(`/api/stocks/${code}/kline${qs({ start, end })}`),

  industries: () => j('/api/industries'),

  factors: () => j('/api/factors'),
  factorEval: (p) => j(`/api/factors/evaluation${qs(p)}`),
  runFactorEval: (p) => post('/api/factors/evaluation', p),

  defaults: () => j('/api/backtest/defaults'),
  runs: () => j('/api/backtest/runs'),
  createRun: (p) => post('/api/backtest/runs', p),
  run: (id) => j(`/api/backtest/runs/${id}`),
  deleteRun: (id) => j(`/api/backtest/runs/${id}`, { method: 'DELETE' }),
  nav: (id, downsample) => j(`/api/backtest/runs/${id}/nav${qs({ downsample })}`),
  drawdown: (id) => j(`/api/backtest/runs/${id}/drawdown`),
  rollingExcess: (id, window = 252) => j(`/api/backtest/runs/${id}/rolling_excess${qs({ window })}`),
  turnover: (id) => j(`/api/backtest/runs/${id}/turnover`),
  runDates: (id) => j(`/api/backtest/runs/${id}/dates`),
  positions: (id, date) => j(`/api/backtest/runs/${id}/positions${qs({ date })}`),
  exposure: (id, date) => j(`/api/backtest/runs/${id}/exposure${qs({ date })}`),
  cost: (id) => j(`/api/backtest/runs/${id}/cost`),
  trades: (id, p) => j(`/api/backtest/runs/${id}/trades${qs(p)}`),

  job: (id) => j(`/api/jobs/${id}`),
  jobs: () => j('/api/jobs'),

  // 数据管道（运维操作，均异步）
  pipelineInfo: () => j('/api/pipeline/info'),
  pipelineDaily: (p) => post('/api/pipeline/daily', p),
  pipelineBackfill: (p) => post('/api/pipeline/backfill', p),
  pipelineChecks: (p) => post('/api/pipeline/checks', p),
  pipelineBuildFactors: (p) => post('/api/pipeline/build_factors', p),

  // 策略实盘跟踪（纸上模拟，均异步）
  liveList: () => j('/api/live/strategies'),
  liveCreate: (p) => post('/api/live/strategies', p),
  liveDetail: (id) => j(`/api/live/strategies/${id}`),
  liveStop: (id) => post(`/api/live/strategies/${id}/stop`),
  liveDelete: (id) => j(`/api/live/strategies/${id}`, { method: 'DELETE' }),
  liveAdvance: (id) => post(`/api/live/strategies/${id}/advance`),
}

// 轮询任务直到结束
export async function pollJob(jobId, onProgress, intervalMs = 1500) {
  for (;;) {
    const j2 = await api.job(jobId)
    onProgress?.(j2)
    if (j2.status === 'SUCCESS' || j2.status === 'FAILED') return j2
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
