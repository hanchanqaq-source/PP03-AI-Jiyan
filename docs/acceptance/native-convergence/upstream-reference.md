# Upstream 参考与许可证保留

## 固定来源

- 项目固定基线：`810204efd317000aafa9836f8e2996ae4b1600ca`。
- 只读 upstream 参考：`ab4ffa077e0b1806fc53164dc7b28731f834e79e`，仓库 `simonlin1212/Vibe-Research`。
- 工作分支：`codex/pp03-native-radar-convergence`。
- 没有整体 merge upstream；仅按计划移植原生导航、Signals、Investment News 相关修复，并在原生 `/intel/sources` 内增加来源管理。

## 文件归属

以下文件与 upstream 参考 blob 完全相同：

| 文件 | upstream blob |
| --- | --- |
| `frontend/src/pages/Signals.tsx` | `b8037393cbbc8c35af3725674a70bc4ec3848d9e` |
| `frontend/src/components/ui/EChart.tsx` | `3c5fcbee1fe52beb9a2c0749d49e9cfe171733da` |
| `backend/signals.py` | `2ccaef8309cdd93839dffd5baa38c4f2d2dea9bd` |
| `backend/data/signals_gpu_seed.json` | `e1698283ebee6727d1c118d61affd5b96cf2f3a3` |
| `backend/tests/test_signals.py` | `8d1b48aefe057eaa3370216d60a9e1fc0a61b9bf` |

`router.tsx`、`Layout.tsx`、`Intel.tsx` 和 `newsradar.py` 以该 upstream 实现为原生骨架，并包含本 Work 的路由收敛、来源管理和安全持久化改动，因此当前 blob 与 upstream 不同。差异由本 Work 测试与验收报告覆盖。

## MIT 许可证

- 当前 `LICENSE` Git blob：`5230eb49300fb022df2c765657e1782f68a6f20e`。
- upstream `LICENSE` Git blob：`5230eb49300fb022df2c765657e1782f68a6f20e`。
- 当前 `LICENSE` SHA-256：`5FD1AEAACD91AE2A0324E4C07356465B3F6F9A18D81062B590813E939F4A62B6`。
- 结论：upstream 作者归属与 MIT 许可证原样保留。
