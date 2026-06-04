#!/usr/bin/env python3
"""
天天基金 Skills API 完整筛选管线
用 FUND_CONDITION_SELECT 替代盈米Skills的数据采集
"""
import json, os, subprocess, sys, uuid, re, datetime

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(WORKDIR, "tmp")
OUTDIR = os.path.join(WORKDIR, "output")
os.makedirs(TMP, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

API_URL = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke"
API_KEY = os.environ.get("TTFUND_APIKEY") or os.environ.get("TTFUND_API_KEY", "")

# Fund type codes for condition select
FUND_TYPES = {
    "mix": "1103",     # 混合型
    "bond": "1106",    # 债券型
}

# OrderField mappings for different time dimensions
# Format: A_B_C where A=category, B=field_id, C=direction(-1=desc)
ORDER_FIELDS = {
    "oneMonth": "5_3_-1",   # 近1月 (猜测)
    "halfYear": "5_4_-1",   # 近6月 (猜测)
    "oneYear":  "5_6_-1",   # 近1年 (已验证)
    "threeYear":"5_7_-1",   # 近3年 (猜测)
}

def call_api(skill_id, version, params):
    if not API_KEY:
        print("  [API Error] Missing TTFUND_APIKEY environment variable.")
        return None
    body = json.dumps({"skill_id": skill_id, "_skill_version": version, "params": params})
    bodyfile = os.path.join(TMP, f"tf_req_{uuid.uuid4().hex[:8]}.json")
    outfile = os.path.join(TMP, f"tf_resp_{uuid.uuid4().hex[:8]}.json")
    with open(bodyfile, "w", encoding="utf-8") as f: f.write(body)
    subprocess.run(
        f'curl -s -X POST "{API_URL}" -H "X-API-Key: {API_KEY}" -H "Content-Type: application/json" -d @"{bodyfile}" > "{outfile}"',
        shell=True, timeout=30
    )
    try: os.remove(bodyfile)
    except: pass
    if os.path.exists(outfile):
        with open(outfile, "r", encoding="utf-8") as f: content = f.read()
        try: os.remove(outfile)
        except: pass
        try: return json.loads(content)
        except: return None
    return None

def get_funds_by_condition(order_field, fund_type, page_size=50):
    """按条件获取基金列表"""
    r = call_api("FUND_CONDITION_SELECT", "1.1.0", {
        "orderField": order_field, "pageIndex": 0, "pageNum": page_size,
        "rsfType": fund_type
    })
    if not r or r.get("code") != 0: return []
    raw = r.get("data", {}).get("raw_result", {})
    body = raw.get("body", {})
    data = body.get("Data", [])
    return data if isinstance(data, list) else []

print("="*60)
print("天天基金 Skills 基金筛选管线")
print("="*60)

# Step 1: Multi-dimension UNION across fund types
print("\n[Step 1] 多维度TOP50并集")
all_codes = set()
fund_info = {}

for group_name, fund_type in FUND_TYPES.items():
    print(f"\n  {group_name} (rsfType={fund_type}):")
    for dim_name, order_field in ORDER_FIELDS.items():
        funds = get_funds_by_condition(order_field, fund_type, 50)
        print(f"    {dim_name}: {len(funds)} 只")
        for f in funds:
            fc = f.get("fundCode")
            if fc:
                all_codes.add(fc)
                if fc not in fund_info:
                    fund_info[fc] = f

print(f"\n  并集合计: {len(all_codes)} 只")

# Step 2: For each fund, extract available metrics
print(f"\n[Step 2] 数据整理与评分")

required_periods = ["oneMonth", "halfYear", "oneYear", "threeYear"]
period_keys = {"oneMonth": "hySyl", "halfYear": "sySyl", "oneYear": "yearSyl", "threeYear": "trySyl"}

scored = []
eliminated = 0

for fc in all_codes:
    info = fund_info.get(fc, {})
    fund_name = info.get("fundName", fc)
    
    # Parse returns
    returns = []
    skip = False
    for p in required_periods:
        key = period_keys[p]
        val_str = info.get(key)
        if val_str is None:
            skip = True
            break
        try:
            val = float(val_str) / 100.0  # Convert from percentage string
            if val <= 0:
                skip = True
                break
            returns.append(val)
        except:
            skip = True
            break
    
    if skip or len(returns) < 4:
        eliminated += 1
        continue
    
    # Simple scoring (matching original algorithm)
    w = [1.5, 2.0, 2.5, 3.0]
    rs = min(sum(r*w[i] for i,r in enumerate(returns))*5, 30)
    
    cs = sum(3 if returns[i]>returns[i-1] else 1 if returns[i]>returns[i-1]*0.8 else 0 for i in range(1,len(returns)))
    cs = min(cs, 15)
    
    total = round(min(rs+cs+20+15+10, 100), 1)  # Default mid-scores for vol/dd/sharpe
    
    rl = 3
    if total >= 72: rk, sg = "优优优优", "买入"
    elif total >= 60: rk, sg = "优良优优", "买入"
    elif total >= 48: rk, sg = "优良优良", "关注"
    elif total >= 35: rk, sg = "优优良良", "持有"
    else: rk, sg = "良好中中", "观望"
    
    scored.append({
        "fundCode": fc, "fundName": fund_name,
        "score": total, "riskScore": rl,
        "ranking": rk, "suggestion": sg,
        "r1m": f"{returns[0]*100:.2f}%",
        "r6m": f"{returns[1]*100:.2f}%",
        "r1y": f"{returns[2]*100:.2f}%",
        "r3y": f"{returns[3]*100:.2f}%",
        "link": f"https://fund.eastmoney.com/{fc}.html",
        "size": info.get("fundSize", ""),
        "company": info.get("company", ""),
        "daySyl": info.get("daySyl", ""),
    })

# Dedup shares
deduped = {}
for s in scored:
    base = re.sub(r'[ABCDEFH]+$', '', s["fundName"])
    if base not in deduped or s["score"] > deduped[base]["score"]:
        deduped[base] = s
scored = list(deduped.values())
scored.sort(key=lambda x: x["score"], reverse=True)

bc = sum(1 for s in scored if s["suggestion"]=="买入")
wc = sum(1 for s in scored if s["suggestion"]=="关注")

print(f"\n  并集: {len(all_codes)} 只")
print(f"  淘汰(无数据/负收益): {eliminated} 只")
print(f"  通过评分: {len(scored)} 只")
print(f"  买入: {bc} | 关注: {wc}")

if scored:
    print(f"\n  Top 10:")
    for s in scored[:10]:
        print(f"    {s['fundCode']} {s['fundName'][:22]}  评分={s['score']} {s['ranking']} {s['suggestion']}")
        print(f"      1月={s['r1m']} 6月={s['r6m']} 1年={s['r1y']} 3年={s['r3y']}")

# Step 3: HTML Report
print(f"\n[Step 3] 报告生成")
ds = datetime.datetime.now().strftime("%Y年%m月%d日")

bond_kw = ["债券","纯债","债基"]
mix = [s for s in scored if not any(k in s["fundName"] for k in bond_kw)]
bnd = [s for s in scored if any(k in s["fundName"] for k in bond_kw)]

def section(title, funds, mx=15):
    if not funds: return f"<h2>{title}</h2><p style='padding:20px;color:#888'>暂无</p>"
    rows = ""
    for i,s in enumerate(funds[:mx]):
        lc = ' style="background:#F00;color:#fff"' if i == min(len(funds)-1,mx-1) else ""
        rows += f"""<tr{lc}>
<td>{s['riskScore']}</td>
<td>{s['score']}</td>
<td>{s['fundCode']}</td>
<td style="text-align:left">{s['fundName'][:22]}</td>
<td class="rk">{s['ranking']}</td>
<td class="sg">{s['suggestion']}</td>
<td>{s['r1m']}</td><td>{s['r6m']}</td><td>{s['r1y']}</td><td>{s['r3y']}</td>
<td>{s.get('daySyl','')}</td>
<td>{s.get('company','')}</td>
<td><a href="{s['link']}" target="_blank" style="color:#e94560;">详情</a></td>
</tr>"""
    return f"""<h2>{title}<span style="color:#888;font-size:13px;margin-left:8px">{len(funds)}只</span></h2>
<div class="tw"><table>
<thead><tr><th>风控</th><th>评分</th><th>代码</th><th>名称</th><th>排名</th><th>建议</th><th>1月</th><th>6月</th><th>1年</th><th>3年</th><th>日涨跌</th><th>公司</th><th>链接</th></tr></thead>
<tbody>{rows}</tbody></table></div>"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>天天基金 Skills - 基金筛选报告</title>
<style>
body{{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#f5f7fa;margin:0;padding:20px}}
.c{{max-width:1400px;margin:0 auto}}
h1{{color:#1a1a2e;font-size:22px}}
.sb{{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}}
.sc{{background:#fff;padding:12px 18px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,.06);flex:1;text-align:center;min-width:100px}}
.sc .nm{{font-size:26px;font-weight:700;color:#1a1a2e}}
.sc .lb{{color:#888;font-size:12px}}
.tw{{background:#fff;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,.06);overflow-x:auto;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;min-width:900px}}
th{{background:#1a1a2e;color:#fff;padding:8px 6px;font-size:11px;text-align:center;white-space:nowrap}}
td{{padding:8px 6px;text-align:center;font-size:12px;border-bottom:1px solid #f0f0f0}}
tr:hover{{background:#f8faff}}
.rk{{color:#e94560;font-weight:700}}
.sg{{color:#e94560;font-weight:700}}
h2{{font-size:17px;margin:20px 0 8px}}
.ft{{margin-top:16px;color:#aaa;font-size:11px;text-align:center}}
</style></head>
<body><div class="c">
<h1>天天基金 Skills - 基金筛选报告 <span style="color:#888;font-size:13px;font-weight:400">{ds}</span></h1>
<div class="sb">
  <div class="sc"><div class="nm">{len(all_codes)}</div><div class="lb">并集池</div></div>
  <div class="sc"><div class="nm" style="color:#e94560">{bc}</div><div class="lb">建议买入</div></div>
  <div class="sc"><div class="nm" style="color:#e9a045">{wc}</div><div class="lb">关注</div></div>
  <div class="sc"><div class="nm" style="color:#888">{eliminated}</div><div class="lb">淘汰</div></div>
</div>
{section("混合型基金", mix)}
{section("债券型基金", bnd)}
<div class="ft">数据: 天天基金 Skills | {ds}</div>
</div></body></html>"""

hp = os.path.join(OUTDIR, "ttfund_recommend.html")
with open(hp, "w", encoding="utf-8") as f: f.write(html)
print(f"\n[OK] 报告: {hp}")
