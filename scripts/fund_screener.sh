#!/bin/bash
# 安隐基金优选 Pipeline — 基于盈米Skills
# 用法: bash scripts/fund_screener.sh

set -e

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
TMPDIR="$WORKDIR/tmp"
OUTDIR="$WORKDIR/output"
mkdir -p "$TMPDIR" "$OUTDIR"

CLI="yingmi-skill-cli"

# Helper: strip node warnings from stdout, extract JSON
strip_json() {
  local infile="$1"
  local outfile="$2"
  # Remove lines starting with "(node:...)" which are Node.js warnings
  grep -v '^(node:' "$infile" > "$outfile"
}

echo "============================================"
echo " 安隐基金优选 - 每日筛选 Pipeline"
echo " 数据源: 盈米基金 (且慢)"
echo "============================================"

# =============================================
# Phase 1: 获取候选基金池
# =============================================
echo ""
echo "[Phase 1] 获取候选基金池..."

# 混合型基金候选（偏股混合型 + 灵活配置型）
for cat in "偏股混合型" "灵活配置型"; do
  echo "  搜索: $cat"
  $CLI mcp call SearchFunds --input "{\"category\":\"${cat}\",\"size\":30,\"sortColumn\":\"收益率\",\"sortOrder\":\"降序\"}" \
    > "$TMPDIR/search_${cat}_raw.json" 2>&1
  strip_json "$TMPDIR/search_${cat}_raw.json" "$TMPDIR/search_${cat}.json"
done

# 债券型基金候选（纯债 + 二级债）
for cat in "纯债"; do
  echo "  搜索: $cat"
  $CLI mcp call SearchFunds --input "{\"category\":\"${cat}\",\"size\":30,\"sortColumn\":\"收益率\",\"sortOrder\":\"降序\"}" \
    > "$TMPDIR/search_${cat}_raw.json" 2>&1
  strip_json "$TMPDIR/search_${cat}_raw.json" "$TMPDIR/search_${cat}.json"
done

# 合并候选基金代码
python3 -c "
import json, os

all_codes = set()
categories = ['偏股混合型', '灵活配置型', '纯债']
for cat in categories:
    f = f'$TMPDIR/search_{cat}.json'
    if not os.path.exists(f):
        continue
    with open(f) as fh:
        data = json.load(fh)
    funds = data.get('funds', [])
    for f in funds:
        all_codes.add(f['fundCode'])
    print(f'  {cat}: {len(funds)} 只')

codes = sorted(list(all_codes))
print(f'  候选池合计: {len(codes)} 只')
with open('$TMPDIR/candidates.json', 'w') as fh:
    json.dump(codes, fh)
"

echo ""

# =============================================
# Phase 2: 批量获取业绩表现
# =============================================
echo "[Phase 2] 批量获取业绩表现..."

python3 -c "
import json, subprocess, os

with open('$TMPDIR/candidates.json') as fh:
    codes = json.load(fh)

# Batch by 20
batch_size = 20
all_results = []
for i in range(0, len(codes), batch_size):
    batch = codes[i:i+batch_size]
    print(f'  批次 {i//batch_size + 1}/{(len(codes)+batch_size-1)//batch_size}: {batch[0]}...{batch[-1]}')
    
    input_json = json.dumps({'fundCodes': batch})
    result = subprocess.run(
        ['$CLI', 'mcp', 'call', 'GetBatchFundPerformance', '--input', input_json],
        capture_output=True, text=True
    )
    # Parse output (strip node warnings)
    lines = result.stdout.split('\n')
    json_lines = [l for l in lines if not l.startswith('(node:')]
    clean = '\n'.join(json_lines)
    
    try:
        batch_data = json.loads(clean)
        all_results.extend(batch_data)
    except:
        print(f'  ⚠️ 批次 {i//batch_size + 1} 解析失败')

with open('$TMPDIR/performance.json', 'w') as fh:
    json.dump(all_results, fh, ensure_ascii=False)
print(f'  成功获取 {len(all_results)} 只基金的业绩数据')
"

echo ""

# =============================================
# Phase 3: 筛选 + 评分 + 输出
# =============================================
echo "[Phase 3] 筛选 + 评分 + 生成报告..."

python3 << 'PYEOF'
import json
import os

TMPDIR = os.path.expanduser("~/WorkBuddy/2026-05-26-09-24-25/tmp")
OUTDIR = os.path.expanduser("~/WorkBuddy/2026-05-26-09-24-25/output")
os.makedirs(OUTDIR, exist_ok=True)

# Load performance data
with open(f"{TMPDIR}/performance.json") as f:
    perf_data = json.load(f)

# Build fund map
fund_map = {}
for item in perf_data:
    fc = item["fundCode"]
    data = item.get("data", {})
    
    # Parse stage returns
    stage_returns = {}
    for sr in data.get("stageReturns", []):
        stage_returns[sr["stageType"]] = sr.get("stageReturn", 0)
    
    # Parse metrics
    metrics_info = {}
    for ma in data.get("metricsAnalyzes", []):
        st = ma["stageType"]
        for m in ma.get("metrics", []):
            key = f"{st}_{m['title']}"
            metrics_info[key] = {
                "value": m.get("metricsValue"),
                "text": m.get("metricsValueText"),
                "rankText": m.get("rankText")
            }
    
    fund_map[fc] = {
        "stageReturns": stage_returns,
        "metrics": metrics_info
    }

# ====== 评分引擎 ======
def score_fund(fc, info):
    sr = info["stageReturns"]
    m = info["metrics"]
    
    # --- 收益评分 (权重 40%) ---
    # 所有周期收益率必须全部为正
    required_periods = ["oneWeek", "oneMonth", "quarter", "halfYear", "oneYear"]
    returns = []
    all_positive = True
    for p in required_periods:
        r = sr.get(p)
        if r is not None:
            returns.append(r)
            if r <= 0:
                all_positive = False
    
    if not returns or not all_positive:
        return None  # 淘汰
    
    # 收益一致性: 长周期 > 短周期（递增趋势）
    trend_score = 0
    for i in range(1, len(returns)):
        if returns[i] > returns[i-1]:
            trend_score += 2
        elif returns[i] > returns[i-1] * 0.8:
            trend_score += 1
    
    # 近期收益权重更高
    near_weight = [1, 1.5, 2, 2.5, 3]  # 1周, 1月, 3月, 6月, 1年
    weighted_return = sum(r * w for r, w in zip(returns, near_weight[:len(returns)]))
    
    return_score = min(weighted_return * 10, 40)  # max 40
    
    # --- 波动率评分 (权重 30%) ---
    vol_score = 0
    vol_key = "oneYear_抗波动能力"
    if vol_key in m:
        vol_val = m[vol_key].get("value")
        vol_text = m[vol_key].get("text", "0%")
        if vol_val is not None:
            vol_pct = vol_val
            if vol_pct < 0.15:
                vol_score = 30
            elif vol_pct < 0.20:
                vol_score = 25
            elif vol_pct < 0.25:
                vol_score = 20
            elif vol_pct < 0.30:
                vol_score = 15
            elif vol_pct < 0.35:
                vol_score = 10
            else:
                vol_score = 5
    
    # --- 回撤评分 (权重 20%) ---
    dd_score = 0
    dd_key = "oneYear_抗回撤能力"
    if dd_key in m:
        dd_val = m[dd_key].get("value")
        dd_text = m[dd_key].get("text", "0%")
        if dd_val is not None:
            dd_abs = abs(dd_val)
            if dd_abs < 0.05:
                dd_score = 20
            elif dd_abs < 0.10:
                dd_score = 18
            elif dd_abs < 0.15:
                dd_score = 15
            elif dd_abs < 0.20:
                dd_score = 10
            elif dd_abs < 0.25:
                dd_score = 5
            else:
                dd_score = 2
    
    # --- 夏普比率加分 (权重 10%) ---
    sharpe_score = 0
    sharpe_key = "oneYear_投资性价比"
    if sharpe_key in m:
        sharpe_val = m[sharpe_key].get("value")
        if sharpe_val is not None:
            if sharpe_val >= 5:
                sharpe_score = 10
            elif sharpe_val >= 3:
                sharpe_score = 8
            elif sharpe_val >= 2:
                sharpe_score = 6
            elif sharpe_val >= 1:
                sharpe_score = 4
            elif sharpe_val > 0:
                sharpe_score = 2
    
    total_score = return_score + vol_score + dd_score + sharpe_score
    trend_bonus = min(trend_score, 10)
    total_score = min(total_score + trend_bonus, 100)
    
    # 等级评定
    if total_score >= 80:
        ranking = "优优优优"
        suggestion = "买入"
    elif total_score >= 65:
        ranking = "优良优优"
        suggestion = "买入"
    elif total_score >= 50:
        ranking = "优良优良"
        suggestion = "关注"
    elif total_score >= 35:
        ranking = "优良中良"
        suggestion = "持有"
    else:
        ranking = "良好中中"
        suggestion = "观望"
    
    # 风险控制分
    risk_score = vol_score + dd_score  # 0-50
    risk_level = 1 if risk_score >= 35 else 2 if risk_score >= 25 else 3
    
    fmt_return = {}
    for p in required_periods:
        r = sr.get(p)
        if r is not None:
            fmt_return[p] = f"{r*100:.2f}%"
    
    return {
        "score": round(total_score, 1),
        "riskScore": risk_level,
        "ranking": ranking,
        "suggestion": suggestion,
        "return1w": fmt_return.get("oneWeek", ""),
        "return1m": fmt_return.get("oneMonth", ""),
        "return3m": fmt_return.get("quarter", ""),
        "return6m": fmt_return.get("halfYear", ""),
        "return1y": fmt_return.get("oneYear", ""),
        "volatility": m.get("oneYear_抗波动能力", {}).get("text", ""),
        "maxDrawdown": m.get("oneYear_抗回撤能力", {}).get("text", ""),
        "sharpeRatio": m.get("oneYear_投资性价比", {}).get("text", ""),
        "rankText": m.get("oneYear_收益能力", {}).get("rankText", ""),
        "trendScore": trend_score,
    }

# Score all funds
scored = []
for fc, info in fund_map.items():
    result = score_fund(fc, info)
    if result:
        result["fundCode"] = fc
        scored.append(result)

# Sort by score descending
scored.sort(key=lambda x: x["score"], reverse=True)

# Print summary
print(f"\n📊 评分完成")
print(f"   候选基金: {len(fund_map)} 只")
print(f"   通过筛选: {len(scored)} 只")
print(f"   淘汰: {len(fund_map) - len(scored)} 只")
print()

# Output top 20 for review
for s in scored[:20]:
    print(f"  {s['fundCode']}: 评分={s['score']} 风控={s['riskScore']} "
          f"排名={s['ranking']} 建议={s['suggestion']} "
          f"1月={s['return1m']} 1年={s['return1y']} "
          f"波动={s['volatility']} 回撤={s['maxDrawdown']}")

# Save results
with open(f"{TMPDIR}/scored_results.json", "w") as f:
    json.dump(scored, f, ensure_ascii=False, indent=2)

# ====== 生成 HTML 报告 ======
# Also load fund names from candidates
html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>安隐基金优选 - 每日筛选报告</title>
<style>
  body { font-family: -apple-system, 'Microsoft YaHei', sans-serif; background: #f5f7fa; margin: 0; padding: 20px; }
  .container { max-width: 1400px; margin: 0 auto; }
  h1 { color: #1a1a2e; font-size: 24px; border-bottom: 3px solid #e94560; padding-bottom: 10px; }
  .date-badge { color: #888; font-size: 14px; font-weight: normal; margin-left: 10px; }
  .subtitle { color: #666; margin-bottom: 20px; }
  .summary-bar { display: flex; gap: 20px; margin: 20px 0; }
  .summary-card { background: white; padding: 15px 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); flex: 1; text-align: center; }
  .summary-card .num { font-size: 28px; font-weight: bold; color: #1a1a2e; }
  .summary-card .label { color: #888; font-size: 13px; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  th { background: #1a1a2e; color: white; padding: 12px 8px; font-size: 13px; text-align: center; white-space: nowrap; }
  td { padding: 10px 8px; text-align: center; font-size: 13px; border-bottom: 1px solid #eee; }
  tr:hover { background: #f0f4ff; }
  .rank-优优优优 { color: #e94560; font-weight: bold; }
  .rank-优良优优 { color: #e96045; font-weight: bold; }
  .rank-优良优良 { color: #e9a045; }
  .suggestion-买入 { color: #e94560; font-weight: bold; }
  .suggestion-关注 { color: #e9a045; font-weight: bold; }
  .suggestion-持有 { color: #2d6a9f; }
  .suggestion-观望 { color: #888; }
  .risk-1 { color: #27ae60; font-weight: bold; }
  .risk-2 { color: #e9a045; }
  .risk-3 { color: #e94560; }
  .score-bar { display: inline-block; height: 6px; border-radius: 3px; background: #eee; width: 60px; vertical-align: middle; margin-right: 6px; }
  .score-bar-inner { height: 100%; border-radius: 3px; }
  .pos { color: #e94560; }
  .neg { color: #27ae60; }
  .footer { margin-top: 20px; color: #aaa; font-size: 12px; text-align: center; }
</style>
</head>
<body>
<div class="container">
<h1>安隐基金优选 <span class="date-badge">{date}</span></h1>
<p class="subtitle">基于盈米基金数据 · 多周期收益持续向上 + 低波动 + 低回撤筛选</p>

<div class="summary-bar">
  <div class="summary-card"><div class="num">{total}</div><div class="label">候选基金</div></div>
  <div class="summary-card"><div class="num">{passed}</div><div class="label">通过筛选</div></div>
  <div class="summary-card"><div class="num">{buy}</div><div class="label">建议买入</div></div>
  <div class="summary-card"><div class="num">{watch}</div><div class="label">建议关注</div></div>
</div>

{section_mix}
{section_bond}
{section_all}

</div>
<div class="footer">数据来源: 盈米基金(且慢) | 报告自动生成 | 仅供投资参考，不构成投资建议</div>
</body>
</html>"""

import subprocess, datetime
now = datetime.datetime.now()
date_str = f"{now.year}年{now.month:02d}月{now.day:02d}日"

# Try to get fund names
fund_names = {}
try:
    result = subprocess.run(
        ['tingmi-skill-cli', 'mcp', 'call', 'SearchFunds', '--input', '{"keyword":"001076","size":1}'],
        capture_output=True, text=True, timeout=10
    )
except:
    pass

# Build table rows
buy_count = 0
watch_count = 0
rows = ""
for s in scored[:15]:
    fc = s['fundCode']
    name = fund_names.get(fc, fc)
    score = s['score']
    risk = s['riskScore']
    
    if s['suggestion'] == '买入':
        buy_count += 1
    elif s['suggestion'] == '关注':
        watch_count += 1
    
    score_pct = int(score)
    bar_color = "#e94560" if score >= 70 else "#e9a045" if score >= 50 else "#888"
    
    row_class = ""
    if s['ranking'] == '优优优优':
        row_class = ' class="rank-优优优优"'
    
    rows += f"""<tr>
    <td>{risk}</td>
    <td><span class="score-bar"><span class="score-bar-inner" style="width:{score_pct}%;background:{bar_color}"></span></span>{score}</td>
    <td>{fc}</td>
    <td style="text-align:left">{fc}</td>
    <td class="rank-{s['ranking']}">{s['ranking']}</td>
    <td class="suggestion-{s['suggestion']}">{s['suggestion']}</td>
    <td class="pos">{s['return1w']}</td>
    <td class="pos">{s['return1m']}</td>
    <td class="pos">{s['return3m']}</td>
    <td class="pos">{s['return6m']}</td>
    <td class="pos">{s['return1y']}</td>
    <td>{s['volatility']}</td>
    <td>{s['maxDrawdown']}</td>
    <td>{s['sharpeRatio']}</td>
    <td>{s['rankText']}</td>
</tr>"""

section_all = f"""<h2>全部通过基金</h2>
<table>
<thead><tr>
<th>风控</th><th>评分</th><th>代码</th><th>基金</th><th>排名</th><th>建议</th>
<th>1周</th><th>1月</th><th>3月</th><th>6月</th><th>1年</th>
<th>波动率</th><th>最大回撤</th><th>夏普</th><th>同类排名</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>"""

html = html.format(
    date=date_str,
    total=len(fund_map),
    passed=len(scored),
    buy=buy_count,
    watch=watch_count,
    section_mix="",
    section_bond="",
    section_all=section_all
)

with open(f"{OUTDIR}/zmail_recommend.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"\n✅ 报告已生成: {OUTDIR}/zmail_recommend.html")
print(f"   建议买入: {buy_count} | 关注: {watch_count} | 通过筛选: {len(scored)}")
PYEOF

echo ""
echo "============================================"
echo " Pipeline 完成!"
echo " 报告: output/zmail_recommend.html"
echo " 数据缓存: tmp/"
echo "============================================"
