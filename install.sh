#!/usr/bin/env bash
# finkg 安装器（POSIX 包装）。真正的逻辑在 install.py，所有参数原样透传。
#
#   ./install.sh              # 交互
#   ./install.sh --all        # 装到所有检测到的 agent
#   ./install.sh --check      # 只校验
#
# 也可以一行装完（不 clone）：
#   curl -fsSL https://raw.githubusercontent.com/Joker-of-Gotham/finkg/main/install.sh | bash -s -- --all
set -euo pipefail

REPO_URL="https://github.com/Joker-of-Gotham/finkg.git"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "${PY:-}" ]; then
  echo "需要 Python 3.9 或更新版本，但没找到 python3/python" >&2
  exit 1
fi

# 走 curl | bash 时脚本不在仓库里，先把仓库取到临时目录
if [ ! -f "$HERE/install.py" ]; then
  command -v git >/dev/null 2>&1 || { echo "需要 git" >&2; exit 1; }
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  echo "正在获取 finkg …"
  git clone --depth 1 "$REPO_URL" "$TMP/finkg" >/dev/null 2>&1
  HERE="$TMP/finkg"
fi

exec "$PY" "$HERE/install.py" "$@"
