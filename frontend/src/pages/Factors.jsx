import { useEffect, useMemo, useState } from 'react'
import { api, pollJob } from '../api'
import Chart, { dateAxis, num, pct, valueAxis } from '../components/Chart'
import { ErrorBox, Loading, Panel, Table } from '../components/ui'
import PoolPicker, { poolLabel, registerIndustries } from '../components/PoolPicker'

const RED = '#d64545'
const BLUE = '#2f6fed'

export default function Factors() {
  const [start, setStart] = useState('20160101')
  const [end, setEnd] = useState('')
  const [freq, setFreq] = useState('W')
  const [pool, setPool] = useState({ spec: 'csi500', err: null })
  const [industries, setIndustries] = useState([])
  const [allFactors, setAllFactors] = useState([])
  const [selFactors, setSelFactors] = useState([])
  const [showFactors, setShowFactors] = useState(false)

  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [job, setJob] = useState(null)
  const [sel, setSel] = useState('')

  // pool spec 用于传输：GET 走查询串（JSON 编码），POST 走 body（对象）
  const params = () => ({
    start, end: end || undefined, freq,
    factors: selFactors.length && selFactors.length < allFactors.length ? selFactors : undefined,
  })

  const load = (spec = pool.spec) => {
    setLoading(true); setError(null)
    api.factorEval({ ...params(), pool: JSON.stringify(spec) })
      .then((d) => { setData(d); setSel(d.reports?.[0]?.name || '') })
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    api.industries().then((list) => { setIndustries(list); registerIndustries(list) }).catch(() => {})
    api.factors().then((fs) => { setAllFactors(fs); setSelFactors(fs.map((x) => x.name)) }).catch(() => {})
    load()
  }, [])

  async function compute() {
    try {
      setError(null)
      const { job_id } = await api.runFactorEval({ ...params(), pool: pool.spec })
      const done = await pollJob(job_id, setJob)
      setJob(null)
      if (done.status === 'FAILED') { setError(new Error(done.message)); return }
      load()
    } catch (e) { setError(e); setJob(null) }
  }

  const toggleFactor = (name) =>
    setSelFactors((s) => (s.includes(name) ? s.filter((x) => x !== name) : [...s, name]))

  const cur = useMemo(() => data?.reports?.find((r) => r.name === sel), [data, sel])

  return (
    <>
      <div className="row between" style={{ marginBottom: 12 }}>
        <h2 className="page-title" style={{ margin: 0 }}>因子分析</h2>
        <div className="row">
          <label className="field">起始<input value={start} size={9} onChange={(e) => setStart(e.target.value)} /></label>
          <label className="field">结束<input value={end} size={9} placeholder="今天" onChange={(e) => setEnd(e.target.value)} /></label>
          <label className="field">频率
            <select value={freq} onChange={(e) => setFreq(e.target.value)}>
              <option value="W">周频</option><option value="M">月频</option>
            </select>
          </label>
          <button className="ghost" onClick={() => setShowFactors((v) => !v)}>因子（{selFactors.length}/{allFactors.length}）</button>
          <button className="ghost" onClick={() => load()}>读取缓存</button>
          <button className="primary" onClick={compute} disabled={!!job || !!pool.err}>重新计算</button>
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <PoolPicker industries={industries} initialIndex="000905.SH" onChange={(spec, err) => setPool({ spec, err })} />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          评估域 = 选股域：这里选的池应与回测一致，否则 IC 结论与实际持仓错配。当前：<b>{poolLabel(pool.spec)}</b>
        </div>
      </div>

      {showFactors && (
        <Panel title="评估因子" sub="默认全部；取消勾选可只评估子集">
          <div className="row">
            {allFactors.map((x) => (
              <label key={x.name} style={{ fontSize: 13 }}>
                <input type="checkbox" checked={selFactors.includes(x.name)} onChange={() => toggleFactor(x.name)} />
                {' '}{x.name}<span className="muted">（{x.style}）</span>
              </label>
            ))}
          </div>
        </Panel>
      )}

      <ErrorBox error={error} />

      {job && (
        <Panel title={`因子评估中：${job.message || ''}`}>
          <div className="progress"><div style={{ width: `${job.progress || 0}%` }} /></div>
        </Panel>
      )}

      {loading && <Loading />}
      {!loading && !data && (
        <Panel><div className="loading">该参数组合尚无缓存结果，点右上角「重新计算」（约需数分钟）。</div></Panel>
      )}

      {!loading && data && (
        <>
          <Panel title="因子有效性总览" sub={`${data.n_dates} 个调仓日　·　池 ${poolLabel(data.params?.pool)}　·　RankIC>0.03 且 |t|>3 为“强”`}>
            <Table
              columns={[
                { key: 'name', title: '因子' },
                { key: 'style', title: '风格' },
                { key: 'direction', title: '方向', num: true, render: (v) => (v > 0 ? '+' : '−') },
                { key: 'rankic_mean', title: 'RankIC', num: true, render: (v) => num(v, 4) },
                { key: 'rankic_ir', title: 'ICIR', num: true, render: (v) => num(v) },
                { key: 'rankic_t', title: 't 值', num: true, render: (v) => num(v, 1) },
                { key: 'pos_ratio', title: '同向占比', num: true, render: (v) => pct(v, 0) },
                { key: 'ls_ann', title: '多空年化', num: true, render: (v) => pct(v) },
                { key: 'ls_sharpe', title: '多空Sharpe', num: true, render: (v) => num(v) },
                { key: 'ls_maxdd', title: '多空回撤', num: true, render: (v) => pct(v) },
                {
                  key: 'verdict', title: '判定',
                  render: (v) => <span className={`tag ${v === '强' ? 'strong' : v === '可用' ? 'ok' : 'weak'}`}>{v}</span>,
                },
              ]}
              rows={[...(data.reports || [])].sort((a, b) => Math.abs(b.rankic_mean) - Math.abs(a.rankic_mean))}
            />
          </Panel>

          <div className="row" style={{ marginBottom: 12 }}>
            <span className="muted">查看因子：</span>
            <select value={sel} onChange={(e) => setSel(e.target.value)}>
              {(data.reports || []).map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
            </select>
          </div>

          <Panel title={`累计 RankIC — ${sel}`} sub="持续上行才是真有效；走平即因子失效">
            <Chart height={280} option={{
              xAxis: dateAxis(data.ic?.[sel]?.x || []),
              yAxis: [valueAxis((v) => v.toFixed(1)), { type: 'value', scale: true, splitLine: { show: false }, axisLabel: { fontSize: 11 } }],
              series: [
                { name: '累计IC', type: 'line', data: data.ic?.[sel]?.cum || [], showSymbol: false, lineStyle: { width: 2, color: RED }, itemStyle: { color: RED } },
                { name: '单期IC', type: 'bar', yAxisIndex: 1, data: data.ic?.[sel]?.ic || [], itemStyle: { color: 'rgba(47,111,237,.35)' } },
              ],
            }} />
          </Panel>

          <div className="grid2">
            <Panel title={`分组收益 — ${sel}`} sub="按因子值升序分 5 组，看单调性">
              <Chart height={260} option={{
                xAxis: { type: 'category', data: (cur?.quantile_returns || []).map((_, i) => `Q${i + 1}`) },
                yAxis: valueAxis((v) => (v * 100).toFixed(2) + '%'),
                legend: { show: false },
                series: [{
                  name: '平均前向收益', type: 'bar',
                  data: cur?.quantile_returns || [],
                  itemStyle: { color: (p) => (p.value >= 0 ? RED : '#2e9e63') },
                }],
              }} />
            </Panel>

            <Panel title={`IC 衰减 — ${sel}`} sub="持有 1/2/4/8 期的 RankIC">
              <Chart height={260} option={{
                xAxis: { type: 'category', data: Object.keys(cur?.decay || {}).map((k) => `${k} 期`) },
                yAxis: valueAxis((v) => v.toFixed(3)),
                legend: { show: false },
                series: [{
                  name: 'RankIC', type: 'line', data: Object.values(cur?.decay || {}),
                  lineStyle: { width: 2, color: BLUE }, itemStyle: { color: BLUE }, symbolSize: 7,
                }],
              }} />
            </Panel>
          </div>

          <Panel title="因子相关性矩阵" sub="识别冗余：同风格高相关的因子只需保留其一">
            <CorrHeatmap corr={data.correlation} />
          </Panel>
        </>
      )}
    </>
  )
}

function CorrHeatmap({ corr }) {
  if (!corr?.names?.length) return <Loading text="无相关性数据" />
  const n = corr.names.length
  const cells = []
  corr.matrix.forEach((row, i) => row.forEach((v, jx) => cells.push([jx, i, v == null ? '-' : Number(v.toFixed(2))])))
  return (
    <Chart height={Math.max(320, n * 26 + 120)} option={{
      grid: { left: 80, right: 30, top: 30, bottom: 80 },
      tooltip: { position: 'top' },
      legend: { show: false },
      xAxis: { type: 'category', data: corr.names, splitArea: { show: true }, axisLabel: { rotate: 45, fontSize: 11 } },
      yAxis: { type: 'category', data: corr.names, splitArea: { show: true }, axisLabel: { fontSize: 11 } },
      visualMap: {
        min: -1, max: 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 6,
        inRange: { color: ['#2e9e63', '#ffffff', '#d64545'] },
      },
      series: [{
        type: 'heatmap', data: cells,
        label: { show: n <= 15, fontSize: 10 },
        emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,.3)' } },
      }],
    }} />
  )
}
