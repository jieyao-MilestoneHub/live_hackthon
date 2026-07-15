#!/usr/bin/env bash
# Probe: 確認 Claude-on-Bedrock 的 entitlement + 強制 tool-use 可用，再寫進 IAM/terraform。
# House rule：每加一個新 AWS 服務/模型，先小 probe(InvokeModel 試打)確認 SCP/entitlement 允許。
#
# 用法（用臨時 workshop 憑證，勿進 repo）：
#   source <你的 scratchpad env 檔>        # 匯出 AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN
#   bash backend-api/scripts/probe_bedrock_claude.sh
#
# 腳本會：(1) 列出帳號已 entitle 的 Anthropic 模型；(2) 對 Haiku 4.5 / Sonnet 5 各試打一次
# Converse + 強制 tool-use，先試 `anthropic.*`、失敗再試 `us.anthropic.*`(跨區 inference profile)，
# 印出可用的 model id。把可用 id 填進 EDIT_PLANNER_MODEL_ID / EDIT_PLANNER_QUALITY_MODEL_ID。
set -uo pipefail
REGION="${AWS_REGION:-us-east-1}"

echo "== 0. caller identity =="
aws sts get-caller-identity --query 'Arn' --output text || {
  echo "  無有效憑證 — 先 source 臨時憑證再跑。"; exit 1; }

echo
echo "== 1. $REGION 內已 entitle 的 Anthropic 模型 =="
aws bedrock list-foundation-models --region "$REGION" --by-provider anthropic \
  --query 'modelSummaries[?contains(modelId,`claude`)].modelId' --output table \
  || echo "  (list 失敗：可能 bedrock:ListFoundationModels 被擋，或無 Bedrock 權限)"

# 強制 tool-use 的最小 toolConfig（與 planner 的 plan_edit 同形狀，只放一個 bool）
TOOLCFG='{"tools":[{"toolSpec":{"name":"plan_edit","description":"probe forced tool use","inputSchema":{"json":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}}}}],"toolChoice":{"tool":{"name":"plan_edit"}}}'
MSGS='[{"role":"user","content":[{"text":"呼叫 plan_edit，ok=true"}]}]'
INFER='{"temperature":0,"maxTokens":128}'

probe_converse () {
  local model="$1"
  local name
  name=$(aws bedrock-runtime converse --region "$REGION" \
      --model-id "$model" \
      --messages "$MSGS" \
      --tool-config "$TOOLCFG" \
      --inference-config "$INFER" \
      --query 'output.message.content[?toolUse].toolUse.name | [0]' \
      --output text 2>/tmp/_probe_err)
  if [ "$name" = "plan_edit" ]; then
    echo "  ✅ 可用：$model  → 回了 plan_edit toolUse（entitlement + 強制 tool-use OK）"
    return 0
  fi
  echo "  ❌ 失敗：$model"
  sed 's/^/       /' /tmp/_probe_err | head -4
  return 1
}

for tier in "haiku-4-5:fast:EDIT_PLANNER_MODEL_ID" "sonnet-5:quality:EDIT_PLANNER_QUALITY_MODEL_ID"; do
  id="${tier%%:*}"; rest="${tier#*:}"; label="${rest%%:*}"; env="${rest##*:}"
  echo
  echo "### tier=$label  ($env) ###"
  if probe_converse "anthropic.claude-$id"; then
    echo "     → 設 $env=anthropic.claude-$id"
  elif probe_converse "us.anthropic.claude-$id"; then
    echo "     → 需跨區 inference profile；設 $env=us.anthropic.claude-$id"
  else
    echo "     → $id 不可用：到 Bedrock console 開 model access，或確認 SCP 未擋 bedrock:InvokeModel。"
  fi
done

echo
echo "== 完成。把上面標「可用」的 id 填進 Lambda env(EDIT_PLANNER_MODEL_ID / _QUALITY_MODEL_ID)， =="
echo "== 並在 IAM 授予對應 foundation-model / inference-profile ARN 的 bedrock:InvokeModel。 =="
