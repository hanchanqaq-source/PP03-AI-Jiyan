# 2026-08-17 AI_Workspace 路径迁移

> 已关闭验收、历史计划和截图中出现的旧路径是迁移前历史事实，不做静默改写；当前项目路径以本记录为准。

## 路径与 Git 基线

- 旧项目路径：迁移前工作区的项目入口目录。
- 新项目路径：`D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan`。
- 迁移日期：2026-08-17。
- 迁移前分支：`codex/pp03-v0.2-w2-market-news`。
- 迁移前 Commit：`fd5e8d61f6b1b801d96acd95feeb519ae569edfb`。
- 迁移分支：`codex/pp03-workspace-layout`，直接从上述 Commit 创建。
- Origin：`https://github.com/hanchanqaq-source/PP03-AI-Jiyan.git`。
- Upstream：`https://github.com/simonlin1212/Vibe-Research.git`。

## 数据与环境边界

- 用户持仓、标签、设置、API 配置和本地模型配置保持在应用既有用户数据位置；本迁移不读取、不复制、不覆盖。
- 旧 `.venv`、`node_modules`、构建产物、缓存、普通日志和临时验收数据不迁移，在新路径按锁文件和依赖文件重建。
- 活动路径由 `scripts/check_active_paths.py` 守卫；历史验收、历史计划、迁移记录和正式截图明确排除。

## 回滚

正式切换前，旧工作区只移动到外部 Stage 的精确隔离目录。若 Git、测试、构建、浏览器、用户数据边界或哈希验证任一失败，保留隔离目录并恢复旧工作区，不删除源副本。
