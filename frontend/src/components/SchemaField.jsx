// 策略 PARAM_SCHEMA 通用表单渲染（回测页 / 实盘跟踪页共用）。
// 按字段 type 渲染 int/float/bool/select；'factors' 类型不走这里，各页面自己在
// 需要因子风格信息（defaults.factors）的地方单独渲染勾选网格。
export default function SchemaField({ field, value, onChange }) {
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

export function schemaDefaults(schema) {
  const out = {}
  ;(schema || []).forEach((f) => { out[f.key] = f.default })
  return out
}
