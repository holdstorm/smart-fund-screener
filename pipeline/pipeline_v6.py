#!/usr/bin/env python3
"""
基金筛选管线 v6
================
继续使用天天基金 Skills API:
  - FUND_CONDITION_SELECT: 候选池
  - FUND_BASE_INFOS: 基础信息和周期收益
  - FUND_NAV_INFO: 仅对最终评分通过基金补净值风险指标

输出:
  - output/zmail_funds_data.json
  - output/zmail_recommend.html
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path

import pipeline_v5 as v5


WORKDIR = Path(v5.WORKDIR)
OUT = Path(v5.OUT)
NAV_RANGE = "3n"
MIN_ENDNAV = 50_000_000
RISK_FETCH_LIMIT = 60
SYL_MAP = {
    "oneWeek": "SYL_Z",
    "oneMonth": "SYL_Y",
    "quarter": "SYL_3Y",
    "halfYear": "SYL_6Y",
    "oneYear": "SYL_1N",
    "twoYear": "SYL_2N",
    "threeYear": "SYL_3N",
}


def cond_select(order_field: str, fund_type: str, page_size: int = 50) -> list[dict]:
    response = v5.call_skill(
        "FUND_CONDITION_SELECT",
        "1.1.0",
        {
            "orderField": order_field,
            "pageIndex": 1,
            "pageNum": page_size,
            "pageType": 1,
            "rankSy": "1",
            "rsfType": fund_type,
        },
        params_nested=True,
    )
    if not response:
        return []
    data = response.get("data", {}).get("raw_result", {}).get("body", {}).get("Data", [])
    return data if isinstance(data, list) else []


def cs_to_base_info(cs_record: dict) -> dict:
    info = v5.cs_to_base_info(cs_record)
    field_map = {
        "SYL_Z": "weekSyl",
        "SYL_Y": "monthSyl",
        "SYL_6Y": "hySyl",
        "SYL_1N": "yearSyl",
        "SYL_2N": "twySyl",
        "SYL_3N": "trySyl",
        "SYL_5N": "fySyl",
        "CS_DD_1M": "monthReturn",
        "CS_DD_3M": "quarterReturn",
        "CS_DD_6M": "hyReturn",
        "CS_DD_1Y": "yearReturn",
        "CS_DD_2Y": "twyReturn",
        "CS_DD_3Y": "tryReturn",
        "CS_DD_5Y": "fyReturn",
        "CS_SHARPE_1Y": "yearSharp",
    }
    for target, source in field_map.items():
        if cs_record.get(source) not in (None, ""):
            info[target] = cs_record.get(source)
    return info


def get_nav_history(fund_code: str, range_type: str = NAV_RANGE) -> tuple[list[dict], str]:
    """Fetch NAV history. Prefer 3y actual data, then fallback to lighter ranges."""
    ranges = []
    for item in [range_type, "n", "3y"]:
        if item not in ranges:
            ranges.append(item)
    for current_range in ranges:
        response = v5.call_skill(
            "FUND_NAV_INFO",
            "1.0.0",
            {"fund_id": fund_code, "range": current_range},
            params_nested=False,
        )
        body = (response or {}).get("data", {}).get("raw_result", {}).get("body", {})
        data = body.get("data", {}) if isinstance(body, dict) else {}
        items = data.get("nav_history", {}).get("items", []) if isinstance(data, dict) else []
        if isinstance(items, list) and items:
            return items, current_range
        time.sleep(0.35)
    return [], ""


def _safe_float(raw) -> float | None:
    if raw in (None, "", "--"):
        return None
    try:
        return float(str(raw).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def parse_assets_value(base_info: dict) -> float | None:
    """Return latest fund net assets in yuan when available."""
    raw = base_info.get("ENDNAV")
    value = _safe_float(raw)
    if value is not None:
        return value
    size = str(base_info.get("FUND_SIZE") or base_info.get("fundSize") or "").strip()
    if not size:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", size)
    if not match:
        return None
    number = float(match.group(1))
    if "亿" in size:
        return number * 100_000_000
    if "万" in size:
        return number * 10_000
    return number


def format_assets_yi(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value / 100_000_000:.2f}"


def base_risk_metrics(base_info: dict, one_year_return: float | None) -> dict:
    max_dd = v5.parse_pct(base_info.get("MAXRETRA1"))
    drawdowns = {
        "m1": v5.parse_pct(base_info.get("CS_DD_1M")),
        "m3": v5.parse_pct(base_info.get("CS_DD_3M")),
        "m6": v5.parse_pct(base_info.get("CS_DD_6M")),
        "y1": v5.parse_pct(base_info.get("CS_DD_1Y")),
        "y3": v5.parse_pct(base_info.get("CS_DD_3Y")),
    }
    if max_dd is None:
        max_dd = drawdowns.get("y1")
    vol = v5.parse_pct(base_info.get("STDDEV1"))
    if max_dd is None:
        max_dd = _safe_float(base_info.get("MAXRETRA1"))
        if max_dd is not None and max_dd > 1:
            max_dd = max_dd / 100
    if vol is None:
        vol = _safe_float(base_info.get("STDDEV1"))
        if vol is not None and vol > 1:
            vol = vol / 100
    sharpe = _safe_float(base_info.get("CS_SHARPE_1Y"))
    if sharpe is None:
        sharpe = round(one_year_return / vol, 2) if one_year_return and vol and vol > 0 else None
    return {
        "drawdowns": drawdowns,
        "navPointCounts": {"m1": 0, "m3": 0, "m6": 0, "y1": 0, "y3": 0},
        "navPointCount": 0,
        "volatility": vol,
        "sharpe": sharpe,
        "primaryDrawdown": max_dd,
        "drawdownSource": "FUND_CONDITION_SELECT" if any(v is not None for v in drawdowns.values()) else "FUND_BASE_INFOS" if max_dd is not None else "missing",
        "navRangeActual": "",
        "navStartDate": "",
        "navEndDate": "",
    }


def parse_nav_points(items: list[dict]) -> list[tuple[datetime.date, float, float | None]]:
    points = []
    for item in items:
        date_raw = item.get("FSRQ")
        nav = _safe_float(item.get("DWJZ"))
        daily = _safe_float(item.get("JZZZL"))
        if not date_raw or nav is None or nav <= 0:
            continue
        try:
            dt = datetime.datetime.strptime(str(date_raw), "%Y-%m-%d").date()
        except ValueError:
            continue
        points.append((dt, nav, daily / 100 if daily is not None else None))
    points.sort(key=lambda x: x[0])
    return points


def max_drawdown(points: list[tuple[datetime.date, float, float | None]]) -> float | None:
    if len(points) < 2:
        return None
    peak = points[0][1]
    worst = 0.0
    for _, nav, _ in points:
        peak = max(peak, nav)
        if peak > 0:
            worst = min(worst, nav / peak - 1)
    return abs(worst)


def window_return(points: list[tuple[datetime.date, float, float | None]], days: int) -> float | None:
    subset = slice_points(points, days)
    if len(subset) < 2 or subset[0][1] <= 0:
        return None
    return subset[-1][1] / subset[0][1] - 1


def annualized_volatility(points: list[tuple[datetime.date, float, float | None]]) -> float | None:
    returns = [daily for _, _, daily in points if daily is not None]
    if len(returns) < 4:
        nav_returns = []
        for prev, cur in zip(points, points[1:]):
            if prev[1] > 0:
                nav_returns.append(cur[1] / prev[1] - 1)
        returns = nav_returns
    if len(returns) < 4:
        return None
    return statistics.pstdev(returns) * math.sqrt(252)


def slice_points(points: list[tuple[datetime.date, float, float | None]], days: int):
    if not points:
        return []
    cutoff = points[-1][0] - datetime.timedelta(days=days)
    return [p for p in points if p[0] >= cutoff]


def calculate_nav_metrics(items: list[dict], one_year_return: float | None, used_range: str = "") -> dict:
    points = parse_nav_points(items)
    windows = {
        "m1": 31,
        "m3": 92,
        "m6": 183,
        "y1": 366,
        "y3": 366 * 3,
    }
    drawdowns = {}
    counts = {}
    for key, days in windows.items():
        subset = slice_points(points, days)
        counts[key] = len(subset)
        drawdowns[key] = max_drawdown(subset)

    vol_points = slice_points(points, 366) or points
    vol = annualized_volatility(vol_points)
    sharpe = round(one_year_return / vol, 2) if one_year_return and vol and vol > 0 else None
    primary_dd = drawdowns.get("y1") or drawdowns.get("m6") or drawdowns.get("m3")
    return {
        "drawdowns": drawdowns,
        "returns": {"oneWeek": window_return(points, 7)},
        "navPointCounts": counts,
        "navPointCount": len(points),
        "volatility": vol,
        "sharpe": sharpe,
        "primaryDrawdown": primary_dd,
        "drawdownSource": "FUND_NAV_INFO" if points else "missing",
        "navRangeActual": used_range if points else "",
        "navStartDate": points[0][0].isoformat() if points else "",
        "navEndDate": points[-1][0].isoformat() if points else "",
    }


def merge_risk_metrics(nav_risk: dict, base_risk: dict) -> dict:
    if nav_risk.get("navPointCount", 0) > 0:
        if nav_risk.get("volatility") is None:
            nav_risk["volatility"] = base_risk.get("volatility")
        if nav_risk.get("sharpe") is None:
            nav_risk["sharpe"] = base_risk.get("sharpe")
        return nav_risk
    return base_risk


def score_components(ftype: str, returns_map: dict, risk: dict | None = None) -> tuple[dict | None, str | None]:
    is_bond = ftype == "bond"
    required = ["oneMonth", "halfYear", "oneYear"] if is_bond else ["oneMonth", "quarter", "halfYear", "oneYear"]
    returns = []
    for period in required:
        value = returns_map.get(period)
        if value is None:
            return None, "数据缺失"
        if is_bond and value < -0.05:
            return None, f"亏损过大({value * 100:.1f}%)"
        if not is_bond and value <= 0:
            return None, "存在负收益"
        returns.append(value)

    r1y = returns_map.get("oneYear")
    r2y = returns_map.get("twoYear")
    r3y = returns_map.get("threeYear")
    max_dd = (risk or {}).get("primaryDrawdown")
    vol = (risk or {}).get("volatility")
    sharpe = (risk or {}).get("sharpe")

    if max_dd is not None:
        if is_bond and max_dd > 0.08:
            return None, f"回撤过大({max_dd * 100:.1f}%)"
        if not is_bond and max_dd > 0.25:
            return None, f"回撤过大({max_dd * 100:.1f}%)"

    if vol is None:
        if r1y is not None and r3y is not None and r3y > 0:
            vol = abs(r1y - r3y / 3) * (0.3 if is_bond else 0.5)
        elif r1y is not None:
            vol = abs(r1y) * (0.2 if is_bond else 0.3)
        else:
            vol = 0.10 if is_bond else 0.25

    if sharpe is None and r1y and vol and vol > 0:
        sharpe = round(r1y / vol, 2)

    if is_bond:
        return_score = min(sum(r * w for r, w in zip(returns, [1.0, 1.5, 2.0])) * 8, 30)
        consistency = 15 if returns[-1] >= returns[0] else 10
        long_term = 2 if r3y is not None and r3y <= 0 else 5
        volatility_score = 18
        if vol is not None:
            if vol > 0.15:
                volatility_score = 8
            elif vol > 0.10:
                volatility_score = 12
            elif vol > 0.06:
                volatility_score = 16
        drawdown_score = 14
        if max_dd is not None:
            if max_dd > 0.05:
                drawdown_score = 6
            elif max_dd > 0.03:
                drawdown_score = 10
        sharpe_score = 6
        if sharpe is not None:
            if sharpe >= 3:
                sharpe_score = 10
            elif sharpe >= 1.5:
                sharpe_score = 8
            elif sharpe > 0:
                sharpe_score = 4
    else:
        return_score = min(sum(r * w for r, w in zip(returns, [1.5, 2.0, 2.5, 3.0])) * 5, 30)
        consistency = min(sum(3 if returns[i] > returns[i - 1] else 1 if returns[i] > returns[i - 1] * 0.8 else 0 for i in range(1, len(returns))), 15)
        long_term = min((2.5 if r2y and r2y > 0 else 0) + (2.5 if r3y and r3y > 0 else 0), 5)
        volatility_score = 20
        if vol is not None:
            if vol > 0.35:
                volatility_score = 5
            elif vol > 0.30:
                volatility_score = 8
            elif vol > 0.25:
                volatility_score = 11
            elif vol > 0.20:
                volatility_score = 14
            elif vol > 0.15:
                volatility_score = 17
        drawdown_score = 15
        if max_dd is not None:
            if max_dd > 0.20:
                drawdown_score = 4
            elif max_dd > 0.15:
                drawdown_score = 7
            elif max_dd > 0.12:
                drawdown_score = 9
            elif max_dd > 0.10:
                drawdown_score = 11
            elif max_dd > 0.08:
                drawdown_score = 13
        sharpe_score = 6
        if sharpe is not None:
            if sharpe >= 5:
                sharpe_score = 10
            elif sharpe >= 3:
                sharpe_score = 8
            elif sharpe >= 2:
                sharpe_score = 6
            elif sharpe >= 1:
                sharpe_score = 4
            elif sharpe > 0:
                sharpe_score = 2

    coverage = min(sum(1 for k in SYL_MAP if returns_map.get(k) is not None and returns_map.get(k, 0) > 0) * 0.7, 5)
    total = round(min(return_score + consistency + long_term + volatility_score + drawdown_score + sharpe_score + coverage, 100), 1)
    risk_level = 1 if (volatility_score + drawdown_score) >= 30 else 2 if (volatility_score + drawdown_score) >= 22 else 3 if (volatility_score + drawdown_score) >= 14 else 4
    if total >= (70 if is_bond else 72):
        ranking, suggestion = "优优优优", "买入"
    elif total >= (55 if is_bond else 60):
        ranking, suggestion = "优良优优", "买入"
    elif total >= (42 if is_bond else 48):
        ranking, suggestion = "优良优良", "关注"
    elif total >= (30 if is_bond else 35):
        ranking, suggestion = "优优良良", "持有"
    else:
        ranking, suggestion = "良好中中", "观望"

    return {
        "score": total,
        "riskLevel": risk_level,
        "ranking": ranking,
        "suggestion": suggestion,
        "drawdown": v5.fmt_pct(max_dd),
        "volatility": v5.fmt_pct(vol),
        "sharpe": sharpe,
        "scoreBreakdown": {
            "returnScore": round(return_score, 1),
            "consistencyScore": round(consistency, 1),
            "longTermScore": round(long_term, 1),
            "volatilityScore": round(volatility_score, 1),
            "drawdownScore": round(drawdown_score, 1),
            "sharpeScore": round(sharpe_score, 1),
            "coverageScore": round(coverage, 1),
        },
    }, None


def classify_fund(base_info: dict, fallback_name: str) -> str:
    ftype_raw = str(base_info.get("FTYPE", ""))
    name = str(base_info.get("SHORTNAME", fallback_name))
    if any(k in ftype_raw for k in ["债券", "债"]):
        ftype = "bond"
    elif any(k in ftype_raw for k in ["混合", "股票", "QDII", "ETF", "指数"]):
        ftype = "mix"
    elif any(k in name for k in ["债券", "纯债", "中短债", "长债"]):
        ftype = "bond"
    else:
        ftype = "mix"
    r1y = v5.parse_pct(base_info.get("SYL_1N"))
    if r1y and r1y > 0.30:
        ftype = "mix"
    return ftype


def build_record(fc: str, base_info: dict, ftype: str, returns_map: dict, score: dict, risk: dict | None) -> dict:
    manager_info = v5.extract_manager_info(base_info.get("expansion"))
    drawdowns = (risk or {}).get("drawdowns", {})
    counts = (risk or {}).get("navPointCounts", {})
    return {
        "fundCode": fc,
        "fundName": base_info.get("SHORTNAME", fc),
        "ftype": ftype,
        **score,
        "r1w": v5.fmt_pct(returns_map.get("oneWeek")),
        "r1m": v5.fmt_pct(returns_map.get("oneMonth")),
        "r3m": v5.fmt_pct(returns_map.get("quarter")),
        "r6m": v5.fmt_pct(returns_map.get("halfYear")),
        "r1y": v5.fmt_pct(returns_map.get("oneYear")),
        "r2y": v5.fmt_pct(returns_map.get("twoYear")),
        "r3y": v5.fmt_pct(returns_map.get("threeYear")),
        "dd1m": v5.fmt_pct(drawdowns.get("m1")),
        "dd3m": v5.fmt_pct(drawdowns.get("m3")),
        "dd6m": v5.fmt_pct(drawdowns.get("m6")),
        "dd1y": v5.fmt_pct(drawdowns.get("y1")),
        "dd3y": v5.fmt_pct(drawdowns.get("y3")),
        "manager": manager_info["name"],
        "size": base_info.get("COMPANY", base_info.get("JJGS", "")),
        "endNavYi": format_assets_yi(parse_assets_value(base_info)),
        "daySyl": v5.parse_pct(base_info.get("RZDF", base_info.get("daySyl", "0"))) or 0,
        "riskMetrics": {
            "drawdownSource": (risk or {}).get("drawdownSource", "missing"),
            "navRangeActual": (risk or {}).get("navRangeActual", ""),
            "navPointCount": (risk or {}).get("navPointCount", 0),
            "navStartDate": (risk or {}).get("navStartDate", ""),
            "navEndDate": (risk or {}).get("navEndDate", ""),
            "windowPointCounts": counts,
        },
    }


def dedup(records: list[dict]) -> list[dict]:
    seen = {}
    for record in records:
        base = fund_series_key(record["fundName"])
        if base not in seen or record["score"] > seen[base]["score"]:
            seen[base] = record
    return list(seen.values())


def fund_series_key(name: str) -> str:
    return re.sub(r"[ABCDEFHI]+$", "", name or "")


def main() -> bool:
    print("=" * 64)
    print("基金筛选管线 v6")
    print("=" * 64)

    fund_types = {"mix": "1103", "bond": "1106"}
    order_fields = {
        "oneMonth": "5_3_-1",
        "halfYear": "5_4_-1",
        "oneYear": "5_6_-1",
        "threeYear": "5_7_-1",
    }

    print("\n[Step 1] 多维度TOP50并集")
    all_codes = set()
    cs_data = {}
    code_type_map = {}
    for group, fund_type in fund_types.items():
        print(f"  [{group}] rsfType={fund_type}")
        for dim, order_field in order_fields.items():
            funds = cond_select(order_field, fund_type, 50)
            print(f"    {dim}: {len(funds)} 只")
            for fund in funds:
                fc = fund.get("fundCode")
                if fc:
                    all_codes.add(fc)
                    cs_data[fc] = fund
                    code_type_map[fc] = group
    print(f"  并集总计: {len(all_codes)} 只")

    print("\n[Step 2] 获取基础信息")
    fund_base = {}
    for i, fc in enumerate(sorted(all_codes), 1):
        sys.stdout.write(f"  [{i}/{len(all_codes)}] {fc} ... ")
        info = v5.get_base_info(fc)
        if info:
            fund_base[fc] = info
            print(str(info.get("SHORTNAME", "?"))[:25])
        elif fc in cs_data:
            fund_base[fc] = cs_to_base_info(cs_data[fc])
            print("[Fallback-CS]")
        else:
            print("[失败]")

    print("\n[Step 3] 初筛评分")
    preliminary = []
    elim_stats = {"no_data": 0, "neg_return": 0, "high_dd": 0, "low_assets": 0, "other": 0}
    for fc, base_info in fund_base.items():
        assets_value = parse_assets_value(base_info)
        if assets_value is None or assets_value < MIN_ENDNAV:
            elim_stats["low_assets"] += 1
            continue
        ftype = classify_fund(base_info, fc)
        returns_map = {key: v5.parse_pct(base_info.get(field)) for key, field in SYL_MAP.items()}
        score, reason = score_components(ftype, returns_map, risk=None)
        if score is None:
            if "数据缺失" in (reason or ""):
                elim_stats["no_data"] += 1
            elif "负" in (reason or "") or "亏损" in (reason or ""):
                elim_stats["neg_return"] += 1
            else:
                elim_stats["other"] += 1
            continue
        preliminary.append((fc, base_info, ftype, returns_map))

    prelim_mix = dedup([build_record(fc, b, f, r, score_components(f, r)[0], None) for fc, b, f, r in preliminary if f == "mix"])
    prelim_bond = dedup([build_record(fc, b, f, r, score_components(f, r)[0], None) for fc, b, f, r in preliminary if f == "bond"])
    prelim_mix.sort(key=lambda x: x["score"], reverse=True)
    prelim_bond.sort(key=lambda x: x["score"], reverse=True)

    selected_series = {fund_series_key(r["fundName"]) for r in prelim_mix + prelim_bond}
    selected_rows = [
        (fc, base_info, ftype, returns_map, score_components(ftype, returns_map)[0]["score"])
        for fc, base_info, ftype, returns_map in preliminary
        if fund_series_key(base_info.get("SHORTNAME", fc)) in selected_series
    ]
    selected_rows.sort(key=lambda row: row[4], reverse=True)
    selected_rows = selected_rows[:RISK_FETCH_LIMIT]
    selected_codes = {fc for fc, _, _, _, _ in selected_rows}
    print(f"  初筛通过: {len(selected_codes)} 只；仅对这些基金请求净值风险指标")

    print("\n[Step 4] 补充净值风险指标")
    risk_by_code = {}
    for i, (fc, base_info, _, returns_map, _) in enumerate(selected_rows, 1):
        sys.stdout.write(f"  [{i}/{len(selected_codes)}] {fc} NAV ... ")
        items, used_range = get_nav_history(fc, NAV_RANGE)
        risk = merge_risk_metrics(
            calculate_nav_metrics(items, returns_map.get("oneYear"), used_range),
            base_risk_metrics(base_info, returns_map.get("oneYear")),
        )
        risk_by_code[fc] = risk
        print(f"{risk['navPointCount']} 点 {risk.get('navRangeActual') or '-'}")

    print("\n[Step 5] 复算最终评分")
    scored_mix = []
    scored_bond = []
    for fc, base_info, ftype, returns_map in preliminary:
        risk = risk_by_code.get(fc)
        if risk:
            for key, value in risk.get("returns", {}).items():
                if returns_map.get(key) is None and value is not None:
                    returns_map[key] = value
        score, reason = score_components(ftype, returns_map, risk=risk)
        if score is None:
            if "回撤" in (reason or ""):
                elim_stats["high_dd"] += 1
            else:
                elim_stats["other"] += 1
            continue
        record = build_record(fc, base_info, ftype, returns_map, score, risk)
        if ftype == "bond":
            scored_bond.append(record)
        else:
            scored_mix.append(record)

    scored_mix = dedup(scored_mix)
    scored_bond = dedup(scored_bond)
    scored_mix.sort(key=lambda x: x["score"], reverse=True)
    scored_bond.sort(key=lambda x: x["score"], reverse=True)

    buy = sum(1 for r in scored_mix + scored_bond if r["suggestion"] == "买入")
    watch = sum(1 for r in scored_mix + scored_bond if r["suggestion"] == "关注")
    nav_covered = sum(1 for r in scored_mix + scored_bond if r["riskMetrics"]["navPointCount"] > 0)
    ds = datetime.datetime.now().strftime("%Y年%m月%d日")
    summary = {
        "total": len(all_codes),
        "detail": len(fund_base),
        "buy": buy,
        "watch": watch,
        "date": ds,
        "elimination": elim_stats,
        "mix_count": len(scored_mix),
        "bond_count": len(scored_bond),
        "nav_risk_count": nav_covered,
        "nav_range": NAV_RANGE,
        "min_endnav": MIN_ENDNAV,
    }
    full_data = {
        "summary": summary,
        "mix_funds": scored_mix,
        "bond_funds": scored_bond,
        "generated_at": ds,
    }

    json_path = OUT / "zmail_funds_data.json"
    html_path = OUT / "zmail_recommend.html"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)
    generate_html_report(str(html_path), full_data)
    ok = validate_output(str(html_path), str(json_path), len(scored_mix), len(scored_bond))
    print(f"\n完成: {len(scored_mix)} 混合, {len(scored_bond)} 债券, 净值覆盖 {nav_covered}")
    return ok


def validate_output(html_path: str, json_path: str, expected_mix: int, expected_bond: int) -> bool:
    ok = v5._validate_output(html_path, json_path, expected_mix, expected_bond)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    sample_pool = data.get("mix_funds", []) or data.get("bond_funds", [])
    if sample_pool:
        sample = sample_pool[0]
        needed = ["scoreBreakdown", "riskMetrics", "dd1m", "dd3m", "dd6m", "dd1y", "dd3y"]
        missing = [k for k in needed if k not in sample]
        if missing:
            print(f"  [WARN] v6字段缺失: {missing}")
            return False
    return ok


def generate_html_report(output_path: str, data: dict):
    summary = data["summary"]
    mix_js = json.dumps(data["mix_funds"], ensure_ascii=False)
    bond_js = json.dumps(data["bond_funds"], ensure_ascii=False)
    summary_js = json.dumps(summary, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>基金筛选工作台 - {summary.get('date', '')}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef1f4;color:#17202a;font-family:"Microsoft YaHei","PingFang SC",sans-serif;font-size:13px}}.app{{min-height:100vh}}.topbar{{background:#121821;color:#f3f6f8;padding:18px 24px;border-bottom:4px solid #c53832}}.toprow{{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}}.brand{{display:flex;align-items:baseline;gap:14px}}.brand h1{{font-size:22px;letter-spacing:0;margin:0}}.meta{{color:#9eadba;font-size:12px}}.actions{{display:flex;align-items:center;gap:10px}}button{{border:0;border-radius:6px;padding:9px 14px;font-weight:700;cursor:pointer;background:#c53832;color:#fff}}button:disabled{{opacity:.55;cursor:not-allowed}}.status{{color:#9eadba;font-size:12px;min-width:180px}}.shell{{padding:18px 22px 28px}}.stats{{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:10px;margin-bottom:14px}}.stat{{background:#fff;border:1px solid #d7dde3;border-radius:6px;padding:12px 14px}}.stat b{{display:block;font-size:24px;line-height:1;color:#111820}}.stat span{{display:block;margin-top:6px;color:#6d7884;font-size:12px}}.buy b{{color:#c53832}}.watch b{{color:#ba7a00}}.banner{{background:#fff7e4;border:1px solid #efd08b;border-left:4px solid #ba7a00;border-radius:6px;padding:10px 12px;margin-bottom:14px;color:#614500}}.tabs{{display:flex;gap:8px;margin:12px 0}}.tab{{background:#dfe5ea;color:#24313d;border:1px solid #cbd4dc}}.tab.active{{background:#121821;color:#fff}}.section{{display:none}}.section.active{{display:block}}.section-title{{display:flex;align-items:center;justify-content:space-between;margin:12px 0 8px}}.section-title h2{{font-size:16px;margin:0}}.count{{background:#121821;color:#fff;padding:2px 9px;border-radius:99px;font-size:12px}}.table-wrap{{background:#fff;border:1px solid #cbd4dc;border-radius:6px;overflow:auto;max-height:72vh}}table{{border-collapse:separate;border-spacing:0;min-width:2380px;width:100%}}thead th{{position:sticky;top:0;z-index:2;background:#18212b;color:#dce3ea;border-bottom:1px solid #303b47;padding:9px 8px;text-align:right;white-space:nowrap;font-size:12px;cursor:pointer}}thead th.identity{{text-align:left}}td{{border-bottom:1px solid #edf0f2;padding:8px;text-align:right;white-space:nowrap}}tbody tr:nth-child(even){{background:#fafbfc}}tbody tr:hover{{background:#fff4ed}}.left{{text-align:left}}.code{{font-family:Consolas,monospace;color:#4d5965}}.name{{font-weight:700;max-width:260px;overflow:hidden;text-overflow:ellipsis}}.pos{{color:#c53832;font-weight:700}}.neg{{color:#0b8f61;font-weight:700}}.muted{{color:#8a96a3}}.good{{color:#0b8f61;font-weight:700}}.warn{{color:#ba7a00;font-weight:700}}.bad{{color:#c53832;font-weight:700}}.tag{{display:inline-block;border-radius:4px;padding:2px 7px;font-weight:700;font-size:12px}}.tag-buy{{background:#c53832;color:#fff}}.tag-watch{{background:#ba7a00;color:#fff}}.tag-neutral{{background:#e4e9ee;color:#596673}}.score{{font-weight:800;font-size:14px;color:#111820}}.risk{{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;color:#fff;font-weight:800}}.r1{{background:#0b8f61}}.r2{{background:#ba7a00}}.r3{{background:#d0672f}}.r4{{background:#c53832}}.empty{{padding:32px;text-align:center;color:#76828e}}.foot{{padding:18px;text-align:center;color:#7a8794;font-size:12px}}
@media(max-width:900px){{.stats{{grid-template-columns:repeat(2,1fr)}}.topbar{{padding:16px}}.shell{{padding:14px}}}}
</style>
</head>
<body><div class="app">
<header class="topbar"><div class="toprow"><div class="brand"><h1>基金筛选工作台</h1><span class="meta">天天基金 Skills · {summary.get('date', '')}</span></div><div class="actions"><button id="refreshBtn" type="button">刷新数据</button><span class="status" id="refreshStatus">本地服务模式可直接刷新</span></div></div></header>
<main class="shell">
<div class="stats">
<div class="stat"><b id="stat-total">{summary.get('total', 0)}</b><span>候选并集</span></div>
<div class="stat"><b id="stat-detail">{summary.get('detail', 0)}</b><span>基础详情</span></div>
<div class="stat buy"><b id="stat-buy">{summary.get('buy', 0)}</b><span>建议买入</span></div>
<div class="stat watch"><b id="stat-watch">{summary.get('watch', 0)}</b><span>建议关注</span></div>
<div class="stat"><b id="stat-nav">{summary.get('nav_risk_count', 0)}</b><span>净值风险覆盖</span></div>
<div class="stat"><b id="stat-range">{summary.get('nav_range', NAV_RANGE)}</b><span>净值窗口</span></div>
</div>
<div class="banner">风险提示：报告基于量化规则自动生成，不构成投资建议。周期涨跌优先使用 FUND_BASE_INFOS 成品字段；多区间回撤、波动、夏普由 FUND_NAV_INFO 返回的净值点计算，并展示净值点数量用于判断覆盖质量。本轮过滤基金资产净值低于 5000 万的产品。</div>
<div class="tabs"><button class="tab active" data-tab="mix">混合/权益 <span id="count-mix">0</span></button><button class="tab" data-tab="bond">债券 <span id="count-bond">0</span></button></div>
<section class="section active" id="sec-mix"><div class="section-title"><h2>混合/权益基金</h2><span class="count" id="pill-mix">0 只</span></div><div class="table-wrap"><table id="table-mix"><thead></thead><tbody id="tbody-mix"></tbody></table></div></section>
<section class="section" id="sec-bond"><div class="section-title"><h2>债券基金</h2><span class="count" id="pill-bond">0 只</span></div><div class="table-wrap"><table id="table-bond"><thead></thead><tbody id="tbody-bond"></tbody></table></div></section>
</main><footer class="foot">输出文件：output/zmail_recommend.html · output/zmail_funds_data.json</footer></div>
<script>
var FUNDS_DATA = {{ "mix": {mix_js}, "bond": {bond_js} }};
var SUMMARY = {summary_js};
var COLUMNS = [
  ['riskLevel','int','风险'],['score','float','总分'],['fundCode','string','代码'],['fundName','string','基金名称'],['ranking','string','评级'],['suggestion','string','建议'],
  ['returnScore','float','收益分'],['consistencyScore','float','一致性'],['longTermScore','float','长期分'],['volatilityScore','float','波动分'],['drawdownScore','float','回撤分'],['sharpeScore','float','夏普分'],['coverageScore','float','覆盖分'],
  ['r1w','pct','1周涨跌'],['r1m','pct','1月涨跌'],['r3m','pct','3月涨跌'],['r6m','pct','6月涨跌'],['r1y','pct','1年涨跌'],['r2y','pct','2年涨跌'],['r3y','pct','3年涨跌'],
  ['dd1m','pct','1月回撤'],['dd3m','pct','3月回撤'],['dd6m','pct','6月回撤'],['dd1y','pct','1年回撤'],['dd3y','pct','3年回撤'],
  ['volatility','pct','年化波动'],['sharpe','float','夏普近似'],['navPointCount','int','净值点'],['endNavYi','float','规模(亿)'],['manager','string','经理'],['size','string','公司'],['daySyl','float','日涨跌%']
];
function val(f,k){{var b=f.scoreBreakdown||{{}}, r=f.riskMetrics||{{}}; if(k in b)return b[k]; if(k==='navPointCount')return r.navPointCount||0; return f[k];}}
function num(v){{if(v===null||v===undefined||v==='')return NaN; return parseFloat(String(v).replace('%',''));}}
function pctCls(v){{var n=num(v); return isNaN(n)?'muted':n>0?'pos':n<0?'neg':'muted';}}
function ddCls(v){{var n=Math.abs(num(v)); return isNaN(n)?'muted':n<=8?'good':n<=15?'warn':'bad';}}
function tag(s){{if(s==='买入')return '<span class="tag tag-buy">买入</span>'; if(s==='关注')return '<span class="tag tag-watch">关注</span>'; return '<span class="tag tag-neutral">'+(s||'-')+'</span>';}}
function fmtCell(f,k){{var v=val(f,k); if(k==='riskLevel')return '<span class="risk r'+v+'">'+v+'</span>'; if(k==='score')return '<span class="score">'+Number(v||0).toFixed(1)+'</span>'; if(k==='fundCode')return '<span class="code">'+(v||'-')+'</span>'; if(k==='fundName')return '<span class="name" title="'+(v||'')+'">'+(v||'-')+'</span>'; if(k==='suggestion')return tag(v); if(k.indexOf('dd')===0||k==='drawdown')return '<span class="'+ddCls(v)+'">'+(v||'-')+'</span>'; if(['r1w','r1m','r3m','r6m','r1y','r2y','r3y'].indexOf(k)>=0)return '<span class="'+pctCls(v)+'">'+(v||'-')+'</span>'; if(k==='daySyl')return '<span class="'+pctCls((v||0)*100)+'">'+(v?((v*100).toFixed(2)+'%'):'-')+'</span>'; return (v===null||v===undefined||v==='')?'-':v;}}
function renderHead(tableId){{var tr='<tr>'; COLUMNS.forEach(function(c){{tr+='<th class="'+((c[0]==='fundName'||c[0]==='fundCode')?'identity':'')+'" data-col="'+c[0]+'" data-type="'+c[1]+'">'+c[2]+'</th>';}}); document.querySelector('#'+tableId+' thead').innerHTML=tr+'</tr>';}}
function renderRows(tbodyId, rows){{var tb=document.getElementById(tbodyId); if(!rows.length){{tb.innerHTML='<tr><td class="empty" colspan="'+COLUMNS.length+'">暂无符合条件的数据</td></tr>';return;}} tb.innerHTML=rows.map(function(f){{return '<tr>'+COLUMNS.map(function(c){{var cls=(c[0]==='fundName'||c[0]==='fundCode'||c[0]==='manager'||c[0]==='size')?'left':''; return '<td class="'+cls+'">'+fmtCell(f,c[0])+'</td>';}}).join('')+'</tr>';}}).join('');}}
function initSort(tableId, rows, tbodyId){{document.querySelectorAll('#'+tableId+' th').forEach(function(th){{th.onclick=function(){{var col=th.dataset.col,type=th.dataset.type,dir=th.dataset.dir==='desc'?'asc':'desc'; document.querySelectorAll('#'+tableId+' th').forEach(function(x){{x.dataset.dir='';}}); th.dataset.dir=dir; rows.sort(function(a,b){{var av=val(a,col),bv=val(b,col); if(type==='pct'||type==='float'||type==='int'){{av=num(av);bv=num(bv); if(isNaN(av))av=-999999; if(isNaN(bv))bv=-999999; return dir==='asc'?av-bv:bv-av;}} av=String(av||''); bv=String(bv||''); return dir==='asc'?av.localeCompare(bv,'zh'):bv.localeCompare(av,'zh');}}); renderRows(tbodyId,rows);}};}});}}
function boot(){{renderHead('table-mix');renderHead('table-bond');renderRows('tbody-mix',FUNDS_DATA.mix||[]);renderRows('tbody-bond',FUNDS_DATA.bond||[]);initSort('table-mix',FUNDS_DATA.mix||[],'tbody-mix');initSort('table-bond',FUNDS_DATA.bond||[],'tbody-bond');document.getElementById('count-mix').textContent=(FUNDS_DATA.mix||[]).length;document.getElementById('count-bond').textContent=(FUNDS_DATA.bond||[]).length;document.getElementById('pill-mix').textContent=(FUNDS_DATA.mix||[]).length+' 只';document.getElementById('pill-bond').textContent=(FUNDS_DATA.bond||[]).length+' 只';}}
document.querySelectorAll('.tab').forEach(function(btn){{btn.onclick=function(){{document.querySelectorAll('.tab').forEach(function(b){{b.classList.remove('active')}});document.querySelectorAll('.section').forEach(function(s){{s.classList.remove('active')}});btn.classList.add('active');document.getElementById('sec-'+btn.dataset.tab).classList.add('active');}};}});
document.getElementById('refreshBtn').onclick=function(){{var btn=this,st=document.getElementById('refreshStatus');btn.disabled=true;st.textContent='刷新中，正在调用天天基金接口...';fetch('/api/refresh',{{method:'POST'}}).then(function(r){{return r.json()}}).then(function(j){{if(!j.ok)throw new Error(j.error||'刷新失败');st.textContent='刷新完成，正在重载页面';location.reload();}}).catch(function(e){{st.textContent='刷新失败：'+e.message;btn.disabled=false;}});}};
boot();
</script></body></html>"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  HTML报告: {output_path}")


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
