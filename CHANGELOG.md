# Changelog

## 1.0.0

- 首次发布：QQ 与 DeepSeek Harness 工作台之间的状态桥
- 共享 `~/.julia/state.json`（情绪 + 消退计时）与 `~/.julia/memory.md`（要点记忆）
- `on_llm_request` 注入，**只对主号会话生效**
- 自动维护 `~/.dsh/AGENTS.md` 标记块，dsh 新会话首次请求即读到
- 命令：`/记住`、`/记忆`、`/心情`、`/同步`
