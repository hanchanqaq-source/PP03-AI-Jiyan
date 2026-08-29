# SUBSCRIPTION_AI_W1 上游选择性移植审计

## 审计边界

- 当前项目基线：`codex/pp03-dual-holdings` @ `e9f1916fa549e1f74472fb82c3da8ea403867a86`
- 上游仓库：`https://github.com/simonlin1212/Vibe-Research.git`
- 固定参考提交：`64ba4527b6e247ec9d26347cbc75158fefd2bb7b`
- 审计方式：只读取固定提交的 Git 对象；未 merge、cherry-pick 或 checkout 上游。
- 迁移原则：移植可验证的行为与测试边界，按 PP03 的 Python/FastAPI/React 架构重新实现，不复制上游完整运行时。

## 逐项迁移矩阵

| 上游文件 / 函数 | 功能 | 当前项目对应文件 | 移植 | Windows 适配 | 不移植或改写原因 |
|---|---|---|---|---|---|
| `codex-version.json` | 记录上游在 macOS 验证的 Codex CLI `0.149.0` | `backend/subscription_ai/codex_provider.py`、定向测试 | 部分 | 版本输出按 Windows UTF-8 解码；以实际命令能力为准 | 上游文件注明 JSON 不是兼容性证明；不复制 SDK/安装元数据，不自动安装或升级 |
| `docs/model-access.md` | 产品独立 `CODEX_HOME`、官方登录、真实测试成功后保存、API 与订阅分离 | 本审计文档、`frontend/src/pages/Settings.tsx` | 是（语义） | 后备命令使用 Windows `set CODEX_HOME=...` 形式，页面不返回真实用户目录 | 不复制 Plus/Pro/Team 名称、第三方 provider 矩阵、研究编排说明 |
| `orchestrator/src/config.ts`：`codexEnvFor`、`codexEnv` | 只保留必要环境并显式覆盖产品 `CODEX_HOME`；ChatGPT 模式不透传 `CODEX_API_KEY` | `backend/cli_runtime.py` | 是（重写） | 保留 Windows `PATH`、代理与证书变量；创建 `%VR_DATA_DIR%\codex-home` 后再启动 | 不移植 RunConfig、provider profile、工具环境策略或 Node 配置链 |
| `orchestrator/test/run.test.ts`：环境隔离用例 | 用外部全局 `CODEX_HOME`/`CODEX_API_KEY` 哨兵证明不会透传 | `backend/tests/test_subscription_ai_codex_provider.py`、`backend/tests/test_fixes.py` | 是（测试思想） | 使用 Windows 路径哨兵并断言所有 Codex 子进程收到产品目录 | 不复制通过源码正则镜像实现的测试 |
| `orchestrator/src/local_agent_runtime.ts`：`probeCodex` | `codex --version` 与产品目录下 `codex login status`，不读 `auth.json` | `backend/subscription_ai/codex_provider.py` | 是（重写并收窄） | `subprocess.run` 显式 UTF-8、`CREATE_NO_WINDOW`；只解析退出码与稳定公开文本 | 不移植 Claude/Qwen/DeepSeek 检测与通用本机 Agent 注册表 |
| `orchestrator/src/local_agent_runtime.ts`：`startCodexLogin`、`codexLoginProgress` | 创建产品 home、官方 `codex login`、同一 home 单实例、超时与进程树清理 | `backend/subscription_ai/codex_provider.py`、FastAPI 登录端点 | 是（重写） | 用 `CREATE_NEW_CONSOLE`/`CREATE_NEW_PROCESS_GROUP` 打开可见官方流程并支持 Windows 进程树终止 | 不读取浏览器 Profile，不捕获登录输出，不复制 Node job map 的数据结构 |
| `orchestrator/test/local_agent_runtime.test.ts` | 假 Codex 验证产品 home、重复登录合并和进程树收口 | `backend/tests/test_subscription_ai_codex_provider.py` | 是（测试思想） | Python fake process/依赖注入；不触碰真实登录目录 | 不运行上游 Node 测试套件 |
| `orchestrator/src/service.ts`：`localAgents`、`startCodexSubscriptionLogin` | 服务层连接探针与官方登录 | `backend/subscription_ai/service.py` | 是（收窄） | 沿用 FastAPI，本机 loopback 门禁放在 `backend/app.py` | 不移植上游 product info、数据路径、provider templates 或 service 全量路由 |
| `orchestrator/src/api.ts`：`/local-agents`、`/local-agents/codex/login` | 状态与登录 HTTP 边界 | `backend/app.py` 的 `/api/subscription-ai/*` | 是（接口语义） | 校验 socket、Host 与 Origin 都是 loopback | 不复制 Bearer/Cookie UI 服务器；PP03 已有后端访问密钥机制 |
| `desktop/src/verticals/finance/pages/Settings.tsx`：`testAndSaveCli` | 真实探针成功后才 `saveLlm`，失败保留现有配置 | `frontend/src/pages/Settings.tsx` | 是（重写） | 使用 PP03 Vite/FastAPI API；保存 `provider=cli-codex, model=codex` | 不复制上游桌面壳、Claude 卡片、第三方模板列表与产品路径展示 |
| `desktop/src/verticals/finance/lib/llmStore.ts` | localStorage 中的 CLI 配置形状与失败不静默回退 | PP03 现有 `frontend/src/lib/llm.ts`/Settings 保存逻辑 | 部分 | 保持现有 `vr-llm` 键和刷新回读 | 不复制整个 store；不得持久化 Token、Cookie、auth 路径、套餐或额度 |
| `orchestrator/bin/codex-chat` | `--ignore-user-config --ignore-rules`，认证仍取产品 `CODEX_HOME` | `backend/cli_runtime.py` | 是（参数语义） | 直接用 Python argv，不引入 POSIX shell wrapper | 不复制 shell 脚本或 SDK；正常页面调用继续使用空临时 cwd、只读沙箱、无项目 MCP |

## 明确不移植

- `orchestrator/` 的完整 Node Orchestrator、Codex SDK Harness、六阶段研究 Agent、gate/ledger/validator、hooks 与 skills 隔离写配置。
- `backtest/`、`datasources/`、`providers/`、上游桌面应用和无关页面。
- `codex mcp add`、产品或用户全局 `config.toml` 修改、PP03 MCP 自动注入、旧 Provider Hub。
- Claude/Qwen/DeepSeek 本机订阅探针，以及任何 API key 自动回退或自动计费路径。

## 本 Work 的适配裁决

1. 产品登录目录固定为 `%VR_DATA_DIR%\codex-home`；`VR_DATA_DIR` 未设置时沿用 PP03 私有数据根 `%USERPROFILE%\.vibe-research`，仍不读取 `%USERPROFILE%\.codex`。
2. `codex --version`、`codex login status`、`codex login`、连接测试以及正常 `cli-codex` 页面回答，全部显式注入同一个产品 `CODEX_HOME`。
3. 连接测试与正常页面回答都在新建空临时目录运行，并使用 `--ignore-user-config --ignore-rules --ephemeral --sandbox read-only --skip-git-repo-check`；不连接 PP03 MCP。
4. 上游 `0.149.0` 只作为固定提交的已测试参考，不自动安装、不凭版本号假绿；缺少本 Work 所需命令能力时报告 `unsupported_version`。
5. 成功响应只暴露任务书白名单字段；真实可执行路径、用户名目录、产品 home、原始 stderr 和认证材料不进入 HTTP 响应。
