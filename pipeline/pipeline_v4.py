#!/usr/bin/env python3
"""
天天基金Skills + 盈米Skills 混合筛选管线 v4
===========================================
策略:
  1) 天天基金 FUND_CONDITION_SELECT → 多维度TOP50并集
  2) 天天基金 FUND_BASE_INFOS → 每只基金完整数据(多周期收益/规模/经理/风险)
  3) 使用盈米格式的评分模型(含夏普/波动/回撤近似)
  4) 输出与原zmail_recommend.html格式一致的报告

注意: FUND_CONDITION_SELECT params放在"params"下
      FUND_BASE_INFOS params放在顶层(不嵌套)
"""
import json, os, subprocess, sys, uuid, re, datetime

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(WORKDIR, "tmp"); OUT = os.path.join(WORKDIR, "output")
os.makedirs(TMP, exist_ok=True); os.makedirs(OUT, exist_ok=True)

API_KEY = os.environ.get("TTFUND_APIKEY") or os.environ.get("TTFUND_API_KEY", "")
API_URL = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke"

# ConditionSelect: params in "params" | BaseInfos: params at top level
def call_skill(skill_id, version, params, params_nested=True):
    if not API_KEY:
        print("  [API Error] Missing TTFUND_APIKEY environment variable.")
        return None
    body = json.dumps({"skill_id": skill_id, "_skill_version": version,
                       "params": params} if params_nested else {**{"skill_id": skill_id, "_skill_version": version}, **params})
    bf = os.path.join(TMP, f"tr_{uuid.uuid4().hex[:8]}.json")
    of = os.path.join(TMP, f"to_{uuid.uuid4().hex[:8]}.json")
    with open(bf, "w", encoding="utf-8") as f: f.write(body)
    subprocess.run(f'curl.exe -s -X POST "{API_URL}" -H "X-API-Key: {API_KEY}" -H "Content-Type: application/json" -d @"{bf}" > "{of}"',
                   shell=True, timeout=30)
    try: os.remove(bf)
    except: pass
    if os.path.exists(of):
        with open(of, "r", encoding="utf-8") as f:
            try: return json.loads(f.read())
            except: pass
        try: os.remove(of)
        except: pass
    return None

def cond_select(order_field, fund_type, page_size=50):
    r = call_skill("FUND_CONDITION_SELECT", "1.1.0",
                   {"orderField": order_field, "pageIndex": 0, "pageNum": page_size, "rsfType": fund_type},
                   params_nested=True)
    if not r or r.get("code") != 0: return []
    raw = r.get("data", {}).get("raw_result", {}).get("body", {})
    data = raw.get("Data", [])
    return data if isinstance(data, list) else []

def get_base(fcode):
    """获取基金完整数据 - params在顶层"""
    r = call_skill("FUND_BASE_INFOS", "1.2.0",
                   {"fcode": fcode, "nav_range": "y"}, params_nested=False)
    if not r or r.get("code") != 0: return None
    raw = r.get("data", {}).get("raw_result", {}).get("body", {})
    data = raw.get("data", [])
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    if isinstance(data, dict):
        return data
    return None

def parse_return(v):
    """Parse percentage string to decimal"""
    if v is None: return None
    try: return float(v) / 100.0
    except: return None

# ====== Step 1: Multi-dimension Union ======
print("="*60)
print("Step 1: 多维度TOP50并集 (天天基金)")
print("="*60)

FUND_TYPES = {"mix": "1103", "bond": "1106"}
ORDER_FIELDS = {"oneMonth": "5_3_-1", "halfYear": "5_4_-1",
                "oneYear": "5_6_-1", "threeYear": "5_7_-1"}

all_codes = set()
cond_data = {}  # fc -> condition select info

for grp, ftype in FUND_TYPES.items():
    print(f"  {grp}:")
    for dim, of in ORDER_FIELDS.items():
        funds = cond_select(of, ftype, 50)
        print(f"    {dim}: {len(funds)} 只")
        for f in funds:
            fc = f.get("fundCode")
            if fc:
                all_codes.add(fc)
                if fc not in cond_data:
                    cond_data[fc] = f

print(f"\n  并集: {len(all_codes)} 只")

# ====== Step 2: Get full details ======
print(f"\nStep 2: 获取基金完整数据 (FUND_BASE_INFOS)")
print("="*60)

fund_base = {}
failed = 0
for i, fc in enumerate(all_codes):
    print(f"  {i+1}/{len(all_codes)}: {fc}", end="")
    info = get_base(fc)
    if info:
        fund_base[fc] = info
        print(f" {info.get('SHORTNAME','')[:20]}")
    else:
        failed += 1
        print(" [失败]")

print(f"\n  成功: {len(fund_base)} 失败: {failed}")

# ====== Step 3: Scoring ======
print(f"\nStep 3: 评分")
print("="*60)

SYL_MAP = {
    "oneWeek": None,    # Not available from this API
    "oneMonth": "SYL_Y",
    "quarter": "SYL_3Y",
    "halfYear": "SYL_6Y",
    "oneYear": "SYL_1N",
    "twoYear": "SYL_2N",
    "threeYear": "SYL_3N",
}
REQUIRED = ["oneMonth", "quarter", "halfYear", "oneYear"]

scored = []
elim = {"no_data": 0, "neg": 0, "high_vol": 0, "high_dd": 0}

for fc in fund_base:
    b = fund_base[fc]
    name = b.get("SHORTNAME", fc)
    ftype = b.get("FTYPE", "")

    # Parse returns
    returns = []
    skip = False
    for p in REQUIRED:
        v = parse_return(b.get(SYL_MAP[p]))
        if v is None:
            skip = True
            elim["no_data"] += 1
            break
        if v <= 0:
            skip = True
            elim["neg"] += 1
            break
        returns.append(v)
    if skip: continue

    # Get 1w and 2y/3y (optional)
    r1w = parse_return(b.get("SYL_1W"))  # might not exist
    r2y = parse_return(b.get("SYL_2N"))
    r3y = parse_return(b.get("SYL_3N"))
    r1m, r3m, r6m, r1y = returns[0], returns[1], returns[2], returns[3]

    # Risk metrics: use manager tenure max drawdown as proxy
    exp = b.get("expansion", {})
    ci = exp.get("comprehensive_info", {})
    mi = ci.get("manager_information", {})
    hm = mi.get("historyManagerInfos", [])
    max_retra = None
    for h in hm:
        si = h.get("SINFO", {})
        mr = si.get("MAXRETRA")
        if mr is not None:
            max_retra = abs(mr)
            break

    # Volatility proxy: use 3-year return magnitude as rough proxy
    # Lower 3y return relative to 1y = more volatile (unstable)
    vol_proxy = None
    if r1y and r3y and r3y > 0:
        vol_proxy = abs(r1y - r3y / 3) * 0.5  # rough estimate
    else:
        vol_proxy = abs(r1y) * 0.3 if r1y else 0.25

    # Hard filter: if drawdown is severe
    if max_retra and max_retra > 0.25:
        elim["high_dd"] += 1
        continue

    # === Scoring ===
    w = [1.5, 2.0, 2.5, 3.0]
    rs = min(sum(r*w[i] for i,r in enumerate(returns)) * 5, 30)

    cs = sum(3 if returns[i]>returns[i-1] else 1 if returns[i]>returns[i-1]*0.8 else 0 for i in range(1,len(returns)))
    cs = min(cs, 15)

    lt = 0
    if r2y and r2y > 0: lt += 2.5
    if r3y and r3y > 0: lt += 2.5
    lt = min(lt, 5)

    # Vol score (0-20)
    vs = 20
    if vol_proxy:
        if vol_proxy > 0.35: vs = 5
        elif vol_proxy > 0.30: vs = 8
        elif vol_proxy > 0.25: vs = 11
        elif vol_proxy > 0.20: vs = 14
        elif vol_proxy > 0.15: vs = 17

    # DD score (0-15)
    dds = 15
    if max_retra:
        if max_retra > 0.20: dds = 4
        elif max_retra > 0.15: dds = 7
        elif max_retra > 0.12: dds = 9
        elif max_retra > 0.10: dds = 11
        elif max_retra > 0.08: dds = 13

    # Sharpe proxy (0-10): use ratio of return to vol
    ss = 6  # default mid
    if vol_proxy and r1y and vol_proxy > 0:
        sharpe_proxy = r1y / vol_proxy
        if sharpe_proxy >= 5: ss = 10
        elif sharpe_proxy >= 3: ss = 8
        elif sharpe_proxy >= 2: ss = 6
        elif sharpe_proxy >= 1: ss = 4
        elif sharpe_proxy > 0: ss = 2

    # Multi-dim coverage
    dc = sum(1 for dim in ORDER_FIELDS if parse_return(b.get(SYL_MAP.get(dim))) and parse_return(b.get(SYL_MAP.get(dim))) > 0)
    md = min(dc * 0.7, 5)

    total = round(min(rs+cs+lt+vs+dds+ss+md, 100), 1)

    rl = 1 if vs+dds>=30 else 2 if vs+dds>=22 else 3 if vs+dds>=14 else 4
    if total >= 72: rk, sg = "优优优优", "买入"
    elif total >= 60: rk, sg = "优良优优", "买入"
    elif total >= 48: rk, sg = "优良优良", "关注"
    elif total >= 35: rk, sg = "优优良良", "持有"
    else: rk, sg = "良好中中", "观望"

    # Manager name
    mgr = ""
    for h in hm:
        si = h.get("SINFO", {})
        if si.get("FCode") == fc:
            mgr = si.get("MgrName", "")
            break

    scored.append({
        "fundCode": fc, "fundName": name,
        "score": total, "riskScore": rl,
        "ranking": rk, "suggestion": sg,
        "r1w": f"{r1w*100:.2f}%" if r1w else "",
        "r1m": f"{r1m*100:.2f}%", "r3m": f"{r3m*100:.2f}%",
        "r6m": f"{r6m*100:.2f}%", "r1y": f"{r1y*100:.2f}%",
        "r2y": f"{r2y*100:.2f}%" if r2y else "",
        "r3y": f"{r3y*100:.2f}%" if r3y else "",
        "manager": mgr,
        "ftype": ftype,
        "size": b.get("JJGS", ""),
        "nav": b.get("DWJZ", ""),
        "link": f"https://qieman.com/funds/{fc}",
        "daySyl": b.get("RZDF", ""),
    })

# Dedup
deduped = {}
for s in scored:
    base = re.sub(r'[ABCDEFH]+$', '', s["fundName"])
    if base not in deduped or s["score"] > deduped[base]["score"]:
        deduped[base] = s
scored = list(deduped.values())
scored.sort(key=lambda x: x["score"], reverse=True)

bc = sum(1 for s in scored if s["suggestion"]=="买入")
wc = sum(1 for s in scored if s["suggestion"]=="关注")
hc = sum(1 for s in scored if s["suggestion"] in ("持有","观望"))

print(f"\n  并集池: {len(all_codes)} 只")
print(f"  获取详情: {len(fund_base)} 只")
print(f"  通过评分: {len(scored)} 只")
for k,v in elim.items():
    if v: print(f"  淘汰-{k}: {v}")
print(f"  买入: {bc} 关注: {wc} 观望: {hc}")

if scored:
    print(f"\n  Top 10:")
    for s in scored[:10]:
        print(f"    {s['fundCode']} {s['fundName'][:22]}  {s['ranking']} {s['suggestion']} 评分={s['score']}")
        print(f"      1月={s['r1m']} 6月={s['r6m']} 1年={s['r1y']} 经理={s['manager']}")

# ====== Step 4: HTML Report ======
print(f"\nStep 4: 报告生成")
ds = datetime.datetime.now().strftime("%Y年%m月%d日")

bond_kw = ["债券","纯债","债基","中短债","长债"]
mix = [s for s in scored if not any(k in s["fundName"] for k in bond_kw)]
bnd = [s for s in scored if any(k in s["fundName"] for k in bond_kw)]

def section(title, funds, mx=20):
    if not funds:
        return f"<h2>{title} <span class='cb'>0只</span></h2><div class='tw'><p style='padding:20px;color:#888;text-align:center'>暂无</p></div>"
    rows = ""
    for i,s in enumerate(funds[:mx]):
        sc = s["score"]; scp = min(int(sc),100)
        bc = "#e94560" if sc>=72 else "#e9a045" if sc>=48 else "#888"
        lc = ' style="background-color:#FF0000;color:#fff"' if i == min(len(funds)-1,mx-1) else ""
        rows += f"""<tr{lc}>
<td>{s['riskScore']}</td>
<td><span class="sb"><span class="sbi" style="width:{scp}%;background:{bc}"></span></span>{sc}</td>
<td>{s['fundCode']}</td>
<td style="text-align:left">{s['fundName'][:25]}</td>
<td class="rk">{s['ranking']}</td>
<td class="sg">{s['suggestion']}</td>
<td class="up">{s['r1w']}</td><td class="up">{s['r1m']}</td><td class="up">{s['r3m']}</td><td class="up">{s['r6m']}</td><td class="up">{s['r1y']}</td>
<td>{s['r2y']}</td><td>{s['r3y']}</td>
<td>{s['manager']}</td>
<td>{s.get('size','')}</td>
<td style="font-size:11px">{s.get('daySyl','')}</td>
<td><a href="{s['link']}" target="_blank" style="color:#e94560;text-decoration:none;">详情</a></td>
</tr>"""
    return f"""<h2>{title} <span class="cb">{len(funds)}只</span></h2>
<div class="tw"><table>
<thead><tr>
<th>风控</th><th>评分</th><th>代码</th><th style="min-width:160px">基金名称</th><th>排名</th><th>建议</th>
<th>1周</th><th>1月</th><th>3月</th><th>6月</th><th>1年</th><th>2年</th><th>3年</th>
<th>经理</th><th>公司</th><th>日涨跌</th><th>链接</th>
</tr></thead>
<tbody>{rows}</tbody></table></div>"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>基金筛选报告 - 多维度TOP50并集</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,'Microsoft YaHei','PingFang SC',sans-serif;background:#f5f7fa;margin:0;padding:20px}}
.c{{max-width:1600px;margin:0 auto}}
h1{{color:#1a1a2e;font-size:24px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
h1 .bd{{background:#e94560;color:#fff;padding:4px 12px;border-radius:20px;font-size:14px}}
.db{{color:#888;font-size:14px;font-weight:400}}
.sb{{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}}
.sc{{background:#fff;padding:15px 20px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.06);flex:1;text-align:center;min-width:120px}}
.sc .nm{{font-size:28px;font-weight:700;color:#1a1a2e}}
.sc .lb{{color:#888;font-size:13px}}
h2{{color:#1a1a2e;font-size:18px;margin:25px 0 10px;display:flex;align-items:center;gap:8px}}
h2 .cb{{background:#eee;color:#666;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:400}}
.tw{{background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow-x:auto;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;min-width:1200px}}
th{{background:#1a1a2e;color:#fff;padding:10px 6px;font-size:11px;text-align:center;white-space:nowrap}}
td{{padding:9px 6px;text-align:center;font-size:12px;border-bottom:1px solid #f0f0f0}}
tr:hover{{background:#f8faff}}
.rk{{color:#e94560;font-weight:700}}
.sg{{color:#e94560;font-weight:700}}
.up{{color:#e94560}}
.sb{{display:inline-block;height:5px;border-radius:3px;background:#eee;width:40px;vertical-align:middle;margin-right:4px}}
.sbi{{height:100%;border-radius:3px}}
.note{{background:#fff8e1;border-left:4px solid #f9a825;padding:10px 14px;margin:15px 0;border-radius:4px;font-size:12px;color:#666}}
.ft{{margin-top:20px;color:#aaa;font-size:11px;text-align:center;padding:20px}}
</style></head>
<body><div class="c">
<h1><span class="bd">基金筛选报告</span>天天基金Skills × 多维度TOP50并集 <span class="db">{ds}</span></h1>
<div class="sb">
  <div class="sc"><div class="nm">{len(all_codes)}</div><div class="lb">多维度并集</div></div>
  <div class="sc"><div class="nm">{len(fund_base)}</div><div class="lb">获取详情</div></div>
  <div class="sc"><div class="nm" style="color:#e94560">{bc}</div><div class="lb">建议买入</div></div>
  <div class="sc"><div class="nm" style="color:#e9a045">{wc}</div><div class="lb">建议关注</div></div>
</div>
<div class="note">⚠️ 风险提示: 基于量化模型自动生成,不构成投资建议。评分方法: 多周期收益(30)+一致性(15)+长期正收益(5)+低波动(20)+低回撤(15)+夏普近似(10)+多维覆盖(5)=100分。收益率数据来源天天基金,波动/回撤为近似估算。</div>
{section("混合型基金", mix)}
{section("债券型基金", bnd)}
<div class="ft">数据: 天天基金 Skills | 盈米基金(且慢) 数据次日可用 | {ds}</div>
</div></body></html>"""

rp = os.path.join(OUT, "zmail_recommend.html")
with open(rp, "w", encoding="utf-8") as f: f.write(html)
print(f"\n[OK] 报告: {rp}")
print(f"\n{'='*60}\n完成!\n{'='*60}")
