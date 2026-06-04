#!/bin/bash
# 安隐基金优选 Pipeline v2 (Shell版)
# 数据源: 盈米Skills (且慢)
# 完整运行脚本

set -e
WORKDIR="$(cd "$(dirname "$0")" && pwd)"
cd "$WORKDIR"
mkdir -p tmp output

CLI="yingmi-skill-cli.cmd"
NOW=$(date +"%Y-%m-%d %H:%M:%S")

echo "============================================"
echo " 安隐基金优选 Pipeline v2"
echo " 时间: $NOW"
echo " 工作目录: $WORKDIR"
echo "============================================"

# =============================================
# Phase 1: 获取候选基金池
# =============================================
echo ""
echo "[Phase 1] 获取候选基金池..."

# 清空候选列表
> tmp/all_codes.txt
echo -n '{"funds":[]}' > tmp/all_funds.json

for cat in "偏股混合型" "灵活配置型" "纯债" "二级债"; do
  echo "  搜索: $cat"
  # 搜索基金并去除node警告
  $CLI mcp call SearchFunds --input "{\"category\":\"${cat}\",\"size\":30,\"sortColumn\":\"收益率\",\"sortOrder\":\"降序\"}" 2>&1 | grep -v "^(node:" > "tmp/search_${cat}.json"
  
  # 提取基金代码
  python3 << EOF
import json
with open("tmp/search_${cat}.json") as f:
    data = json.load(f)
funds = data.get("funds", [])
print(f"    -> 获取 {len(funds)} 只")

# 追加到总列表
with open("tmp/all_funds.json") as f:
    all_data = json.load(f)
existing = {f["fundCode"]: f for f in all_data["funds"]}
for f in funds:
    existing[f["fundCode"]] = f
all_data["funds"] = list(existing.values())
with open("tmp/all_funds.json", "w") as f:
    json.dump(all_data, f, ensure_ascii=False)
EOF
done

echo ""
# 统计候选池
python3 << EOF
import json
with open("tmp/all_funds.json") as f:
    data = json.load(f)
codes = [f["fundCode"] for f in data["funds"]]
with open("tmp/all_codes.json", "w") as f:
    json.dump(codes, f)
# 保存名称映射
names = {f["fundCode"]: f["fundName"] for f in data["funds"]}
with open("tmp/fund_names.json", "w") as f:
    json.dump(names, f, ensure_ascii=False)
print(f"  ✅ 候选池合计: {len(codes)} 只 (去重后)")
EOF

# =============================================
# Phase 2: 批量获取业绩表现
# =============================================
echo ""
echo "[Phase 2] 批量获取业绩表现..."

python3 << 'PYEOF'
import json, subprocess, os, sys

CLI = "yingmi-skill-cli"
TMPDIR = "tmp"

with open(f"{TMPDIR}/all_codes.json") as f:
    codes = json.load(f)

batch_size = 20
all_perf = {}
total = len(codes)

for i in range(0, total, batch_size):
    batch = codes[i:i+batch_size]
    batch_num = i // batch_size + 1
    total_batches = (total + batch_size - 1) // batch_size
    print(f"  批次 {batch_num}/{total_batches}: [{batch[0]} ... {batch[-1]}]")

    input_json = json.dumps({"fundCodes": batch}, ensure_ascii=False)
    result = subprocess.run(
        [CLI, "mcp", "call", "GetBatchFundPerformance", "--input", input_json],
        capture_output=True, text=True, timeout=60
    )
    
    # Strip node warnings
    lines = [l for l in result.stdout.split('\n') if not l.strip().startswith('(node:')]
    clean = '\n'.join(lines)
    
    try:
        batch_data = json.loads(clean)
        for item in batch_data:
            fc = item["fundCode"]
            all_perf[fc] = item.get("data", {})
        print(f"    -> 成功: {len(batch_data)} 只")
    except json.JSONDecodeError as e:
        print(f"    -> 解析失败: {e}")

with open(f"{TMPDIR}/performance.json", "w", encoding="utf-8") as f:
    json.dump(all_perf, f, ensure_ascii=False)
print(f"\n  ✅ 获取完成: {len(all_perf)} 只")
PYEOF

# =============================================
# Phase 3: 评分 + 报告
# =============================================
echo ""
echo "[Phase 3] 评分筛选 + 报告生成..."

python3 << 'PYEOF'
import json
import os
import datetime

TMPDIR = "tmp"
OUTDIR = "output"
os.makedirs(OUTDIR, exist_ok=True)

# Load data
with open(f"{TMPDIR}/performance.json") as f:
    perf_data = json.load(f)

with open(f"{TMPDIR}/fund_names.json") as f:
    fund_names = json.load(f)

required_periods = ["oneWeek", "oneMonth", "quarter", "halfYear", "oneYear"]
scored = []
screened = 0

for fc, data in perf_data.items():
    # Parse stage returns
    stage_returns = {}
    for sr in data.get("stageReturns", []):
        stage_returns[sr["stageType"]] = sr.get("stageReturn")

    # Parse metrics
    metrics = {}
    for ma in data.get("metricsAnalyzes", []):
        for m in ma.get("metrics", []):
            key = f"{ma['stageType']}_{m['title']}"
            metrics[key] = m

    # Screening: all periods positive
    returns = []
    skip = False
    for p in required_periods:
        r = stage_returns.get(p)
        if r is None:
            skip = True
            break
        returns.append(r)
        if r <= 0:
            skip = True
            break

    if skip or len(returns) < 5:
        screened += 1
        continue

    # ====== Scoring ======
    # 1. Return (0-35)
    weights = [1.0, 1.5, 2.0, 2.5, 3.0]
    weighted_return = sum(r * w for r, w in zip(returns, weights))
    return_score = min(weighted_return * 10, 35)

    # Trend bonus
    trend_score = 0
    for i in range(1, len(returns)):
        if returns[i] > returns[i-1]:
            trend_score += 3
        elif returns[i] > returns[i-1] * 0.7:
            trend_score += 1
    trend_score = min(trend_score, 10)

    # 2. Volatility (0-25)
    vol_score = 0
    vol_key = "oneYear_抗波动能力"
    if vol_key in metrics:
        vol_val = metrics[vol_key].get("metricsValue")
        if vol_val:
            if vol_val < 0.15: vol_score = 25
            elif vol_val < 0.20: vol_score = 22
            elif vol_val < 0.25: vol_score = 18
            elif vol_val < 0.30: vol_score = 14
            elif vol_val < 0.35: vol_score = 10
            else: vol_score = 5

    # 3. Drawdown (0-20)
    dd_score = 0
    dd_key = "oneYear_抗回撤能力"
    if dd_key in metrics:
        dd_val = metrics[dd_key].get("metricsValue")
        if dd_val:
            dd_abs = abs(dd_val)
            if dd_abs < 0.05: dd_score = 20
            elif dd_abs < 0.10: dd_score = 17
            elif dd_abs < 0.15: dd_score = 14
            elif dd_abs < 0.20: dd_score = 10
            elif dd_abs < 0.25: dd_score = 6
            else: dd_score = 3

    # 4. Sharpe Ratio (0-10)
    sharpe_score = 0
    sharpe_key = "oneYear_投资性价比"
    if sharpe_key in metrics:
        sharpe_val = metrics[sharpe_key].get("metricsValue")
        if sharpe_val:
            if sharpe_val >= 5: sharpe_score = 10
            elif sharpe_val >= 3: sharpe_score = 8
            elif sharpe_val >= 2: sharpe_score = 6
            elif sharpe_val >= 1: sharpe_score = 4
            elif sharpe_val > 0: sharpe_score = 2

    total_score = return_score + vol_score + dd_score + sharpe_score + trend_score
    total_score = min(total_score, 100)

    # Risk rating (1-4, lower is better = less volatile)
    risk_raw = vol_score + dd_score
    if risk_raw >= 35: risk_level = 1
    elif risk_raw >= 25: risk_level = 2
    elif risk_raw >= 15: risk_level = 3
    else: risk_level = 4

    # Ranking
    if total_score >= 80:
        ranking, suggestion = "优优优优", "买入"
    elif total_score >= 65:
        ranking, suggestion = "优良优优", "买入"
    elif total_score >= 50:
        ranking, suggestion = "优良优良", "关注"
    elif total_score >= 35:
        ranking, suggestion = "优优良良", "持有"
    else:
        ranking, suggestion = "良好中中", "观望"

    fund_name = fund_names.get(fc, fc)

    scored.append({
        "fundCode": fc, "fundName": fund_name,
        "score": round(total_score, 1), "riskScore": risk_level,
        "ranking": ranking, "suggestion": suggestion,
        "return1w": f"{returns[0]*100:.2f}%",
        "return1m": f"{returns[1]*100:.2f}%",
        "return3m": f"{returns[2]*100:.2f}%",
        "return6m": f"{returns[3]*100:.2f}%",
        "return1y": f"{returns[4]*100:.2f}%",
        "volatility": metrics.get("oneYear_抗波动能力", {}).get("metricsValueText", ""),
        "maxDrawdown": metrics.get("oneYear_抗回撤能力", {}).get("metricsValueText", ""),
        "sharpeRatio": metrics.get("oneYear_投资性价比", {}).get("metricsValueText", ""),
        "rankText": metrics.get("oneYear_收益能力", {}).get("rankText", ""),
    })

scored.sort(key=lambda x: x["score"], reverse=True)

# Save intermediate
with open(f"{TMPDIR}/scored_results.json", "w", encoding="utf-8") as f:
    json.dump(scored, f, ensure_ascii=False, indent=2)

buy_count = sum(1 for s in scored if s["suggestion"] == "买入")
watch_count = sum(1 for s in scored if s["suggestion"] == "关注")
hold_count = sum(1 for s in scored if s["suggestion"] in ("持有", "观望"))

print(f"\n  📊 评分完成")
print(f"     候选基金: {len(perf_data)} 只")
print(f"     通过筛选: {len(scored)} 只")
print(f"     淘汰(负收益/无数据): {screened} 只")
print(f"     建议买入: {buy_count} 只 | 关注: {watch_count} 只 | 持有/观望: {hold_count} 只")

if scored:
    print(f"\n  🏆 Top 10:")
    for s in scored[:10]:
        print(f"    {s['fundCode']} {s['fundName'][:20]}")
        print(f"      评分={s['score']} 风控={s['riskScore']} 排名={s['ranking']} 建议={s['suggestion']}")
        print(f"      1周={s['return1w']} 1月={s['return1m']} 3月={s['return3m']} 6月={s['return6m']} 1年={s['return1y']}")

# ====== Generate HTML ======
now = datetime.datetime.now()
date_str = f"{now.year}年{now.month:02d}月{now.day:02d}日"

rows = ""
for i, s in enumerate(scored[:20]):
    score = s["score"]
    score_pct = min(int(score), 100)
    bar_color = "#e94560" if score >= 70 else "#e9a045" if score >= 50 else "#888"
    last_cls = ' style="background-color: #FF0000; color: white;"' if i == min(len(scored)-1, 19) else ""

    rows += f"""<tr{last_cls}>
    <td>{s['riskScore']}</td>
    <td><div class="score-bar"><div class="score-bar-inner" style="width:{score_pct}%;background:{bar_color}"></div></div>{score}</td>
    <td>{s['fundCode']}</td>
    <td style="text-align:left">{s['fundName'][:20]}</td>
    <td class="rank-{s['ranking']}">{s['ranking']}</td>
    <td class="suggestion-{s['suggestion']}">{s['suggestion']}</td>
    <td class="pos">{s['return1w']}</td>
    <td class="pos">{s['return1m']}</td>
    <td class="pos">{s['return3m']}</td>
    <td class="pos">{s['return6m']}</td>
    <td class="pos">{s['return1y']}</td>
    <td>{s['volatility']}</td>
    <td>{s['maxDrawdown']}</td>
    <td>{s.get('sharpeRatio', '')}</td>
    <td style="font-size:11px">{s.get('rankText', '')}</td>
    <td><a href="https://qieman.com/funds/{s['fundCode']}" target="_blank">详情</a></td>
</tr>"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>安隐基金优选 - 每日筛选报告</title>
<style>
  body {{ font-family: -apple-system, 'Microsoft YaHei', 'PingFang SC', sans-serif; background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 100%); margin: 0; padding: 20px; }}
  .container {{ max-width: 1500px; margin: 0 auto; }}
  h1 {{ color: #1a1a2e; font-size: 26px; display: flex; align-items: center; gap: 10px; }}
  h1 .badge {{ background: #e94560; color: white; padding: 4px 12px; border-radius: 20px; font-size: 14px; }}
  .date-badge {{ color: #888; font-size: 14px; font-weight: normal; margin-left: 10px; }}
  .subtitle {{ color: #666; margin-bottom: 25px; font-size: 14px; }}
  .summary-bar {{ display: flex; gap: 15px; margin: 20px 0 30px; }}
  .summary-card {{ background: white; padding: 18px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); flex: 1; text-align: center; transition: transform 0.2s; }}
  .summary-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.1); }}
  .summary-card .num {{ font-size: 30px; font-weight: bold; color: #1a1a2e; }}
  .summary-card .label {{ color: #888; font-size: 13px; margin-top: 4px; }}
  .summary-card.buy .num {{ color: #e94560; }}
  .summary-card.watch .num {{ color: #e9a045; }}
  .summary-card.hold .num {{ color: #2d6a9f; }}
  .table-wrap {{ background: white; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; min-width: 1200px; }}
  th {{ background: #1a1a2e; color: white; padding: 14px 8px; font-size: 12px; text-align: center; position: sticky; top: 0; z-index: 1; }}
  th:first-child {{ border-radius: 12px 0 0 0; }}
  th:last-child {{ border-radius: 0 12px 0 0; }}
  td {{ padding: 12px 8px; text-align: center; font-size: 13px; border-bottom: 1px solid #f0f0f0; }}
  tr:hover {{ background: #f8faff; }}
  tr:last-child td {{ border-bottom: none; }}
  .rank-优优优优 {{ color: #e94560; font-weight: bold; }}
  .rank-优良优优 {{ color: #e96045; font-weight: bold; }}
  .rank-优良优良 {{ color: #e9a045; font-weight: bold; }}
  .rank-优优良良 {{ color: #2d6a9f; }}
  .suggestion-买入 {{ color: #e94560; font-weight: bold; }}
  .suggestion-关注 {{ color: #e9a045; font-weight: bold; }}
  .suggestion-持有 {{ color: #2d6a9f; }}
  .suggestion-观望 {{ color: #888; }}
  .score-bar {{ display: inline-block; height: 6px; border-radius: 3px; background: #eee; width: 50px; vertical-align: middle; margin-right: 5px; }}
  .score-bar-inner {{ height: 100%; border-radius: 3px; }}
  .pos {{ color: #e94560; }}
  .neg {{ color: #27ae60; }}
  .footer {{ margin-top: 25px; color: #aaa; font-size: 12px; text-align: center; padding: 20px; }}
  .footer a {{ color: #aaa; text-decoration: none; }}
  .note {{ background: #fff8e1; border-left: 4px solid #f9a825; padding: 12px 16px; margin: 20px 0; border-radius: 4px; font-size: 13px; color: #666; }}
  @media (max-width: 768px) {{ .summary-bar {{ flex-wrap: wrap; }} .summary-card {{ flex: 1 1 45%; }} }}
</style>
</head>
<body>
<div class="container">
<h1><span class="badge">安隐基金优选</span>每日筛选报告 <span class="date-badge">{date_str}</span></h1>
<p class="subtitle">筛选逻辑: 多周期收益持续向上 + 低波动率 + 低最大回撤 | 数据源: 盈米基金(且慢)</p>
<div class="summary-bar">
  <div class="summary-card"><div class="num">{len(perf_data)}</div><div class="label">候选基金</div></div>
  <div class="summary-card buy"><div class="num">{buy_count}</div><div class="label">建议买入</div></div>
  <div class="summary-card watch"><div class="num">{watch_count}</div><div class="label">建议关注</div></div>
  <div class="summary-card hold"><div class="num">{hold_count}</div><div class="label">持有/观望</div></div>
</div>
<div class="note">⚠️ <strong>风险提示</strong>: 本报告基于量化模型自动生成，仅供投资参考，不构成投资建议。历史业绩不代表未来表现，投资有风险，决策需谨慎。评分模型: 多周期收益一致性(35%) + 低波动率(25%) + 低回撤(20%) + 夏普比率(10%) + 趋势加分(10%)。</div>
<div class="table-wrap">
<table>
<thead><tr><th>风控</th><th>评分</th><th>代码</th><th style="min-width:160px">基金名称</th><th>排名</th><th>建议</th><th>1周</th><th>1月</th><th>3月</th><th>6月</th><th>1年</th><th>波动率</th><th>最大回撤</th><th>夏普</th><th>同类排名</th><th>链接</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
<div class="footer">数据来源: <a href="https://qieman.com" target="_blank">盈米基金(且慢)</a> | 报告生成于 {date_str} | <a href="https://github.com/zeusmail/anyin-fund" target="_blank">原项目 zeusmail/anyin-fund</a></div>
</div>
</body>
</html>"""

with open(f"{OUTDIR}/zmail_recommend.html", "w", encoding="utf-8") as f:
    f.write(html)
print(f"\n  ✅ HTML报告: {OUTDIR}/zmail_recommend.html")

# Text summary
with open(f"{OUTDIR}/zmail_summary.txt", "w", encoding="utf-8") as f:
    f.write(f"安隐基金优选 - 筛选报告 ({date_str})\n")
    f.write("=" * 50 + "\n\n")
    for s in scored[:10]:
        f.write(f"{s['fundCode']} {s['fundName']:20s}  评分:{s['score']:5.1f}  风控:{s['riskScore']}  {s['ranking']}  {s['suggestion']}\n")
        f.write(f"  1周:{s['return1w']:>7s}  1月:{s['return1m']:>7s}  3月:{s['return3m']:>7s}  6月:{s['return6m']:>7s}  1年:{s['return1y']:>7s}\n")
        f.write(f"  波动:{s['volatility']:>7s}  回撤:{s['maxDrawdown']:>7s}\n\n")
print(f"  ✅ 文本摘要: {OUTDIR}/zmail_summary.txt")
PYEOF

echo ""
echo "============================================"
echo " 🎉 Pipeline 完成!"
echo "    报告: output/zmail_recommend.html"
echo "    文本: output/zmail_summary.txt"
echo "============================================"
