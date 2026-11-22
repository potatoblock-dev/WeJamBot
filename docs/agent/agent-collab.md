# Agent Collab — WeJamBot


> 多 Agent / 多对话共享的**项目协作记忆**。由 `context.md` 迁移于 2026-11-11 19:57 +0800。
> 最后更新：2026-11-21 00:45 +0800

## 读哪些

| 场景 | 读 |
|------|-----|
| 接手 / 新对话 / 子 Agent 默认 | **当前焦点** + **活跃泳道** + **阻塞** + **Handoff** |
| 要改架构或核对旧结论 | 再读 **架构与决策**（或 `docs/agent/decisions.md`） |
| 禁止 | 整份灌进上下文；勿读整个 `conversations/` |

## 当前焦点

- **任务**：消息 SoT = **仅** Callus-Bridge GUI `:7701`（个人微信群）。
- **下一步**：按需收紧 `speak_mode` / 白名单；跨群同人继续人工绑定。
- **活跃对话**：GUI Callus 接入（bridge）；封禁风险（risk）
- **近况**：d28 引用消息解析+宿主注入；archive tag `archive/console-tools-culture-2026-11-21` 已推 origin。

## 里程碑

| ID | 日期 | 说明 | 定位 |
|----|------|------|------|
| m1 | 2026-11-21 | Callus `:7701` 登录后，宿主 listen/reply 在「0x02测试」活体跑通；环缓冲 1000、近 20 句加权、跨群 person_id、容器 start 保登录、回复 `min_interval=120`。 | git tag `milestone/callus-gui-live-m1`；分支 `feat/console-wechat-view` |

## 活跃泳道

| 对话 | lane | 范围 | 状态 | 更新 |
|------|------|------|------|------|
| WeJamBot Callus bridge | bridge | `../Callus-Bridge/agent/**`、宿主 `WEJAM_TARGET` | active（GUI enrich） | 2026-10-26 |
| 封禁风险与bridge评估 | risk | 风险评估；不改收发实现 | active | 2026-10-27 |
| WeJamBot同类仓库对照 | survey | 只读对照外部仓库；不改收发与人设 | idle | 2026-10-26 |
| WeJamBot regression fix | host-tests | 宿主单测/README/env；不改 Callus 仓 | done | 2026-10-30 |

## 阻塞 / 未决

- **阻塞**：无（d16 用户已接受 GUI 风险；仍禁止后台投递/纯视觉/改指纹）。
- **未决问题**：控制台是否改走 `WeChatEyes`，另开任务。发送成功不能证明「协助诈骗」标已清；出现限制立刻停。
- **审计 Medium 仍未决**：SDK/quickstart 示例仍写 `:7700`（归档容器口）；runner `not_listed` / observe 对无 path 空 emotion 静默。High 三条已于 2026-10-30 修（d24）。

## Handoff

契约在 `bot/eyes.py`。`WeJamHands.subscribe` 把文本行收成 `SeenMessage`，时间/系统行不出这个文件。大脑用 `incoming_turn` 和 `TextSender`。换实现只改 `_open_wejam`。不要改 `proto/` 或人设。控制台仍走 `wejam.Client`。

## 架构与决策

| ID | 日期 | 决策 | 相关文件 |
|----|------|------|----------|
| d17 | 2026-10-28 | openclaw（ilink）技术路线**不能**让 Bot 加入个人微信群。后续**不使用** openclaw，除非其协议能接入群聊。日常收发只走 GUI Callus `:7701`。代码可保留归档，不作运行默认。 | `../Callus-Bridge/openclaw/`、`docs/agent/context.md` |
| d18 | 2026-10-28 | 旁听：`data/observe_chats.txt` + `ChatBot.observe`（不 send）；档案写印象/群文化/可选进化。回复 `ChatCompleter` 与档案 `ArchiveCompleter` 分客户端；factory 预留合并。档案能见已发出的回复正文。 | `bot/llm/`、`bot/chat.py`、`bot/admin/desktop.py` |
| d19 | 2026-10-28 | 旁听按会话**显示名**精确匹配（群改名须重勾）。印象键=`群:昵称`，GUI 无 wxid；改昵称不合并旧档。预览拆 sender 拒绝 `[链接]` 等伪昵称。未入名单入站打 `not_listed` log。 | `data/observe_chats.txt`、`bot/runner.py`、`bot/sender_name.py`、`../Callus-Bridge/agent/sender_name.py` |
| d20 | 2026-10-29 | bot 运行中旁听/白名单入站写入 `inbound_transcript` 环缓冲；旁听档案按 `observe_digest_min_messages` 或间隔批量消化；回复优先本地最近 N 条，够则跳过 `read_context`。 | `bot/transcript/`、`bot/chat.py`、`reply_policy.toml` |
| d21 | 2026-10-29 | 印象按内部 `person_id`；`(群,昵称)` 别名表；控制台人工合并改名前后档案。GUI 无 wxid，不做自动同人。 | `bot/identity/`、`bot/admin/desktop.py`、`data/identity/aliases.json` |
| d22 | 2026-10-29 | 微信登录态靠命名卷 + **不删容器**：`start_session`/`docker/run.sh` 已有则 `docker start`；`make down` 只 stop；`make destroy` 才 rm。重开控制台本身不启停容器。冷启后微信或需一键登录，但不必扫码重登。 | `bot/admin/docker_control.py`、`docker/run.sh`、`../Callus-Bridge/docker/run.sh` |
| d23 | 2026-10-30 | `consume_messages` 省略 mode 时默认 **listen**（安全，与 d18 一致）；回复类单测须显式 `mode=reply`。不改生产默认。 | `bot/runner.py`、`tests/test_bot_brain.py` |
| d24 | 2026-10-30 | 群空 emotion：`speak_mode=auto` 仅在活跃 thread 放行；decide 记账后 enrich/发送失败 `abandon_reply_slot`；`ReadLatest` 按 msg_id/index 对齐，否则只填空 sender/chat_type。 | `bot/reply_policy.py`、`bot/chat.py` |
| d25 | 2026-11-20 | 跨群印象：同一 `person_id` 读写同一档案；控制台「绑定别名」把 `(群,昵称)` 挂到已有 id；两档已存在则用合并。仍不按同名自动认同人（GUI 无 wxid）。 | `bot/identity/directory.py`、`bot/admin/desktop.py` |
| d26 | 2026-11-21 | **m1**：以 Callus GUI 为 SoT 的宿主 bot 活体里程碑（旁听/回复/限频/印象/环缓冲）。默认策略可按风险再收紧，勿回退到 openclaw 进群。 | `reply_policy.toml`、`bot/transcript/`、tag `milestone/callus-gui-live-m1` |
| d27 | 2026-11-21 | Identity `aliases.json` mtime 热重载；ReplyGate reservation 按线程栈；`send_bubbles` 返回已发送数（>0 则 confirm）；LLM 不可用回退不 `note_bot_reply`；observe digest 加锁。 | `bot/identity/directory.py`、`bot/reply_policy.py`、`bot/reply.py`、`bot/chat.py` |
| d28 | 2026-11-21 | 引用消息：`quote_text`/`quote_sender` 协议字段；桥侧 `parse_quote_message`；宿主 `user_text` 注入引用块；`_apply_latest` 合并全文；Linux a11y 无引用段时 transcript/ReadContext 弱兜底。 | `bot/quote_parse.py`、`bot/channel.py`、`bot/chat.py`、`../Callus-Bridge/agent/` |

完整决策史见 [decisions.md](./decisions.md)。


## 进展

- [x] `WeChatEyes`：列会话、订阅、发文本、登录状态、二维码
- [x] `WeJamHands` 实现该契约，挡掉 MessageEvent
- [x] `consume_messages` / `TextSender` / `incoming_turn` 只吃普通消息
- [x] `tests/test_bot_brain.py` 用假手眼跑大脑，导入大脑不加载 wejam
- [ ] 控制台登录仍直接用 `wejam.Client`（这次不迁）
- [x] 更新日志弹窗：选对「关闭」、热更新进运行中容器
- [x] 宿主默认 `WEJAM_TARGET=127.0.0.1:7701`（GUI）
- [x] 10-26 一次私聊活体（openclaw，历史）：已通；**d17 后日常不再用 openclaw**
- [x] 10-30 回归：brain `mode=reply`、Settings `bot_mode_path`、README/env 日常 `:7701`；`make test` 83 passed

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
| 提示词进化 | `bot/prompt_evolution/` → `data/prompt_evolution/{pending,active}`；控制台审批后才注入 |
| 知识库 | `bot/knowledge/` → `data/knowledge/notebook.md`；种子 `examples/knowledge/notebook.md` |
| 更新日志关弹层 | `agent/update_dialog.py`、`agent/chat.py` `_popup_guard` |
| GUI Callus（唯一日常桥） | `../Callus-Bridge/agent/`，`WEJAM_TARGET=127.0.0.1:7701` |
| OpenClaw（搁置） | `../Callus-Bridge/openclaw/`，`:7702`；d17 前勿作运行路径 |
| 旁听名单 | `data/observe_chats.txt`（含 `99+`）；群改名须重勾；控制台「旁听会话」 |
| 入站全文环缓冲 | `data/bot.sqlite3` 表 `inbound_transcript`；`bot/transcript/` |
| 身份别名 | `data/identity/aliases.json`；印象键=`person_id`；控制台「印象」合并 |
| 档案 LLM | `ARCHIVE_LLM_*`（空则回落 `LLM_*`）；`bot/llm/archive.py` + `factory.py` |
| 微信容器启停 | `bot/admin/docker_control.py`；`docker/run.sh`（不 rm）；卷 `callus-data` |
| 监听聊天记录 | `data/bot.sqlite3` 表 `inbound_transcript`；控制台「聊天记录」；`bot/transcript/` |

## 专题文档

| 文档 | 用途 |
|------|------|
| `README.md` | 手眼 / 脑分层说明 |
| `docs/agent/survey-snowluma.md` | SnowLuma 边界对照 + 可学功能说明（已有/守住/以后）；不改收发 |
| `docs/agent/decisions.md` | 冷区：完整决策史 |
