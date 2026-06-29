/** 市场/子市场/板块中文映射 */
export const MARKET_LABELS: Record<string, string> = {
  A: 'A股',
  HK: '港股',
  SH: '上海',
  SZ: '深圳',
  BJ: '北京',
  主板: '主板',
  创业板: '创业板',
  科创板: '科创板',
  北交所: '北交所',
  其他: '其他',
}

export function marketLabel(key: string): string {
  return MARKET_LABELS[key] || key || '-'
}
