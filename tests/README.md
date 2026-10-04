# 测试

这里放的是**活体集成检查** —— 需要容器里的微信已登录，因此不是纯单元测试，
也没有用 pytest 组织（它们是可直接运行的脚本）。

```bash
python tests/check_docs_examples.py    # 文档里的每个示例真跑一遍
python tests/check_concurrency.py      # 并发发送，验证消息逐字完整不被交错
```

## 为什么没有单元测试

这个项目的能力几乎全部来自**外部界面状态**（无障碍树、X11、像素）。
把它 mock 掉之后，测试只能验证「我调用了 mock」，验证不了任何真实约束 ——
真正会出问题的地方（渲染时机、坐标映射、锁的实际效果）恰好全在边界之外。

所以这里的检查都直接打真机：

| 脚本 | 验证的是 |
|---|---|
| `check_docs_examples.py` | 文档里的代码**真的能跑**，不是写给读者看的 |
| `check_concurrency.py` | 并发发送时消息**逐字完整**（未加锁会变成乱码，有对照实验记录在 README） |

两者都不依赖当前工作目录，也不需要先 `pip install`。

## 提交前建议跑

```bash
bash scripts/gen-proto.sh --check     # 生成的 stub 是否与 proto 同步
python tests/check_docs_examples.py   # 文档是否与代码脱节
```
