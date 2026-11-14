# 测试

两类检查：

1. **离线单测**（pytest，默认 `make test`）：宿主闲聊脑、策略、channel 等，不需要微信登录。
2. **活体集成检查**（`make test-live`）：需要容器里的微信已登录。

```bash
make test                                          # pytest tests/test_bot_*.py …
make test-live                                     # 文档示例 + 并发发送
python tests/check_docs_examples.py                # 文档里的每个示例真跑一遍
python tests/check_concurrency.py                  # 并发发送，验证消息逐字完整不被交错
```

## 为什么 agent UI 仍以活体为主

容器内能力大量依赖**外部界面状态**（无障碍树、X11、像素）。
把 a11y mock 掉之后，测试只能验证「我调用了 mock」，验证不了渲染时机与坐标映射。

因此微信 UI 相关仍靠活体脚本；宿主侧策略 / LLM / 记忆 / 印象文件名则用单测覆盖。

| 入口 | 验证的是 |
|---|---|
| `make test` / `test_bot_*.py` | 宿主脑与策略的离线回归 |
| `check_docs_examples.py` | 文档里的代码**真的能跑** |
| `check_concurrency.py` | 并发发送时消息**逐字完整** |

## 提交前建议跑

```bash
bash scripts/gen-proto.sh --check     # 生成的 stub 是否与 proto 同步
make test                             # 离线单测
# 有登录会话时再：
make test-live
```
