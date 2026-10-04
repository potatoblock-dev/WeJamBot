import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'WeJamBot',
  description: '微信 Linux 自动化：不用 OCR、不用输入法、不依赖静态坐标',
  lang: 'zh-CN',
  themeConfig: {
    nav: [
      { text: '快速开始', link: '/quickstart' },
      { text: '设计', link: '/design' },
      { text: '能力边界', link: '/capability' },
      { text: '政策', link: '/legal/acceptable-use' },
    ],
    sidebar: [
      {
        text: '指南',
        items: [
          { text: '快速开始', link: '/quickstart' },
          { text: '设计取舍', link: '/design' },
          { text: '能力边界', link: '/capability' },
        ],
      },
      {
        text: '政策',
        items: [{ text: '可接受使用政策', link: '/legal/acceptable-use' }],
      },
    ],
    outline: { label: '本页目录' },
    docFooter: { prev: '上一篇', next: '下一篇' },
  },
})
