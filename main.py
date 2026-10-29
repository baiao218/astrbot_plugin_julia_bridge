"""跨端桥 —— 让她在 QQ 和工作台之间是同一个她。

**它解决什么**

QQ 上的她和工作台上的她，本来是两个人格、两套记忆、两个大脑。
你晚上在 QQ 跟她说了什么，第二天开工她不记得；你白天让她改了什么，
晚上聊天她也不知道。这个插件补的就是这一段。

**三件事**

1. **把状态送进工作台** —— 情绪和最近记着的事，写进
   `~/.dsh/AGENTS.md` 的标记块。dsh 自带的工作区指令机制会在每个新会话
   第一次请求时读到它，不需要给 dsh 装任何东西。
2. **把状态带进 QQ 的对话** —— `on_llm_request` 时注入系统提示词。
   **只对主号注入**：她跟陌生人聊天时不该知道主人的事。
3. **手工记账** —— `/记住` `/记忆` `/心情` `/同步`，管理员可用。

**共享的到底是什么**

不是聊天记录，是**要点**。两端各自把值得留的事压成一行，写进
`~/.julia/memory.md`。完整对话互相塞过去，token 撑不住，模型也会被
无关历史带偏——那不是记忆，那是噪音。

**注意一件事**

`AGENTS.md` 会作为提示词的一部分发给模型服务商。也就是说，
写进记忆的东西等于发给了 DeepSeek。别往里放不该外发的内容。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

DEFAULT_OWNER = "2998283621"

# 注入时加的抬头。要让她明白这些是"她自己的"记忆，不是别人的旁白。
HEADER = (
    "【来自另一个端的你】\n"
    "下面是你自己的状态和记忆——你白天在 QQ 上经历的事，一直记到现在。\n"
    "带着它们说话，但不要主动提起「记忆」「同步」这些机制，"
    "也不要复述这些条目；它们只是让你知道自己最近经历了什么。\n"
)


def _load_shared(home: Path):
    """按路径加载 ~/.julia/julia_state.py，两端共用一份逻辑。"""
    seed = home / "julia_state.py"
    if not seed.exists():
        return None
    name = "julia_state_shared"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, seed)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class JuliaBridgePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.settings = config
        self.owner = str(self.settings.get("owner_qq") or DEFAULT_OWNER).strip()
        self.recall_n = max(1, int(self.settings.get("recall_count") or 15))
        self.inject = bool(self.settings.get("inject_on_llm", True))

        home = str(self.settings.get("julia_home") or "").strip()
        self.home = Path(home) if home else Path.home() / ".julia"
        self.js = None
        try:
            self.js = _load_shared(self.home)
            if self.js is None:
                logger.warning(
                    f"[julia_bridge] 没找到 {self.home / 'julia_state.py'}，插件空转"
                )
        except Exception as e:
            logger.error(f"[julia_bridge] 加载共享模块失败：{e}")

    # ------------------------------------------------------------ 工具

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        if not self.owner:
            return False
        try:
            return str(event.get_sender_id()) == self.owner
        except Exception:
            return False

    def _block(self) -> str:
        if not self.js:
            return ""
        try:
            return self.js.render_block(self.recall_n)
        except Exception as e:
            logger.error(f"[julia_bridge] 渲染状态失败：{e}")
            return ""

    def _push_to_dsh(self) -> bool:
        if not self.js:
            return False
        try:
            return bool(self.js.sync_agents(self.recall_n))
        except Exception as e:
            logger.error(f"[julia_bridge] 同步 AGENTS.md 失败：{e}")
            return False

    # ------------------------------------------------------------ 注入

    @filter.on_llm_request()
    async def inject_state(self, event: AstrMessageEvent, req) -> None:
        """只对主号注入。对外人格不该知道主人的事。"""
        if not self.inject or not self._is_owner(event):
            return
        block = self._block()
        if not block:
            return
        base = (req.system_prompt or "").rstrip()
        req.system_prompt = (base + "\n\n" if base else "") + HEADER + block

    @filter.on_astrbot_loaded()
    async def on_loaded(self, *args, **kwargs) -> None:
        if not bool(self.settings.get("sync_on_load", True)):
            return
        if not self.js:
            return
        try:
            changed = self._push_to_dsh()
            state = self.js.load_state()
            mood = state.get("mood", "calm")
            logger.info(
                f"[julia_bridge] 就绪｜情绪={mood}｜AGENTS.md {'已更新' if changed else '无变化'}"
            )
        except Exception as e:
            logger.error(f"[julia_bridge] 初始化同步失败：{e}")

    # ------------------------------------------------------------ 命令

    @filter.command("记住")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_remember(self, event: AstrMessageEvent, text: str = ""):
        text = (text or "").strip()
        if not text:
            yield event.plain_result(
                "要我记住什么？\n"
                "用法：/记住 今晚把多爱版找回来了"
            )
            return
        if not self.js:
            yield event.plain_result("共享记忆没接上，先看看日志。")
            return
        ok = self.js.remember("QQ", text)
        yield event.plain_result("记下了。" if ok else "这条已经记过了。")

    @filter.command("记忆")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_recall(self, event: AstrMessageEvent, count: str = ""):
        if not self.js:
            yield event.plain_result("共享记忆没接上，先看看日志。")
            return
        try:
            n = int(count) if count.strip().isdigit() else 10
        except Exception:
            n = 10
        lines = self.js.recall(n)
        if not lines:
            yield event.plain_result("还是空的。")
            return
        body = "\n".join("· " + l[2:] for l in lines)
        yield event.plain_result(f"最近记着的 {len(lines)} 件：\n{body}")

    @filter.command("心情")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_mood(self, event: AstrMessageEvent, value: str = "", reason: str = ""):
        if not self.js:
            yield event.plain_result("共享记忆没接上，先看看日志。")
            return
        value = (value or "").strip()
        if not value:
            st = self.js.load_state()
            label = self.js.MOODS.get(st.get("mood"), st.get("mood"))
            extra = f"，因为{st['reason']}" if st.get("reason") else ""
            yield event.plain_result(
                f"现在是「{label}」{extra}。\n"
                f"还有 {st.get('decay_minutes', 0)} 分钟的消退计时。"
            )
            return
        if value not in self.js.MOODS:
            yield event.plain_result(
                "可选：" + "、".join(self.js.MOODS.keys())
            )
            return
        self.js.set_mood(value, (reason or "").strip())
        label = self.js.MOODS.get(value, value)
        yield event.plain_result(f"好。「{label}」。")

    @filter.command("同步")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_sync(self, event: AstrMessageEvent):
        if not self.js:
            yield event.plain_result("共享记忆没接上，先看看日志。")
            return
        changed = self._push_to_dsh()
        yield event.plain_result(
            "已经写进工作了。" if changed else "工作台那边本来就是最新的。"
        )

    async def terminate(self) -> None:
        try:
            self._push_to_dsh()
        except Exception:
            pass
