#!/usr/bin/env python3
"""
基金筛选管线 v5 — 干净的数据输出 + 专业前端
============================================
输入: 天天基金 Skills API (FUND_CONDITION_SELECT + FUND_BASE_INFOS)
输出:
  1. output/zmail_recommend.html  — 完整可排序的HTML报告
  2. output/zmail_funds_data.json — 结构化JSON数据(供调试/复用)

改进点(v4→v5):
  - 输出包含回撤/波动/夏普全部指标字段
  - 债券基金评分标准独立化（允许低正收益）
  - 移除盈米链接依赖
  - 前端模板分离, 数据注入式渲染
"""

import json, os, subprocess, sys, uuid, re, datetime

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(WORKDIR, "tmp")
OUT = os.path.join(WORKDIR, "output")
os.makedirs(TMP, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

API_KEY = os.environ.get("TTFUND_APIKEY") or os.environ.get("TTFUND_API_KEY", "")
API_URL = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke"


# ============================================================
# API Layer
# ============================================================
def call_skill(skill_id, version, params, params_nested=True):
    """Call TTFund skill API. Returns parsed JSON or None."""
    if not API_KEY:
        print("  [API Error] Missing TTFUND_APIKEY environment variable.")
        return None
    body = json.dumps(
        {"skill_id": skill_id, "_skill_version": version, "params": params}
        if params_nested else
        {**{"skill_id": skill_id, "_skill_version": version}, **params}
    )
    bf = os.path.join(TMP, f"req_{uuid.uuid4().hex[:8]}.json")
    of = os.path.join(TMP, f"resp_{uuid.uuid4().hex[:8]}.json")
    try:
        with open(bf, "w", encoding="utf-8") as f:
            f.write(body)
        subprocess.run(
            f'curl.exe -s -X POST "{API_URL}" '
            f'-H "X-API-Key: {API_KEY}" '
            f'-H "Content-Type: application/json" '
            f'-d @"{bf}" > "{of}"',
            shell=True, timeout=30
        )
        if os.path.exists(of):
            with open(of, "r", encoding="utf-8") as f:
                r = json.loads(f.read())
                return r if r.get("code") == 0 else None
    except Exception as e:
        print(f"  [API Error] {e}")
    finally:
        for fp in [bf, of]:
            try:
                os.remove(fp)
            except OSError:
                pass
    return None


def cond_select(order_field, fund_type, page_size=50):
    """FUND_CONDITION_SELECT: params nested under 'params'."""
    r = call_skill("FUND_CONDITION_SELECT", "1.1.0",
                   {"orderField": order_field, "pageIndex": 0,
                    "pageNum": page_size, "rsfType": fund_type},
                   params_nested=True)
    if not r:
        return []
    raw = r.get("data", {}).get("raw_result", {}).get("body", {})
    data = raw.get("Data", [])
    return data if isinstance(data, list) else []


def get_base_info(fcode):
    """FUND_BASE_INFOS: params at top level (NOT nested)."""
    r = call_skill("FUND_BASE_INFOS", "1.2.0",
                   {"fcode": fcode, "nav_range": "y"},
                   params_nested=False)
    if not r:
        return None
    raw = r.get("data", {}).get("raw_result", {}).get("body", {})
    data = raw.get("data", [])
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


# ============================================================
# Data Parsing Helpers
# ============================================================

# SYL field mapping: internal key → TTFund FUND_BASE_INFOS field name
SYL_MAP = {
    "oneWeek": "SYL_1W",
    "oneMonth": "SYL_Y",
    "quarter": "SYL_3Y",
    "halfYear": "SYL_6Y",
    "oneYear": "SYL_1N",
    "twoYear": "SYL_2N",
    "threeYear": "SYL_3N",
}

# CS field mapping: internal key → FUND_CONDITION_SELECT field name
CS_SYL_MAP = {
    "oneWeek": None,        # CS doesn't have 1-week
    "oneMonth": None,       # CS doesn't have 1-month explicitly
    "quarter": None,        # CS doesn't have quarter
    "halfYear": "hySyl",    # 近半年
    "oneYear": "yearSyl",   # 近1年
    "twoYear": None,        # CS doesn't have 2-year
    "threeYear": "trySyl",  # 近3年
}


def parse_pct(raw_val):
    """Parse percentage string like '31.31%' → 0.3131 float, or None."""
    if raw_val is None or raw_val == '':
        return None
    try:
        s = str(raw_val).strip().rstrip('%')
        return float(s) / 100.0
    except (ValueError, TypeError):
        return None


def fmt_pct(val, decimals=2):
    """Format decimal back to percentage string. None → ''."""
    if val is None:
        return ''
    return f"{val * 100:.{decimals}f}%"


def cs_to_base_info(cs_record):
    """
    Convert FUND_CONDITION_SELECT record to FUND_BASE_INFOS-compatible format.
    This is the FALLBACK when Base Infos API fails.
    CS fields: fundCode, fundName, ftype, riskLevel, company,
               daySyl, sySyl(今年), hySyl(半年), yearSyl(1年), trySyl(3年),
               fundSize, establishDate
    """
    ftype = cs_record.get("ftype", "")

    # Map CS return fields to Base Info SYL_* format
    # CS percentages are already in "xx.xx" string format (not decimal)
    info = {
        "SHORTNAME": cs_record.get("fundName", ""),
        "FTYPE": ftype,
        "RISERANK": cs_record.get("riskLevel", ""),
        "COMPANY": cs_record.get("company", ""),
        "FUND_SIZE": cs_record.get("fundSize"),
        # Day return
        "SYL_1W": cs_record.get("daySyl", ""),
        # This year
        "SYL_Y": "",  # CS doesn't separate 1M
        "SYL_3Y": "",  # CS doesn't have 3M
        "SYL_6Y": cs_record.get("hySyl", ""),   # 半年
        "SYL_1N": cs_record.get("yearSyl", ""),  # 1年
        "SYL_2N": "",  # no 2Y in CS
        "SYL_3N": cs_record.get("trySyl", ""),   # 3年
        "_source": "cs_fallback",
    }

    # Estimate 1M from daySyl * 20 (rough) or use hySyl / 6
    hy = parse_pct(cs_record.get("hySyl", ""))
    if hy is not None:
        info["SYL_Y"] = str(round(hy / 6 * 100, 2))      # ~1M estimate
        info["SYL_3Y"] = str(round(hy / 2 * 100, 2))       # ~3M estimate

    # Estimate 2Y from 1Y and 3Y
    y1 = parse_pct(cs_record.get("yearSyl", ""))
    y3 = parse_pct(cs_record.get("trySyl", ""))
    if y1 is not None and y3 is not None:
        est_2y = (y1 * 2 + y3 / 1.5) / 2  # weighted interpolation
        info["SYL_2N"] = str(round(est_2y * 100, 2))

    return info


def extract_manager_info(expansion):
    """Extract manager info from expansion.comprehensive_info block."""
    result = {"name": "", "max_drawdown": None}
    try:
        ci = (expansion or {}).get("comprehensive_info", {})
        mi = ci.get("manager_information", {})
        hm = mi.get("historyManagerInfos", [])
        for h in hm:
            si = h.get("SINFO", {})
            mr = si.get("MAXRETRA")
            if mr is not None:
                result["max_drawdown"] = abs(float(mr))
            mgr = si.get("MgrName", "")
            if mgr and not result["name"]:
                result["name"] = mgr
    except (TypeError, AttributeError, ValueError):
        pass
    return result


# ============================================================
# Scoring Engine (v5: separated equity vs bond criteria)
# ============================================================

def score_equity_fund(b, returns_map):
    """
    Score an equity/mixed fund.
    Returns (score_dict | None, elimination_reason | None)

    Scoring weights (100 total):
      multi_period_return 30  consistency 15  long_term_positive 5
      low_volatility     20  low_drawdown    15  sharpe_proxy   10
      multi_dim_coverage  5
    """
    required = ["oneMonth", "quarter", "halfYear", "oneYear"]
    returns = []
    for p in required:
        v = returns_map.get(p)
        if v is None:
            return None, "数据缺失"
        if v <= 0:
            return None, "存在负收益"
        returns.append(v)

    r1w = returns_map.get("oneWeek")
    r2y = returns_map.get("twoYear")
    r3y = returns_map.get("threeYear")
    r1m, r3m, r6m, r1y = returns[0], returns[1], returns[2], returns[3]

    # --- Risk Metrics ---
    mgr_info = extract_manager_info(b.get("expansion"))
    max_dd = mgr_info["max_drawdown"]

    # Volatility proxy: deviation of 1Y from annualized 3Y
    vol_proxy = None
    if r1y is not None and r3y is not None and r3y > 0:
        vol_proxy = abs(r1y - r3y / 3) * 0.5
    elif r1y is not None:
        vol_proxy = abs(r1y) * 0.3
    else:
        vol_proxy = 0.25

    # Hard filter: severe drawdown
    if max_dd is not None and max_dd > 0.25:
        return None, f"回撤过大({max_dd*100:.1f}%)"

    # --- Scoring Components ---
    # 1. Multi-period Return (0-30)
    w = [1.5, 2.0, 2.5, 3.0]
    rs = min(sum(r * w[i] for i, r in enumerate(returns)) * 5, 30)

    # 2. Consistency (0-15): each period better than previous
    cs = sum(
        3 if returns[i] > returns[i - 1]
        else 1 if returns[i] > returns[i - 1] * 0.8
        else 0
        for i in range(1, len(returns))
    )
    cs = min(cs, 15)

    # 3. Long-term positive reward (0-5)
    lt = 0
    if r2y is not None and r2y > 0:
        lt += 2.5
    if r3y is not None and r3y > 0:
        lt += 2.5
    lt = min(lt, 5)

    # 4. Volatility Score (0-20): lower is better
    vs = 20
    if vol_proxy is not None:
        if vol_proxy > 0.35: vs = 5
        elif vol_proxy > 0.30: vs = 8
        elif vol_proxy > 0.25: vs = 11
        elif vol_proxy > 0.20: vs = 14
        elif vol_proxy > 0.15: vs = 17

    # 5. Drawdown Score (0-15): lower is better
    dds = 15
    if max_dd is not None:
        if max_dd > 0.20: dds = 4
        elif max_dd > 0.15: dds = 7
        elif max_dd > 0.12: dds = 9
        elif max_dd > 0.10: dds = 11
        elif max_dd > 0.08: dds = 13

    # 6. Sharpe Proxy (0-10)
    ss = 6
    if vol_proxy and r1y and vol_proxy > 0:
        sp = r1y / vol_proxy
        if sp >= 5: ss = 10
        elif sp >= 3: ss = 8
        elif sp >= 2: ss = 6
        elif sp >= 1: ss = 4
        elif sp > 0: ss = 2

    # 7. Multi-dimension coverage (0-5)
    dc = sum(1 for k in SYL_MAP
             if returns_map.get(k) is not None and returns_map.get(k, 0) > 0)
    md = min(dc * 0.7, 5)

    total = round(min(rs + cs + lt + vs + dds + ss + md, 100), 1)

    # Risk Level (1=best, 4=worst)
    rl = 1 if (vs + dds) >= 30 else 2 if (vs + dds) >= 22 \
        else 3 if (vs + dds) >= 14 else 4

    # Rating & Suggestion
    if total >= 72:
        rk, sg = "优优优优", "买入"
    elif total >= 60:
        rk, sg = "优良优优", "买入"
    elif total >= 48:
        rk, sg = "优良优良", "关注"
    elif total >= 35:
        rk, sg = "优优良良", "持有"
    else:
        rk, sg = "良好中中", "观望"

    return {
        "score": total,
        "riskLevel": rl,
        "ranking": rk,
        "suggestion": sg,
        # Raw metric values for display
        "drawdown": fmt_pct(max_dd),
        "volatility": fmt_pct(vol_proxy),
        "sharpe": round(sp, 2) if vol_proxy and r1y and vol_proxy > 0 else None,
        "_breakdown": {
            "return_score": round(rs, 1), "consistency": cs,
            "long_term": round(lt, 1), "volatility_score": vs,
            "drawdown_score": dds, "sharpe_score": ss,
            "coverage": round(md, 1)
        }
    }, None


def score_bond_fund(b, returns_map):
    """
    Score a bond fund — relaxed criteria.
    Bond funds: allow low positive returns; no hard negative filter.
    Use lower thresholds appropriate for fixed income.
    """
    required = ["oneMonth", "halfYear", "oneYear"]
    returns = []
    for p in required:
        v = returns_map.get(p)
        if v is None:
            return None, "数据缺失"
        # Bond funds can have near-zero returns; only skip if very negative
        if v < -0.05:  # Allow up to -5%
            return None, f"亏损过大({v*100:.1f}%)"
        returns.append(v)

    r1w = returns_map.get("oneWeek")
    r2y = returns_map.get("twoYear")
    r3y = returns_map.get("threeYear")
    r1m, r6m, r1y = returns[0], returns[1], returns[2]

    # Risk metrics
    mgr_info = extract_manager_info(b.get("expansion"))
    max_dd = mgr_info["max_drawdown"]

    vol_proxy = None
    if r1y is not None and r3y is not None and r3y > 0:
        vol_proxy = abs(r1y - r3y / 3) * 0.3
    elif r1y is not None:
        vol_proxy = abs(r1y) * 0.2
    else:
        vol_proxy = 0.10

    # Hard filter
    if max_dd is not None and max_dd > 0.08:  # Bond tolerance: 8%
        return None, f"回撤过大({max_dd*100:.1f}%)"

    # Bond-specific scoring (same categories, different scale expectations)
    w = [1.0, 1.5, 2.0]  # Lighter weight on returns for bonds
    rs = min(sum(r * w[i] for i, r in enumerate(returns)) * 8, 30)

    cs = 10  # Default consistency bonus for stable bond funds
    if len(returns) >= 2 and returns[-1] >= returns[0]:
        cs = 15  # Upward trend bonus

    lt = 5  # Bonds usually have long track records
    if r3y is not None and r3y <= 0:
        lt = 2

    vs = 18  # Bonds generally low volatility
    if vol_proxy is not None:
        if vol_proxy > 0.15: vs = 8
        elif vol_proxy > 0.10: vs = 12
        elif vol_proxy > 0.06: vs = 16

    dds = 14  # Bonds generally low drawdown
    if max_dd is not None:
        if max_dd > 0.05: dds = 6
        elif max_dd > 0.03: dds = 10

    # Sharpe proxy for bonds (return/vol)
    ss = 6
    if vol_proxy and r1y and vol_proxy > 0:
        sp = r1y / vol_proxy
        if sp >= 3: ss = 10
        elif sp >= 1.5: ss = 8
        elif sp >= 0.8: ss = 6
        elif sp > 0: ss = 4

    dc = sum(1 for k in SYL_MAP
             if returns_map.get(k) is not None and returns_map.get(k, 0) > 0)
    md = min(dc * 0.7, 5)

    total = round(min(rs + cs + lt + vs + dds + ss + md, 100), 1)

    rl = 1 if (vs + dds) >= 28 else 2 if (vs + dds) >= 22 \
        else 3 if (vs + dds) >= 16 else 4

    if total >= 70:
        rk, sg = "优优优优", "买入"
    elif total >= 55:
        rk, sg = "优良优优", "买入"
    elif total >= 42:
        rk, sg = "优良优良", "关注"
    elif total >= 30:
        rk, sg = "优优良良", "持有"
    else:
        rk, sg = "良好中中", "观望"

    return {
        "score": total,
        "riskLevel": rl,
        "ranking": rk,
        "suggestion": sg,
        "drawdown": fmt_pct(max_dd),
        "volatility": fmt_pct(vol_proxy),
        "sharpe": round(sp, 2) if vol_proxy and r1y and vol_proxy > 0 else None,
        "_breakdown": {
            "return_score": round(rs, 1), "consistency": cs,
            "long_term": round(lt, 1), "volatility_score": vs,
            "drawdown_score": dds, "sharpe_score": ss,
            "coverage": round(md, 1)
        }
    }, None


# ============================================================
# Main Pipeline
# ============================================================

def main():
    print("=" * 64)
    print("基金筛选管线 v5")
    print("=" * 64)

    # ---- Step 1: Condition Select — Multi-dim TOP50 union ----
    print("\n[Step 1] 多维度TOP50并集 (天天基金 FUND_CONDITION_SELECT)")
    print("-" * 50)

    FUND_TYPES = {"mix": "1103", "bond": "1106"}
    ORDER_FIELDS = {
        "oneMonth": "5_3_-1",
        "halfYear": "5_4_-1",
        "oneYear": "5_6_-1",
        "threeYear": "5_7_-1",
    }

    all_codes = set()
    code_type_map = {}  # fc -> 'mix' | 'bond'
    cs_data = {}       # fc -> full Condition Select record (for fallback)

    for grp, ftype in FUND_TYPES.items():
        print(f"\n  [{grp}] rsfType={ftype}:")
        for dim, of in ORDER_FIELDS.items():
            funds = cond_select(of, ftype, 50)
            count = len(funds) if funds else 0
            print(f"    {dim}: {count} 只")
            for f in (funds or []):
                fc = f.get("fundCode")
                if fc:
                    all_codes.add(fc)
                    code_type_map[fc] = grp
                    cs_data[fc] = f  # Store full record for fallback

    print(f"\n  并集总计: {len(all_codes)} 只")

    # ---- Step 2: Base Info Fetching ----
    print(f"\n[Step 2] 获取完整数据 (FUND_BASE_INFOS)")
    print("-" * 50)

    fund_base = {}
    failed_codes = []

    for i, fc in enumerate(sorted(all_codes)):
        sys.stdout.write(f"  [{i+1}/{len(all_codes)}] {fc} ... ")
        info = get_base_info(fc)
        if info:
            fund_base[fc] = info
            print(f"{info.get('SHORTNAME', '?')[:25]}")
        elif fc in cs_data:
            # FALLBACK: use Condition Select data
            fb = cs_to_base_info(cs_data[fc])
            fund_base[fc] = fb
            print(f"[Fallback-CS] {fb.get('SHORTNAME', '?')[:25]}")
        else:
            failed_codes.append(fc)
            print("[失败]")

    print(f"\n  成功: {len(fund_base)}  失败: {len(failed_codes)}")
    if failed_codes:
        print(f"  失败代码: {failed_codes[:10]}{'...' if len(failed_codes)>10 else ''}")

    # ---- Step 3: Scoring ----
    print(f"\n[Step 3] 评分计算")
    print("-" * 50)

    elim_stats = {"no_data": 0, "neg_return": 0, "high_dd": 0, "other": 0}
    scored_mix = []
    scored_bond = []

    for fc in fund_base:
        b = fund_base[fc]
        name = b.get("SHORTNAME", fc)
        # Use ACTUAL FTYPE from API response, not query source
        ftype_raw = b.get("FTYPE", "")
        # Classify: FTYPE field from TTFund
        # Common FTYPE values: 混合型/股票型/债券型/ETF etc.
        if any(k in ftype_raw for k in ["债券", "债"]):
            ftype = "bond"
        elif any(k in ftype_raw for k in ["混合", "股票", "QDII", "ETF", "指数"]):
            ftype = "mix"
        else:
            # Fallback: check fund name
            if any(k in name for k in ["债券", "纯债", "中短债", "长债"]):
                ftype = "bond"
            else:
                ftype = "mix"  # default to equity/mixed

        # Sanity: if returns look like equity (>30% in 1Y), force mix
        r1y_check = parse_pct(b.get("SYL_1N"))
        if r1y_check and r1y_check > 0.30:
            ftype = "mix"

        # Parse all return periods
        returns_map = {}
        for key, field in SYL_MAP.items():
            returns_map[key] = parse_pct(b.get(field))

        # Choose scorer based on type
        if ftype == "bond":
            result, reason = score_bond_fund(b, returns_map)
            target_list = scored_bond
        else:
            result, reason = score_equity_fund(b, returns_map)
            target_list = scored_mix

        if result is None:
            if reason:
                if "数据缺失" in reason:
                    elim_stats["no_data"] += 1
                elif "负" in reason or "亏损" in reason:
                    elim_stats["neg_return"] += 1
                elif "回撤" in reason:
                    elim_stats["high_dd"] += 1
                else:
                    elim_stats["other"] += 1
            continue

        # Build record with ALL fields for frontend
        is_fallback = b.get("_source") == "cs_fallback"
        record = {
            "fundCode": fc,
            "fundName": name,
            "ftype": ftype,
            **result,
            # Returns
            "r1w": fmt_pct(returns_map.get("oneWeek")),
            "r1m": fmt_pct(returns_map.get("oneMonth")),
            "r3m": fmt_pct(returns_map.get("quarter")),
            "r6m": fmt_pct(returns_map.get("halfYear")),
            "r1y": fmt_pct(returns_map.get("oneYear")),
            "r2y": fmt_pct(returns_map.get("twoYear")),
            "r3y": fmt_pct(returns_map.get("threeYear")),
            # Manager & Company
            "manager": extract_manager_info(b.get("expansion"))["name"] if not is_fallback else "",
            "size": b.get("COMPANY", b.get("JJGS", "")),
            # Daily change — use SYL_1W or daySyl
            "daySyl": parse_pct(b.get("SYL_1W", b.get("daySyl", "0"))) or 0,
        }
        if is_fallback:
            record["manager"] = ""
        target_list.append(record)

    # Dedup by base name (remove A/C suffix)
    def dedup(records):
        seen = {}
        for s in records:
            base = re.sub(r'[ABCDEFH]+$', '', s["fundName"])
            if base not in seen or s["score"] > seen[base]["score"]:
                seen[base] = s
        return list(seen.values())

    scored_mix = dedup(scored_mix)
    scored_bond = dedup(scored_bond)

    # Sort by score desc
    scored_mix.sort(key=lambda x: x["score"], reverse=True)
    scored_bond.sort(key=lambda x: x["score"], reverse=True)

    # Summary counts
    bc = sum(1 for s in scored_mix if s["suggestion"] == "买入")
    wc = sum(1 for s in scored_mix if s["suggestion"] == "关注")
    bc_bond = sum(1 for s in scored_bond if s["suggestion"] == "买入")
    wc_bond = sum(1 for s in scored_bond if s["suggestion"] == "关注")

    # Count by type
    mix_in_pool = sum(1 for c, t in code_type_map.items() if t == 'mix' and c in fund_base)
    bond_in_pool = sum(1 for c, t in code_type_map.items() if t == 'bond' and c in fund_base)

    print(f"\n  混合型: 入池={mix_in_pool} "
          f"评分通过={len(scored_mix)}  买入={bc} 关注={wc}")
    print(f"  债券型: 入池={bond_in_pool} "
          f"评分通过={len(scored_bond)}  买入={bc_bond} 关注={wc_bond}")

    for k, v in elim_stats.items():
        if v:
            print(f"  淘汰-{k}: {v}")

    if scored_mix:
        print(f"\n  混合型 Top 5:")
        for s in scored_mix[:5]:
            bd = s.get("_breakdown", {})
            print(f"    {s['fundCode']} {s['fundName'][:22]:22s}  "
                  f"{s['ranking']} {s['suggestion']:4s}  "
                  f"分={s['score']:5.1f}  回撤={s['drawdown'] or '-':>6}  "
                  f"夏普={str(s['sharpe']) if s['sharpe'] else '-':>5}")

    if scored_bond:
        print(f"\n  债券型 Top 5:")
        for s in scored_bond[:5]:
            print(f"    {s['fundCode']} {s['fundName'][:22]:22s}  "
                  f"{s['ranking']} {s['suggestion']:4s}  分={s['score']:5.1f}")

    # ---- Step 4: Output (JSON only) ----
    print(f"\n[Step 4] 生成报告")
    print("-" * 50)

    ds = datetime.datetime.now().strftime("%Y年%m月%d日")

    # Prepare summary object
    summary = {
        "total": len(all_codes),
        "detail": len(fund_base),
        "buy": bc + bc_bond,
        "watch": wc + wc_bond,
        "date": ds,
        "elimination": elim_stats,
        "mix_count": len(scored_mix),
        "bond_count": len(scored_bond),
    }

    # Clean data for output (remove internal keys)
    def clean_for_output(record):
        return {k: v for k, v in record.items() if not k.startswith("_")}

    # Save JSON data file
    full_data = {
        "summary": summary,
        "mix_funds": [clean_for_output(s) for s in scored_mix],
        "bond_funds": [clean_for_output(s) for s in scored_bond],
        "generated_at": ds,
    }
    json_output_path = os.path.join(OUT, "zmail_funds_data.json")
    with open(json_output_path, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)
    print(f"  JSON数据: {json_output_path} ({len(scored_mix)}混基, {len(scored_bond)}债基)")

    # Generate complete HTML report (data inline, NO template replacement)
    html_output_path = os.path.join(OUT, "zmail_recommend.html")
    _generate_html_report(html_output_path, full_data)

    # ---- Validation ----
    print("\n[验证] 检查输出完整性...")
    _validate_output(html_output_path, json_output_path, len(scored_mix), len(scored_bond))

    print(f"\n{'='*64}\n完成!\n{'='*64}")


def _generate_html_report(output_path, data):
    """
    Generate a COMPLETE self-contained HTML report.
    Data is injected directly as JS variables — no template placeholders, no replacement.
    This eliminates the entire class of template-replacement bugs.
    """

def _generate_html_report(output_path, data):
    """Generate complete self-contained HTML with data inlined — no template replacement."""
    summary = data["summary"]
    mix = data["mix_funds"]
    bond = data["bond_funds"]
    mix_js = json.dumps(mix, ensure_ascii=False)
    bond_js = json.dumps(bond, ensure_ascii=False)
    summary_js = json.dumps(summary, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>基金筛选报告 - {summary.get('date', '')}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;color:#1a1a2e;padding:20px;line-height:1.5;}}
.header{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:24px;}}
.badge{{display:inline-block;padding:6px 18px;border-radius:20px;color:#fff;font-size:15px;font-weight:700;}}
.badge-pink{{background:linear-gradient(135deg,#e94560,#c0392b);}}
.header-meta{{color:#666;font-size:14px;}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px;}}
.stat-card{{background:#fff;border-radius:12px;padding:20px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06);}}
.stat-num{{font-size:36px;font-weight:800;line-height:1.2;}}
.stat-label{{font-size:13px;color:#888;margin-top:4px;}}
.c-buy{{color:#e94560;}}.c-watch{{color:#f59e0b;}}.c-total{{color:#1a1a2e;}}
.banner{{background:#fef3cd;border-left:4px solid #f59e0b;padding:14px 20px;border-radius:8px;margin-bottom:24px;font-size:13px;color:#856404;line-height:1.6;}}
.section{{margin-bottom:28px;}}
.section-title{{font-size:17px;font-weight:700;margin-bottom:10px;color:#1a1a2e;}}
.section-count{{display:inline-block;background:#1a1a2e;color:#fff;font-size:11px;padding:2px 10px;border-radius:10px;margin-left:8px;}}
.table-wrap{{overflow-x:auto;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08);background:#fff;}}
table{{width:100%;border-collapse:collapse;min-width:1200px;}}
thead{{background:linear-gradient(135deg,#1a1a2e,#16213e);}}
th{{padding:12px 10px;text-align:center;font-size:12px;font-weight:600;color:#ddd;white-space:nowrap;cursor:pointer;user-select:none;letter-spacing:.5px;text-transform:uppercase;}}
th:hover{{background:rgba(255,255,255,.1);}}
td{{padding:12px 10px;text-align:center;font-size:13px;border-bottom:1px solid #eeeef2;white-space:nowrap;}}
tr:nth-child(even){{background:#fafbfc;}}tr:hover{{background:#f0f4ff;}}
.fund-name{{text-align:left!important;min-width:160px;font-weight:500;color:#1a1a2e;max-width:220px;overflow:hidden;text-overflow:ellipsis;}}
.company-name{{text-align:left!important;color:#555;}}
.val-pos{{color:#e94560;font-weight:600;}}.val-neg{{color:#00b894;font-weight:600;}}.val-neutral{{color:#888;}}
.metric-good{{color:#00b894;font-weight:600;}}.metric-warn{{color:#f59e0b;font-weight:600;}}.metric-bad{{color:#e94560;font-weight:600;}}
.tag-buy{{display:inline-block;background:#e94560;color:#fff;padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600;}}
.tag-watch{{display:inline-block;background:#f59e0b;color:#fff;padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600;}}
.tag-rank{{display:inline-block;background:#00b894;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;}}
.tag-rank-mid{{display:inline-block;background:#6c757d;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;}}
.risk-dot{{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;font-size:11px;font-weight:700;color:#fff;}}
.risk-1{{background:#00b894;}}.risk-2{{background:#f59e0b;}}.risk-3{{background:#e17055;}}.risk-4{{background:#e94560;}}
.score-bar{{display:inline-block;width:50px;height:6px;border-radius:3px;background:#eee;overflow:hidden;vertical-align:middle;margin-left:6px;}}
.score-fill{{height:100%;border-radius:3px;transition:width .3s;}}
.empty-state{{padding:30px;text-align:center;color:#9aa0a6;font-size:14px;}}
.empty-stage{{padding:40px;text-align:center;color:#9aa0a6;font-size:14px;background:#fff;border-radius:10px;}}
.daily-chg{{font-family:"SF Mono",Monaco,monospace;font-size:12px;font-weight:500;}}
.footer{{text-align:center;color:#aaa;font-size:12px;padding:20px;margin-top:20px;}}
</style>
</head>
<body>
<div class="header">
  <span class="badge badge-pink">基金筛选报告</span>
  <span style="font-size:18px;font-weight:700;color:#1a1a2e;">多维度TOP50并集</span>
  <span class="header-meta">{summary.get('date', '')}</span>
</div>
<div class="stats">
  <div class="stat-card" id="card-total"><div class="stat-num c-total" id="stat-total">{summary.get('total', 0)}</div><div class="stat-label">多维度并集</div></div>
  <div class="stat-card" id="card-detail"><div class="stat-num c-total" id="stat-detail">{summary.get('detail', 0)}</div><div class="stat-label">获取详情</div></div>
  <div class="stat-card" id="card-buy"><div class="stat-num c-buy" id="stat-buy">{summary.get('buy', 0)}</div><div class="stat-label">建议买入</div></div>
  <div class="stat-card" id="card-watch"><div class="stat-num c-watch" id="stat-watch">{summary.get('watch', 0)}</div><div class="stat-label">建议关注</div></div>
</div>
<div class="banner">
⚠️ 风险提示：基于量化模型自动生成，不构成投资建议。评分方法（100分制）：多周期收益 + 一致性(15) + 长期正收益(5) + 低波动(20) + 低回撤(15) + 夏普近似(10) + 多维覆盖(5)。收益率数据来自天天基金，波动/回测为近似估算。
</div>
<div class="section" id="sec-mix">
  <div class="section-title">混合型基金<span class="section-count" id="count-mix">--只</span></div>
  <div class="table-wrap" id="table-mix-container">
    <table id="table-mix"><thead><tr>
      <th data-col="riskLevel" data-type="int">风险</th><th data-col="score" data-type="float">评分</th>
      <th data-col="fundCode" data-type="string">代码</th><th data-col="fundName" data-type="string">基金名称</th>
      <th data-col="ranking" data-type="string">评级</th><th data-col="suggestion" data-type="string">建议</th>
      <th data-col="r1w" data-type="pct">1周↕</th><th data-col="r1m" data-type="pct">1月↕</th>
      <th data-col="r3m" data-type="pct">3月↕</th><th data-col="r6m" data-type="pct">6月↕</th>
      <th data-col="r1y" data-type="pct">1年↕</th><th data-col="r2y" data-type="pct">2年↕</th>
      <th data-col="r3y" data-type="pct">3年↕</th><th data-col="drawdown" data-type="pct">最大回撤↕</th>
      <th data-col="volatility" data-type="pct">波动近似↕</th><th data-col="sharpe" data-type="float">夏普近似↕</th>
      <th data-col="manager" data-type="string">经理</th><th data-col="size" data-type="string">公司</th>
      <th data-col="daySyl" data-type="float">日涨跌%↕</th></tr></thead>
      <tbody id="tbody-mix"></tbody></table></div></div>

<div class="section" id="sec-bond">
  <div class="section-title">债券型基金<span class="section-count" id="count-bond">--只</span></div>
  <div class="table-wrap" id="table-bond-container">
    <table id="table-bond"><thead><tr>
      <th data-col="riskLevel" data-type="int">风险</th><th data-col="score" data-type="float">评分</th>
      <th data-col="fundCode" data-type="string">代码</th><th data-col="fundName" data-type="string">基金名称</th>
      <th data-col="ranking" data-type="string">评级</th><th data-col="suggestion" data-type="string">建议</th>
      <th data-col="r1w" data-type="pct">1周↕</th><th data-col="r1m" data-type="pct">1月↕</th>
      <th data-col="r3m" data-type="pct">3月↕</th><th data-col="r6m" data-type="pct">6月↕</th>
      <th data-col="r1y" data-type="pct">1年↕</th><th data-col="r2y" data-type="pct">2年↕</th>
      <th data-col="r3y" data-type="pct">3年↕</th><th data-col="drawdown" data-type="pct">最大回撤↕</th>
      <th data-col="volatility" data-type="pct">波动近似↕</th><th data-col="sharpe" data-type="float">夏普近似↕</th>
      <th data-col="manager" data-type="string">经理</th><th data-col="size" data-type="string">公司</th>
      <th data-col="daySyl" data-type="float">日涨跌%↕</th></tr></thead>
      <tbody id="tbody-bond"></tbody></table></div></div>

<div class="footer">数据来源: 天天基金 Skills | 报告生成于 {summary.get('date', '')}</div>
<script>
var FUNDS_DATA = {{ mix: {mix_js}, bond: {bond_js} }};
var SUMMARY = {summary_js};
console.log('[Report] Data loaded: mix=' + FUNDS_DATA.mix.length + ' bond=' + FUNDS_DATA.bond.length);

function scoreColor(s) {{ return s >= 72 ? '#e94560' : s >= 48 ? '#f59e0b' : '#888'; }}
function pctClass(v) {{ if (v===''||v==null||v===undefined) return ''; const n=parseFloat(String(v).replace('%','')); return isNaN(n)?'': n>0?'val-pos':n<0?'val-neg':'val-neutral'; }}
function metricColor(m,v,t) {{ if(v==null||v==undefined||v==='') return 'val-neutral'; const n=parseFloat(v); if(isNaN(n)) return 'val-neutral'; return t.lowerBetter ? n<=t.good?'metric-good':n<=t.warn?'metric-warn':'metric-bad' : n>=t.good?'metric-good':n>=t.warn?'metric-warn':'metric-bad'; }}

function suggestionTag(sg) {{ if (sg==='买入') return '<span class=tag-buy>'+sg+'</span>'; if (sg==='关注') return '<span class=tag-watch>'+sg+'</span>'; return '<span style=color:#888>'+sg+'</span>'; }}
function rankingTag(rk) {{ if (!rk) return '-'; if (rk.indexOf('优优优优')>=0) return '<span class=tag-rank>'+rk+'</span>'; if (rk.indexOf('优良优')>=0) return '<span class=tag-rank>'+rk+'</span>'; return '<span class=tag-rank-mid>'+rk+'</span>'; }}
function riskDot(l) {{ return '<span class=risk-dot risk-'+l+'>'+l+'</span>'; }}
function renderScoreCell(sc) {{ var c = scoreColor(sc); return '<span style=color:'+c+';font-weight:700;font-size:14px>' + sc.toFixed(1) + '</span>' + '<span class=score-bar><span class=score-fill style=width:' + Math.min(sc,100) + '%;background:' + c + '></span></span>'; }}

function renderTable(tbodyId, funds) {{
  console.log('[renderTable] tbodyId=' + tbodyId + ' funds.length=' + (funds?funds.length:'NULL'));
  var tb = document.getElementById(tbodyId);
  if (!tb) {{ console.error('ERROR: #' + tbodyId + ' not found!'); return; }}
  if (!funds || !funds.length) {{ tb.innerHTML = '<tr><td colspan=19 class=empty-state>暂无数据</td></tr>'; return; }}
  var h = '';
  for (var i = 0; i < funds.length; i++) {{
    var f = funds[i];
    if (!f) continue;
    h += '<tr data-index="' + i + '">';
    h += '<td>' + riskDot(f.riskLevel) + '</td>';
    h += '<td>' + renderScoreCell(f.score) + '</td>';
    h += '<td style=font-family:monospace;font-size:12px;color:#555>' + (f.fundCode||'-') + '</td>';
    h += '<td class=fund-name title="' + (f.fundName||'') + '">' + (f.fundName||'-') + '</td>';
    h += '<td>' + rankingTag(f.ranking) + '</td>';
    h += '<td>' + suggestionTag(f.suggestion) + '</td>';
    ['r1w','r1m','r3m','r6m','r1y','r2y','r3y'].forEach(function(k) {{ h += '<td class=' + pctClass(f[k]||'') + '>' + (f[k]||'-') + '</td>'; }});
    h += '<td class=' + metricColor('dd', f.drawdown, {{lowerBetter:true,good:8,warn:15,bad:20}}) + '>' + (f.drawdown||'-') + '</td>';
    h += '<td class=' + metricColor('vol', f.volatility, {{lowerBetter:true,good:15,warn:25,bad:35}}) + '>' + (f.volatility||'-') + '</td>';
    h += '<td class=' + metricColor('sh', f.sharpe, {{lowerBetter:false,good:3,warn:1.5,bad:0.5}}) + '>' + ((f.sharpe!==undefined&&f.sharpe!=='')?f.sharpe:'-') + '</td>';
    h += '<td style=text-align:left;font-size:12px;color:#555>' + (f.manager||'-') + '</td>';
    h += '<td class=company-name>' + (f.size||'-') + '</td>';
    var dv = f.daySyl;
    h += '<td class=daily-chg ' + pctClass(dv?dv*100+'%':'') + '>' + (dv===0?'0%':(dv?(dv*100).toFixed(2)+'%':'-')) + '</td>';
    h += '</tr>';
  }}
  tb.innerHTML = h;
  console.log('[renderTable] Rendered ' + funds.length + ' rows into #' + tbodyId);
}}

function initSort(tableId) {{
  var table = document.getElementById(tableId);
  if (!table) return;
  var headers = table.querySelectorAll('th[data-col]');
  var sortCol = null, sortDir = 'desc';
  headers.forEach(function(th) {{
    th.addEventListener('click', function() {{
      var col = this.dataset.col, type = this.dataset.type;
      if (sortCol === col) {{ sortDir = sortDir==='asc'?'desc':'asc'; }} else {{ sortCol = col; sortDir = type==='string'?'asc':'desc'; }}
      headers.forEach(function(h) {{ h.classList.remove('sort-asc','sort-desc'); }});
      this.classList.add(sortDir==='asc'?'sort-asc':'sort-desc');
      var tb = table.querySelector('tbody'), rows = Array.from(tb.rows);
      var colIdx = Array.from(headers).findIndex(function(h){{return h.dataset.col===col}});
      rows.sort(function(a,b) {{
        var av = a.cells[colIdx].textContent, bv = b.cells[colIdx].textContent;
        if (type==='pct') {{ var an=parseFloat(av.replace('%','')), bn=parseFloat(bv.replace('%','')); if(isNaN(an)||isNaN(bn)) return 0; return sortDir==='asc'?an-bn:bn-an; }}
        else if (type==='float'||type==='int') {{ var an=parseFloat(av), bn=parseFloat(bv); if(isNaN(an)||isNaN(bn)) return 0; return sortDir==='asc'?an-bn:bn-an; }}
        else {{ return sortDir==='asc'?av.localeCompare(bv,'zh'):bv.localeCompare(av,'zh'); }}
      }});
      rows.forEach(function(r) {{ tb.appendChild(r); }});
    }});
  }});
}}

document.addEventListener('DOMContentLoaded', function() {{
  if (SUMMARY) {{
    if (SUMMARY.total!==undefined) document.getElementById('stat-total').textContent = SUMMARY.total;
    if (SUMMARY.detail!==undefined) document.getElementById('stat-detail').textContent = SUMMARY.detail;
    if (SUMMARY.buy!==undefined) document.getElementById('stat-buy').textContent = SUMMARY.buy;
    if (SUMMARY.watch!==undefined) document.getElementById('stat-watch').textContent = SUMMARY.watch;
  }}
  renderTable('tbody-mix', FUNDS_DATA.mix);
  renderTable('tbody-bond', FUNDS_DATA.bond);
  var mc = (FUNDS_DATA.mix||[]).length, bc = (FUNDS_DATA.bond||[]).length;
  document.getElementById('count-mix').textContent = mc + '只';
  document.getElementById('count-bond').textContent = bc + '只';
  if (bc === 0) document.getElementById('table-bond-container').innerHTML = '<div class=empty-stage>暂无符合筛选条件的债券基金（rsfType=1106返回的主要为固收+/可转债基，已归入混合型）</div>';
  if (mc > 0) initSort('table-mix');
  if (bc > 0) initSort('table-bond');
  console.log('[Init] Done. Mix:', mc, 'Bond:', bc);
}});
</script></body></html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  HTML报告: {output_path}")


def _validate_output(html_path, json_path, expected_mix, expected_bond):
    """Validate output files before declaring success."""
    errors = []

    # Check JSON
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            jd = json.load(f)
        mj = len(jd.get("mix_funds", []))
        bj = len(jd.get("bond_funds", []))
        if mj != expected_mix:
            errors.append(f"JSON混基数:{mj}≠期望{expected_mix}")
        if bj != expected_bond:
            errors.append(f"JSON债基数:{bj}≠期望{expected_bond}")
        if jd.get("mix_funds"):
            sample = jd["mix_funds"][0]
            missing = [k for k in ["fundCode","fundName","score","ftype"] if k not in sample]
            if missing:
                errors.append(f"JSON缺少字段:{missing}")
        print(f"  [OK] JSON verify: {mj} mix, {bj} bond")
    except Exception as e:
        errors.append(f"JSON error: {e}")
        print(f"  [FAIL] JSON: {e}")

    # Check HTML data injection
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            hc = f.read()
        
        import re
        m = re.search(r'FUNDS_DATA\s*=\s*(\{.+?\})\s*;\s*\n\s*var SUMMARY', hc, re.DOTALL)
        if not m:
            errors.append("HTML中未找到FUNDS_DATA")
        else:
            ds = m.group(1)
            mm = re.search(r'"mix"\s*:\s*(\[.*?\])\s*,\s*"bond"', ds, re.DOTALL)
            if not mm:
                errors.append("无法解析mix字段")
            else:
                cc = mm.group(1).count('"fundCode"')
                if cc != expected_mix:
                    errors.append(f"HTML混基fundCode数:{cc}≠期望{expected_mix}")
                else:
                    print(f"  [OK] HTML data injection verify: {cc} fund objects")
    except Exception as e:
        errors.append(f"HTML error: {e}")
        print(f"  [FAIL] HTML: {e}")

    if errors:
        print("\n  [WARN] Issues found:")
        for err in errors:
            print(f"     - {err}")
        return False
    else:
        print("\n  [OK] All validations passed!")
        return True


if __name__ == "__main__":
    main()
