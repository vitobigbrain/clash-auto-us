#!/usr/bin/env bash
#
# 让终端在 Clash 开启时自动继承代理（写入 shell rc，幂等）。
#
# 用法:
#   ./setup-terminal-proxy.sh            # 写入 ~/.zshrc
#   ./setup-terminal-proxy.sh ~/.zshrc.profile   # 写入指定文件
#
# 原理: 每次提示符出现 / 执行命令前, 探测本地 Clash 的混合端口是否可连,
#       可连则导出 http_proxy / https_proxy / all_proxy, 否则清除。
#       端口自动探测常见值(7890 / 17890 / 7891 / 1080), 不写死。
#
set -euo pipefail

RC="${1:-$HOME/.zshrc}"
BEGIN="# >>> clash-auto-us terminal proxy >>>"
END="# <<< clash-auto-us terminal proxy <<<"

SNIPPET='# >>> clash-auto-us terminal proxy >>>
# 当本地 Clash 的 mixed/SOCKS 端口可连时，自动导出终端代理变量；否则清除。
_clash_auto_us_unset_proxy() {
  local v
  for v in http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; do
    case "${(P)v}" in
      http://127.0.0.1:*|socks5://127.0.0.1:*) unset "$v" ;;
    esac
  done
}
_clash_auto_us_refresh_proxy() {
  local p fd found=""
  local -a candidates
  zmodload zsh/net/tcp 2>/dev/null || { _clash_auto_us_unset_proxy; return 0; }
  if [[ -n ${_clash_auto_us_port:-} ]]; then
    candidates=("$_clash_auto_us_port" 7890 17890 7891 1080)
  else
    candidates=(7890 17890 7891 1080)
  fi
  for p in "${candidates[@]}"; do
    if ztcp 127.0.0.1 "$p" 2>/dev/null; then
      fd=$REPLY
      ztcp -c "$fd" 2>/dev/null
      found=$p
      break
    fi
  done
  if [[ -n $found ]]; then
    _clash_auto_us_port=$found
    export http_proxy="http://127.0.0.1:$found"
    export https_proxy="$http_proxy"
    export all_proxy="socks5://127.0.0.1:$found"
    export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy" ALL_PROXY="$all_proxy"
  else
    _clash_auto_us_port=""
    _clash_auto_us_unset_proxy
  fi
  return 0
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _clash_auto_us_refresh_proxy
add-zsh-hook preexec _clash_auto_us_refresh_proxy
_clash_auto_us_refresh_proxy
# <<< clash-auto-us terminal proxy <<<'

touch "$RC"
if grep -qF "$BEGIN" "$RC" 2>/dev/null; then
    echo "已存在 clash-auto-us 终端代理配置，跳过。"
    exit 0
fi
printf '\n%s\n' "$SNIPPET" >> "$RC"
echo "✅ 已写入 $RC"
echo "   新开一个终端，或执行: exec zsh"
