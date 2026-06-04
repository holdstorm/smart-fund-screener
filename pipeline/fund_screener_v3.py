#!/usr/bin/env python3
"""
安隐基金优选 Pipeline v3.2 — 多维度TOP50并集 + 持久化缓存
===========================================================
修复:
  - 持久化缓存：每次成功数据保存到 tmp/snapshots/{date}/
  - 缓存穿透保护：API调用失败时自动回退到最近快照
  - 类别分离筛选 (混合型/债券型各自做并集)
  - HTML恢复盈米链接 + 完整CSS

使用: python pipeline/fund_screener_v3.py <mode>
  mode: --fresh   强制从API获取
        --cached  只用缓存（跳过API）
        不传      自动：有缓存用缓存，无缓存从API
"""

import json, subprocess, os, re, datetime, time, sys, shutil

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMPDIR = os.path.join(WORKDIR, "tmp")
SNAPDIR = os.path.join(TMPDIR, "snapshots")
OUTDIR = os.path.join(WORKDIR, "output")
os.makedirs(TMPDIR, exist_ok=True)
os.makedirs(SNAPDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

CLI = [r"C:\Users\xyx\AppData\Roaming\npm\yingmi-skill-cli.cmd"]

FUND_CATEGORIES = {
    "mix":  {"cats": ["偏股混合型", "灵活配置型"],   "size": 150},
    "bond": {"cats": ["纯债", "二级债"],              "size": 100},
}
DIMENSIONS = {
    "oneWeek":"近1周","oneMonth":"近1月","quarter":"近3月",
    "halfYear":"近6月","oneYear":"近1年",
    "twoYear":"近2年","threeYear":"近3年",
}

TODAY = datetime.datetime.now().strftime("%Y%m%d")
SNAPSHOT_DIR = os.path.join(SNAPDIR, TODAY)
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

def strip_warn(text):
    if not text: return ""
    return '\n'.join(l for l in text.split('\n') if not l.strip().startswith('(node:'))

def call_cli(tool, input_data, retries=3):
    ij = json.dumps(input_data, ensure_ascii=False)
    for a in range(retries):
        r = subprocess.run(CLI + ['mcp', 'call', tool, '--input', ij],
            capture_output=True, text=True, timeout=90, encoding='utf-8', errors='replace')
        c = strip_warn(r.stdout)
        try:
            return json.loads(c)
        except:
            if a < retries-1:
                w = (a+1)*3
                print(f"  [重试{a+1}/{retries} 等待{w}s]", end="")
                time.sleep(w)
            else:
                print(f"  [失败: API调用失败]")
                return None

def save_snapshot(data, name):
    """Save a data snapshot and copy to main working file"""
    path = os.path.join(SNAPSHOT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    # Also save to tmp/ for easy access
    tmp_path = os.path.join(TMPDIR, name)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def load_last_good(name):
    """Load the most recent snapshot of a data file, checking today first then going backwards"""
    # Check tmp/ first (most recent run)
    tmp_path = os.path.join(TMPDIR, name)
    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 100:
        try:
            with open(tmp_path) as f: return json.load(f)
        except: pass
    # Check today's snapshot
    snap_path = os.path.join(SNAPSHOT_DIR, name)
    if os.path.exists(snap_path) and os.path.getsize(snap_path) > 100:
        try:
            with open(snap_path) as f: return json.load(f)
        except: pass
    # Check older snapshots (last 7 days)
    dates = sorted([d for d in os.listdir(SNAPDIR) if os.path.isdir(os.path.join(SNAPDIR,d)) and d.isdigit()], reverse=True)
    for d in dates:
        snap_path = os.path.join(SNAPDIR, d, name)
        if os.path.exists(snap_path) and os.path.getsize(snap_path) > 100:
            try:
                with open(snap_path) as f: return json.load(f)
            except: pass
    return None

def validate_data(data):
    """Check if data is valid (has actual fund entries, not empty)"""
    if isinstance(data, dict) and len(data) > 10:
        return True
    if isinstance(data, list) and len(data) > 10:
        return True
    return False

# ========== Phase 1 ==========
def phase1_large_pool(force_fresh=False):
    print("="*60+"\nPhase 1: 大池搜索\n"+"="*60)

    if not force_fresh:
        cached = load_last_good("large_pool.json")
        if cached and validate_data(cached[0] if isinstance(cached, list) else list(cached.keys())):
            info = load_last_good("fund_info.json")
            if info:
                print(f"  使用缓存: {len(cached)} 只")
                return cached, info

    all_codes, all_info = [], []
    for grp, cfg in FUND_CATEGORIES.items():
        for cat in cfg["cats"]:
            sz = cfg["size"]
            print(f"\n  {cat} (TOP {sz})")
            data = call_cli("SearchFunds", {"category":cat, "size":sz,
                "sortColumn":"收益率", "sortOrder":"降序"})
            if data and "funds" in data:
                for f in data["funds"]:
                    if f["fundCode"] not in [x["fundCode"] for x in all_info]:
                        all_info.append(f)
                        all_codes.append(f["fundCode"])
                print(f"    -> {len(data['funds'])} 只")
            else:
                print(f"    -> 搜索失败 (API可能限流)")
                # If this fails, try to fall back to cache
                cached = load_last_good("large_pool.json")
                if cached:
                    print(f"  [回退] 使用缓存数据: {len(cached)} 只")
                    return cached, load_last_good("fund_info.json") or {}

    print(f"\n  [OK] 大池: {len(all_codes)} 只")
    save_snapshot(all_codes, "large_pool.json")
    info_dict = {x["fundCode"]: x for x in all_info}
    save_snapshot(info_dict, "fund_info.json")
    return all_codes, info_dict

# ========== Phase 2 ==========
def phase2_batch_perf(codes, force_fresh=False):
    print("\n"+"="*60+"\nPhase 2: 批量业绩\n"+"="*60)

    if not force_fresh:
        cached = load_last_good("performance.json")
        if cached and validate_data(cached):
            # Check if we have data for all our codes
            missing = [c for c in codes if c not in cached]
            if len(missing) < len(codes) * 0.2:  # If 80%+ coverage, use cache
                print(f"  使用缓存: {len(cached)} 只 (缺失{len(missing)}只)")
                return cached

    all_perf = {}
    total = len(codes)
    failed_batches = 0
    for i in range(0, total, 20):
        batch = codes[i:i+20]
        bn, tb = i//20+1, (total+19)//20
        print(f"  批次 {bn}/{tb}", end="")
        data = call_cli("GetBatchFundPerformance", {"fundCodes": batch})
        if data:
            for it in data:
                all_perf[it["fundCode"]] = it.get("data", {})
            print(f" -> {len(data)} 只")
        else:
            failed_batches += 1
            print(f" -> 失败")
            # On first failure, fall back to cache for missing records
            cached = load_last_good("performance.json")
            if cached:
                for c in batch:
                    if c in cached:
                        all_perf[c] = cached[c]
                print(f"    [回退] 从缓存补充 {sum(1 for c in batch if c in cached)} 只")

    if len(all_perf) == 0:
        cached = load_last_good("performance.json")
        if cached:
            print(f"\n  [回退] 全部API失败，使用缓存: {len(cached)} 只")
            return cached

    print(f"\n  [OK] 获取: {len(all_perf)} 只 (失败{ failed_batches}批)")
    save_snapshot(all_perf, "performance.json")
    return all_perf

# ========== Phase 3 ==========
def phase3_multi_dim_union_by_category(perf_data, fund_info):
    print("\n"+"="*60+"\nPhase 3: 分类别多维度TOP50并集\n"+"="*60)
    fund_returns = {}
    for fc, data in perf_data.items():
        sr = {}
        for s in data.get("stageReturns", []):
            sr[s["stageType"]] = s.get("stageReturn")
        if sr: fund_returns[fc] = sr

    bond_kw = ["债券","纯债","债基"]
    mix_codes = [fc for fc in fund_returns if not any(k in fund_info.get(fc,{}).get("fundName","") for k in bond_kw)]
    bond_codes = [fc for fc in fund_returns if any(k in fund_info.get(fc,{}).get("fundName","") for k in bond_kw)]
    print(f"  混合型: {len(mix_codes)} 只, 债券型: {len(bond_codes)} 只")

    union_all, dim_stats = set(), {}
    for grp_name, grp_codes in [("混合型", mix_codes), ("债券型", bond_codes)]:
        print(f"\n  --- {grp_name} ---")
        for dim, label in DIMENSIONS.items():
            ranked = [(fc, fund_returns[fc].get(dim)) for fc in grp_codes]
            ranked = [(fc, r) for fc, r in ranked if r is not None and r > 0]
            ranked.sort(key=lambda x: x[1], reverse=True)
            top = [fc for fc,_ in ranked[:50]]
            union_all.update(top)
            key = f"{grp_name}_{dim}"
            dim_stats[key] = {"label": f"{grp_name}-{label}", "total": len(ranked), "added": len(top)}
            print(f"    {label}: {len(ranked):3d}只正收益 -> TOP50 -> +{len(top)}")

    print(f"\n  [OK] 并集: {len(union_all)} 只")
    save_snapshot(list(union_all), "union_codes.json")
    save_snapshot(dim_stats, "dim_stats.json")
    return union_all, dim_stats, fund_returns

# ========== Phase 4 ==========
def phase4_deep_scoring(union_codes, fund_returns, perf_data, fund_info):
    print("\n"+"="*60+"\nPhase 4: 深度评分\n"+"="*60)
    required = ["oneWeek","oneMonth","quarter","halfYear","oneYear"]
    scored = []
    sc = {"negative":0, "high_vol":0, "high_dd":0}
    for fc in union_codes:
        data = perf_data.get(fc, {})
        sr = fund_returns.get(fc, {})
        metrics = {}
        for ma in data.get("metricsAnalyzes", []):
            for m in ma.get("metrics", []):
                metrics[f"{ma['stageType']}_{m['title']}"] = m
        returns = []
        skip = False
        for p in required:
            r = sr.get(p)
            if r is None or r <= 0: skip = True; sc["negative"]+=1 if r is not None and r<=0 else 0; break
            returns.append(r)
        if skip or len(returns) < 5: continue
        vv = metrics.get("oneYear_抗波动能力",{}).get("metricsValue")
        dv = metrics.get("oneYear_抗回撤能力",{}).get("metricsValue")
        sv = metrics.get("oneYear_投资性价比",{}).get("metricsValue")
        if vv and abs(vv) > 0.40: sc["high_vol"]+=1; continue
        if dv and abs(dv) > 0.25: sc["high_dd"]+=1; continue
        w = [1,1.5,2,2.5,3]
        rs = min(sum(r*w[i] for i,r in enumerate(returns))*5, 30)
        cs = min(sum(3 if returns[i]>returns[i-1] else 1 if returns[i]>returns[i-1]*0.8 else 0 for i in range(1,len(returns))), 15)
        lt = min(sum(2.5 for bp in ["twoYear","threeYear"] if (r:=sr.get(bp)) and r>0), 5)
        vs = 10
        if vv:
            if vv<0.12: vs=20
            elif vv<0.18: vs=17
            elif vv<0.22: vs=14
            elif vv<0.26: vs=11
            elif vv<0.30: vs=8
            elif vv<0.35: vs=5
            else: vs=3
        dds = 8
        if dv:
            da = abs(dv)
            if da<0.05: dds=15
            elif da<0.08: dds=13
            elif da<0.12: dds=11
            elif da<0.15: dds=9
            elif da<0.18: dds=7
            elif da<0.22: dds=5
            else: dds=3
        ss = 4
        if sv:
            if sv>=5: ss=10
            elif sv>=3: ss=8
            elif sv>=2: ss=6
            elif sv>=1: ss=4
            elif sv>0: ss=2
            else: ss=0
        dc = sum(1 for dim in DIMENSIONS if (r:=sr.get(dim)) and r>0)
        total = round(min(rs+cs+lt+vs+dds+ss+min(dc*0.7,5), 100), 1)
        rl = 1 if vs+dds>=30 else 2 if vs+dds>=22 else 3 if vs+dds>=14 else 4
        if total>=72: rk,sg="优优优优","买入"
        elif total>=60: rk,sg="优良优优","买入"
        elif total>=48: rk,sg="优良优良","关注"
        elif total>=35: rk,sg="优优良良","持有"
        else: rk,sg="良好中中","观望"
        fn = fund_info.get(fc,{}).get("fundName", fc)
        def pct(v): return f"{v*100:.2f}%" if v is not None else ""
        scored.append({
            "fundCode":fc,"fundName":fn,"score":total,"riskScore":rl,
            "ranking":rk,"suggestion":sg,
            "r1w":pct(returns[0]),"r1m":pct(returns[1]),
            "r3m":pct(returns[2]),"r6m":pct(returns[3]),"r1y":pct(returns[4]),
            "r2y":pct(sr.get("twoYear")),"r3y":pct(sr.get("threeYear")),
            "vol":str(metrics.get("oneYear_抗波动能力",{}).get("metricsValueText","") or ""),
            "dd":str(metrics.get("oneYear_抗回撤能力",{}).get("metricsValueText","") or ""),
            "sharpe":str(metrics.get("oneYear_投资性价比",{}).get("metricsValueText","") or ""),
            "rankText":str(metrics.get("oneYear_收益能力",{}).get("rankText","") or ""),
            "dimCount":dc,"link":f"https://qieman.com/funds/{fc}",
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    deduped = {}
    for s in scored:
        base = re.sub(r'[ABCDEFHI]+$', '', s["fundName"])
        if base not in deduped or s["score"] > deduped[base]["score"]:
            deduped[base] = s
    scored = list(deduped.values())
    scored.sort(key=lambda x: x["score"], reverse=True)
    save_snapshot(scored, "scored_results.json")
    bc = sum(1 for s in scored if s["suggestion"]=="买入")
    wc = sum(1 for s in scored if s["suggestion"]=="关注")
    hc = sum(1 for s in scored if s["suggestion"] in ("持有","观望"))
    print(f"\n  [OK] 评分完成: {len(scored)} 只 (去重)")
    for k,v in sc.items():
        if v: print(f"     淘汰-{k}: {v} 只")
    print(f"     买入: {bc} | 关注: {wc} | 观望: {hc}")
    if scored:
        print(f"\n  Top 10:")
        for s in scored[:10]:
            print(f"    {s['fundCode']} {s['fundName'][:22]}  评分={s['score']} {s['ranking']} {s['suggestion']}")
    return scored, bc, wc, hc

# ========== Phase 5 ==========
def phase5_reports(scored, union_codes, perf_data, fund_info, dim_stats, bc, wc, hc):
    print("\n"+"="*60+"\nPhase 5: 报告生成\n"+"="*60)
    now = datetime.datetime.now()
    ds = f"{now.year}年{now.month:02d}月{now.day:02d}日"

    # Check and report cache source
    cache_info = ""
    snap_path = os.path.join(SNAPSHOT_DIR, "performance.json")
    if os.path.exists(snap_path) and os.path.getsize(snap_path) > 100:
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(snap_path))
        cache_info = f' | <span style="color:#888;font-size:11px">数据快照: {mtime.strftime("%m-%d %H:%M")}</span>'

    def section(title, funds, mx=15):
        if not funds: return f"""<h2>{title} <span class="cb">0只</span></h2><div class="tw"><p style="padding:20px;color:#888;text-align:center;">暂无符合筛选条件的基金</p></div>"""
        rows = ""
        for i, s in enumerate(funds[:mx]):
            sc = s["score"]; scp = min(int(sc),100)
            bc = "#e94560" if sc>=72 else "#e9a045" if sc>=48 else "#888"
            lc = ' style="background-color:#FF0000;color:#fff"' if i == min(len(funds)-1,mx-1) else ""
            rows += f"""<tr{lc}>
<td>{s['riskScore']}</td>
<td><div class="sb"><div class="sbi" style="width:{scp}%;background:{bc}"></div></div>{sc}</td>
<td>{s['fundCode']}</td>
<td style="text-align:left">{s['fundName'][:25]}</td>
<td class="rk-{s['ranking']}">{s['ranking']}</td>
<td class="sg-{s['suggestion']}">{s['suggestion']}</td>
<td class="up">{s['r1w']}</td><td class="up">{s['r1m']}</td><td class="up">{s['r3m']}</td><td class="up">{s['r6m']}</td><td class="up">{s['r1y']}</td>
<td>{s['r2y']}</td><td>{s['r3y']}</td>
<td>{s['vol']}</td><td>{s['dd']}</td><td>{s['sharpe']}</td>
<td style="font-size:11px">{s['rankText']}</td>
<td><a href="{s['link']}" target="_blank" style="color:#e94560;text-decoration:none;">详情</a></td>
</tr>"""
        return f"""<h2>{title} <span class="cb">{len(funds)}只</span></h2>
<div class="tw"><table>
<thead><tr><th>风控</th><th>评分</th><th>代码</th><th style="min-width:150px">基金名称</th><th>排名</th><th>建议</th><th>1周</th><th>1月</th><th>3月</th><th>6月</th><th>1年</th><th>2年</th><th>3年</th><th>波动率</th><th>最大回撤</th><th>夏普</th><th>同类排名</th><th>链接</th></tr></thead>
<tbody>{rows}</tbody></table></div>"""

    bond_kw = ["债券","纯债","债基"]
    mix = [s for s in scored if not any(k in s["fundName"] for k in bond_kw)]
    bnd = [s for s in scored if any(k in s["fundName"] for k in bond_kw)]
    sm = section("混合型基金", mix)
    sb = section("债券型基金", bnd)
    dr = "".join(f"<tr><td>{v['label']}</td><td>{v['total']}</td><td>{v['added']}</td></tr>\n" for k,v in sorted(dim_stats.items()))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>安隐基金优选 - 多维度TOP50并集筛选报告</title>
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
table{{width:100%;border-collapse:collapse;min-width:1300px}}
th{{background:#1a1a2e;color:#fff;padding:10px 6px;font-size:11px;text-align:center;white-space:nowrap}}
td{{padding:9px 6px;text-align:center;font-size:12px;border-bottom:1px solid #f0f0f0}}
tr:hover{{background:#f8faff}}
.rk-优优优优{{color:#e94560;font-weight:700}}
.rk-优良优优{{color:#e96045;font-weight:700}}
.rk-优良优良{{color:#e9a045;font-weight:700}}
.sg-买入{{color:#e94560;font-weight:700}}
.sg-关注{{color:#e9a045;font-weight:700}}
.sg-持有{{color:#2d6a9f}}
.sb{{display:inline-block;height:5px;border-radius:3px;background:#eee;width:40px;vertical-align:middle;margin-right:4px}}
.sbi{{height:100%;border-radius:3px}}
.up{{color:#e94560}}
.note{{background:#fff8e1;border-left:4px solid #f9a825;padding:10px 14px;margin:15px 0;border-radius:4px;font-size:12px;color:#666}}
.ft{{margin-top:20px;color:#aaa;font-size:11px;text-align:center;padding:20px}}
.mb{{background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.06);padding:20px;margin:20px 0;font-size:13px;line-height:1.8}}
.mb h3{{color:#1a1a2e;font-size:16px;margin:0 0 10px}}
.mb table{{min-width:auto;width:auto}}
.mb th,.mb td{{padding:6px 12px;font-size:12px}}
</style></head>
<body><div class="c">
<h1><span class="bd">安隐基金优选</span>多维度TOP50并集筛选 <span class="db">{ds}</span>{cache_info}</h1>
<div class="sb">
  <div class="sc"><div class="nm">{len(perf_data)}</div><div class="lb">大池搜索</div></div>
  <div class="sc"><div class="nm">{len(union_codes)}</div><div class="lb">多维度并集</div></div>
  <div class="sc"><div class="nm" style="color:#e94560">{bc}</div><div class="lb">建议买入</div></div>
  <div class="sc"><div class="nm" style="color:#e9a045">{wc}</div><div class="lb">建议关注</div></div>
</div>
<div class="note">⚠️ <strong>风险提示</strong>: 本报告基于量化模型自动生成，仅供投资参考，不构成投资建议。历史业绩不代表未来表现。</div>
{sm}{sb}
<div class="mb">
<h3>筛选方法</h3>
<p><b>1. 大池</b>: 偏股混合/灵活配置各TOP150 + 纯债/二级债各TOP100 = {len(perf_data)}只</p>
<p><b>2. 分类别多维度并集</b>: 混合/债券分别排名，7个维度各取TOP50：</p>
<table><tr><th>类别-维度</th><th>正收益</th><th>入并集</th></tr>{dr}</table>
<p><b>3. 评分(100分)</b>: 收益(30)+一致性(15)+长期(5)+波动(20)+回撤(15)+夏普(10)+覆盖(5)</p>
<p><b>4. 淘汰</b>: 负收益 | 波动>40% | 回撤>25% | A/C去重</p>
<p><b>排名</b>: ≥72优优优优(买入) | 60-71优良优优(买入) | 48-59优良优良(关注) | 35-47优优良良(持有) | <35良好中中(观望)</p>
</div>
<div class="ft">数据: 盈米基金(且慢) | {ds}</div>
</div></body></html>"""
    hp = os.path.join(OUTDIR, "zmail_recommend_multi_dim.html")
    with open(hp,"w",encoding="utf-8") as f: f.write(html)
    hp2 = os.path.join(WORKDIR, "zmail_recommend.html")
    with open(hp2,"w",encoding="utf-8") as f: f.write(html)
    print(f"  [OK] 报告: {hp}")

    sp = os.path.join(OUTDIR, "zmail_summary_multi_dim.txt")
    with open(sp,"w",encoding="utf-8") as f:
        f.write(f"安隐基金优选 - 多维度TOP50并集筛选 ({ds})\n")
        f.write("="*60+"\n数据缓存: tmp/snapshots/{TODAY}/\n\n")
        f.write(f"大池: {len(perf_data)}只 | 并集: {len(union_codes)}只 | 通过: {len(scored)}只\n")
        f.write(f"买入: {bc} | 关注: {wc} | 观望: {hc}\n\n")
        for s in scored[:15]:
            f.write(f"{s['fundCode']} {s['fundName'][:22]}  评分={s['score']} {s['ranking']} {s['suggestion']}\n")
            f.write(f"  1w={s['r1w']} 1m={s['r1m']} 3m={s['r3m']} 6m={s['r6m']} 1y={s['r1y']}  2y={s['r2y']} 3y={s['r3y']}\n")
            f.write(f"  波动={s['vol']} 回撤={s['dd']} 夏普={s['sharpe']}\n\n")
    print(f"  [OK] 摘要: {sp}")
    print(f"  [OK] 缓存: {SNAPSHOT_DIR}/")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    force_fresh = mode == "--fresh"

    print(f"{'='*60}")
    print(f"安隐基金优选 Pipeline v3.2")
    print(f"模式: {'强制刷新API' if force_fresh else '自动缓存优先'}")
    print(f"缓存: {SNAPSHOT_DIR}")
    print(f"{'='*60}")

    codes, info = phase1_large_pool(force_fresh)
    if len(codes) == 0:
        print("\n[错误] 无法获取基金列表，API可能限流或无缓存。明天再试。")
        sys.exit(1)

    perf = phase2_batch_perf(codes, force_fresh)
    if len(perf) == 0:
        print("\n[错误] 无法获取业绩数据，API可能限流或无缓存。明天再试。")
        sys.exit(1)

    union, dstats, fret = phase3_multi_dim_union_by_category(perf, info)
    if len(union) == 0:
        print("\n[警告] 并集为空，没有基金通过多维度初筛。生成报告可能无数据。")

    scored, bc, wc, hc = phase4_deep_scoring(union, fret, perf, info)
    phase5_reports(scored, union, perf, info, dstats, bc, wc, hc)

    print(f"\n{'='*60}")
    print(f"完成! 缓存位置: tmp/snapshots/{TODAY}/")
    print(f"下次运行: python pipeline/fund_screener_v3.py")
    print(f"强制刷新: python pipeline/fund_screener_v3.py --fresh")
    print(f"{'='*60}")
