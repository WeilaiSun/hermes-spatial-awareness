# 空间感知系统（Spatial Awareness）设计文档 v1.0

> 作者：Hermes（大圣拍板立项）· 2026-10-10
> 状态：待审查（子 Agent 量化评审 GO/NO-GO）
> 关联：time-injector 插件（结构模板）、hermespilot-ops skill、HA 桥接

---

## 1. 背景与目标

### 1.1 需求（大圣 2026-10-10 拍板）

1. **经纬度级实时位置**：手机侧 iOS 快捷指令主动上报位置，Hermes 每轮对话自动感知"我在哪"
2. **通道感知 + 位置感知合并注入**：判断消息来自哪个通道（桌面 GUI / HermesPilot App / 飞书），用于决定文件发送方式与交互场景；**不设"手机端简短回复"类规则**（App 端工具调用和代码块自带折叠）
3. **地理口径勘误（必须遵守）**：公司在**上海**（非苏州）；家=上海；装修中的房子在**苏州**

### 1.2 被动推断能力已验证（L1-L3）

2026-10-10 实测：Tailscale status 可判定设备在场与连接路径（手机 DERP 中继=室外蜂窝、直连某设备=在对应 WiFi）；宽带出口 IP 可得城市锚点（家=上海电信 114.88.x.x）。本设计补齐 **L4 精确坐标**。

### 1.3 设计原则

- **复用优先**：接收+聚合层用社区成熟方案 whereabouts-mcp，不重复造轮子
- **注入层薄**：自研插件只做"读状态文件→组注入串→返回"，不做聚合逻辑
- **隐私最小化**：Token 保护、仅 Tailscale 网段可达、环形保留不做轨迹库、设计文档/日志不含真实坐标
- **降级可用**：位置服务挂掉/超时/数据过期时，注入自动降级为时间+通道，不影响对话

---

## 2. 社区方案调研结论（2026-10-10）

| 方案 | 匹配度（主观评估，非实测） | 结论 |
|------|--------|------|
| **whereabouts-mcp**（WenXiaoWendy，39★，AGPL-3.0，中文） | ~90% | **采用为接收端**。POST /location/ingest + Bearer Token；停留聚合（Stay/Movement/in_transit）、家/司语义标签（配置中心坐标+半径）、电量趋势；本地 JSON 存储；Node ≥22（本机 v24.14 ✅） |
| OwnTracks（HTTP/MQTT） | 40% | 不采用：需专用 App + MQTT 生态过重；借鉴其 payload 字段规范 |
| HA Companion App | 70%（备选） | GPS 上报全自动且比快捷指令可靠，但需装 HA App；用户已拍板快捷指令路线，HA 路线列为 Plan B |
| Hermes 社区插件 | 0 | 无现成位置/通道感知插件，注入层必须自研（time-injector 为结构模板） |
| Hermes Link REST | 0（不能加路由） | Link 133 路由闭源固定，无法扩展；改用独立接收服务 + Tailscale 入口，达意相同 |

**AGPL-3.0 合规**：whereabouts-mcp 仅作为独立本地服务运行（不修改其源码、不与其组成单一分发作品），自研插件通过文件系统与其解耦，无传染风险；如其源码被修改则需开源修改部分——本设计不改其源码。

---

## 3. 总体架构

```
┌─ iPhone 快捷指令（自动化触发）────────────────┐
│ 触发: WiFi 连/断 · 到达/离开家司地理围栏 ·      │
│       手动按钮 · 定时(≥30min兜底)              │
│ 动作: 获取当前位置 → POST JSON                 │
└──────────────┬───────────────────────────────┘
               │ POST http://100.75.68.9:4318/location/ingest
               │ Authorization: Bearer <TOKEN>
               │ {latitude, longitude, timestamp, trigger,
               │  batteryLevel, deviceName, address?}
               ▼
┌─ 接收端：whereabouts-mcp serve（PC 常驻）─────┐
│ · Token 校验 · 停留点聚合 · home/work 标签     │
│ · 写 ~/.whereabouts-mcp/locations.json        │
└──────────────┬───────────────────────────────┘
               ▼
┌─ spatial-injector 插件（自研，HERMES_HOME/plugins/）┐
│ pre_llm_call hook 每轮:                            │
│ ① 读 locations.json → 最新位置+停留状态            │
│ ② 判定消息通道（见 §6）                            │
│ ③ 组装注入串（见 §5.3），读文件失败自动降级         │
└──────────────────────────────────────────────────┘
```

---

## 4. 组件设计

### 4.1 接收端：whereabouts-mcp 部署

- **安装**：`npm i -g whereabouts-mcp`（或 clone 到 `HERMES_WORK/spatial-awareness/whereabouts-mcp/` 固定版本）
- **常驻**：**计划任务 S4U 用户态自启**（沿用 `gateway-service/Hermes_Gateway_S4U.cmd` 模式），**不用 NSSM/LocalSystem 服务**——v1.2 修订：审查第 3 轮实证 `HermesWatchdog` 为 LocalSystem 账户，若 whereabouts 同样以 LocalSystem 运行，`~` 解析为 `C:\Windows\system32\config\systemprofile`，与用户态 Hermes 进程读的 `C:\Users\Administrator\.whereabouts-mcp\` 路径分裂（10/7 serve-SYSTEM 分裂坑同类）。**双保险**：①服务以用户身份跑（S4U 计划任务）；②显式设 `WHEREABOUTS_STATE_DIR=<spatial-root>\state` 绝对路径，彻底消除 HOME 依赖，插件读同一绝对路径
- **监听**：`WHEREABOUTS_HOST=0.0.0.0`（Tailscale 网卡+局域网均可达），`PORT=4318`
- **鉴权**：`WHEREABOUTS_TOKEN` 写入 `HERMES_HOME/.env`（不入库不入 git；Windows 防火墙新增入站规则仅放行 100.64.0.0/10 + 192.168.110.0/24）
- **地点标签**（真实坐标**部署时配在环境变量**，本文档及任何日志只允许占位符）：
  - `WHEREABOUTS_HOME_CENTER=<home_lat>,<home_lng>`（上海）
  - `WHEREABOUTS_WORK_CENTER=<work_lat>,<work_lng>`（上海）
  - `PLACE_RADIUS_METERS=150`
  - 可选扩展：`KNOWN_PLACES` 加 `suzhou_home`（苏州装修房）标签
- **数据保留**：`HISTORY_LIMIT=20`、`MOVEMENT_EVENT_LIMIT=20`、`BATTERY_HISTORY_LIMIT=100`（默认即环形，不做轨迹库）

### 4.2 手机端：iOS 快捷指令规格

**自动化触发器（建议 4 条，均关"运行前询问"）**：
| 触发 | 动作 | trigger 字段 |
|------|------|--------------|
| 加入家庭 WiFi / 离开 | 获取当前位置→上报 | arrive_home / leave_home |
| 到达公司 / 离开公司（地理围栏） | 同上 | arrive_work / leave_work |
| 打开/关闭 HermesPilot App | 上报当前位置 | app_open |
| 手动快捷指令按钮 | 上报 | manual |

**上报动作**：`获取当前位置` → `URL 内容 POST`（JSON）：
```json
{
  "latitude": "<当前纬度>",        // 快捷指令变量，系统默认 6 位以上小数
  "longitude": "<当前经度>",
  "timestamp": "<当前日期时间 ISO>",
  "trigger": "arrive_home",
  "batteryLevel": "<电池电量>",
  "deviceName": "iPhone-15",
  "source": "shortcuts"
}
```
- URL：`http://100.75.68.9:4318/location/ingest`，Header：`Authorization: Bearer <TOKEN>`
- 超时 10s、不重试（下一个触发器会再报）；失败静默（位置感知是增强，不是关键路径）
- 上报频控：服务端 5 分钟内同 trigger 重复上报直接覆盖，无成本

### 4.3 注入插件：spatial-injector（自研）

**结构**（完全复刻 time-injector）：
```
HERMES_HOME/plugins/spatial-injector/
├── plugin.yaml    # name/version/kind: standalone/hooks: [pre_llm_call]
└── __init__.py    # ~120 行
```

**plugin.yaml**：
```yaml
name: spatial-injector
version: "1.0.0"
description: "Injects location (from whereabouts-mcp state file) and channel context into each turn via pre_llm_call. Degrades gracefully to time-only."
author: "Hermes + 大圣"
kind: standalone
hooks:
  - pre_llm_call
```

**注入格式**（单行、方括号、与时间注入并列）：
```
[Current context: 2026-10-10 22:33 CST | Location: work(上海) · 已停留3h12m · 上报于5min前 · 电量82% | Channel: hermespilot]
```
- 无位置数据/数据过期（>24h）：`[Current context: 2026-10-10 22:33 CST | Location: unknown(上次上报 3 天前) | Channel: hermespilot]`
- 状态文件读取失败：`[Current context: 2026-10-10 22:33 CST | Channel: hermespilot]`
- 位置精度**注入时降为 3 位小数**（~110m，够场景判断且不在会话记录留精确坐标；工具需要精确坐标时现场读状态文件）

**逻辑要点**：
1. 读 `~/.whereabouts-mcp/locations.json`：取 currentStay（有 placeTag 用标签，无则用坐标+address 城市名）；停留时长、上报新鲜度（receivedAt）
2. 通道判定（§6）结果做会话内缓存（同轮多次 LLM 调用不重复判定）
3. 全程 try/except，任何异常降级为只注入时间——**绝不阻塞对话**
4. 坐标→城市：优先用上报自带 address 字段；无 address 时本地不加逆地理编码（避免外网依赖），只显示标签/坐标

### 4.4 通道判定策略（v1.1 修订，D0 源码实证后）

**已实证（2026-10-10，agent/turn_context.py pre_llm_call 调用点）**：hook payload 原生携带以下字段，接受 `**kwargs` 的回调可直接读取：

```python
session_id=agent.session_id, task_id=..., turn_id=...,
user_message=..., conversation_history=[...], is_first_turn=bool,
model=agent.model,
platform=getattr(agent, "platform", None) or "",      # ← 通道标识，直读
parent_session_id=..., sender_id=getattr(agent, "_user_id", None) or "",
```

**v1.1 判定逻辑（简化）**：
1. **主路**：直接读 `kwargs["platform"]` 字段映射通道（字段值→通道的枚举映射在编码时用三通道实测各发 1 条消息标定；预期 tui=桌面/TUI、gateway 平台标识=飞书、HermesPilot 走 tui_gateway 需结合 sender_id/会话特征区分——实测标定表进验收清单）
2. **fallback**（platform 字段值无法区分 HermesPilot 与桌面 TUI 时）：才启用 link.db 会话比对（conversation_stats 表 conversation_id/hermes_session_id/updated_at 列已实证存在，只读 URI + 1s 超时）
3. 兜底：`unknown`

优于 v1.0 之处：主路零 IO、无 db 锁风险；link.db 从必需依赖降级为 fallback。**遗留实证项**：HermesPilot 会话的 platform 字段实际取值需编码后在真机标定（三通道各发 1 条消息记录字段值，验收清单 §6.2 已含）。

---

## 5. 隐私与安全边界

1. **传输**：仅 Tailscale 网段 + 家庭局域网可达（防火墙白名单），公网不可达；Bearer Token 必填
2. **存储**：whereabouts 本地 JSON 环形保留（20 条停留/20 条移动/100 条电量），不建轨迹历史库
3. **注入脱敏**：进会话记录的坐标截断到 3 位小数；会话日志不落 Token
4. **文档/日志红线**：真实家/司坐标只存在于 `HERMES_HOME/.env` 与快捷指令内；本设计文档、git 仓库、日志、截图一律占位符（whereabouts 官方同款纪律）
5. **控制权**：手机快捷指令随时可删=上报即停；服务停掉=插件自动降级；无云端第三方经手（Tailscale 点对点）
6. **撤销**：`sc stop WhereaboutsLocation` + 删插件目录 + `/reset` 即完全移除

---

## 6. 部署与验收

### 6.1 部署顺序

1. `npm i -g whereabouts-mcp` → .env 配 TOKEN/坐标 → 防火墙规则 → NSSM 服务注册 → `curl -X POST 本机:4318/location/ingest` 模拟上报自测
2. 部署 spatial-injector → `/reset` 生效 → 桌面端看注入串（时间+Channel，Location=unknown）
3. 手机配快捷指令（4 条自动化）→ 真机上报 → 注入串出现 Location
4. 通道实测：分别在 App/桌面 GUI/飞书 发消息，核对 Channel 字段

### 6.2 验收清单（命令化，审查与编码后逐条取证）

- [ ] `curl -H "Authorization: Bearer <TOKEN>" -d @test_payload.json http://127.0.0.1:4318/location/ingest` 返回 `{"ok":true,...}`
- [ ] 无 Token 请求返回 401/403
- [ ] `cat ~/.whereabouts-mcp/locations.json` 出现 currentStay 且 placeTag 正确（home/work 标签命中）
- [ ] 停留聚合验证：连续 2 次不同 trigger 上报同一坐标，currentStay 合并为一个停留
- [ ] `/reset` 后新会话注入串含 `[Current context: ...]` 且格式合法
- [ ] 停掉 whereabouts 服务 → 注入降级为时间+通道，对话正常（降级演练）
- [ ] 过期数据演练：手工改 locations.json 时间戳为 3 天前 → 注入显示 unknown 分支
- [ ] 手机真机 4 条触发器各验 1 次（WiFi 切换/围栏/App 冷启/手动）上报到达
- [ ] 通道判定：三通道各发 1 条消息，Channel 字段正确
- [ ] 会话记录中的坐标 ≤3 位小数（脱敏抽查）
- [ ] NSSM 服务重启机后自恢复

### 6.3 风险与回滚

| 风险 | 缓解 | 回滚 |
|------|------|------|
| iOS 快捷指令后台触发不执行（系统限制） | 多触发器冗余（WiFi+围栏+App 开启）；关键场景手动按钮兜底 | Plan B：HA Companion App 全自动上报 |
| link.db 被锁/读超时 | 只读 URI + try/except 降级 | 通道字段退化为 unknown |
| whereabouts 停止/崩溃 | NSSM 自动重启；插件降级不阻塞 | sc delete 即移除 |
| daemon 侧拿不到会话标识导致通道判定不准 | D0 源码核对前置；启发式兜底 | 保留时间+位置注入，通道后补 |
| 插件注入拖慢每轮延迟 | 读本地文件+1s 超时上限，预计 <10ms | time-injector 同级开销，无需熔断 |

---

## 7. 明确不做（v1 范围外）

- 不做轨迹历史库/周报（whereabouts 有 summary 能力，v2 再议）
- 不做「移动事件唤醒 Agent」主动打扰（Cyberboss 有此模式，与用户习惯不符）
- 不做 PC/Mac 侧自身定位（场景价值低）
- 不改 Hermes Link 源码（升级覆盖铁律）
- 不做电量预警推送（字段先收着，v2 再议）
