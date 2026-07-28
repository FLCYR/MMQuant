import { useEffect, useState } from 'react'
import { api, pollJob } from '../api'
import Chart, { money, valueAxis } from '../components/Chart'
import { Card, ErrorBox, Loading, Panel, Table } from '../components/ui'

export default function DataOverview() {
  const [ov, setOv] = useState(null)
  const [quality, setQuality] = useState([])
  const [sync, setSync] = useState(null)
  const [info, setInfo] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const reload = () => Promise.all([api.overview(), api.quality(200), api.synclog(), api.pipelineInfo()])
    .then(([o, q, s, i]) => { setOv(o); setQuality(q); setSync(s); setInfo(i) })
    .catch(setError)

  useEffect(() => { reload().finally(() => setLoading(false)) }, [])

  if (loading) return <Loading />
  if (error && !ov) return <ErrorBox error={error} />

  const quote = ov?.facts?.find((f) => f.name === 'daily_quote')
  const failed = quality.filter((q) => !q.passed)

  return (
    <>
      <h2 className="page-title">数据概览</h2>

      <ManagementConsole info={info} onDone={reload} setError={setError} />
      <ErrorBox error={error} />

      <div className="cards">
        <Card label="日线行情" value={money(quote?.rows)} />
        <Card label="覆盖股票" value={money(quote?.entities)} />
        <Card label="覆盖交易日" value={money(quote?.dates)} />
        <Card label="时间跨度" value={`${quote?.start || '-'} ~ ${quote?.end || '-'}`} />
        <Card label="在市 / 退市" value={`${ov?.stock_status?.L ?? '-'} / ${ov?.stock_status?.D ?? '-'}`} />
        <Card label="校验未通过" value={failed.length} tone={failed.length ? 'neg' : undefined} />
      </div>

      <div className="grid2">
        <Panel title="事实表">
          <Table
            columns={[
              { key: 'name', title: '表' },
              { key: 'rows', title: '行数', num: true, render: (v) => money(v) },
              { key: 'entities', title: '标的数', num: true, render: (v) => money(v) },
              { key: 'dates', title: '日期数', num: true, render: (v) => money(v) },
              { key: 'start', title: '起' },
              { key: 'end', title: '止' },
            ]}
            rows={ov?.facts}
          />
        </Panel>
        <Panel title="维度表">
          <Table
            columns={[
              { key: 'name', title: '表' },
              { key: 'rows', title: '行数', num: true, render: (v) => money(v) },
            ]}
            rows={ov?.dims}
          />
        </Panel>
      </div>

      <Panel title="逐年覆盖" sub="不同股票数 / 日均行数——一眼看出数据缺口">
        <Chart height={280} option={{
          xAxis: { type: 'category', data: (ov?.yearly || []).map((y) => y.year) },
          yAxis: valueAxis((v) => v),
          series: [
            { name: '股票数', type: 'bar', data: (ov?.yearly || []).map((y) => y.stocks), itemStyle: { color: '#2f6fed' } },
            { name: '日均行数', type: 'line', data: (ov?.yearly || []).map((y) => y.avg_per_day), lineStyle: { color: '#d64545' }, itemStyle: { color: '#d64545' } },
          ],
        }} />
      </Panel>

      <Panel title="数据校验结果" sub={`未通过 ${failed.length} 条（FATAL/ERROR 需关注）`}>
        <Table
          columns={[
            { key: 'check_id', title: '规则' },
            { key: 'table_name', title: '表' },
            { key: 'biz_date', title: '业务日' },
            { key: 'level', title: '级别', render: (v) => <span className={`tag ${v === 'WARN' ? 'weak' : 'fail'}`}>{v}</span> },
            { key: 'passed', title: '结果', render: (v) => <span className={`tag ${v ? 'pass' : 'fail'}`}>{v ? '通过' : '未通过'}</span> },
            { key: 'fail_count', title: '失败数', num: true },
            { key: 'sample', title: '样本', render: (v) => <span className="muted">{String(v || '').slice(0, 90)}</span> },
          ]}
          rows={quality}
        />
      </Panel>

      <div className="grid2">
        <Panel title="同步任务汇总">
          <Table
            columns={[
              { key: 'task_name', title: '任务' },
              { key: 'status', title: '状态', render: (v) => <span className={`tag ${v === 'SUCCESS' ? 'pass' : 'fail'}`}>{v}</span> },
              { key: 'n', title: '条数', num: true, render: (v) => money(v) },
            ]}
            rows={sync?.summary}
          />
        </Panel>
        <Panel title="同步失败明细" sub="可用「日度增量 / 回补」续补">
          <Table
            columns={[
              { key: 'task_name', title: '任务' },
              { key: 'biz_date', title: '业务日' },
              { key: 'status', title: '状态' },
              { key: 'error_msg', title: '错误', render: (v) => <span className="muted">{String(v || '').slice(0, 60)}</span> },
            ]}
            rows={sync?.failures}
            empty="无失败记录 ✓"
          />
        </Panel>
      </div>
    </>
  )
}

// ------------------------------------------------------------------ 数据管理控制台
function ManagementConsole({ info, onDone, setError }) {
  const [job, setJob] = useState(null)
  const [result, setResult] = useState(null)
  const [showBf, setShowBf] = useState(false)
  const [buildStart, setBuildStart] = useState('')
  const [bf, setBf] = useState({ phases: [], start: '20150101', end: '', resume: true })

  const running = !!job

  async function run(kind, params = {}, label = '') {
    if (running) return
    const fn = {
      daily: api.pipelineDaily, build_factors: api.pipelineBuildFactors,
      checks: api.pipelineChecks, backfill: api.pipelineBackfill,
    }[kind]
    try {
      setResult(null); setError(null)
      const { job_id } = await fn(params)
      const done = await pollJob(job_id, setJob)
      setJob(null)
      if (done.status === 'FAILED') { setError(new Error(done.message)); return }
      setResult({ label: label || kind, ...(done.result || {}) })
      onDone?.()
    } catch (e) { setError(e); setJob(null) }
  }

  const togglePhase = (p) =>
    setBf((s) => ({ ...s, phases: s.phases.includes(p) ? s.phases.filter((x) => x !== p) : [...s.phases, p] }))

  return (
    <Panel title="数据管理" sub="运维操作均为异步任务，可离开页面稍后回来">
      {info?.last_quote_date && (
        <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
          行情最新至 <b>{info.last_quote_date}</b>
          　·　因子面板 {info.factor_panel_dates} 个调仓日
          {info.factor_panel_span && `（${info.factor_panel_span[0]}~${info.factor_panel_span[1]}）`}
        </div>
      )}

      <div className="row" style={{ marginBottom: running || result ? 12 : 0 }}>
        <button className="primary" disabled={running} onClick={() => run('daily', {}, '日度增量')}>日度增量</button>
        <button disabled={running} onClick={() => run('build_factors', buildStart ? { start: buildStart } : {}, '重建因子面板')}>
          重建因子面板
        </button>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <span className="muted" style={{ fontSize: 12 }}>起始</span>
          <input value={buildStart} onChange={(e) => setBuildStart(e.target.value)}
            placeholder={info?.factor_panel_span?.[1] || '全量'} size={9} title="因子面板起始日；留空=全量（数分钟）" />
        </label>
        <button disabled={running} onClick={() => run('checks', {}, '离线校验')}>离线校验</button>
        <button disabled={running} onClick={() => setShowBf((v) => !v)}>回补…</button>
      </div>

      {showBf && (
        <div style={{ padding: '10px 12px', background: '#fafbfc', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 12 }}>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 8 }}>
            分阶段回补（勾选阶段）。<b>全量回补耗时数小时且会拉取大量 Tushare 数据</b>，一般只在初始化或补历史时用；日常增量用「日度增量」。
          </div>
          <div className="row" style={{ marginBottom: 8, gap: '4px 14px' }}>
            {(info?.phases || []).map((p) => (
              <label key={p.id} style={{ fontSize: 12.5 }}>
                <input type="checkbox" checked={bf.phases.includes(p.id)} onChange={() => togglePhase(p.id)} />
                {' '}{p.id.toUpperCase()} {p.label}
              </label>
            ))}
          </div>
          <div className="row">
            <label className="field">起始日<input value={bf.start} onChange={(e) => setBf((s) => ({ ...s, start: e.target.value }))} size={9} /></label>
            <label className="field">结束日<input value={bf.end} onChange={(e) => setBf((s) => ({ ...s, end: e.target.value }))} placeholder="今天" size={9} /></label>
            <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={bf.resume} onChange={(e) => setBf((s) => ({ ...s, resume: e.target.checked }))} />
              <span>断点续补（跳过已成功）</span>
            </label>
            <button className="primary" disabled={running || !bf.phases.length}
              onClick={() => run('backfill', { phases: bf.phases, start: bf.start, end: bf.end || undefined, resume: bf.resume }, '回补')}>
              开始回补
            </button>
          </div>
        </div>
      )}

      {running && (
        <div>
          <div className="progress"><div style={{ width: `${job.progress || 0}%` }} /></div>
          <div className="muted" style={{ marginTop: 6, fontSize: 12.5 }}>
            {job.message || '运行中'}　·　{job.progress || 0}%　·　{job.status}
          </div>
        </div>
      )}

      {result && !running && (
        <div className="tag pass" style={{ display: 'inline-block', marginTop: 4, padding: '4px 12px' }}>
          ✓ {result.label} 完成{result.message ? `：${result.message}` : ''}
        </div>
      )}
    </Panel>
  )
}
