# 二、快速开始

## 快速开始

:::warning 使用边界
本示例仅面向用户本人控制的低频辅助操作。请勿用于批量营销、垃圾信息、骚扰或向大量不特定用户发送信息。开始前请阅读[可接受使用政策](/legal/acceptable-use)。
:::

:::tip 前置条件
容器已经在跑，并且微信已登录。没登录就先执行 `wejam login`。
:::

### 获取微信实例

```python
from wejam import WeChat

# 初始化微信实例
wx = WeChat()
```

默认连本机 `127.0.0.1:7700`，换地址传参即可：`WeChat("192.168.1.9:7700")`。

### 发送消息

```python
# 发送消息
wx.send("你好", chat="文件传输助手")
```

返回 `bool`。服务端会**回读输入框**确认真的发出去了 —— 微信发送失败时不会有任何提示，
所以这里不猜。

### 获取当前聊天窗口消息

```python
# 获取当前聊天窗口消息
msgs = wx.messages()

for msg in msgs:
    print('==' * 30)
    print(msg.text)
```

`msg` 是 dataclass，可直接打印：

| 字段 | 含义 |
|---|---|
| `msg.text` | 正文 |
| `msg.sender` | 群聊发送者（单聊为空） |
| `msg.chat` | 所属会话 |
| `msg.from_self` | **服务端溯源**：这条是自己发的 |
| `msg.preview` | 来自会话列表预览，可能被截断 |
| `msg.kind` | `1` 正文 / `2` 时间 / `3` 系统 |

:::tip ✅
恭喜，你已经成功进行了自动化操作，接下来你可以继续探索更多功能。
:::

---

## 会话列表

```python
for chat in wx.chats():
    print(chat)
```

```
1145  克里斯蒂娜: 你一定要活下去。。  16:30  [置顶 未读12]
文件传输助手  最终复测：无前缀  16:21
公众号  荆楚网: 零跑汽车发声明…  16:03
```

`chat` 字段：`name` / `last_message` / `time` / `unread` / `pinned` / `muted`。

## 读取指定会话

给 `chat` 传会话名即可。**服务端会自动打开它** —— 微信一次只渲染一个会话，
不先打开就读不到。

```python
for msg in wx.messages(chat="文件传输助手", limit=20):
    print(msg.text)
```

## 监听新消息

### 全局监听

覆盖**所有会话**，且**完全不打开任何会话**（不动界面、不清你的未读）：

```python
for chat, msg in wx.watch(seconds=60):
    print(f"[{chat}] {msg.sender}{msg.text}")
```

代价：只能拿到会话列表的**预览**（`msg.preview` 为真，可能被截断）。
所以事件里还带了该会话的未读总数，用来判断有没有漏。

### 单个会话

能拿到**完整正文**，代价是服务端要保持该会话打开 → 会清掉它的未读。

```python
for chat, msg in wx.watch(chat="文件传输助手", seconds=60):
    print(msg.text)
```

### 不回自己的消息

`msg.from_self` 由服务端标注，**不需要给消息加 `[bot]` 之类的前缀**：

```python
for chat, msg in wx.watch(seconds=60):
    if msg.from_self:          # 自己发的，跳过
        continue
    print("收到新消息:", msg.text)
```

## 未读徽标

```python
b = wx.badges()
print(b["unread_sessions"], "个会话有未读，共", b["unread_messages"], "条")
print(b["nav"]["微信"])        # True = 导航图标上有红点
```

:::details 徽标为什么这么读
导航图标上那个小红点是**画出来的装饰**，不在无障碍树里 —— 有徽标与没徽标在
节点结构上完全一样。所以位置取自无障碍节点，是否变红由像素颜色判定。
既不是 OCR，也不依赖分辨率。徽标里的**数字**读不出来（那是 OCR 的活），
但会话级未读数 `chat.unread` 是精确的结构化字段。
:::

## 登录

:::warning 只在需要时用
已经登录的实例不需要重复执行。微信的登录态与设备指纹绑定，
服务端会在状态卷里持久化，容器重建也不会掉。
:::

```python
wx.login_state()               # 'LOGGED_IN' / 'QR_READY' / 'WAIT_PHONE' / ...
wx.qr_code()                   # 二维码**内容**字符串，自己渲染
wx.submit_login()              # 「记住账号」界面下推进登录
```

查看状态：

```python
print(wx.status())
# {'version': '4.1.13.23', 'login': 'LOGGED_IN', 'uptime_ms': 123456}
```

## 逃生舱

契约里还有两个原子接口，给需要深度定制的场景：

```python
# 1. 直接 dump 无障碍节点树
for node in wx.tree().nodes:
    if node.text or node.name:
        print(f"{'  ' * node.depth}[{node.role}] {node.text or node.name}")

# 2. 按角色/名字点击节点（位置由服务端运行时算出，无静态坐标）
ok, detail = wx.click_node("push button", "登录")
```

:::tip
逃生舱是给「契约里还没覆盖的能力」用的。如果你发现某个常用操作必须靠它，
那多半是契约该补一个业务语义接口了 —— 欢迎提 issue。
:::

## 直接用 gRPC 客户端

门面只是薄包装。需要并发、订阅细节或非 Python 语言时，直接用契约：

```python
from wejam import Client

with Client("127.0.0.1:7700") as c:
    for chat in c.list_chats().chats:
        print(chat.name, chat.unread_count)
```

契约定义在 `proto/wejam/v1/wejam.proto`，任何语言都能生成自己的 stub：

```proto
service WeJam {
  rpc GetStatus(GetStatusRequest) returns (Status);
  rpc GetLoginState(GetLoginStateRequest) returns (LoginState);
  rpc WatchLogin(WatchLoginRequest) returns (stream LoginState);
  rpc GetLoginQRCode(GetLoginQRCodeRequest) returns (LoginQRCode);
  rpc SubmitLogin(SubmitLoginRequest) returns (LoginState);
  rpc ListChats(ListChatsRequest) returns (ListChatsResponse);
  rpc ListMessages(ListMessagesRequest) returns (ListMessagesResponse);
  rpc WatchMessages(WatchMessagesRequest) returns (stream MessageEvent);
  rpc SendText(SendTextRequest) returns (SendResult);
  rpc GetBadges(GetBadgesRequest) returns (GetBadgesResponse);
  rpc DumpA11yTree(DumpA11yTreeRequest) returns (DumpA11yTreeResponse);
  rpc InvokeNode(InvokeNodeRequest) returns (InvokeNodeResponse);
}
```
