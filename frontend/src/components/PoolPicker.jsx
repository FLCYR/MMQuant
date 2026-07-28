import { useEffect, useMemo, useState } from 'react'

// ---------------- 股票池 spec 工具（回测/因子页共用） ----------------
export const BASE_OPTS = [
  { v: 'all', label: '全市场' },
  { v: '000300.SH', label: '沪深300' },
  { v: '000905.SH', label: '中证500' },
  { v: '000852.SH', label: '中证1000' },
  { v: '000016.SH', label: '上证50' },
  { v: 'board:主板', label: '主板' },
  { v: 'board:创业板', label: '创业板' },
  { v: 'board:科创板', label: '科创板' },
  { v: 'board:北交所', label: '北交所' },
]
const INDEX_NAME = { '000300.SH': '沪深300', '000905.SH': '中证500', '000852.SH': '中证1000', '000016.SH': '上证50' }
export const ALIAS_TO_CODE = { csi500: '000905.SH', hs300: '000300.SH', csi1000: '000852.SH', sz50: '000016.SH' }
const INDUSTRY_NAME = {}          // SW一级 code→name，页面加载后经 registerIndustries 填充

export function registerIndustries(list) {
  (list || []).forEach((i) => { INDUSTRY_NAME[i.code] = i.name })
}

// 由 base 值（'all'/指数码/'board:名'）解析基础成员池
function baseSpec(indexVal) {
  if (indexVal === 'all') return null
  if (indexVal.startsWith('board:')) return { board: indexVal.slice(6) }
  return { index: indexVal }
}

// 由内部字段拼出 pool spec：base → 行业 → 市值/流动性 依次串联（json 模式直接解析文本）
export function buildPoolSpec(f) {
  if (f.mode === 'json') return JSON.parse(f.json)
  const stages = []
  const b = baseSpec(f.index)
  if (b) stages.push(b)
  if (f.indOn && f.industries?.length) stages.push({ industry: f.industries })
  const k = Number(f.sizeK)
  if (f.size === 'size_bottom') stages.push({ size: { bottom: k } })
  else if (f.size === 'size_top') stages.push({ size: { top: k } })
  else if (f.size === 'size_q') stages.push({ size: { q: [Number(f.qLo), Number(f.qHi)] } })
  else if (f.size === 'liq_top') stages.push({ liquidity: { top: k } })
  else if (f.size === 'liq_bottom') stages.push({ liquidity: { bottom: k } })
  if (stages.length === 0) return 'all'
  if (stages.length === 1) return stages[0]
  return { then: stages }
}

function sizeLabel(v) {
  if (v?.bottom) return `市值最小${v.bottom}`
  if (v?.top) return `市值最大${v.top}`
  if (v?.q) return `市值分位${v.q[0]}~${v.q[1]}`
  return '市值筛选'
}
function liqLabel(v) {
  const t = v?.top ? `流动性最高${v.top}` : v?.bottom ? `流动性最低${v.bottom}`
    : v?.q ? `流动性分位${v.q[0]}~${v.q[1]}` : '流动性筛选'
  return v?.by === 'turnover' ? `${t}(换手)` : t
}
const asArr = (v) => (Array.isArray(v) ? v : String(v).split(',').filter(Boolean))

// pool spec → 中文简label（供运行列表 / 参数摘要展示）
export function poolLabel(pool) {
  if (pool == null) return '中证500'
  if (typeof pool === 'string') {
    if (pool === 'all') return '全市场'
    if (ALIAS_TO_CODE[pool]) return INDEX_NAME[ALIAS_TO_CODE[pool]]
    if (pool.startsWith('index:')) return INDEX_NAME[pool.slice(6)] || pool.slice(6)
    if (pool.startsWith('board:')) return asArr(pool.slice(6)).join('/')
    return pool
  }
  const [k, v] = Object.entries(pool)[0] || []
  if (k === 'index') return INDEX_NAME[v] || v
  if (k === 'all') return '全市场'
  if (k === 'size') return sizeLabel(v)
  if (k === 'liquidity') return liqLabel(v)
  if (k === 'board') return asArr(v).join('/')
  if (k === 'industry') {
    const arr = asArr(v)
    return arr.length <= 3 ? `行业:${arr.map((c) => INDUSTRY_NAME[c] || c).join('/')}` : `行业(${arr.length})`
  }
  if (k === 'custom') return `自定义(${asArr(v).length}只)`
  if (k === 'then') return v.map(poolLabel).join(' → ')
  if (k === 'and') return v.map(poolLabel).join(' ∩ ')
  if (k === 'or') return v.map(poolLabel).join(' ∪ ')
  if (k === 'without') return `${poolLabel(v[0])} − ${poolLabel(v[1])}`
  return JSON.stringify(pool)
}

const DEFAULT_JSON = '{\n  "then": [\n    {"index": "000852.SH"},\n    {"size": {"bottom": 100}}\n  ]\n}'

/**
 * 股票池构建器：可视化（基础指数/板块 + 行业多选 + 市值/流动性叠加筛选）+ 高级 JSON。
 * 受控输出：每当拼出的 spec 变化，回调 onChange(spec, errString)。
 */
export default function PoolPicker({ industries = [], initialIndex = '000905.SH', onChange }) {
  const [f, setF] = useState({
    mode: 'visual', index: initialIndex,
    indOn: false, industries: [],
    size: 'none', sizeK: 100, qLo: 0, qHi: 0.3,
    json: DEFAULT_JSON,
  })
  const set = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value
    setF((s) => ({ ...s, [k]: v }))
  }
  const toggleInd = (code) =>
    setF((s) => ({ ...s, industries: s.industries.includes(code) ? s.industries.filter((x) => x !== code) : [...s.industries, code] }))

  const preview = useMemo(() => {
    try { return { spec: buildPoolSpec(f), err: null } }
    catch (e) { return { spec: null, err: e.message } }
  }, [f])

  useEffect(() => { onChange?.(preview.spec, preview.err) }, [preview.spec, preview.err])

  return (
    <div style={{ padding: '10px 12px', background: '#fafbfc', border: '1px solid var(--border)', borderRadius: 8 }}>
      <div className="row between" style={{ marginBottom: 10 }}>
        <div className="muted" style={{ fontSize: 12.5, fontWeight: 600 }}>股票池</div>
        <label style={{ fontSize: 12.5 }}>
          <input type="checkbox" checked={f.mode === 'json'}
            onChange={(e) => setF((s) => ({ ...s, mode: e.target.checked ? 'json' : 'visual' }))} />
          {' '}高级（JSON 组合）
        </label>
      </div>

      {f.mode === 'visual' ? (
        <>
          <div className="row">
            <label className="field">
              基础
              <select value={f.index} onChange={set('index')}>
                {BASE_OPTS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
              </select>
            </label>
            <label className="field">
              叠加筛选
              <select value={f.size} onChange={set('size')}>
                <option value="none">不筛</option>
                <optgroup label="市值">
                  <option value="size_bottom">市值最小 N（小盘）</option>
                  <option value="size_top">市值最大 N（大盘）</option>
                  <option value="size_q">市值分位带</option>
                </optgroup>
                <optgroup label="流动性">
                  <option value="liq_top">流动性最高 N（活跃）</option>
                  <option value="liq_bottom">流动性最低 N</option>
                </optgroup>
              </select>
            </label>
            {['size_bottom', 'size_top', 'liq_top', 'liq_bottom'].includes(f.size) && (
              <label className="field">N<input type="number" value={f.sizeK} onChange={set('sizeK')} style={{ width: 90 }} /></label>
            )}
            {f.size === 'size_q' && (
              <>
                <label className="field">分位下界<input type="number" step="0.05" value={f.qLo} onChange={set('qLo')} style={{ width: 80 }} /></label>
                <label className="field">分位上界<input type="number" step="0.05" value={f.qHi} onChange={set('qHi')} style={{ width: 80 }} /></label>
              </>
            )}
          </div>
          <div style={{ marginTop: 10 }}>
            <label style={{ fontSize: 12.5 }}>
              <input type="checkbox" checked={f.indOn} onChange={set('indOn')} /> 限定行业（SW 一级，多选取并集）
            </label>
            {f.indOn && (
              <div className="row" style={{ marginTop: 6, gap: '4px 14px' }}>
                {industries.map((ind) => (
                  <label key={ind.code} style={{ fontSize: 12.5 }}>
                    <input type="checkbox" checked={f.industries.includes(ind.code)} onChange={() => toggleInd(ind.code)} />
                    {' '}{ind.name}
                  </label>
                ))}
                {!industries.length && <span className="muted" style={{ fontSize: 12 }}>无行业数据</span>}
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          <textarea value={f.json} onChange={set('json')} spellCheck={false}
            style={{ width: '100%', minHeight: 110, font: '12.5px/1.5 ui-monospace, Consolas, monospace', padding: 8, border: '1px solid var(--border)', borderRadius: 7, resize: 'vertical' }} />
          <div className="muted" style={{ fontSize: 12, lineHeight: 1.7 }}>
            算子：<code>and</code>(交) / <code>or</code>(并) / <code>then</code>(串·排序池用) / <code>without</code>(减)<br />
            池：<code>{'{"index":"000905.SH"}'}</code>、<code>{'{"size":{"bottom":200}}'}</code>、
            <code>{'{"liquidity":{"top":300}}'}</code>、<code>{'{"industry":["801780.SI"]}'}</code>（SW一级）、
            <code>{'{"board":["创业板"]}'}</code>、<code>{'{"custom":["600519.SH"]}'}</code>、<code>{'{"all":{}}'}</code>
          </div>
        </>
      )}

      <div style={{ marginTop: 8, fontSize: 12.5 }}>
        {preview.err
          ? <span className="err" style={{ padding: '2px 8px' }}>JSON 解析失败：{preview.err}</span>
          : <span className="muted">将使用：<b>{poolLabel(preview.spec)}</b>　<code>{JSON.stringify(preview.spec)}</code></span>}
      </div>
    </div>
  )
}
