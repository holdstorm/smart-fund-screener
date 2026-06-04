#!/usr/bin/env python3
"""
天天基金 Skills API 客户端 — 文件中介模式（避免GBK编码问题）
"""
import json, os, subprocess, sys, uuid

TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp")
API_URL = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke"
API_KEY = os.environ.get("TTFUND_APIKEY") or os.environ.get("TTFUND_API_KEY", "")

def invoke_skill(skill_id, version, params):
    if not API_KEY:
        return {"error": "missing_env", "message": "Set TTFUND_APIKEY before calling the TTFund API."}
    """Call a天天基金 Skill via HTTP API (file-mediated)"""
    body = json.dumps({"skill_id": skill_id, "_skill_version": version, "params": params})
    outfile = os.path.join(TMP, f"ttfund_resp_{uuid.uuid4().hex[:8]}.json")
    
    # Write request body to file, use curl to POST, save response to file
    bodyfile = os.path.join(TMP, f"ttfund_req_{uuid.uuid4().hex[:8]}.json")
    with open(bodyfile, "w", encoding="utf-8") as f:
        f.write(body)
    
    curl_cmd = (
        f'curl -s -X POST "{API_URL}" '
        f'-H "X-API-Key: {API_KEY}" '
        f'-H "Content-Type: application/json" '
        f'-d @"{bodyfile}" '
        f'> "{outfile}"'
    )
    
    result = subprocess.run(curl_cmd, shell=True, capture_output=True, text=True, timeout=30)
    
    # Clean up request body
    try: os.remove(bodyfile)
    except: pass
    
    if os.path.exists(outfile):
        with open(outfile, "r", encoding="utf-8") as f:
            content = f.read()
        try: os.remove(outfile)
        except: pass
        try:
            return json.loads(content)
        except:
            return {"error": "parse_failed", "raw": content[:500]}
    else:
        return {"error": "no_output", "stderr": result.stderr[:500]}

def condition_select(order_field, page=0, page_size=30, fund_type=None):
    params = {"orderField": order_field, "pageIndex": page, "pageNum": page_size}
    if fund_type: params["rsfType"] = fund_type
    return invoke_skill("FUND_CONDITION_SELECT", "1.1.0", params)

def get_fund_nav(fund_id, range_type="y"):
    return invoke_skill("FUND_NAV_INFO", "1.0.0", {"fund_id": fund_id, "range": range_type})

def parse_condition_result(response):
    if response.get("code") != 0: return []
    raw = response.get("data", {}).get("raw_result", {})
    body = raw.get("body", {})
    data = body.get("Data", [])
    return data if isinstance(data, list) else []

if __name__ == "__main__":
    print("=== 测试条件选基 ===")
    r = condition_select("5_6_-1", 0, 3, "1103")
    print(f"Response code: {r.get('code')}")
    funds = parse_condition_result(r)
    for f in funds:
        print(f"  {f.get('fundCode')} {f.get('fundName')}  近1年={f.get('yearSyl')}%  近1月={f.get('hySyl')}%")
    if not funds:
        print(f"  Raw: {json.dumps(r, ensure_ascii=False, indent=2)[:1000]}")
