# whereabouts-mcp weather 字段补丁

> 使 `POST /location/ingest` 接受并持久化 `weather` 可选字段（随停留记录落盘）。
> 上游 README 声明了 `notes` 字段但代码未实现，本补丁亦不涉及 `notes`。

上游：https://github.com/WenXiaoWendy/whereabouts-mcp（GPL-3.0）
本补丁许可证：**GPL-3.0**（对 GPL 项目的修改必须同许可证发布）

## 为什么需要

iOS 快捷指令上报时顺带携带当地天气（`从当前位置获取当前天气` 动作），
注入插件即可在每轮上下文里直接展示天气，无需额外调用天气 API。

## 应用方法

```bash
cd whereabouts-mcp
patch -p1 < patches/whereabouts-weather-field.patch
npm run check && npm test   # 19/19 应全过
# 重启服务生效
```

## 改动内容（3 处、各 1 行）

均为同一模式：在 `src/location-store.js` 的字段白名单中加入 `weather`。

1. `normalizeLocationPoint()` — 接收入库
2. `applyPointMetadata()` — 合并进停留（stay）
3. `createPendingBreak()` — 新停留点携带

## 建议同时向上游提 PR

本补丁对所有用户有价值（`notes` 字段同样只差一行），欢迎以此为底稿
向上游提交 PR；合并后可直接删除本补丁。
