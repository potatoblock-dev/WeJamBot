# Agent Context — WeJamBot

> 多 Agent / 多对话共享的项目记忆。Hub `conversations/{标题}.md` 只存指针与本场状态；**可复用的决策与进展写在此文件**。
>
> 最后更新：2026-10-24 00:20 +0800

## 当前焦点

- **任务**：消息 SoT 迁 Callus-Bridge openclaw 非 GUI（`:7702`）。
- **下一步**：解限后扫码活体；WeJamBot `WEJAM_TARGET=127.0.0.1:7702` 联调。
- **活跃对话**：OpenClaw 降风险接入

## 架构与决策

| ID | 日期 | 决策 | 相关文件 |
|----|------|------|----------|
| d1 | 2026-10-20 | 手眼契约在宿主 `bot/eyes.py`（`WeChatEyes`、普通数据、`HandsError`）。不改 proto。驱动事件只留在 `WeJamHands`。大脑不 import protobuf / wejam。 | `bot/eyes.py` |
| d2 | 2026-10-20 | 默认人设＝群聊搭子（短句、无 Markdown/客服腔、few-shot）；分条仍用单独一行 `---`。安全条留在 `reply_policy.toml`。 | `examples/personas/default/persona.txt` |
| d3 | 2026-10-20 | 「复刻人设」结论：部分可行。驱动已有 ListMessages（仅当前可见窗）；eyes 未暴露。无滚屏＝无完整历史。推荐离线草稿+人工审，勿与 impression（对用户印象）混用。 | `bot/eyes.py`、`agent/chat.py` |
| d4 | 2026-10-22 | 群味：每群 JSON，成功回复后重写并注入 PromptBook。知识库：全局 `notebook.md`（固定 ## + 宿主 TOC），回合后 pipe 行 upsert；只注入目录+命中词条。不联网、不扩 bridge。 | `bot/group_culture/`、`bot/knowledge/` |
| d5 | 2026-10-23 | 新 bridge 独立仓 Callus-Bridge（拷贝自本仓 agent，不改本仓 agent）。gRPC 仍 `wejam.v1` 线兼容；增量 ReadLatest/ReadContext/CaptureMessageMedia。宿主 `context_message_limit` 默认 30；表情图 base64 送 deepseek-flash。 | `../Callus-Bridge/`、`bot/chat.py`、`reply_policy.toml` |
| d6 | 2026-10-23 | 版本更新日志 Esc 无效；树里有两个「关闭」，只能点版本标题后的那个。`_popup_guard` 负责关弹层并取消误触的退出登录。 | `agent/chat.py`、`agent/update_dialog.py` |
| d7 | 2026-10-24 | 降风险：收发 SoT = Callus-Bridge `feat/openclaw-non-gui`（ilink 扫码+长轮询，`:7702`）。Linux GUI WeJam/Callus 仅遗留。大脑仍 `WeChatEyes`。 | `../Callus-Bridge/openclaw/`、`docs/openclaw.md` |

## 进展

- [x] `WeChatEyes`：列会话、订阅、发文本、登录状态、二维码
- [x] `WeJamHands` 实现该契约，挡掉 MessageEvent
- [x] `consume_messages` / `TextSender` / `incoming_turn` 只吃普通消息
- [x] `tests/test_bot_brain.py` 用假手眼跑大脑，导入大脑不加载 wejam
- [ ] 控制台登录仍直接用 `wejam.Client`（这次不迁）
- [x] 更新日志弹窗：选对「关闭」、热更新进运行中容器

## 关键路径

| 用途 | 路径 |
|------|------|
| 手眼契约 | `bot/eyes.py` |
| WeJam 实现 | `bot/hands.py` |
| 大脑入口 | `bot/chat.py`、`bot/runner.py` |
| 假手眼单测 | `tests/test_bot_brain.py` |
| 默认人设 | `examples/personas/default/persona.txt`（运行时 `data/personas/`，gitignore） |
| 群文化 | `bot/group_culture/` → `data/group_culture/` |
| 知识库 | `bot/knowledge/` → `data/knowledge/notebook.md`；种子 `examples/knowledge/notebook.md` |
| 更新日志关弹层 | `agent/update_dialog.py`、`agent/chat.py` `_popup_guard` |
| OpenClaw 桥（推荐） | `../Callus-Bridge/` 分支 `feat/openclaw-non-gui`，`WEJAM_TARGET=127.0.0.1:7702` |

## 阻塞 / 未决

- **阻塞**：无
- **未决问题**：控制台是否改走 `WeChatEyes`，另开任务

## 专题文档

| 文档 | 用途 |
|------|------|
| `README.md` | 手眼 / 脑分层说明 |

## Handoff

契约在 `bot/eyes.py`。`WeJamHands.subscribe` 把文本行收成 `SeenMessage`，时间/系统行不出这个文件。大脑用 `incoming_turn` 和 `TextSender`。换实现只改 `_open_wejam`。不要改 `proto/` 或人设。控制台仍走 `wejam.Client`。
