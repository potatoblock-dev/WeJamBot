# Agent Decisions — WeJamBot

> 冷区：完整决策史。按需查阅，勿默认整文件灌进上下文。
> 最后更新：2026-11-11 19:57 +0800

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
| d15 | 2026-10-27 | 风险：无新提示仍须等到 17:30 才决定低频私聊。GUI `:7701` 拉群活体不批准（与 10-23 设备锁/协助诈骗同路径）。代码可合，禁止对这个号开 Linux 微信跑 bot。 | `docs/agent/context.md` |
| d16 | 2026-10-27 | 三支视频（Persona Morph / iLink 教程 / 鲸鱼娘）无更低风险新技术。用户接受 GUI 与 10-23 同类账号风险。只用现有 Callus `:7701` 可见窗口；不采用后台投递、纯视觉；不搬请求头/设备标识。人在时开、离开就停。 | `docs/agent/context.md` |
| d17 | 2026-10-28 | openclaw（ilink）技术路线**不能**让 Bot 加入个人微信群。后续**不使用** openclaw，除非其协议能接入群聊。日常收发只走 GUI Callus `:7701`。代码可保留归档，不作运行默认。 | `../Callus-Bridge/openclaw/`、`docs/agent/context.md` |
| d18 | 2026-10-28 | 旁听：`data/observe_chats.txt` + `ChatBot.observe`（不 send）；档案写印象/群文化/可选进化。回复 `ChatCompleter` 与档案 `ArchiveCompleter` 分客户端；factory 预留合并。档案能见已发出的回复正文。 | `bot/llm/`、`bot/chat.py`、`bot/admin/desktop.py` |
| d19 | 2026-10-28 | 旁听按会话**显示名**精确匹配（群改名须重勾）。印象键=`群:昵称`，GUI 无 wxid；改昵称不合并旧档。预览拆 sender 拒绝 `[链接]` 等伪昵称。未入名单入站打 `not_listed` log。 | `data/observe_chats.txt`、`bot/runner.py`、`bot/sender_name.py`、`../Callus-Bridge/agent/sender_name.py` |
| d20 | 2026-10-29 | bot 运行中旁听/白名单入站写入 `inbound_transcript` 环缓冲；旁听档案按 `observe_digest_min_messages` 或间隔批量消化；回复优先本地最近 N 条，够则跳过 `read_context`。 | `bot/transcript/`、`bot/chat.py`、`reply_policy.toml` |
| d21 | 2026-10-29 | 印象按内部 `person_id`；`(群,昵称)` 别名表；控制台人工合并改名前后档案。GUI 无 wxid，不做自动同人。 | `bot/identity/`、`bot/admin/desktop.py`、`data/identity/aliases.json` |
| d22 | 2026-10-29 | 微信登录态靠命名卷 + **不删容器**：`start_session`/`docker/run.sh` 已有则 `docker start`；`make down` 只 stop；`make destroy` 才 rm。重开控制台本身不启停容器。冷启后微信或需一键登录，但不必扫码重登。 | `bot/admin/docker_control.py`、`docker/run.sh`、`../Callus-Bridge/docker/run.sh` |
| d23 | 2026-10-30 | `consume_messages` 省略 mode 时默认 **listen**（安全，与 d18 一致）；回复类单测须显式 `mode=reply`。不改生产默认。 | `bot/runner.py`、`tests/test_bot_brain.py` |
| d24 | 2026-10-30 | 群空 emotion：`speak_mode=auto` 仅在活跃 thread 放行；decide 记账后 enrich/发送失败 `abandon_reply_slot`；`ReadLatest` 按 msg_id/index 对齐，否则只填空 sender/chat_type。 | `bot/reply_policy.py`、`bot/chat.py` |
