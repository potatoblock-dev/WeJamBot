# WeJamBot

把微信 Linux 版跑进容器，通过 **gRPC** 对外提供「读会话 / 读消息 / 发消息」的能力。

**不使用 OCR，不使用输入法，不依赖静态坐标。**

```
┌─ 容器 wejam-session ──────────────────────────┐
│  Xvfb ── 微信 ── AT-SPI                        │
│              └── agent（gRPC 服务）            │
│                  登录状态机 + 会话/消息解析     │
└──────────────────┬─────────────────────────────┘
                   │ gRPC  127.0.0.1:7700
┌──────────────────▼─────────────────────────────┐
│  宿主：wejam 客户端库 / wejam CLI            │
└────────────────────────────────────────────────┘
```

## 为什么能做到不用 OCR

微信 Linux 版基于 GTK + Chromium，**默认就会向无障碍总线暴露完整的节点树**。
打开 AT-SPI 之后，会话列表与消息正文都是结构化的文本节点：

```
[list] '会话'
  [list item] '文件传输助手\n剪贴板方案验证：中文、标点，以及 emoji 🚀 都试试\n13:32\n'
[list] '消息'
  [list item] '13:32'
  [list item] '剪贴板方案验证：中文、标点，以及 emoji 🚀 都试试'
```

发送则用「临时把空闲键码重映射成 Unicode keysym」的方式注入，因此中文和 emoji
都能直接输入，**不经过任何输入法**。

## 快速开始

### 1. 构建镜像

```bash
docker build -f docker/Dockerfile \
  --build-arg WECHAT_ARCH=x86_64 \
  -t wejam/wechat:latest .
```

微信本体从**腾讯官方 CDN** 下载安装，不依赖宿主已装微信。支持的架构：
`x86_64` / `arm64` / `LoongArch`。

> 若本地没有 `wejam/ubuntu-base:24.04` 基础镜像，先从清华镜像取 rootfs 再导入：
> ```bash
> curl -O https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-amd64.tar.gz
> docker import ubuntu-base-24.04.5-base-amd64.tar.gz wejam/ubuntu-base:24.04
> ```

### 2. 启动会话容器

```bash
bash docker/run.sh
```

只 publish 一个本地端口（默认 `127.0.0.1:7700`），状态存在命名卷 `wejam-data`。

### 3. 登录

```bash
python -m wejam.cli login --qr-out wejam-qr.png
```

服务端解出二维码内容，客户端渲染成图。你只需**扫码 + 手机上确认** ——
二维码刷新、过期重取、重连都由服务端与客户端自动处理。

### 4. 用起来

```bash
python -m wejam.cli chats                  # 会话列表
python -m wejam.cli messages               # 当前会话的消息
python -m wejam.cli send 文件传输助手 "你好"
python -m wejam.cli watch                  # 增量订阅新消息
```

## 使用示例

### 三行上手（Pythonic 门面）

```python
from wejam import WeChat

wx = WeChat()                                  # 默认连 127.0.0.1:7700
wx.send("你好", chat="文件传输助手")            # 发送（服务端会回读校验）
for msg in wx.messages():                      # 读当前会话
    print(msg.text)
```

门面只做省字，不做新能力。需要精细控制时用 `Client`：

```python
from wejam import Client

with Client("127.0.0.1:7700") as c:
    for chat in c.list_chats().chats:
        print(chat.name, chat.unread_count)
```

> 类名 PascalCase、方法与字段 snake_case。
> `WeChat` 的每个方法都是 `Client` 的薄包装。

### 完整文档

VitePress 文档在 [`docs/`](docs/)：

- [快速开始](docs/quickstart.md) — 覆盖、发送、监听、徽标、登录、逃生舱
- [设计取舍](docs/design.md) — 为什么不用 OCR / 不用输入法 / 零静态坐标，以及踩过的坑
- [可接受使用政策](docs/legal/acceptable-use.md)

文档里的每个示例都由 [`tests/check_docs_examples.py`](tests/check_docs_examples.py) 真跑一遍，防止文档与代码脱节。

```bash
python tests/check_docs_examples.py      # 当前：全部通过
```

### 可运行示例

仓库里还有两个可直接运行的例子：

| 文件 | 内容 |
|---|---|
| [`examples/quickstart.py`](examples/quickstart.py) | 走一遍全部接口：状态 / 会话 / 读消息 / 发消息 / 徽标 / 逃生舱 |
| [`examples/echo_bot.py`](examples/echo_bot.py) | 全局监听 + 自动回复的最小机器人（带白名单与防死循环） |

```bash
python examples/quickstart.py     # 接口总览
python examples/echo_bot.py       # 60 秒监听，只在白名单会话里自动回复
```

### 最小机器人

```python
import time
from wejam import Client

ALLOW = {"文件传输助手"}          # 只在这些会话里回复 ⚠️ 加真实会话 = 真替你在群里说话

def main():
    while True:
        try:
            with Client() as c:
                for ev in c.watch_messages():        # 不指定 chat = 全局监听
                    for m in ev.added:
                        print(f"[{ev.chat}] {m.sender}{m.text}  未读{ev.unread}")
                        if m.from_self:              # 服务端已溯源，跳过自己发的
                            continue
                        if ev.chat in ALLOW:
                            c.send_text(ev.chat, f"收到「{m.text}」")
        except Exception:
            time.sleep(2)                            # 服务重启也不怕

main()
```

**不用给消息加 `[bot]` 前缀也能安全** —— 服务端在 `SendText` 时记录自己发过什么，
推送时用 `from_self` 标出来。客户端不需要任何约定，任何语言的客户端都直接受益。

实测输出（在另一个终端发一条到 `文件传输助手`）：

```
监听中… 只在 {'文件传输助手'} 内自动回复（Ctrl-C 退出）
[文件传输助手] 无前缀安全性复测  [自己发的]        ← 自己发的，不回复 → 不成环
```

真实外部消息的场景（`1145` 群）：

```
[1145] 还原和平第一人: 不会啊                     ← 别人发的 → 回复
          → 已回复
[1145] 收到「不会啊」  [自己发的]                 ← 自己的回复 → 不再触发
```

> 溯源匹配要处理的细节：会话列表给的是**预览**（可能被截断），群聊预览还可能带
> 「昵称: 」前缀。所以按「去前缀 + 前缀匹配」判定，并对过短文本只做精确匹配，
> 避免「好」误匹配到「好的我知道了」。见 `agent/selflog.py`。

### 库接口一览

```python
from wejam import Client

with Client("127.0.0.1:7700") as c:
    c.status()                                   # 服务与微信状态
    c.login_state(); c.watch_login()             # 登录状态 / 订阅
    c.qr_code(); c.submit_login()                # 二维码内容 / 一键登录
    c.list_chats()                               # 会话列表
    c.list_messages(chat="X", limit=20)          # 读消息
    c.send_text("X", "hello")                    # 发消息
    c.watch_messages(chat="X")                   # 订阅（不传 chat 即全局）
    c.get_badges()                               # 未读徽标
    c.dump_tree(); c.invoke_node("push button", "登录")   # 逃生舱
```

## CLI

| 命令 | 说明 |
|---|---|
| `status` | 服务与微信状态（含版本号） |
| `login` | 订阅并自动推进登录 |
| `qr` | 按需拉取**当前**二维码（带内容指纹与年龄） |
| `chats` | 会话列表（含未读数、置顶、免打扰） |
| `messages [--chat X] [--limit N]` | 读取消息 |
| `send <chat> <text>` | 发送文本 |
| `watch [--chat X]` | 增量订阅新消息 |
| `badges` | 未读徽标（小红点） |
| `tree [--all] [--depth N]` | **[逃生舱]** dump 无障碍节点树 |
| `invoke <role> <name>` | **[逃生舱]** 按角色/名字点击节点 |

## Python 库

```python
from wejam import Client

with Client("127.0.0.1:7700") as c:
    for chat in c.list_chats().chats:
        print(chat.name, chat.last_message, chat.unread_count)

    resp = c.list_messages(chat="文件传输助手", limit=20)
    for m in resp.messages:
        if m.kind == 1:              # TEXT
            print(m.sender, m.text)

    c.send_text("文件传输助手", "来自 API 的消息")
```

## 契约

契约在 [`proto/wejam/v1/wejam.proto`](proto/wejam/v1/wejam.proto)，业务语义为主：

```
GetStatus / GetLoginState / WatchLogin / GetLoginQRCode / SubmitLogin
ListChats / ListMessages / WatchMessages / SendText
DumpA11yTree / InvokeNode          # 逃生舱
```

任何语言都能生成自己的 stub —— 契约里没有 a11y、XTEST、截图、坐标这些实现细节。

## 设计原则

这些是踩过坑之后定下来的，改动时请守住：

| 原则 | 原因 |
|---|---|
| **服务持有状态** | 登录状态机在容器内。客户端只 Get/Watch，不做判断与决策 |
| **契约只说能力与状态** | 实现手段（无障碍/注入/解码）不进契约，也不进客户端代码 |
| **一切界面操作串行化** | 界面 / X / pyatspi 本质是单线程资源，而 gRPC 有 8 个 worker 线程 + 登录轮询线程。所有「读界面/动界面」都经 `agent/ui.py` 的全局 RLock |
| **动作级原子、读取不长持锁** | `send_text` 是「开会话→定位→点击→输入→回车」一整串，必须整体持锁；而登录轮询每轮要走整棵无障碍树（约 1 秒），整体持锁会饿死所有 RPC，所以它只在「点击」那一步持锁并重新定位 |
| **发送必须校验** | 回车失败时微信毫无提示。发完要回读输入框确认已清空，否则重试并如实报错 |
| **等渲染稳定再解析** | 打开会话时消息列表是渐进渲染的，立刻解析会读到半截 |
| **零静态坐标** | 定位一律按 a11y 的 role/name；坐标由运行时比例映射导出 |
| **不用宿主 X** | 宿主不装 X11、不共享网络命名空间，Docker Desktop 也能跑 |
| **不用输入法** | 键码重映射注入 Unicode，避免 IME 污染（曾把 `e` 打成「呃」） |
| **不 OCR** | 无障碍树直接给结构化文本 |
| **危险按钮黑名单** | `退出/注销/清空/删除/撤回/解散` 拒绝点击（曾误触退出登录） |
| **节点定位角色必须精确优先** | 子串匹配时 `"list" in "list item"` 为真，找会话列表容器会命中正文里含「会话」二字的消息项 → `ListChats` 静默返回空。现在精确匹配优先、子串兜底 |
| **能应付意外弹窗** | 模态框会挡住后续所有操作，必须自动识别并取消 |

## 排障

**二维码扫了没反应**
微信对过期码是**静默忽略**，不报错。扫之前先拉一次当前的码：

```bash
python -m wejam.cli qr --qr-out wejam-qr.png
```

`payload` 与 `revision` 变化说明微信换过码；`内容年龄` 太大就重新拉。

**容器日志**

```bash
docker logs wejam-session
docker exec wejam-session cat /data/home/wechat.log
docker exec wejam-session cat /data/home/agent.log
```

**改 agent 代码后无需重启微信**（agent 是独立进程，不丢登录态）：

```bash
docker cp agent/. wejam-session:/opt/wejam/agent/
docker exec -u 0 wejam-session pkill -f agent.main
```

**重置登录态**

```bash
docker rm -f wejam-session && docker volume rm wejam-data
```

**版本探测的优先级**
`dpkg`（官方 deb 安装时）→ `GUI`（设置 → 关于微信 → 「版本信息」）→ 进程参数兜底。

**未读徽标（小红点）能读到什么、读不到什么**

导航图标右上角那个徽标**不在无障碍树里** —— 它是绘制出来的装饰。
实测每个导航按钮的 a11y 结构完全相同（`push button '微信'` 下挂两个 `filler`），
有徽标与没徽标在 role / name / state 上没有任何差别。

所以 `wejam badges` 用的是「**无障碍定位置 + 像素颜色判定**」：
位置取该按钮**最后一个子节点**的矩形（恰好是右上角徽标位，无静态坐标），
再看这方块里的红色像素数。既不是 OCR，也不依赖分辨率。

```
$ wejam badges
  🔴 有  微信           红像素 139/289
  ⚪ 无  通讯录          红像素 0/289
  ⚪ 无  收藏           红像素 0/289
  ...
有未读的会话数   : 3
未读消息总数     : 81
```

| 想拿到 | 能否 |
|---|---|
| 某个导航项**有没有**徽标 | ✅ 像素判定（位置来自 a11y） |
| **会话级未读数**（精确数字） | ✅ `ListChats` 的结构化字段，不需要像素 |
| 徽标里那个**数字本身** | ❌ 不 OCR 读不到。而且它的口径未明：实测导航徽标显示 `7`，而当时三个会话未读是 `72 / 7 / 2`（合计 81、非免打扰合计 9、会话数 3），**对不上任何合理聚合**，怀疑是客户端自身实现细节 |

**做机器人实际需要的是「哪个会话有未读、有多少条」—— 这个 ListChats 给的是精确值。**
导航徽标只适合做「有没有新东西」的粗判断。

**`watch` 的两种模式**

| 用法 | 覆盖范围 | 拿到的内容 | 代价 |
|---|---|---|---|
| `watch --chat X` | 单个会话 | **完整正文** | 服务端要保持 X 打开 → 会清掉它的未读 |
| `watch`（不指定） | **所有会话** | 会话列表**预览**（`preview_only=true`） | 只轮询列表，**完全不动界面** |

全局监听实测能捕获别的会话里**别人发来的**消息：

```
$ wejam watch
[00:08:50] [预览] 文件传输助手  全局监听A：应捕获
[00:09:01] [预览] 1145        不好意思这是机器人发的      ← 群里别人发的
[00:09:11] [预览] 1145        吴寒 7.12: 气笑了          ← 又一条
```

**固有代价**：全局模式是**轮询**（1 秒一次），一个周期内到达多条时只反映最后一条。
所以事件里带了该会话的 `unread` 未读总数 —— 客户端据此判断「实际新增了几条」，
必要时再调 `ListMessages` 取详情。

> 为什么不能既全局又拿完整正文：微信**打开会话才算读过**，
> 全局地把每个会话都打开一遍会清掉用户所有未读，代价不可接受。

**并发**

agent 的 gRPC 线程池默认 8 个 worker，加上登录轮询线程，都会访问同一个界面。
所有界面操作走 `agent/ui.py` 的全局 RLock 串行化。

并发发送的回归脚本：

```bash
python tests/check_concurrency.py
```

它会并发发多条消息再回读逐字比对。**验证过这个测试不是空转**：
把锁临时改成空操作后，同样的用例会失败（消息被键码交错污染）。

## 宿主闲聊 Bot（文本第一版）

`wejam/` 是微信驱动；`bot/` 是跑在宿主上的闲聊脑（人设、印象、回复策略、PyQt 控制台）。**不改** `agent/` 与 proto。容器仍用 `bash docker/run.sh`。

```
微信容器 :7700  →  wejam.Client  →  bot.runner  →  LLM
                                      ↑
                               bot.admin 控制台
```

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[bot]"
cp .env.example .env   # 填 LLM_API_KEY
./scripts/start-bot.sh
./scripts/start-admin.sh
```

控制台页：运行（启停 runner）、登录（二维码 / 一键登录）、Bot（会话白名单与群发言模式）、API（`.env`）、人设、回复策略、印象。

能力边界：

- 全局 `watch_messages()` 只有会话预览，可能被截断；一轮多条时可能只看到最后一条。
- 只能 `SendText`。模型若输出 `[[sticker:id]]` 会被剥掉。
- 白名单为空则不回复（默认仅「文件传输助手」）。微信没有官方 @：点名看 `reply_policy.toml` 的 `bot_names`。
- `.env`、`data/` 不入库。

单测（不需要已登录微信）：`make test-bot`

## 目录

```
proto/wejam/v1/wejam.proto   gRPC 契约（唯一的接口真相）
agent/                       服务端，容器内运行：无障碍、几何映射、状态机、消息解析
wejam/                       宿主侧客户端库 + CLI（wejam/v1/ 是**入库**的生成 stub）
bot/                         宿主闲聊脑 + PyQt 控制台（人设 / 印象 / 策略）
docker/                      Dockerfile 与入口脚本
docs/                        VitePress 文档站
examples/                    可运行示例（含 examples/personas/default）
tests/                       活体集成检查 + test_bot_*.py 单测
scripts/                     构建与运维脚本（含 start-bot / start-admin）
tools/                       宿主侧调试小工具（xctl / xgrab）
```

常用任务都在 `Makefile` 里：

```bash
make help            # 列出全部任务
make proto-check     # 校验生成的 stub 与 proto 是否同步
make status          # 查看服务与微信状态
make test            # 跑活体检查（文档示例 + 并发）
```

**不参与版本控制的东西**（见 `.gitignore`）：`wxhome/` 与 `run/` 是容器外的
运行时数据目录（几百 MB），`.venv/`、`__pycache__/`、`*.egg-info/` 是本地构建产物，
`wejam-qr.png`、`*.log` 是运行产物。

> `wejam/v1/*_pb2*.py` 是**入库**的生成代码 —— 否则从干净克隆 `pip install`
> 会因缺少 `wejam.v1` 而失败。与 proto 的一致性由 `make proto-check` 保证。

