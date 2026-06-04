#!/usr/bin/env python3
"""
安隐基金优选 Pipeline v2 — 基于盈米Skills
===========================================
Phase 1: 获取候选基金池 (SearchFunds)
Phase 2: 批量获取业绩数据 (GetBatchFundPerformance)
Phase 3: 评分筛选 + 输出HTML报告

使用: python pipeline/fund_screener.py
"""

import json
import subprocess
import os
import re
import datetime

# ============================================================
# Config
# ============================================================
WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMPDIR = os.path.join(WORKDIR, "tmp")
OUTDIR = os.path.join(WORKDIR, "output")
os.makedirs(TMPDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

CLI = [r"C:\Users\xyx\AppData\Roaming\npm\yingmi-skill-cli.cmd"]

FUND_CATEGORIES = {
    "mix": ["偏股混合型", "灵活配置型"],
    "bond": ["纯债", "二级债"]
}

# ============================================================
# Helpers
# ============================================================
def strip_node_warning(text):
    """Remove Node.js TLS warning lines from CLI output"""
    if not text:
        return ""
    lines = text.split('\n')
    return '\n'.join(l for l in lines if not l.strip().startswith('(node:'))


def call_cli(tool, input_data):
    """Call yingmi-skill-cli and return parsed JSON"""
    input_json = json.dumps(input_data, ensure_ascii=False)
    result = subprocess.run(
        CLI + ['mcp', 'call', tool, '--input', input_json],
        capture_output=True, text=True, timeout=60,
        encoding='utf-8', errors='replace'
    )
    clean = strip_node_warning(result.stdout)
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ⚠️ JSON解析失败: {e}")
        if clean:
            print(f"  原始输出前500字: {clean[:500]}")
        return None


# ============================================================
# Phase 1: Get Candidate Funds
# ============================================================
def phase1_get_candidates():
    print("=" * 60)
    print("Phase 1: 获取候选基金池")
    print("=" * 60)

    all_codes = []
    all_search = {}

    for group in ["mix", "bond"]:
        for cat in FUND_CATEGORIES[group]:
            print(f"\n  搜索: {cat}")
            data = call_cli("SearchFunds", {
                "category": cat,
                "size": 30,
                "sortColumn": "收益率",
                "sortOrder": "降序"
            })
            if data and "funds" in data:
                funds = data["funds"]
                print(f"    → 获取 {len(funds)} 只")
                for f in funds:
                    fc = f["fundCode"]
                    if fc not in all_search:
                        all_search[fc] = f
                        all_codes.append(fc)
            else:
                print(f"    → 搜索失败")

    # Save fund info
    fund_info = {fc: all_search[fc] for fc in all_codes}
    info_path = os.path.join(TMPDIR, "fund_info.json")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(fund_info, f, ensure_ascii=False, indent=2)

    codes_path = os.path.join(TMPDIR, "candidate_codes.json")
    with open(codes_path, "w") as f:
        json.dump(all_codes, f)

    print(f"\n  ✅ 候选池合计: {len(all_codes)} 只 (去重后)")
    return all_codes, fund_info


# ============================================================
# Phase 2: Batch Get Performance Data
# ============================================================
def phase2_get_performance(codes):
    print("\n" + "=" * 60)
    print("Phase 2: 批量获取业绩表现")
    print("=" * 60)

    batch_size = 20
    all_perf = {}
    total = len(codes)

    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(f"\n  批次 {batch_num}/{total_batches}: [{batch[0]} ... {batch[-1]}]")

        data = call_cli("GetBatchFundPerformance", {"fundCodes": batch})
        if data:
            for item in data:
                fc = item["fundCode"]
                all_perf[fc] = item.get("data", {})
            print(f"    → 成功: {len(data)} 只")
        else:
            print(f"    → 失败")

    # Save
    perf_path = os.path.join(TMPDIR, "performance.json")
    with open(perf_path, "w", encoding="utf-8") as f:
        json.dump(all_perf, f, ensure_ascii=False)

    print(f"\n  ✅ 获取完成: {len(all_perf)} 只")
    return all_perf


# ============================================================
# Phase 3: Scoring Engine + HTML Report
# ============================================================
def phase3_score_and_report(perf_data, fund_info):
    print("\n" + "=" * 60)
    print("Phase 3: 评分筛选 + 报告生成")
    print("=" * 60)

    required_periods = ["oneWeek", "oneMonth", "quarter", "halfYear", "oneYear"]

    scored = []
    total = len(perf_data)
    screened = 0

    for fc, data in perf_data.items():
        # --- Parse stage returns ---
        stage_returns = {}
        for sr in data.get("stageReturns", []):
            stage_returns[sr["stageType"]] = sr.get("stageReturn")

        # --- Parse metrics ---
        metrics = {}
        for ma in data.get("metricsAnalyzes", []):
            for m in ma.get("metrics", []):
                key = f"{ma['stageType']}_{m['title']}"
                metrics[key] = m

        # --- Screening: all periods must have data and be positive ---
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

        # --- Fetch risk metrics ---
        vol_val = None
        dd_val = None
        sharpe_val = None
        vol_key = "oneYear_抗波动能力"
        dd_key = "oneYear_抗回撤能力"
        sharpe_key = "oneYear_投资性价比"
        rank_key = "oneYear_收益能力"

        if vol_key in metrics:
            vol_val = metrics[vol_key].get("metricsValue")
        if dd_key in metrics:
            dd_val = metrics[dd_key].get("metricsValue")
        if sharpe_key in metrics:
            sharpe_val = metrics[sharpe_key].get("metricsValue")

        # --- Hard filters (淘汰线) ---
        # Fund age filter: 1w return must exist (fund has at least 1 week of data)
        # Volatility must be reasonable: cap at 40%
        vol_abs = abs(vol_val) if vol_val else 0
        if vol_abs > 0.40:
            screened += 1
            continue

        # Max drawdown must be reasonable: cap at 25%
        dd_abs = abs(dd_val) if dd_val else 999
        if dd_abs > 0.25:
            screened += 1
            continue

        # ====== Calculate Scores ======
        # Strategy: favor funds with steady multi-period positive returns
        # and lower volatility/drawdown. Penalize high vol even if returns are good.

        # 1. Return Score (0-30)
        # Weighted: longer periods get higher weight
        weights_sc = [1.0, 1.5, 2.0, 2.5, 3.0]
        weighted_return = sum(r * w for r, w in zip(returns, weights_sc))
        # Normalize: 1y=100% → ~25pts, 1y=50% → ~15pts
        return_score = min(weighted_return * 5, 30)

        # 2. Consistency bonus (0-15)
        # All periods positive AND each longer period > corresponding shorter
        cons_score = 0
        for i in range(1, len(returns)):
            if returns[i] > returns[i-1]:
                cons_score += 3
            elif returns[i] > returns[i-1] * 0.8:
                cons_score += 1
        cons_score = min(cons_score, 15)

        # 3. Volatility Score (0-25)
        if vol_val:
            if vol_val < 0.12: vol_score = 25
            elif vol_val < 0.18: vol_score = 22
            elif vol_val < 0.22: vol_score = 18
            elif vol_val < 0.26: vol_score = 14
            elif vol_val < 0.30: vol_score = 10
            elif vol_val < 0.35: vol_score = 6
            else: vol_score = 3
        else:
            vol_score = 10

        # 4. Drawdown Score (0-20)
        if dd_val:
            dda = abs(dd_val)
            if dda < 0.05: dd_score = 20
            elif dda < 0.08: dd_score = 18
            elif dda < 0.12: dd_score = 15
            elif dda < 0.15: dd_score = 12
            elif dda < 0.18: dd_score = 9
            elif dda < 0.22: dd_score = 6
            else: dd_score = 3
        else:
            dd_score = 8

        # 5. Sharpe Ratio Score (0-10)
        if sharpe_val:
            if sharpe_val >= 5: ss = 10
            elif sharpe_val >= 3: ss = 8
            elif sharpe_val >= 2: ss = 6
            elif sharpe_val >= 1: ss = 4
            elif sharpe_val > 0: ss = 2
            else: ss = 0
        else:
            ss = 4

        total_score = return_score + cons_score + vol_score + dd_score + ss
        total_score = round(min(total_score, 100), 1)

        # Risk rating (1-4, based on vol + dd score: higher = safer)
        risk_raw = vol_score + dd_score
        if risk_raw >= 35:
            risk_level = 1
        elif risk_raw >= 25:
            risk_level = 2
        elif risk_raw >= 15:
            risk_level = 3
        else:
            risk_level = 4

        # Ranking grade
        if total_score >= 72:
            ranking = "优优优优"
            suggestion = "买入"
        elif total_score >= 60:
            ranking = "优良优优"
            suggestion = "买入"
        elif total_score >= 48:
            ranking = "优良优良"
            suggestion = "关注"
        elif total_score >= 35:
            ranking = "优优良良"
            suggestion = "持有"
        else:
            ranking = "良好中中"
            suggestion = "观望"

        fund_name = fund_info.get(fc, {}).get("fundName", fc)

        scored.append({
            "fundCode": fc,
            "fundName": fund_name,
            "score": round(total_score, 1),
            "riskScore": risk_level,
            "ranking": ranking,
            "suggestion": suggestion,
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

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Dedup A/C/E/F share classes: keep only the highest-scoring share class per fund family
    # Detect base fund name by stripping trailing A/B/C/D/E/F/H/I
    import re
    deduped = {}
    for s in scored:
        name = s["fundName"]
        # Strip share class suffix: e.g. "财通收益增强债券A" -> "财通收益增强债券"
        base = re.sub(r'[ABCDEFHI]+$', '', name)
        key = base
        if key not in deduped or s["score"] > deduped[key]["score"]:
            deduped[key] = s
    scored = list(deduped.values())
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Save intermediate
    result_path = os.path.join(TMPDIR, "scored_results.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(scored, f, ensure_ascii=False, indent=2)

    # Summary
    buy_count = sum(1 for s in scored if s["suggestion"] == "买入")
    watch_count = sum(1 for s in scored if s["suggestion"] == "关注")
    hold_count = sum(1 for s in scored if s["suggestion"] in ("持有", "观望"))

    print(f"\n  📊 评分完成")
    print(f"     候选基金: {len(perf_data)} 只")
    print(f"     通过筛选: {len(scored)} 只")
    print(f"     淘汰(负收益/无数据): {screened} 只")
    print(f"     建议买入: {buy_count} 只")
    print(f"     建议关注: {watch_count} 只")

    if scored:
        print(f"\n  🏆 Top 10:")
        for s in scored[:10]:
            print(f"    {s['fundCode']} {s['fundName']}")
            print(f"      评分={s['score']} 风控={s['riskScore']} 排名={s['ranking']} 建议={s['suggestion']}")
            print(f"      1周={s['return1w']} 1月={s['return1m']} 3月={s['return3m']} 6月={s['return6m']} 1年={s['return1y']}")

    # ====== Generate HTML (two sections: mix + bond) ======
    generate_html(scored, fund_info, len(perf_data), buy_count, watch_count, hold_count)

    return scored


def generate_html(scored, fund_info, total_candidates, buy_count, watch_count, hold_count):
    """Generate the final HTML report matching the original project's format"""
    now = datetime.datetime.now()
    date_str = f"{now.year}年{now.month:02d}月{now.day:02d}日"

    def build_section(title, funds_slice, max_rows=10):
        if not funds_slice:
            return f"<h2>{title}</h2><p style='color:#888;padding:10px;'>暂无符合筛选条件的基金</p>"
        rows = ""
        display = funds_slice[:max_rows]
        for i, s in enumerate(display):
            score = s["score"]
            score_pct = min(int(score), 100)
            bar_color = "#e94560" if score >= 72 else "#e9a045" if score >= 48 else "#888"
            last_cls = ' style="background-color: #FF0000; color: white;"' if i == len(display) - 1 else ""
            rows += f"""<tr{last_cls}>
    <td>{s['riskScore']}</td>
    <td><div class="score-bar"><div class="score-bar-inner" style="width:{score_pct}%;background:{bar_color}"></div></div>{score}</td>
    <td>{s['fundCode']}</td>
    <td style="text-align:left">{s['fundName'][:25]}</td>
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
        return f"""<h2>{title} <span class="count-badge">{len(funds_slice)}只</span></h2>
<div class="table-wrap">
<table>
<thead><tr><th>风控</th><th>评分</th><th>代码</th><th style="min-width:160px">基金名称</th><th>排名</th><th>建议</th><th>1周</th><th>1月</th><th>3月</th><th>6月</th><th>1年</th><th>波动率</th><th>最大回撤</th><th>夏普</th><th>同类排名</th><th>链接</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>"""

    # Separate mixed vs bond funds based on fund name keywords
    bond_kw = ["债券", "纯债", "债基"]
    mixed_funds = [s for s in scored if not any(k in s["fundName"] for k in bond_kw)]
    bond_funds = [s for s in scored if any(k in s["fundName"] for k in bond_kw)]

    section_mix = build_section("混合型基金", mixed_funds, 10)
    section_bond = build_section("债券型基金", bond_funds, 10)

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

  .risk-1 {{ color: #27ae60; font-weight: bold; }}
  .risk-2 {{ color: #e9a045; font-weight: bold; }}
  .risk-3 {{ color: #e96045; }}
  .risk-4 {{ color: #e94560; }}

  .score-bar {{ display: inline-block; height: 6px; border-radius: 3px; background: #eee; width: 50px; vertical-align: middle; margin-right: 5px; }}
  .score-bar-inner {{ height: 100%; border-radius: 3px; }}
  .pos {{ color: #e94560; }}
  .neg {{ color: #27ae60; }}

  .footer {{ margin-top: 25px; color: #aaa; font-size: 12px; text-align: center; padding: 20px; }}
  .footer a {{ color: #aaa; text-decoration: none; }}

  .note {{ background: #fff8e1; border-left: 4px solid #f9a825; padding: 12px 16px; margin: 20px 0; border-radius: 4px; font-size: 13px; color: #666; }}
</style>
</head>
<body>
<div class="container">

<h1>
  <span class="badge">安隐基金优选</span>
  <span>每日筛选报告 <span class="date-badge">{date_str}</span></span>
</h1>
<p class="subtitle">筛选逻辑: 多周期收益持续向上 + 低波动率 + 低最大回撤 | 数据源: 盈米基金(且慢)</p>

<div class="summary-bar">
  <div class="summary-card"><div class="num">{total_candidates}</div><div class="label">候选基金</div></div>
  <div class="summary-card buy"><div class="num">{buy_count}</div><div class="label">建议买入</div></div>
  <div class="summary-card watch"><div class="num">{watch_count}</div><div class="label">建议关注</div></div>
  <div class="summary-card hold"><div class="num">{hold_count}</div><div class="label">持有/观望</div></div>
</div>

<div class="note">
  ⚠️ <strong>风险提示</strong>: 本报告基于量化模型自动生成，仅供投资参考，不构成投资建议。
  历史业绩不代表未来表现，投资有风险，决策需谨慎。
  评分模型基于: 多周期收益一致性(35%) + 低波动率(25%) + 低回撤(20%) + 夏普比率(10%) + 趋势加分(10%)。
</div>

{section_mix}
{section_bond}

<div class="footer">
  数据来源: <a href="https://qieman.com" target="_blank">盈米基金(且慢)</a> |
  报告自动生成于 {date_str} |
  <a href="https://github.com/zeusmail/anyin-fund" target="_blank">原项目: zeusmail/anyin-fund</a>
</div>

</div>
</body>
</html>"""

    out_path = os.path.join(OUTDIR, "zmail_recommend.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  ✅ HTML报告: {out_path}")

    # Also save a simple text summary
    summary_path = os.path.join(OUTDIR, "zmail_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"安隐基金优选 - 筛选报告 ({date_str})\n")
        f.write("=" * 50 + "\n\n")
        for s in scored[:10]:
            f.write(f"{s['fundCode']} {s['fundName']:20s}  "
                    f"评分:{s['score']:5.1f}  风控:{s['riskScore']}  "
                    f"{s['ranking']}  {s['suggestion']}\n")
            f.write(f"  1周:{s['return1w']:>7s}  1月:{s['return1m']:>7s}  "
                    f"3月:{s['return3m']:>7s}  6月:{s['return6m']:>7s}  "
                    f"1年:{s['return1y']:>7s}\n")
            f.write(f"  波动:{s['volatility']:>7s}  回撤:{s['maxDrawdown']:>7s}\n\n")
    print(f"  ✅ 文本摘要: {summary_path}")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("安隐基金优选 Pipeline v2")
    print(f"工作目录: {WORKDIR}")
    print("=" * 60)

    # Phase 1
    codes, fund_info = phase1_get_candidates()

    # Phase 2
    perf_data = phase2_get_performance(codes)

    # Phase 3
    scored = phase3_score_and_report(perf_data, fund_info)

    print("\n" + "=" * 60)
    print("🎉 Pipeline 完成!")
    print(f"   报告: {OUTDIR}/zmail_recommend.html")
    print(f"   文本: {OUTDIR}/zmail_summary.txt")
    print(f"   缓存: {TMPDIR}/")
    print("=" * 60)
