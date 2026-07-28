export function Panel({ title, sub, children, actions }) {
  return (
    <div className="panel">
      {(title || actions) && (
        <div className="row between" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>
            {title}
            {sub && <span className="sub">{sub}</span>}
          </h3>
          <div className="row">{actions}</div>
        </div>
      )}
      {children}
    </div>
  )
}

export function Card({ label, value, tone }) {
  const cls = tone === 'pos' ? 'value pos' : tone === 'neg' ? 'value neg' : 'value'
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className={cls}>{value}</div>
    </div>
  )
}

export function Loading({ text = '加载中…' }) {
  return <div className="loading">{text}</div>
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="err">出错：{String(error.message || error)}</div>
}

export function Table({ columns, rows, empty = '暂无数据' }) {
  if (!rows || rows.length === 0) return <div className="loading">{empty}</div>
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.num ? 'num' : ''}>{c.title}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.key} className={c.num ? 'num' : ''}>
                  {c.render ? c.render(r[c.key], r) : (r[c.key] ?? '-')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
