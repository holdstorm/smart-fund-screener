#!/bin/bash
# Step 1: Search for candidate funds
# Categories: 偏股混合型, 灵活配置型, 纯债, 二级债

CATEGORIES=("偏股混合型" "灵活配置型" "纯债" "二级债")

for cat in "${CATEGORIES[@]}"; do
  echo "=== 搜索: $cat ==="
  yingmi-skill-cli mcp call SearchFunds --input "$(cat <<JSON
{"category":"${cat}","size":50,"sortColumn":"收益率","sortOrder":"降序"}
JSON
)"
done
