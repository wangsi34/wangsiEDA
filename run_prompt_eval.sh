#!/bin/bash
# 在本机（macOS / Linux）用真实模型评测内置提示词，不需要计算主机和 EDA 工具。
#   ./run_prompt_eval.sh                 # 4 个 Agent × A/B/C，约 60–90 次调用
#   ./run_prompt_eval.sh --repeats 2     # 其余参数原样传给 prompt-eval，见 --help
# API key 只在本次运行的进程里使用，不会写入任何文件。
set -e
cd "$(dirname "$0")/cloud"
command -v python3 >/dev/null || { echo "找不到 python3"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "需要 Python 3.9 或以上"; exit 1; }
[ -f wangsi_config.json ] || { echo "缺少 cloud/wangsi_config.json（至少要有 llm_model）"; exit 1; }
if [ -z "${WANGSI_LLM_API_KEY:-}" ]; then
  read -r -s -p "模型 API key（输入时不显示）: " WANGSI_LLM_API_KEY; echo
fi
export WANGSI_LLM_API_KEY
export WANGSI_LLM_BASE_URL="${WANGSI_LLM_BASE_URL:-https://api.deepseek.com}"
python3 -B -u -m wangsieda.prompt_eval "$@"
