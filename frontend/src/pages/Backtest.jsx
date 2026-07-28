import { useEffect, useMemo, useState } from 'react'
import { api, pollJob } from '../api'
import Chart, { dateAxis, money, num, pct, valueAxis } from '../components/Chart'
import { Card, ErrorBox, Loading, Panel, Table } from '../components/ui'
import PoolPicker, { poolLabel, registerIndustries, ALIAS_TO_CODE } from '../components/PoolPicker'

const RED = '#d64545'
const BLUE = '#2f6fed'
const GRAY = '#98a2b3'
const GREEN = '#2e9e63'

// CSV 导出：BOM + 引号转义，Excel 直接打开中文不乱码
function toCsv(rows, cols) {
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
}
function download(name, text) {
  const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = name; a.click()
  URL.revokeObjectURL(url)
}

// 策略 id → 中文名；旧记录（无 strategy 字段）一律是当年唯一策略"多因子选股"
function strategyLabel(strategies, id) {
  if (!id) return '多因子选股'
  return (strategies || []).find((s) => s.id === id)?.label || id
}

// 策略参数摘要：新记录读 strategy_params，旧记录（拍平在 params 上）向后兼容
function strategyParamsSummary(params) {
  const sp = params?.strategy_params || params || {}
  const bits = []
  if (sp.topn != null) bits.push(`top${sp.topn}`)
  if (sp.combiner) bits.push(sp.combiner === 'ic' ? '滚动IC加权' : '等权z-score')
  if (sp.industry_neutral) bits.push('行业中性')
  if (sp.max_weight) bits.push(`权重上限${(sp.max_weight * 100).toFixed(0)}%`)
  if (sp.factors?.length) bits.push(`因子 ${sp.factors.join(' ')}`)
  return bits.join('　·　')
}

export default function Backtest() {
  const [runs, setRuns] = useState([])
  const [runId, setRunId] = useState('')
  const [meta, setMeta] = useState(null)
  const [nav, setNav] = useState(null)
  const [dd, setDd] = useState(null)
  const [roll, setRoll] = useState(null)
  const [turn, setTurn] = useState(null)
  const [cost, setCost] = useState(null)
  const [dates, setDates] = useState([])
  const [posDate, setPosDate] = useState('')
  const [positions, setPositions] = useState(null)
  const [exposure, setExposure] = useState(null)
  const [trades, setTrades] = useState(null)
  const [tradePage, setTradePage] = useState(1)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  // 新建回测
  const [showForm, setShowForm] = useState(false)
  const [defaults, setDefaults] = useState(null)
  const [job, setJob] = useState(null)

  useEffect(() => {
    api.runs()
      .then((rs) => {
        setRuns(rs)
        if (rs.length) setRunId(rs[0].run_id)
        else setLoading(false)
      })
      .catch((e) => { setError(e); setLoading(false) })
    api.defaults().then((d) => {
      setDefaults(d)
      registerIndustries(d.industries)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!runId) return
    setLoading(true); setError(null)
    Promise.all([
      api.run(runId), api.nav(runId, 1500), api.drawdown(runId),
      api.rollingExcess(runId, 252), api.turnover(runId), api.cost(runId), api.runDates(runId),
    ])
      .then(([m, n, d, r, t, c, ds]) => {
        setMeta(m); setNav(n); setDd(d); setRoll(r); setTurn(t); setCost(c); setDates(ds)
        setPosDate(ds.length ? ds[ds.length - 1] : '')
        setTradePage(1)
      })
      .catch(setError)
      .finally(() => setLoading(false))
  }, [runId])

  useEffect(() => {
    if (!runId || !posDate) return
    api.positions(runId, posDate).then(setPositions).catch(() => setPositions(null))
    api.exposure(runId, posDate).then(setExposure).catch(() => setExposure(null))
  }, [runId, posDate])

  useEffect(() => {
    if (!runId) return
    api.trades(runId, { page: tradePage, size: 50 }).then(setTrades).catch(() => setTrades(null))
  }, [runId, tradePage])

  const perf = useMemo(() => {
    const list = meta?.performance || []
    return {
      cost: list.find((p) => p.label === '含成本'),
      zero: list.find((p) => p.label === '零成本'),
      bench: list.find((p) => p.label === '中证500'),
    }
  }, [meta])

  // 超额净值（策略/基准，双双归一）——纯多头最关键的一张图
  const excess = useMemo(() => {
    if (!nav?.x || !nav.nav || !nav.bench) return null
    const n0 = nav.nav[0], b0 = nav.bench[0]
    if (!n0 || !b0) return null
    return nav.x.map((_, i) =>
      nav.nav[i] && nav.bench[i] ? (nav.nav[i] / n0) / (nav.bench[i] / b0) : null)
  }, [nav])

  async function submitRun(params) {
    try {
      setError(null)
      const { job_id } = await api.createRun(params)
      setShowForm(false)
      const done = await pollJob(job_id, setJob)
      setJob(null)
      if (done.status === 'FAILED') { setError(new Error(done.message)); return }
      const rs = await api.runs()
      setRuns(rs)
      setRunId(done.result.run_id)
    } catch (e) { setError(e); setJob(null) }
  }

  async function removeRun() {
    if (!runId || !window.confirm(`删除回测记录 ${runId}？此操作不可恢复。`)) return
    try {
      await api.deleteRun(runId)
      const rs = await api.runs()
      setRuns(rs)
      if (rs.length) setRunId(rs[0].run_id)
      else { setRunId(''); setMeta(null) }
    } catch (e) { setError(e) }
  }

  async function exportTrades() {
    const all = await api.trades(runId, { page: 1, size: 100000 })
    download(`${runId}_trades.csv`, toCsv(all.rows || [], ['date', 'ts_code', 'side', 'amount', 'cost']))
  }

  return (
    <>
      <div className="row between" style={{ marginBottom: 14 }}>
        <h2 className="page-title" style={{ margin: 0 }}>回测分析</h2>
        <div className="row">
          <select value={runId} onChange={(e) => setRunId(e.target.value)} style={{ minWidth: 300 }}>
            {runs.length === 0 && <option value="">（暂无回测记录）</option>}
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id}　{r.params?.start}~{r.params?.end}　{strategyLabel(defaults?.strategies, r.params?.strategy)}　{poolLabel(r.params?.pool)}
              </option>
            ))}
          </select>
          <button className="ghost" disabled={!runId} onClick={removeRun} title="删除当前回测记录">删除</button>
          <button className="primary" onClick={() => setShowForm((v) => !v)}>新建回测</button>
        </div>
      </div>

      <ErrorBox error={error} />

      {job && (
        <Panel title={`回测运行中：${job.message || ''}`}>
          <div className="progress"><div style={{ width: `${job.progress || 0}%` }} /></div>
          <div className="muted" style={{ marginTop: 6, fontSize: 12.5 }}>
            状态 {job.status}　进度 {job.progress || 0}%　（回测需数分钟，可离开页面稍后回来）
          </div>
        </Panel>
      )}

      {showForm && defaults && <RunForm defaults={defaults} onSubmit={submitRun} onCancel={() => setShowForm(false)} />}

      {loading && <Loading />}
      {!loading && !runId && !showForm && (
        <Panel><div className="loading">还没有回测记录，点右上角「新建回测」开始。</div></Panel>
      )}

      {!loading && meta && (
        <>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
            {meta.params?.start}~{meta.params?.end}　·　策略 <b>{strategyLabel(defaults?.strategies, meta.params?.strategy)}</b>
            　·　股票池 <b>{poolLabel(meta.params?.pool)}</b>
            　·　{strategyParamsSummary(meta.params)}
          </div>

          <div className="cards">
            <Card label="年化收益（含成本）" value={pct(perf.cost?.ann_return)} tone={perf.cost?.ann_return >= 0 ? 'pos' : 'neg'} />
            <Card label="超额年化" value={pct(perf.cost?.excess_ann)} tone={perf.cost?.excess_ann >= 0 ? 'pos' : 'neg'} />
            <Card label="信息比率 IR" value={num(perf.cost?.info_ratio)} />
            <Card label="Sharpe" value={num(perf.cost?.sharpe)} />
            <Card label="最大回撤" value={pct(perf.cost?.max_dd)} tone="neg" />
            <Card label="跟踪误差" value={pct(perf.cost?.tracking_error)} />
            <Card label="年化换手（单边）" value={`${num(perf.cost?.turnover_ann, 1)}x`} />
            {/* 成本是正数，用红涨绿跌配色会误读为“上涨”，故保持中性 */}
            <Card label="累计成本/本金" value={pct(perf.cost?.total_cost_pct)} />
          </div>

          <Panel title="超额净值曲线" sub="策略 ÷ 基准，纯多头策略的 alpha 全在这条线上">
            <Chart height={280} option={{
              xAxis: dateAxis(nav.x),
              yAxis: valueAxis((v) => v.toFixed(2)),
              series: [{
                name: '超额净值', type: 'line', data: excess, showSymbol: false,
                lineStyle: { width: 2, color: RED }, itemStyle: { color: RED },
                markLine: { silent: true, symbol: 'none', data: [{ yAxis: 1 }],
                  lineStyle: { color: GRAY, type: 'dashed' }, label: { show: false } },
              }],
            }} />
          </Panel>

          <div className="grid2">
            <Panel title="净值曲线" sub="含成本 / 零成本 / 基准">
              <Chart height={280} option={{
                xAxis: dateAxis(nav.x),
                yAxis: valueAxis((v) => (v / 1e4).toFixed(0) + '万'),
                series: [
                  { name: '含成本', type: 'line', data: nav.nav, showSymbol: false, lineStyle: { width: 2, color: RED }, itemStyle: { color: RED } },
                  { name: '零成本', type: 'line', data: nav.nav_zero, showSymbol: false, lineStyle: { width: 1.4, type: 'dashed', color: BLUE }, itemStyle: { color: BLUE } },
                  { name: '中证500', type: 'line', data: nav.bench, showSymbol: false, lineStyle: { width: 1.4, color: GRAY }, itemStyle: { color: GRAY } },
                ],
              }} />
            </Panel>

            <Panel title="回撤" sub="水下图（含成本）">
              <Chart height={280} option={{
                xAxis: dateAxis(dd?.x || []),
                yAxis: valueAxis((v) => (v * 100).toFixed(0) + '%'),
                legend: { show: false },
                series: [{
                  name: '回撤', type: 'line', data: dd?.y || [], showSymbol: false,
                  areaStyle: { color: 'rgba(214,69,69,.18)' },
                  lineStyle: { width: 1, color: RED }, itemStyle: { color: RED },
                }],
              }} />
            </Panel>
          </div>

          <div className="grid2">
            <Panel title="滚动 12 个月超额（年化）" sub="看策略是否在衰减，比全样本 IR 更有决策价值">
              <Chart height={260} option={{
                xAxis: dateAxis(roll?.x || []),
                yAxis: valueAxis((v) => (v * 100).toFixed(0) + '%'),
                legend: { show: false },
                series: [{
                  name: '滚动超额', type: 'line', data: roll?.y || [], showSymbol: false,
                  lineStyle: { width: 1.8, color: BLUE }, itemStyle: { color: BLUE },
                  markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: GRAY, type: 'dashed' }, label: { show: false } },
                }],
              }} />
            </Panel>

            <Panel title="分年度收益" sub="策略（含成本） vs 基准">
              <YearlyChart perf={perf} />
            </Panel>
          </div>

          <div className="grid2">
            <Panel title="累计交易成本" sub={`共 ${cost?.n_trades || 0} 笔，合计 ${money(cost?.total)} 元`}>
              <Chart height={250} option={{
                xAxis: dateAxis(cost?.cum?.x || []),
                yAxis: valueAxis((v) => (v / 1e4).toFixed(0) + '万'),
                legend: { show: false },
                series: [{
                  name: '累计成本', type: 'line', data: cost?.cum?.y || [], showSymbol: false,
                  areaStyle: { color: 'rgba(47,111,237,.14)' },
                  lineStyle: { width: 1.6, color: BLUE }, itemStyle: { color: BLUE },
                }],
              }} />
            </Panel>

            <Panel title="每期换手率" sub="单边，成交额 ÷ 调仓前净值">
              <Chart height={250} option={{
                xAxis: dateAxis(turn?.x || []),
                yAxis: valueAxis((v) => (v * 100).toFixed(0) + '%'),
                legend: { show: false },
                series: [{ name: '换手', type: 'bar', data: turn?.y || [], itemStyle: { color: '#7f9cf5' } }],
              }} />
            </Panel>
          </div>

          <Panel
            title="行业暴露" sub="组合 vs 基准（域内等权口径）"
            actions={
              <select value={posDate} onChange={(e) => setPosDate(e.target.value)}>
                {dates.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            }
          >
            {exposure?.industries?.length ? (
              <Chart height={330} option={{
                grid: { left: 70, right: 20, top: 34, bottom: 90 },
                xAxis: { type: 'category', data: exposure.names || exposure.industries, axisLabel: { rotate: 45, fontSize: 11 } },
                yAxis: valueAxis((v) => (v * 100).toFixed(0) + '%'),
                series: [
                  { name: '组合', type: 'bar', data: exposure.portfolio, itemStyle: { color: RED } },
                  { name: '基准', type: 'bar', data: exposure.benchmark, itemStyle: { color: GRAY } },
                ],
              }} />
            ) : <Loading text="无行业数据" />}
          </Panel>

          <div className="grid2">
            <Panel title="持仓明细" sub={positions?.date ? `${positions.date}　共 ${positions.rows?.length || 0} 只` : ''}
              actions={
                <button className="ghost" disabled={!positions?.rows?.length}
                  onClick={() => download(`${runId}_positions_${positions.date}.csv`, toCsv(positions.rows, ['ts_code', 'name', 'industry_name', 'weight']))}>
                  导出CSV
                </button>
              }
            >
              <Table
                columns={[
                  { key: 'ts_code', title: '代码' },
                  { key: 'name', title: '名称' },
                  { key: 'industry_name', title: '行业' },
                  { key: 'weight', title: '权重', num: true, render: (v) => pct(v) },
                ]}
                rows={positions?.rows}
              />
            </Panel>

            <Panel
              title="交易流水" sub={trades ? `共 ${trades.total} 笔` : ''}
              actions={
                <>
                  <button className="ghost" disabled={!trades?.total} onClick={exportTrades} title="导出全部成交流水">导出CSV</button>
                  <button className="ghost" disabled={tradePage <= 1} onClick={() => setTradePage((p) => p - 1)}>上一页</button>
                  <span className="muted">{tradePage}</span>
                  <button className="ghost"
                    disabled={!trades || tradePage * (trades.size || 50) >= trades.total}
                    onClick={() => setTradePage((p) => p + 1)}>下一页</button>
                </>
              }
            >
              <Table
                columns={[
                  { key: 'date', title: '日期' },
                  { key: 'ts_code', title: '代码' },
                  { key: 'side', title: '方向', render: (v) => <span className={`tag ${v === 'BUY' ? 'fail' : 'pass'}`}>{v === 'BUY' ? '买入' : '卖出'}</span> },
                  { key: 'amount', title: '金额', num: true, render: (v) => money(v) },
                  { key: 'cost', title: '费用', num: true, render: (v) => num(v, 1) },
                ]}
                rows={trades?.rows}
              />
            </Panel>
          </div>
        </>
      )}
    </>
  )
}

function YearlyChart({ perf }) {
  const years = Object.keys(perf.cost?.yearly || {}).sort()
  if (!years.length) return <Loading text="无分年度数据" />
  return (
    <Chart height={260} option={{
      xAxis: { type: 'category', data: years, axisLabel: { fontSize: 11 } },
      yAxis: valueAxis((v) => (v * 100).toFixed(0) + '%'),
      series: [
        { name: '策略', type: 'bar', data: years.map((y) => perf.cost?.yearly?.[y] ?? null), itemStyle: { color: RED } },
        { name: '中证500', type: 'bar', data: years.map((y) => perf.bench?.yearly?.[y] ?? null), itemStyle: { color: GRAY } },
      ],
    }} />
  )
}

// 按 param_schema 的 type 渲染单个字段（int/float/bool/select）；'factors' 类型不走这里，
// 单独在下方渲染因子勾选网格（需要 defaults.factors 的风格信息）。
function SchemaField({ field, value, onChange }) {
  if (field.type === 'int' || field.type === 'float') {
    return (
      <label className="field">
        {field.label}
        <input type="number" step={field.type === 'float' ? '0.01' : '1'}
          value={value ?? ''} placeholder={field.default == null ? '无' : String(field.default)}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
          style={{ width: 90 }} />
      </label>
    )
  }
  if (field.type === 'bool') {
    return (
      <label className="field" style={{ justifyContent: 'flex-end' }}>
        <span style={{ visibility: 'hidden' }}>.</span>
        <span><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {field.label}</span>
      </label>
    )
  }
  if (field.type === 'select') {
    return (
      <label className="field">
        {field.label}
        <select value={value ?? field.default} onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
    )
  }
  return null
}

function schemaDefaults(schema) {
  const out = {}
  ;(schema || []).forEach((f) => { out[f.key] = f.default })
  return out
}

function RunForm({ defaults, onSubmit, onCancel }) {
  const p0 = defaults.params
  const initIdx = typeof p0.pool === 'string' ? (ALIAS_TO_CODE[p0.pool] || (p0.pool === 'all' ? 'all' : '000905.SH')) : '000905.SH'
  const strategies = defaults.strategies || []

  const [f, setF] = useState({ start: p0.start, end: p0.end, freq: p0.freq || 'W', cash: p0.cash ?? 1e7 })
  const [strategyId, setStrategyId] = useState(p0.strategy || strategies[0]?.id || '')
  const [sp, setSp] = useState(p0.strategy_params || schemaDefaults(strategies[0]?.param_schema))
  const [pool, setPool] = useState({ spec: null, err: null })

  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))
  const curStrategy = strategies.find((s) => s.id === strategyId)
  const factorsField = curStrategy?.param_schema.find((x) => x.type === 'factors')

  // 切策略即重置为该策略默认参数（若切回初始策略，沿用页面加载时的默认值）
  function changeStrategy(id) {
    setStrategyId(id)
    const spec = strategies.find((s) => s.id === id)
    setSp(id === p0.strategy ? p0.strategy_params : schemaDefaults(spec?.param_schema))
  }
  const setSpField = (key) => (val) => setSp((s) => ({ ...s, [key]: val }))
  const toggleFactor = (key, name) =>
    setSp((s) => {
      const cur = s[key] || []
      return { ...s, [key]: cur.includes(name) ? cur.filter((x) => x !== name) : [...cur, name] }
    })

  const factorsEmpty = factorsField && !(sp[factorsField.key] || []).length

  function submit() {
    onSubmit({
      start: f.start, end: f.end, freq: f.freq, cash: Number(f.cash), pool: pool.spec,
      strategy: strategyId, strategy_params: sp,
    })
  }

  return (
    <Panel title="新建回测">
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field">起始日<input value={f.start} onChange={set('start')} size={10} /></label>
        <label className="field">结束日<input value={f.end} onChange={set('end')} size={10} /></label>
        <label className="field">频率
          <select value={f.freq} onChange={set('freq')}>
            <option value="W">周频</option><option value="M">月频</option>
          </select>
        </label>
        <label className="field">初始资金<input type="number" value={f.cash} onChange={set('cash')} style={{ width: 120 }} /></label>
        <label className="field">
          策略
          <select value={strategyId} onChange={(e) => changeStrategy(e.target.value)}>
            {strategies.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
      </div>
      {curStrategy?.description && (
        <div className="muted" style={{ fontSize: 12.5, marginTop: -6, marginBottom: 12 }}>{curStrategy.description}</div>
      )}

      <div style={{ marginBottom: 12 }}>
        <PoolPicker industries={defaults.industries} initialIndex={initIdx} onChange={(spec, err) => setPool({ spec, err })} />
      </div>

      {curStrategy && (
        <div className="row" style={{ marginBottom: 12 }}>
          {curStrategy.param_schema.filter((x) => x.type !== 'factors').map((field) => (
            <SchemaField key={field.key} field={field} value={sp[field.key]} onChange={setSpField(field.key)} />
          ))}
        </div>
      )}

      {factorsField && (
        <div style={{ marginBottom: 12 }}>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>{factorsField.label}（默认为评估中“强/可用”者）</div>
          <div className="row">
            {defaults.factors.map((x) => (
              <label key={x.name} style={{ fontSize: 13 }}>
                <input type="checkbox" checked={(sp[factorsField.key] || []).includes(x.name)}
                  onChange={() => toggleFactor(factorsField.key, x.name)} />
                {' '}{x.name}<span className="muted">（{x.style}）</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="row">
        <button className="primary" onClick={submit} disabled={factorsEmpty || !!pool.err}>开始回测</button>
        <button className="ghost" onClick={onCancel}>取消</button>
        <span className="muted" style={{ fontSize: 12.5 }}>全区间回测约需数分钟</span>
      </div>
    </Panel>
  )
}
