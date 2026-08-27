# 原生收敛定向与回归结果

日期：2026-08-28
分支：`codex/pp03-native-radar-convergence`
固定基线：`810204efd317000aafa9836f8e2996ae4b1600ca`

## Stage B 用户实页验收

- `USER_PAGE_ACCEPTANCE=PASS`
- `USER_COMMENT=目前看起来还可以`

该事实由用户在 Stage C 启动指令中明确确认；本报告不从自动化结果反推用户验收。

## 最终定向回归

| 项目 | 命令范围 | 结果 |
| --- | --- | --- |
| Backend 安全收口定向 | source manager + newsradar | `71 passed`，exit 0 |
| Frontend 定向 | native convergence routes、SourceManagement、radar source API | `3 files / 18 tests passed`，exit 0 |
| 内置来源删除合同 | 真实 HTTP DELETE 内置 OpenAI source | `409 Conflict`；来源仍存在；内置配置哈希不变 |
| 重复 URL 合同 | manager + API | 独立 `DuplicateSourceError`；API `409 Conflict` |
| 移除路径合同 | 7 条旧路径 | 全部回落 `/daily-review`；不渲染旧页面，不产生路由 error |

## 最终完整回归

所有命令均使用项目 `.tmp/acceptance/native-convergence-stage-c-tests` 下的临时 data、report、news cache、TEMP、TMP 和 npm cache；credential-like 环境变量在测试进程内清除，未运行 Live/付费 Provider 测试。

| 项目 | 结果 |
| --- | --- |
| Backend 全量非 Live | `220 passed, 12 deselected, 1 warning`，exit 0 |
| Frontend 全量 | `3 files / 18 tests passed`，exit 0 |
| Legacy | `16 passed, 0 failed`，exit 0 |
| Production build | `2462 modules transformed`，exit 0 |
| Git diff check | `git diff --cached --check` 对完整 staged diff 执行，exit 0 |
| 依赖/lockfile 漂移 | `NONE` |
| 生产 caller scan | 旧页面/API/中台活动引用 `0` |

非阻塞告警：Backend 有 1 条 Starlette TestClient/httpx 弃用告警；Vite 对两个大于 500 kB 的既有 bundle chunk 发出 code-splitting 建议。两者均未被隐藏或通过降低标准消除。

## TDD 修复记录

1. 移除旧路由最初出现 React Router 默认错误页；先新增 7 个旧路径负向测试并观察 RED，再加一个全局回落到 `/daily-review` 的最小路由，最终 11 个 route tests 全绿。
2. 内置来源删除最初返回 400；先新增 409 合同测试并观察 RED，再只修改异常映射。
3. 重复 URL 最初没有独立冲突类型；先新增 manager/API 两层 RED，再增加 `DuplicateSourceError` 并映射 409。
4. 独立复审指出产品刷新绕过安全 probe；先新增私网初始 URL、有界响应、固定 DNS socket 等 RED，再让 radar 刷新/重试与来源测试共用安全传输。
5. 逐条配置语义、非 UTF-8、敏感 URL 展示、fragment、DNS 尾点重复、健康状态手工篡改均先有失败测试后修复。
6. 首轮浏览器复验暴露 Python `HTTPSHandler` 私有属性兼容性回归；新增针对运行时 handler 调用形状的 RED，修复后 BBC HTTPS RSS 解析 20 条并完成浏览器全流程。
7. 第二轮独立复审指出 cache/API 原始 feed URL、来源投影、健康语义、双 ID 算法和未知 Intel 子路由问题；分别新增失败测试后收敛为统一 `source_id`、host-only 投影、严格健康 schema 与显式重定向。
8. 最终自查发现不可哈希 health/probe 字段可抛 TypeError；新增 2 个 RED，加入显式类型守卫后定向 71 项、全量非 Live 220 项通过。
