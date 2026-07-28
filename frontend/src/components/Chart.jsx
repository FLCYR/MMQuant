import ReactECharts from 'echarts-for-react'

const BASE = {
  grid: { left: 58, right: 22, top: 34, bottom: 42 },
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  legend: { top: 2, icon: 'roundRect', itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 12 } },
  textStyle: { fontFamily: '"Segoe UI","Microsoft YaHei",sans-serif' },
}

export default function Chart({ option, height = 300 }) {
  return (
    <ReactECharts
      option={{ ...BASE, ...option }}
      style={{ height, width: '100%' }}
      notMerge
      lazyUpdate
    />
  )
}

export const pct = (v, nd = 2) => (v === null || v === undefined || Number.isNaN(v) ? '-' : `${(v * 100).toFixed(nd)}%`)
export const num = (v, nd = 2) => (v === null || v === undefined || Number.isNaN(v) ? '-' : Number(v).toFixed(nd))
export const money = (v) => (v === null || v === undefined ? '-' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 }))

// 时间轴通用配置：日期太密时只显示部分刻度
export const dateAxis = (x) => ({
  type: 'category',
  data: x,
  axisLabel: { hideOverlap: true, fontSize: 11 },
  axisTick: { show: false },
})

export const valueAxis = (formatter) => ({
  type: 'value',
  scale: true,
  axisLabel: { formatter, fontSize: 11 },
  splitLine: { lineStyle: { color: '#eef0f3' } },
})
