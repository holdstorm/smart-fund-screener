#!/usr/bin/env python3
"""Small TTFund Skills API helper layer used by the current screener."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path


WORKDIR = Path(__file__).resolve().parents[1]
TMP = WORKDIR / "tmp"
OUT = WORKDIR / "output"
TMP.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

API_URL = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke"
API_KEY = os.environ.get("TTFUND_APIKEY") or os.environ.get("TTFUND_API_KEY", "")
CURL_BIN = "curl.exe" if os.name == "nt" else "curl"


def call_skill(skill_id: str, version: str, params: dict, params_nested: bool = True) -> dict | None:
    """Call a TTFund skill and return parsed JSON when the request succeeds."""
    if not API_KEY:
        print("  [API Error] Missing TTFUND_APIKEY environment variable.")
        return None

    body = json.dumps(
        {"skill_id": skill_id, "_skill_version": version, "params": params}
        if params_nested
        else {**{"skill_id": skill_id, "_skill_version": version}, **params},
        ensure_ascii=False,
    )
    request_path = TMP / f"req_{uuid.uuid4().hex[:8]}.json"
    response_path = TMP / f"resp_{uuid.uuid4().hex[:8]}.json"
    try:
        request_path.write_text(body, encoding="utf-8")
        subprocess.run(
            f'{CURL_BIN} -s -X POST "{API_URL}" '
            f'-H "X-API-Key: {API_KEY}" '
            f'-H "Content-Type: application/json" '
            f'-d @"{request_path}" > "{response_path}"',
            shell=True,
            timeout=30,
        )
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
            return response if response.get("code") == 0 else None
    except Exception as exc:
        print(f"  [API Error] {exc}")
    finally:
        for path in (request_path, response_path):
            try:
                path.unlink()
            except OSError:
                pass
    return None


def get_base_info(fund_code: str) -> dict | None:
    """Fetch base information for one fund."""
    response = call_skill(
        "FUND_BASE_INFOS",
        "1.2.0",
        {"fcode": fund_code, "nav_range": "y"},
        params_nested=False,
    )
    if not response:
        return None
    body = response.get("data", {}).get("raw_result", {}).get("body", {})
    data = body.get("data", []) if isinstance(body, dict) else []
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def parse_pct(raw_val) -> float | None:
    """Parse a percentage value such as '31.31%' into 0.3131."""
    if raw_val is None or raw_val == "":
        return None
    try:
        return float(str(raw_val).strip().rstrip("%")) / 100.0
    except (TypeError, ValueError):
        return None


def fmt_pct(value: float | None, decimals: int = 2) -> str:
    """Format a decimal percentage. None becomes an empty string."""
    if value is None:
        return ""
    return f"{value * 100:.{decimals}f}%"


def cs_to_base_info(cs_record: dict) -> dict:
    """Convert a condition-select row to a base-info-like shape."""
    info = {
        "SHORTNAME": cs_record.get("fundName", ""),
        "FTYPE": cs_record.get("ftype", ""),
        "RISERANK": cs_record.get("riskLevel", ""),
        "COMPANY": cs_record.get("company", ""),
        "FUND_SIZE": cs_record.get("fundSize"),
        "SYL_Z": cs_record.get("weekSyl", ""),
        "SYL_Y": cs_record.get("monthSyl", ""),
        "SYL_3Y": cs_record.get("quarterSyl", ""),
        "SYL_6Y": cs_record.get("hySyl", ""),
        "SYL_1N": cs_record.get("yearSyl", ""),
        "SYL_2N": cs_record.get("twySyl", ""),
        "SYL_3N": cs_record.get("trySyl", ""),
        "_source": "cs_fallback",
    }
    return info


def extract_manager_info(expansion) -> dict:
    """Extract manager name and manager-level max drawdown when present."""
    result = {"name": "", "max_drawdown": None}
    try:
        manager_info = (expansion or {}).get("comprehensive_info", {}).get("manager_information", {})
        for history in manager_info.get("historyManagerInfos", []):
            summary = history.get("SINFO", {})
            max_retra = summary.get("MAXRETRA")
            if max_retra is not None:
                result["max_drawdown"] = abs(float(max_retra))
            manager = summary.get("MgrName", "")
            if manager and not result["name"]:
                result["name"] = manager
    except (TypeError, AttributeError, ValueError):
        pass
    return result


def validate_output(html_path: Path, json_path: Path, expected_mix: int, expected_bond: int) -> bool:
    """Validate generated HTML and JSON files at a basic structural level."""
    if not html_path.exists() or not json_path.exists():
        print("  [FAIL] Missing output files")
        return False
    data = json.loads(json_path.read_text(encoding="utf-8"))
    mix_count = len(data.get("mix_funds", []))
    bond_count = len(data.get("bond_funds", []))
    if mix_count != expected_mix or bond_count != expected_bond:
        print(f"  [FAIL] JSON count mismatch: {mix_count} mix, {bond_count} bond")
        return False
    html = html_path.read_text(encoding="utf-8")
    if "FUNDS_DATA" not in html or "刷新数据" not in html:
        print("  [FAIL] HTML missing expected dashboard markers")
        return False
    print(f"  [OK] JSON verify: {mix_count} mix, {bond_count} bond")
    print(f"  [OK] HTML data injection verify: {mix_count + bond_count} fund objects")
    return True
