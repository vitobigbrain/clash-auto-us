#!/usr/bin/env bash
#
# clash-auto-us 安装器
# 用法:
#   ./install.sh
#   CLASH_CONTROLLER=http://127.0.0.1:9090 CLASH_SECRET=xxx ./install.sh
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.clash.auto-us"
SCRIPT_DST="$HOME/.config/clash/clash-auto-us.py"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$HOME/.config/clash/clash-auto-us.log"
ERR="$HOME/.config/clash/clash-auto-us.err"

if [ ! -f "$SRC_DIR/clash-auto-us.py" ]; then
    echo "✗ 找不到 clash-auto-us.py，请在仓库目录内运行。" >&2
    exit 1
fi

mkdir -p "$HOME/.config/clash" "$HOME/Library/LaunchAgents"
cp "$SRC_DIR/clash-auto-us.py" "$SCRIPT_DST"
chmod 755 "$SCRIPT_DST"

# 可选：把控制接口配置写入 launchd 环境变量
ENV_BLOCK=""
if [ -n "${CLASH_CONTROLLER:-}" ]; then
    ENV_BLOCK="${ENV_BLOCK}
        <key>CLASH_CONTROLLER</key><string>${CLASH_CONTROLLER}</string>"
fi
if [ -n "${CLASH_SECRET:-}" ]; then
    ENV_BLOCK="${ENV_BLOCK}
        <key>CLASH_SECRET</key><string>${CLASH_SECRET}</string>"
fi
if [ -n "$ENV_BLOCK" ]; then
    ENV_BLOCK="    <key>EnvironmentVariables</key>
    <dict>${ENV_BLOCK}
    </dict>"
fi

cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${SCRIPT_DST}</string>
        <string>--once</string>
    </array>
    <key>StartInterval</key><integer>1800</integer>
    <key>RunAtLoad</key><true/>
${ENV_BLOCK}
    <key>StandardOutPath</key><string>${LOG}</string>
    <key>StandardErrorPath</key><string>${ERR}</string>
</dict>
</plist>
EOF

# 先卸载同名旧任务（若存在），再加载
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load -w "$PLIST_DST" 2>/dev/null || launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true

echo "✅ 已安装 clash-auto-us"
echo "   脚本 : $SCRIPT_DST"
echo "   任务 : ${PLIST_DST}（每 1800 秒运行一次）"
echo "   日志 : $LOG"
echo
echo "先手动验证一次（只测速、不切换）："
echo "   /usr/bin/python3 \"$SCRIPT_DST\" --once --dry-run"
