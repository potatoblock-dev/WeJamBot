# WeJamBot 常用任务。
#
# 这里只做「多个命令的组合」，不隐藏任何细节 —— 每个 target 展开后
# 都是一条你可以直接复制去跑的命令。

SHELL := /bin/bash
PYTHON ?= .venv/bin/python
NAME ?= wejam-session
IMAGE ?= wejam/wechat:latest

.DEFAULT_GOAL := help
.PHONY: help proto proto-check build up down logs shell status test test-docs test-concurrency test-bot clean

help:  ## 显示所有可用任务
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------- 协议 ----------
proto:  ## 从 proto 重新生成 gRPC stub
	bash scripts/gen-proto.sh

proto-check:  ## 校验生成的 stub 是否与 proto 同步
	bash scripts/gen-proto.sh --check

# ---------- 容器 ----------
build:  ## 构建微信镜像
	docker build -f docker/Dockerfile -t $(IMAGE) .

up:  ## 启动会话容器
	bash docker/run.sh

down:  ## 停止并删除容器（不动状态卷）
	docker rm -f $(NAME)

logs:  ## 跟随容器日志
	docker logs -f $(NAME)

shell:  ## 进入容器
	docker exec -it $(NAME) bash

status:  ## 查看服务与微信状态
	$(PYTHON) -m wejam.cli status

# ---------- 测试 ----------
test-bot:  ## 宿主闲聊 bot 单测（不需要已登录微信）
	$(PYTHON) -m pytest tests/test_bot_*.py -q

test: test-docs test-concurrency  ## 跑全部活体检查

test-docs:  ## 检查文档里的示例是否真能跑
	$(PYTHON) tests/check_docs_examples.py

test-concurrency:  ## 并发发送，验证消息不被交错
	$(PYTHON) tests/check_concurrency.py

# ---------- 清理 ----------
clean:  ## 清理缓存与构建产物
	find . -name __pycache__ -not -path './.venv/*' -not -path './wxhome/*' -prune -exec rm -rf {} +
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
