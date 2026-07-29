import { useEffect, useState } from 'react'
import { api, pollJob } from '../api'
import Chart, { dateAxis, money, num, pct, valueAxis } from '../components/Chart'
import { Card, ErrorBox, Loading, Panel, Table } from '../components/ui'
import PoolPicker, { poolLabel, registerIndustries, ALIAS_TO_CODE } from '../components/PoolPicker'
import SchemaField, { schemaDefaults } from '../components/SchemaField'

const RED = '#d64545'
const BLUE = '#2f6fed'
const GRAY = '#98a2b3'

function strategyLabel(strategies, id) {
  return (strategies || []).find((s) => s.id === id)?.label || id
}

// 跟踪实例显示名：优先用户自定义 name，否则退回 run_id（旧记录无 name 字段）
function runLabel(s) {
  return s?.name?.trim() || s?.run_id || ''
}

export default function LiveTracking() {
  const [list, setList] = useState([])
  const [runId, setRunId] = useState('')
  const [detail, setDetail] = useState(null)
  const [defaults, setDefaults] = useState(null)   // 复用回测的 defaults：strategies/factors/industries
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [job, setJob] = useState(null)

  const reloadList = () => api.liveList().then((rs) => {
    setList(rs)
    if (!runId && rs.length) setRunId(rs[0].run_id)
    return rs
  })

  useEffect(() => {
    Promise.all([reloadList(), api.defaults()])
      .then(([, d]) => { setDefaults(d); registerIndustries(d.industries) })
      .catch(setError)
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!runId) { setDetail(null); return }
    api.liveDetail(runId).then(setDetail).catch(() => setDetail(null))
  }, [runId])

  async function submitCreate(params) {
    try {
      setError(null)
      const { job_id } = await api.liveCreate(params)
      setShowForm(false)
      const done = await pollJob(job_id, setJob)
      setJob(null)
      if (done.status === 'FAILED') { setError(new Error(done.message)); return }
      const rs = await reloadList()
      setRunId(done.result.run_id || rs[0]?.run_id)
    } catch (e) { setError(e); setJob(null) }
  }

  async function manualAdvance() {
    if (!runId) return
    try {
      setError(null)
      const { job_id } = await api.liveAdvance(runId)
      const done = await pollJob(job_id, setJob)
      setJob(null)
      if (done.status === 'FAILED') { setError(new Error(done.message)); return }
      await reloadList()
      api.liveDetail(runId).then(setDetail)
    } catch (e) { setError(e); setJob(null) }
  }

  async function doStop() {
    if (!runId || !window.confirm('停止该跟踪实例？停止后不再参与每日推进，历史记录保留。')) return
    await api.liveStop(runId)
    await reloadList()
    api.liveDetail(runId).then(setDetail)
  }

  async function doDelete() {
    if (!runId || !window.confirm(`删除跟踪实例 ${runLabel(state) || runId}？此操作不可恢复。`)) return
    await api.liveDelete(runId)
    const rs = await reloadList()
    setRunId(rs.length ? rs[0].run_id : '')
    if (!rs.length) setDetail(null)
  }

  const state = detail?.state
  const nav = detail?.nav_history
  const excessAvailable = false   // 无基准对照（纸上模拟不预设基准，净值曲线本身即结论）

  return (
    <>
      <div className="row between" style={{ marginBottom: 14 }}>
        <h2 className="page-title" style={{ margin: 0 }}>策略跟踪</h2>
        <div className="row">
          <select value={runId} onChange={(e) => setRunId(e.target.value)} style={{ minWidth: 320 }}>
            {list.length === 0 && <option value="">（暂无跟踪实例）</option>}
            {list.map((s) => (
              <option key={s.run_id} value={s.run_id}>
                {runLabel(s)}　{strategyLabel(defaults?.strategies, s.strategy)}　{poolLabel(s.pool)}
                　{s.status === 'stopped' ? '（已停止）' : ''}
              </option>
            ))}
          </select>
          <button className="ghost" disabled={!runId} onClick={manualAdvance}>立即推进一天</button>
          <button className="ghost" disabled={!runId || state?.status === 'stopped'} onClick={doStop}>停止</button>
          <button className="ghost" disabled={!runId} onClick={doDelete}>删除</button>
          <button className="primary" onClick={() => setShowForm((v) => !v)}>新建跟踪</button>
        </div>
      </div>

      <div className="muted" style={{ fontSize: 12.5, marginBottom: 14 }}>
        纸上模拟：每天自动拉最新数据、算出目标持仓，与前一日对比生成买卖信号并虚拟记账——
        不接触任何真实资金或券商账户。调度由 Python 定时任务每日触发
        （<code>python scripts/scheduler.py</code>），本页"立即推进一天"仅供测试/补跑手动触发。
      </div>

      <ErrorBox error={error} />

      {job && (
        <Panel title={`处理中：${job.message || ''}`}>
          <div className="progress"><div style={{ width: `${job.progress || 0}%` }} /></div>
        </Panel>
      )}

      {showForm && defaults && <CreateForm defaults={defaults} onSubmit={submitCreate} onCancel={() => setShowForm(false)} />}

      {loading && <Loading />}
      {!loading && !runId && !showForm && (
        <Panel><div className="loading">还没有跟踪实例，点右上角「新建跟踪」开始。</div></Panel>
      )}

      {!loading && detail && (
        <>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
            {state.name?.trim() && <><b>{state.name}</b>　·　</>}
            创建于 {state.created_at}　·　策略 <b>{strategyLabel(defaults?.strategies, state.strategy)}</b>
            　·　股票池 <b>{poolLabel(state.pool)}</b>　·　频率 {state.freq === 'M' ? '月频' : '周频'}
            　·　最后推进至 <b>{state.last_advanced_date}</b>
            　·　状态 {state.status === 'active' ? '运行中' : '已停止'}
            {state.source_label && <>　·　参数复用自回测 <b>{state.source_label}</b></>}
          </div>

          <div className="cards">
            <Card label="当前净值" value={money(nav?.nav?.length ? nav.nav[nav.nav.length - 1] : state.init_cash)} />
            <Card label="累计收益" value={pct(nav?.nav?.length ? nav.nav[nav.nav.length - 1] / state.init_cash - 1 : 0)}
                  tone={(nav?.nav?.length ? nav.nav[nav.nav.length - 1] / state.init_cash - 1 : 0) >= 0 ? 'pos' : 'neg'} />
            <Card label="当前持仓数" value={detail.positions?.length || 0} />
            <Card label="累计成交笔数" value={detail.n_trades || 0} />
            <Card label="初始资金" value={money(state.init_cash)} />
          </div>

          <Panel title="净值曲线" sub="从创建当日起逐日累计，非历史回放">
            <Chart height={280} option={{
              xAxis: dateAxis(nav?.x || []),
              yAxis: valueAxis((v) => (v / 1e4).toFixed(0) + '万'),
              legend: { show: false },
              series: [{
                name: '净值', type: 'line', data: nav?.nav || [], showSymbol: false,
                lineStyle: { width: 2, color: RED }, itemStyle: { color: RED },
              }],
            }} />
          </Panel>

          <Panel title="下一交易日待执行信号" sub="今日收盘算出、下一交易日开盘执行——结构上无未来函数">
            {detail.pending_signal ? (
              <div className="muted" style={{ fontSize: 12.5 }}>
                信号日 {detail.pending_signal.signal_date}　共 {Object.keys(detail.pending_signal.weights).length} 只标的，
                将在下一交易日开盘按目标权重调仓（含涨跌停/停牌约束、真实费率模拟）。
              </div>
            ) : <div className="loading">当前无待执行信号（非调仓日，或上次信号已执行）</div>}
          </Panel>

          <div className="grid2">
            <Panel title="当前持仓">
              <Table
                columns={[
                  { key: 'ts_code', title: '代码' },
                  { key: 'name', title: '名称' },
                  { key: 'weight', title: '权重', num: true, render: (v) => pct(v) },
                  { key: 'value', title: '市值', num: true, render: (v) => money(v) },
                ]}
                rows={detail.positions}
              />
            </Panel>

            <Panel title="交易流水" sub={`共 ${detail.n_trades || 0} 笔（近100条）`}>
              <Table
                columns={[
                  { key: 'date', title: '日期' },
                  { key: 'ts_code', title: '代码' },
                  { key: 'side', title: '方向', render: (v) => <span className={`tag ${v === 'BUY' ? 'fail' : 'pass'}`}>{v === 'BUY' ? '买入' : '卖出'}</span> },
                  { key: 'amount', title: '金额', num: true, render: (v) => money(v) },
                  { key: 'cost', title: '费用', num: true, render: (v) => num(v, 1) },
                ]}
                rows={detail.recent_trades}
              />
            </Panel>
          </div>
        </>
      )}
    </>
  )
}

function CreateForm({ defaults, onSubmit, onCancel }) {
  const strategies = defaults.strategies || []
  const [mode, setMode] = useState('manual')   // 'manual' | 'copy'（复用已有回测参数）
  const [backtestRuns, setBacktestRuns] = useState(null)
  const [sourceRunId, setSourceRunId] = useState('')
  const [copiedPool, setCopiedPool] = useState(null)   // copy 模式下直接取自回测，不经 PoolPicker

  const [f, setF] = useState({ name: '', freq: 'W', cash: 1e7 })
  const [strategyId, setStrategyId] = useState(strategies[0]?.id || '')
  const [sp, setSp] = useState(schemaDefaults(strategies[0]?.param_schema))
  const [pool, setPool] = useState({ spec: null, err: null })

  useEffect(() => {
    if (mode === 'copy' && backtestRuns === null) api.runs().then(setBacktestRuns).catch(() => setBacktestRuns([]))
  }, [mode, backtestRuns])

  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))
  const curStrategy = strategies.find((s) => s.id === strategyId)
  const factorsField = curStrategy?.param_schema?.find((x) => x.type === 'factors')
  const sourceRun = (backtestRuns || []).find((r) => r.run_id === sourceRunId)

  function changeStrategy(id) {
    setStrategyId(id)
    const spec = strategies.find((s) => s.id === id)
    setSp(schemaDefaults(spec?.param_schema))
  }
  const setSpField = (key) => (val) => setSp((s) => ({ ...s, [key]: val }))
  const toggleFactor = (key, name) =>
    setSp((s) => {
      const cur = s[key] || []
      return { ...s, [key]: cur.includes(name) ? cur.filter((x) => x !== name) : [...cur, name] }
    })
  const factorsEmpty = mode === 'manual' && factorsField && !(sp[factorsField.key] || []).length

  // 选中某个回测：直接套用其策略/参数/股票池/频率，不再重新走选择流程
  function pickSource(id) {
    setSourceRunId(id)
    const run = (backtestRuns || []).find((r) => r.run_id === id)
    if (!run) return
    const rp = run.params || {}
    setStrategyId(rp.strategy || strategies[0]?.id || '')
    setSp(rp.strategy_params || {})
    setCopiedPool(rp.pool ?? null)
    setF((s) => ({ ...s, freq: rp.freq || 'W', cash: rp.cash ?? s.cash }))
  }

  function submit() {
    if (mode === 'copy') {
      if (!sourceRun) return
      onSubmit({
        name: f.name, pool: copiedPool, freq: f.freq, cash: Number(f.cash), strategy: strategyId, strategy_params: sp,
        source_run_id: sourceRun.run_id, source_label: sourceRun.params?.name?.trim() || sourceRun.run_id,
      })
      return
    }
    onSubmit({ name: f.name, pool: pool.spec, freq: f.freq, cash: Number(f.cash), strategy: strategyId, strategy_params: sp })
  }

  return (
    <Panel title="新建跟踪">
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field">名称（可选）<input value={f.name} onChange={set('name')} placeholder="不填则用自动生成的编号" style={{ width: 160 }} /></label>
        <label style={{ fontSize: 13 }}>
          <input type="radio" checked={mode === 'manual'} onChange={() => setMode('manual')} /> 手动配置
        </label>
        <label style={{ fontSize: 13 }}>
          <input type="radio" checked={mode === 'copy'} onChange={() => setMode('copy')} /> 复用已有回测的参数
        </label>
      </div>

      {mode === 'copy' ? (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <label className="field" style={{ minWidth: 340 }}>
              选择回测
              <select value={sourceRunId} onChange={(e) => pickSource(e.target.value)}>
                <option value="">（请选择）</option>
                {(backtestRuns || []).map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.params?.name?.trim() || r.run_id}　{r.params?.start}~{r.params?.end}　{strategyLabel(defaults?.strategies, r.params?.strategy)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">初始资金<input type="number" value={f.cash} onChange={set('cash')} style={{ width: 120 }} /></label>
          </div>
          {sourceRun ? (
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
              将复用：策略 <b>{strategyLabel(defaults?.strategies, strategyId)}</b>　·　股票池 <b>{poolLabel(copiedPool)}</b>
              　·　频率 {f.freq === 'M' ? '月频' : '周频'}
            </div>
          ) : (
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
              {backtestRuns === null ? '加载中…' : backtestRuns.length ? '请选择一个回测记录' : '暂无回测记录，请先在「回测分析」页创建'}
            </div>
          )}
        </>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <label className="field">
              策略
              <select value={strategyId} onChange={(e) => changeStrategy(e.target.value)}>
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </label>
            <label className="field">调仓频率
              <select value={f.freq} onChange={set('freq')}>
                <option value="W">周频</option><option value="M">月频</option>
              </select>
            </label>
            <label className="field">初始资金<input type="number" value={f.cash} onChange={set('cash')} style={{ width: 120 }} /></label>
          </div>
          {curStrategy?.description && (
            <div className="muted" style={{ fontSize: 12.5, marginTop: -6, marginBottom: 12 }}>{curStrategy.description}</div>
          )}

          <div style={{ marginBottom: 12 }}>
            <PoolPicker industries={defaults.industries} initialIndex="000905.SH" onChange={(spec, err) => setPool({ spec, err })} />
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
              <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>{factorsField.label}</div>
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
        </>
      )}

      <div className="row">
        <button className="primary" onClick={submit} disabled={mode === 'copy' ? !sourceRun : (factorsEmpty || !!pool.err)}>开始跟踪</button>
        <button className="ghost" onClick={onCancel}>取消</button>
        <span className="muted" style={{ fontSize: 12.5 }}>创建即立即计算一次初始信号，下一交易日开盘执行</span>
      </div>
    </Panel>
  )
}
