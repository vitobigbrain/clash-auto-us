# clash-auto-us

自动挑选并切换到 Clash Meta 订阅里**延迟最低的美国节点**，每 30 分钟一次。

- 基于 Clash Meta (mihomo) 的**外部控制接口**（RESTful API），不重启内核、不改订阅配置。
- 自动识别当前订阅的「主节点组」，换订阅（TAGSS / TapFog / …）无需改代码。
- 默认只使用 **1x 倍率**的美国节点，避免 5x / 10x 节点成倍消耗流量（可关闭）。
- 并发测速，几十个节点约 3 秒测完。
- 用 macOS 原生 `launchd` 定时，开机自启、重启不丢。

## 依赖

- macOS
- Python 3（系统自带 `/usr/bin/python3` 即可，**无需 pip 安装任何包**）
- Clash Meta / mihomo 内核（ClashX Meta、Clash Verge Rev、Mihomo Party 等 GUI 均可），且已开启 **External Controller**

## 安装

```bash
git clone https://github.com/vitobigbrain/clash-auto-us.git
cd clash-auto-us
chmod +x install.sh
./install.sh
```

安装器会：
1. 把 `clash-auto-us.py` 复制到 `~/.config/clash/`
2. 生成 `~/Library/LaunchAgents/com.clash.auto-us.plist`（`StartInterval` = 1800 秒）
3. `launchctl load` 注册，并立即跑一次

### 控制接口端口 / 密钥

脚本会**自动探测**常见端口（`9090` / `9097` / `19092` / `50441`），并尝试从 ClashX、ClashX Meta 的偏好设置里读取 `api-secret`。

如果自动探测失败（例如 Clash Verge Rev 用了特殊端口/密钥），安装时显式指定：

```bash
CLASH_CONTROLLER=http://127.0.0.1:9097 CLASH_SECRET=你的密钥 ./install.sh
```

也可以在 Clash GUI 的「设置 → 外部控制 / External Controller」里查看地址和密钥。

## 验证

```bash
# 只测速、看会切到哪个（不实际切换）
/usr/bin/python3 ~/.config/clash/clash-auto-us.py --once --dry-run

# 实时看日志
tail -f ~/.config/clash/clash-auto-us.log
```

正常输出示例：

```
[2026-10-27 12:03:34] 按倍率过滤:32 个美国节点 → 保留 24 个 1x 节点。
[2026-10-27 12:03:34] 主节点组: '🙂 TAGSS' | 当前节点: '🇺🇸 美国-家宽 06丨5x US'
[2026-10-27 12:03:34] 发现 24 个美国节点,开始并发测速…
[2026-10-27 12:03:38]   1. 🇺🇸 美国-夏洛特丨1x US: 191 ms
[2026-10-27 12:03:38]   2. 🇺🇸 美国-实验节点丨1x US: 197 ms
[2026-10-27 12:03:38] 🔄 已把 '🙂 TAGSS' 切换到: '🇺🇸 美国-夏洛特丨1x US' (191 ms)
```

## 终端自动继承代理（可选）

很多命令行工具（`curl`、`git`、`pip`、`npm`…）只认 `http_proxy` / `https_proxy` 环境变量，**不会**自动使用 macOS 的系统代理。跑一次：

```bash
./setup-terminal-proxy.sh
```

它会向 `~/.zshrc` 追加一段（幂等、带标记块）自包含配置：每次执行命令前探测本地 Clash 的混合端口
（自动识别 `7890` / `17890` / `7891` / `1080`，并缓存上次命中的端口），端口可连就自动导出代理变量，
关闭 VPN 后自动清除。新开终端即生效。

> 仅支持 zsh（macOS 默认 shell）。用 bash 可把脚本生成的片段改放到 `~/.bashrc`。

## 配置

均可用环境变量覆盖（或改脚本顶部常量）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `CLASH_CONTROLLER` | 自动探测 | 外部控制地址，如 `http://127.0.0.1:9090` |
| `CLASH_SECRET` | 空 | 外部控制密钥（没有就留空） |
| `CLASH_GROUP` | 自动识别 | 主节点组名，如 `🙂 TAGSS` |
| `CLASH_US_KEYWORDS` | `🇺🇸,美国` | 判定美国节点的关键词，逗号分隔 |
| `CLASH_X_FILTER` | `1` | 只保留该倍率节点；设为空字符串 `""` 关闭倍率过滤 |
| `CLASH_DELAY_URL` | gstatic 204 | 测速地址 |
| `CLASH_TIMEOUT_MS` | `5000` | 单节点测速超时（毫秒） |
| `CLASH_MIN_GAIN_MS` | `20` | 比当前节点快多少才切换（防抖动） |
| `CLASH_CONCURRENCY` | `16` | 并发测速线程数 |

修改每 30 分钟的周期：编辑 `~/Library/LaunchAgents/com.clash.auto-us.plist` 里的
`<key>StartInterval</key><integer>1800</integer>`（单位秒），然后：

```bash
launchctl unload ~/Library/LaunchAgents/com.clash.auto-us.plist
launchctl load   ~/Library/LaunchAgents/com.clash.auto-us.plist
```

## 手动使用（不装 launchd）

```bash
python3 clash-auto-us.py --once            # 跑一次
python3 clash-auto-us.py --once --dry-run  # 只看结果不切换
python3 clash-auto-us.py --loop            # 常驻循环
```

## 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.clash.auto-us.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.clash.auto-us.plist
rm -f ~/.config/clash/clash-auto-us.py
```

## 实现说明

- **主节点组识别**：在除 `GLOBAL` 外的 `Selector` 组中，取「真实节点数最多」者；并列时取「被其他组引用次数最多」者（即各分流组默认指向的那个）。TAGSS、TapFog 两种结构都能正确命中。
- **美国节点判定**：节点名包含 `美国` 或 `🇺🇸`（可配置）。
- **倍率过滤**：匹配节点名里的 `(\d+)x`；`X_FILTER=1` 时只保留 1x，`5x`/`10x` 等被排除。没有倍率标记的命名（如 TapFog 的 `Bronze-美国-…`）直接放行。
- **切换**：`PUT /proxies/{组}`，请求体 `{"name": 节点}`，mihomo 即时生效。

## License

MIT
