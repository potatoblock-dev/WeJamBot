# Agent Context — WeJamBot

> 多 Agent / 多对话共享的项目记忆。Hub `conversations/{标题}.md` 只存指针与本场状态；**可复用的决策与进展写在此文件**。
>
> 最后更新：2026-10-26 20:00 +0800

## 当前焦点

- **任务**：消息 SoT = Callus-Bridge **GUI `:7701`**（个人微信群主路径）；openclaw `:7702` 仅私聊旁路。
- **下一步**：合入 Callus GUI enrich PR；活体验收 `make up` → 拉群 → `WEJAM_TARGET=7701`。
- **活跃对话**：GUI Callus 接入（bridge）；封禁风险（risk）
- **近况**：宿主默认切回 `7701`；openclaw 私聊活体仍记在 d12。

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
| d8 | 2026-10-24 | 人设：`template` 填空包（Role/Task/Voice，借鉴玖帕喵结构）；`default` 收紧短句/不代言/知识库示例。酒馆卡需精简再贴。 | `examples/personas/` |
| d9 | 2026-10-25 | 封禁评估：openclaw 去掉 GUI 点击面，仍是个人号 bot。会话被标成单聊，群闸门不生效，也不能坐进现有微信群。限制未在手机上解除前不扫码；解限后只做一次私聊，不接群、不开 `:7700`/`:7701`。 | `openclaw/server.py`、`reply_policy.toml` |
| d10 | 2026-10-26 | 复评：距 10-23「协助诈骗」约两天半，仍无手机解限证据。日期本身不构成解限。手机仍有限制则不扫码；手机已能正常收发才允许一次私聊，不接群、不开 GUI。 | `docs/agent/context.md` |
| d11 | 2026-10-26 | 用户自述用 bot 号给自己发了一条，当次无账号异常提示。满足一次私聊试通的手机侧条件。扫码仍是另一次 bot 绑定，不挂机、不接群、不开 GUI。 | `docs/agent/context.md` |
| d12 | 2026-10-26 | 17:29 一次私聊已通（收「测试」回「在，测啥」，sendmessage 200，进程已停）。低频私聊尚未允许。观察至 2026-10-27 17:30，无新限制再由 risk 对话决定。 | `docs/agent/context.md` |
| d13 | 2026-10-26 | survey：[SnowLuma/SnowLuma](https://github.com/SnowLuma/SnowLuma) @ `3bd74ac`。只借鉴边界（历史不进当轮、self 用 id、媒体短引用、操作面默认只读）。许可非 OSI、不搬源码/原生组件。眼睛层已同构不必重做。观察期结束前不落地。 | `docs/agent/survey-snowluma.md` |
| d14 | 2026-10-26 | 个人微信群要求 → **GUI Callus `:7701` 为主 SoT**（可进群）；openclaw 不能进个人群，降为私聊旁路。宿主默认 `WEJAM_TARGET=127.0.0.1:7701`。 | `wejam/client.py`、`bot/config.py`、`../Callus-Bridge/agent/` |

## 进展

- [x] `WeChatEyes`：列会话、订阅、发文本、登录状态、二维码
- [x] `WeJamHands` 实现该契约，挡掉 MessageEvent
- [x] `consume_messages` / `TextSender` / `incoming_turn` 只吃普通消息
- [x] `tests/test_bot_brain.py` 用假手眼跑大脑，导入大脑不加载 wejam
- [ ] 控制台登录仍直接用 `wejam.Client`（这次不迁）
- [x] 更新日志弹窗：选对「关闭」、热更新进运行中容器
- [x] 宿主默认切回 `WEJAM_TARGET=127.0.0.1:7701`（GUI 主路径）；openclaw `:7702` 旁路文档保留
- [x] 10-26 一次私聊活体（openclaw）：`LOGGED_IN` → 白名单 → 收一条回一条 → 进程已停

## 关键路径

| 用途 | 路径 |
|------|------|
| 手眼契约 | `bot/eyes.py` |
| WeJam 实现 | `bot/hands.py` |
| 大脑入口 | `bot/chat.py`、`bot/runner.py` |
| 假手眼单测 | `tests/test_bot_brain.py` |
| 默认人设 | `examples/personas/default/persona.txt`（运行时 `data/personas/`，gitignore） |
| 人设模板 | `examples/personas/template/`；说明见 `examples/personas/README.md` |
| 群文化 | `bot/group_culture/` → `data/group_culture/` |
| 知识库 | `bot/knowledge/` → `data/knowledge/notebook.md`；种子 `examples/knowledge/notebook.md` |
| 更新日志关弹层 | `agent/update_dialog.py`、`agent/chat.py` `_popup_guard` |
| GUI Callus（主） | `../Callus-Bridge/agent/`，`WEJAM_TARGET=127.0.0.1:7701` |
| OpenClaw 旁路 | `../Callus-Bridge/openclaw/`，`:7702` 私聊可选 |

## 活跃泳道

| 对话 | lane | 范围 | 状态 | 更新 |
|------|------|------|------|------|
| WeJamBot Callus bridge | bridge | `../Callus-Bridge/agent/**`、宿主 `WEJAM_TARGET` | active（GUI enrich） | 2026-10-26 |
| 封禁风险与bridge评估 | risk | 风险评估与观察期；不改收发实现 | active | 2026-10-26 |
| WeJamBot同类仓库对照 | survey | 只读对照外部仓库；不改收发与人设 | idle | 2026-10-26 |

## 阻塞 / 未决

- **阻塞**：无。活体已通；观察期内勿二次扫码。
- **未决问题**：控制台是否改走 `WeChatEyes`，另开任务。2026-10-27 17:30 前不决定低频私聊。发送成功不能证明「协助诈骗」标已清。

## 专题文档

| 文档 | 用途 |
|------|------|
| `README.md` | 手眼 / 脑分层说明 |
| `docs/agent/survey-snowluma.md` | SnowLuma 边界对照 + 可学功能说明（已有/守住/以后）；不改收发 |

## Handoff

契约在 `bot/eyes.py`。`WeJamHands.subscribe` 把文本行收成 `SeenMessage`，时间/系统行不出这个文件。大脑用 `incoming_turn` 和 `TextSender`。换实现只改 `_open_wejam`。不要改 `proto/` 或人设。控制台仍走 `wejam.Client`。
