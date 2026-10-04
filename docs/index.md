---
layout: home

hero:
  name: WeJamBot
  text: 微信 Linux 自动化
  tagline: 不用 OCR、不用输入法、不依赖静态坐标
  actions:
    - theme: brand
      text: 快速开始
      link: /quickstart
    - theme: alt
      text: 设计取舍
      link: /design

features:
  - title: 直接读结构化文本
    details: 微信基于 GTK + Chromium，本就向无障碍总线暴露完整节点树。会话、消息、发送者、时间戳都是现成的文本节点，不需要截图和 OCR。
  - title: 服务持有状态
    details: 登录状态机、二维码生命周期、消息轮询都在容器内的 agent 里。客户端只是一份 gRPC 契约的消费者。
  - title: 零静态坐标
    details: 定位一律按无障碍节点的角色与名字；需要坐标时由运行时比例映射导出，抗 DPI、抗分辨率、抗窗口位置变化。
  - title: 跨平台宿主
    details: 不要求宿主装 X11，不使用 --network host，只 publish 一个本地端口 —— Docker Desktop 上同样可用。
---
