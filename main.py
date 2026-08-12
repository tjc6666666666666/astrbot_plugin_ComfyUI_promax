"""
ComfyUI AI绘画插件 — AstrBot 平台适配层

职责：
- 接收 AstrBot 消息事件 → 解析为用户输入 → 调用 WorkflowEngine
- 将 WorkflowResult 转换为 AstrBot 消息链
- 管理 Filters、事件处理、消息工具、帮助系统、文件上传

依赖：
- workflow_engine.py (WorkflowEngine)
- gui_server.py (GuiServer, ConfigManager)
"""
import asyncio
import aiohttp
import io
import json
import logging
import os
import random
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import CustomFilter
from astrbot.api.message_components import Image, Node, Nodes, Plain, Record, Reply, Video
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import llm_tool
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path
from aiohttp import web

# 使用包内导入：AstrBot 重载时会清理 data.plugins.<插件名> 下的所有模块缓存。
from .workflow_engine import WorkflowEngine, WorkflowResult
from .gui_server import GuiServer

# 模块级 WorkflowFilter — 直接引用引擎的 workflow_prefixes
_workflow_engine_ref: Optional[WorkflowEngine] = None

class WorkflowFilter(CustomFilter):
    """自定义 Workflow 过滤器（模块级，避免嵌套类变量作用域问题）"""
    def filter(self, event, cfg):
        global _workflow_engine_ref
        if _workflow_engine_ref is None:
            return False
        text = event.message_obj.message_str.strip()
        if not text:
            return False
        words = text.split()
        return len(words) > 0 and words[0] in _workflow_engine_ref.workflow_prefixes


# ============================================================
#  主插件类
# ============================================================

@register(
    "mod-comfyui",
    "",
    "使用多服务器ComfyUI文生图/图生图（支持模型选择、LoRA、自定义Workflow和服务器轮询）。\n开放时间：{open_time_ranges}\n文生图：发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填（例：/aimg girl 宽512,高768 批量2 model:写实风格 lora:儿童:0.8 lora:可爱!1.0）\n图生图：发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息（例：img2img 猫咪 噪声:0.7 批量2 model:动漫风格 lora:动物:1.2!0.9 + 图片/引用图片消息）\n自定义Workflow：发送「<前缀> [参数名:值 ...]」+ 图片（如需要），支持中英文参数名（例：encrypt 模式:decrypt 或 t2l 提示词:可爱女孩 种子:123 采样器:euler）\n输出压缩包：发送「comfyuioutput」获取今天生成的图片压缩包（需开启自动保存）\nLLM工具：可通过AI助手调用comfyui_txt2img工具进行文生图（支持英文提示词，空格自动转换为下划线）\n模型使用说明：\n  - 格式：model:描述（描述对应配置中的模型描述）\n  - 例：model:写实风格\nLoRA使用说明：\n  - 基础格式：lora:描述（使用默认强度1.0/1.0，描述对应配置中的LoRA描述）\n  - 仅模型强度：lora:描述:0.8（strength_model=0.8）\n  - 仅CLIP强度：lora:描述!1.0（strength_clip=1.0）\n  - 双强度：lora:描述:0.8!1.3（model=0.8, clip=1.3）\n  - 多LoRA：空格分隔多个lora参数（例：lora:儿童 lora:学生:0.9）\nWorkflow参数说明：\n  - 支持中英文参数名和别名（如：width/宽度/w，sampler_name/采样器/sampler）\n  - 参数格式：参数名:值（例：宽度:800 或 采样器:euler）\n  - 具体支持的参数名请查看各workflow的配置说明\n多服务器轮询处理，所有生成图片将合并为一条消息发送，未指定参数则用默认配置（文生图默认批量数：{txt2img_batch_size}，图生图默认批量数：{img2img_batch_size}，默认噪声系数：{default_denoise}，默认模型：{ckpt_name}）。\n限制说明：文生图最大批量{max_txt2img_batch}，图生图最大批量{max_img2img_batch}，分辨率范围{min_width}~{max_width}x{min_height}~{max_height}，任务队列最大{max_task_queue}个，每用户最大并发{max_concurrent_tasks_per_user}个\n可用模型列表：\n{model_list_desc}\n可用LoRA列表：\n{lora_list_desc}\n可用Workflow列表：\n{workflow_list_desc}",
    "3.6"  # 重构版本：engine + gui 分离
)
class ModComfyUI(Star):
    """ComfyUI AI绘画插件 — AstrBot 适配层"""

    # ========== 过滤器 ==========

    class ImgGenerateFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg) -> bool:
            msgs = event.get_messages()
            if any(isinstance(m, Image) for m in msgs):
                return False
            text = event.message_obj.message_str.strip()
            return (text.startswith("aimg") or text.startswith("aimg ")) and text != "aimg"

    class Img2ImgFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg) -> bool:
            msgs = event.get_messages()
            has_img = any(isinstance(m, Image) for m in msgs)
            reply = next((s for s in msgs if isinstance(s, Reply)), None)
            has_reply_img = bool(reply and reply.chain and any(isinstance(s, Image) for s in reply.chain))
            text = event.message_obj.message_str.strip()
            starts = (text.startswith("img2img") or text.startswith("img2img ")) and text != "img2img"
            return starts and (has_img or has_reply_img)

    class TomatoDecryptFilter(CustomFilter):
        def filter(self, event, cfg): return event.message_obj.message_str.strip() == "小番茄图片解密"

    class TeeeFilter(CustomFilter):
        def filter(self, event, cfg): return event.message_obj.message_str.strip() == "teeee"

    class AddServerFilter(CustomFilter):
        def filter(self, event, cfg):
            return event.message_obj.message_str.strip().startswith("添加服务器")

    class HelpFilter(CustomFilter):
        def filter(self, event, cfg):
            msgs = event.get_messages()
            if any(isinstance(m, Image) for m in msgs):
                return False
            return event.message_obj.message_str.strip() in ["aimg", "img2img"]

    class OutputZipFilter(CustomFilter):
        def filter(self, event, cfg):
            msgs = event.get_messages()
            if any(isinstance(m, Image) for m in msgs):
                return False
            return event.message_obj.message_str.strip() == "comfyuioutput"

    # ========== 初始化 ==========

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.plugin_dir = str(Path(__file__).parent)
        self.data_dir = StarTools.get_data_dir()
        self.data_dir.mkdir(exist_ok=True)

        # 1. 创建引擎（构造函数是同步的，内部用 create_task 启动异步组件）
        self.engine = WorkflowEngine(
            config=config, plugin_dir=self.plugin_dir, data_dir=self.data_dir
        )

        # 2. 适配层专有配置
        self.group_whitelist = [str(g) for g in config.get("group_whitelist", [])]
        self.enable_auto_recall = config.get("enable_auto_recall", False)
        self.auto_recall_delay = config.get("auto_recall_delay", 20)
        self.enable_help_image = config.get("enable_help_image", True)
        self.help_server_port = config.get("help_server_port", 8080)
        self.enable_output_zip = config.get("enable_output_zip", True)
        self.enable_fake_forward = config.get("enable_fake_forward", False)
        self.fake_forward_threshold = config.get("fake_forward_threshold", 2)
        self.fake_forward_qq = config.get("fake_forward_qq", "")
        self.enable_audio_to_voice = config.get("enable_audio_to_voice", True)
        self.daily_download_limit = config.get("daily_download_limit", 1)

        # 3. 帮助服务器
        self.help_server_thread: Optional[threading.Thread] = None
        self.help_server_runner: Optional[web.AppRunner] = None
        self.help_server_site: Optional[web.TCPSite] = None
        self.actual_help_port = self.help_server_port

        # 4. 设置模块级引擎引用（供 WorkflowFilter 直接读取 workflow_prefixes）
        global _workflow_engine_ref
        _workflow_engine_ref = self.engine

        # 5. GUI 服务器
        self.gui_server: Optional[GuiServer] = None
        self._init_gui(config)

        # 激活 LLM 工具
        self.context.activate_llm_tool("comfyui_txt2img")
        self.context.activate_llm_tool("comfyui_queue_status")
        self.context.activate_llm_tool("comfyui_workflow_list")
        self.context.activate_llm_tool("comfyui_workflow_run")

    def _init_gui(self, config: dict):
        """初始化 GUI 服务器"""
        if not config.get("enable_gui", False):
            return
        gui_port = config.get("gui_port", 7777)
        config_dir = Path(self.plugin_dir)
        self.gui_server = GuiServer(
            config_dir=config_dir,
            workflow_dir=config_dir / "workflow",
            user_workflow_dir=self.engine.user_workflow_dir,
            main_config_file=config_dir / "config.json",
            gui_port=gui_port,
            gui_username=config.get("gui_username", "123"),
            gui_password=config.get("gui_password", "123")
        )
        self.gui_server.init_app()
        self.gui_server.start()

    # ========== 结果回调处理 ==========

    @staticmethod
    def _format_comfyui_error(error: str) -> str:
        """将 ComfyUI 原始错误 JSON 解析为人类可读的错误描述"""
        if not error:
            return "未知错误"
        # 尝试解析 JSON 格式的 ComfyUI 错误
        try:
            # 提取 JSON 部分（可能嵌在 Exception 文本中）
            json_start = error.find('{')
            if json_start >= 0:
                err_data = json.loads(error[json_start:])
            else:
                return error[:500]
            parts = []
            # 顶层错误
            if isinstance(err_data, dict):
                err_obj = err_data.get("error", err_data)
                if isinstance(err_obj, dict):
                    err_type = err_obj.get("type", "")
                    err_msg = err_obj.get("message", "")
                    if err_type:
                        parts.append(f"[{err_type}]")
                    if err_msg:
                        parts.append(err_msg)
                # 节点级错误
                node_errors = err_data.get("node_errors", {})
                if node_errors:
                    parts.append("\n节点错误详情：")
                    for node_id, nd_err in node_errors.items():
                        errs = nd_err.get("errors", [])
                        for e in errs:
                            etype = e.get("type", "")
                            emsg = e.get("message", "")
                            detail = e.get("details", "")
                            extra = e.get("extra_info", {})
                            input_name = extra.get("input_name", detail)
                            display = extra.get("input_config", [])
                            min_val = max_val = None
                            if isinstance(display, list) and len(display) >= 2 and isinstance(display[1], dict):
                                min_val = display[1].get("min")
                                max_val = display[1].get("max")
                            param_desc = input_name if input_name else detail
                            # 构建可读的错误描述
                            if etype == "value_smaller_than_min":
                                parts.append(f"  • 节点 {node_id} 参数「{param_desc}」：{emsg}（最小值：{min_val}，当前值小于最小值，请使用 ≥{min_val} 的值）")
                            elif etype == "value_bigger_than_max":
                                parts.append(f"  • 节点 {node_id} 参数「{param_desc}」：{emsg}（最大值：{max_val}）")
                            else:
                                parts.append(f"  • 节点 {node_id} 参数「{param_desc}」：{emsg}")
                return "\n".join(parts) if parts else error[:500]
            return error[:500]
        except Exception:
            return error[:500]

    def _make_result_callback(self, event: AstrMessageEvent, task_type: str = "txt2img",
                               metadata: dict = None):
        """创建任务完成后的回调函数，负责将 WorkflowResult 发回给用户"""
        async def _send_result(result: WorkflowResult):
            try:
                if not result.success:
                    formatted = self._format_comfyui_error(result.error or '未知错误')
                    meta = result.metadata or {}
                    if meta.get("is_ai_initiated"):
                        task_id = meta.get("task_id") or "未知"
                        if self._wake_main_agent_for_ai_task_failure(event, task_id, formatted):
                            return
                    await self._send_with_auto_recall(
                        event, event.plain_result(f"\n❌ 生成失败：{formatted}")
                    )
                    return
                
                if task_type == "llm_tool":
                    if result.images:
                        import aiohttp
                        import os
                        meta = metadata or {}
                        url = result.images[0]["url"]
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, timeout=30) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    tmp_dir = os.path.join(self.plugin_dir, 'temp')
                                    os.makedirs(tmp_dir, exist_ok=True)
                                    img_path = os.path.join(tmp_dir, f'llm_temp_{result.images[0]["filename"]}')
                                    with open(img_path, 'wb') as f:
                                        f.write(data)
                                    chain = [Image.fromFileSystem(img_path)]
                                    await event.send(event.chain_result(chain))
                                    self._schedule_cleanup(img_path)
                                    return
                        await event.send(event.plain_result("图片下载失败"))
                    return

                # 构建结果消息
                result_parts = []
                if result.images:
                    result_parts.append(f"{len(result.images)}张图片")
                if result.videos:
                    result_parts.append(f"{len(result.videos)}个视频")
                if result.audios:
                    result_parts.append(f"{len(result.audios)}个音频")
                if result.models_3d:
                    result_parts.append(f"{len(result.models_3d)}个3D模型")
                if result.files:
                    result_parts.append(f"{len(result.files)}个文件")

                meta = result.metadata or {}
                if task_type == "workflow":
                    result_text = f"Workflow「{meta.get('workflow_title', '')}」执行完成！"
                else:
                    prompt = meta.get('prompt', '')
                    seed = meta.get('seed', '')
                    is_i2i = meta.get('is_img2img', False)
                    denoise = meta.get('denoise')
                    batch = meta.get('batch_size', 1)
                    w, h = meta.get('width', ''), meta.get('height', '')
                    if is_i2i:
                        result_text = (f"提示词：{self._truncate_prompt(prompt)}\nSeed：{seed}\n"
                                       f"噪声系数：{denoise}\n批量数：{batch}\n图生图生成完成！")
                    else:
                        result_text = (f"提示词：{self._truncate_prompt(prompt)}\nSeed：{seed}\n"
                                       f"分辨率：{w}x{h}\n批量数：{batch}\n文生图生成完成！")

                if result_parts:
                    result_text += f"\n\n共{'、'.join(result_parts)}："

                # 构建合并消息链
                merged_chain = [Plain(result_text)]

                # 添加图片
                for idx, img_info in enumerate(result.images, 1):
                    merged_chain.append(Plain(f"\n\n第{idx}/{len(result.images) + len(result.videos) + len(result.audios) + len(result.models_3d) + len(result.files)}张："))
                    merged_chain.append(Image.fromURL(img_info["url"]))

                # 添加视频
                for idx, vinfo in enumerate(result.videos, len(result.images) + 1):
                    merged_chain.append(Plain(f"\n\n第{idx}个视频：正在下载处理..."))
                    try:
                        temp_path = await self._download_to_temp(vinfo["url"], vinfo["filename"])
                        if temp_path:
                            await self._send_video(event, temp_path, vinfo["filename"], idx)
                            merged_chain.append(Plain(f"\n✅ 视频{idx}处理完成"))
                    except Exception as e:
                        merged_chain.append(Plain(f"\n❌ 视频{idx}处理失败: {str(e)}"))

                # 添加音频
                for idx, ainfo in enumerate(result.audios, len(result.images) + len(result.videos) + 1):
                    merged_chain.append(Plain(f"\n\n第{idx}个音频：正在上传..."))
                    try:
                        ap = await self._download_to_temp(ainfo["url"], ainfo["filename"])
                        if ap:
                            dur = await self._get_audio_duration(ap)
                            ok = await self._upload_audio_file(event, ap, ainfo["filename"], dur)
                            merged_chain.append(Plain(f"\n{'✅' if ok else '❌'} 音频{idx}上传{'成功' if ok else '失败'}"))
                    except Exception as e:
                        merged_chain.append(Plain(f"\n❌ 音频{idx}上传失败: {str(e)}"))

                # 添加 3D 模型
                for idx, minfo in enumerate(result.models_3d,
                                            len(result.images) + len(result.videos) + len(result.audios) + 1):
                    merged_chain.append(Plain(f"\n\n第{idx}个3D模型：正在上传..."))
                    try:
                        model_path = await self._download_to_temp(minfo["url"], minfo["filename"])
                        if model_path:
                            ok = await self._upload_3d_model_file(event, model_path, minfo["filename"])
                            merged_chain.append(Plain(f"\n{'✅' if ok else '❌'} 3D模型{idx}上传{'成功' if ok else '失败'}"))
                    except Exception as e:
                        merged_chain.append(Plain(f"\n❌ 3D模型{idx}上传失败: {str(e)}"))

                # 添加未识别的通用文件
                for idx, finfo in enumerate(result.files,
                                            len(result.images) + len(result.videos) + len(result.audios) + len(result.models_3d) + 1):
                    merged_chain.append(Plain(f"\n\n第{idx}个文件：正在上传..."))
                    try:
                        file_path = await self._download_to_temp(finfo["url"], finfo["filename"])
                        if file_path:
                            ok = await self._upload_file(event, file_path, finfo["filename"])
                            merged_chain.append(Plain(f"\n{'✅' if ok else '❌'} 文件{idx}上传{'成功' if ok else '失败'}"))
                    except Exception as e:
                        merged_chain.append(Plain(f"\n❌ 文件{idx}上传失败: {str(e)}"))

                # 发送（优先伪造转发）
                await self.send_fake_forward_message(event, merged_chain, len(result.images))

            except Exception as e:
                logger.error(f"发送结果失败: {e}")
                try:
                    await self._send_with_auto_recall(event, event.plain_result(f"\n发送结果时出错：{str(e)[:200]}"))
                except Exception:
                    pass

        return _send_result

    async def _download_to_temp(self, url: str, filename: str, cleanup: bool = True) -> Optional[str]:
        """下载文件到临时目录"""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=120) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
            tmp_dir = self.data_dir / "temp"
            tmp_dir.mkdir(exist_ok=True)
            path = tmp_dir / filename
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: path.write_bytes(data))
            if cleanup:
                self._schedule_cleanup(str(path))
            return str(path)
        except Exception as e:
            logger.warning(f"下载文件失败: {e}")
            return None

    def _parse_image_inputs(self, image_inputs: Any) -> List[str]:
        """解析 LLM 工具传入的图片数组，支持 URL 和本地路径。"""
        if not image_inputs:
            return []
        if isinstance(image_inputs, (list, tuple)):
            candidates = image_inputs
        elif isinstance(image_inputs, str):
            text = image_inputs.strip()
            if not text:
                return []
            try:
                decoded = json.loads(text)
                if isinstance(decoded, (list, tuple)):
                    candidates = decoded
                else:
                    candidates = [decoded]
            except Exception:
                candidates = re.split(r"[\n,，]+", text)
        else:
            candidates = [image_inputs]

        images = []
        for item in candidates:
            image = str(item or "").strip()
            if not image:
                continue
            parsed = urlparse(image)
            if parsed.scheme and parsed.scheme not in ("http", "https", "file"):
                raise ValueError(f"无效图片输入：{image[:120]}")
            if parsed.scheme in ("http", "https") and not parsed.netloc:
                raise ValueError(f"无效图片URL：{image[:120]}")
            images.append(image)
        return images

    def _is_http_image_input(self, image_input: str) -> bool:
        parsed = urlparse(image_input)
        return parsed.scheme in ("http", "https")

    def _image_input_to_local_path(self, image_input: str) -> str:
        parsed = urlparse(image_input)
        if parsed.scheme == "file":
            path = unquote(parsed.path)
            if os.name == "nt" and re.match(r"^/[a-zA-Z]:/", path):
                path = path[1:]
            return path
        return image_input

    def _workspace_root_for_event(self, event: AstrMessageEvent) -> Optional[Path]:
        umo = getattr(event, "unified_msg_origin", None)
        if not umo:
            return None
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(umo).strip()) or "unknown"
        return (Path(get_astrbot_workspaces_path()) / normalized).resolve(strict=False)

    def _wake_main_agent_for_ai_task_failure(self, event: AstrMessageEvent,
                                             task_id: str, error: str) -> bool:
        """将 AI 发起的后台任务失败交回主 Agent 处理。"""
        umo = getattr(event, "unified_msg_origin", None)
        if not umo:
            logger.warning("AI任务失败但缺少 unified_msg_origin，无法唤醒主 Agent")
            return False
        try:
            session = MessageSession.from_str(str(umo))
            message = (
                "ComfyUI 任务执行失败，请结合当前会话上下文向用户说明并决定下一步。\n"
                f"任务ID：{task_id or '未知'}\n"
                f"错误信息：{error or '未知错误'}"
            )
            cron_event = CronMessageEvent(
                context=self.context,
                session=session,
                message=message,
                message_type=session.message_type,
                extras={
                    "trigger_source": "comfyui_ai_task_failure",
                    "task_id": task_id,
                    "error": error,
                },
            )
            self.context.get_event_queue().put_nowait(cron_event)
            return True
        except Exception as e:
            logger.error(f"唤醒主 Agent 处理AI任务失败时出错: {e}")
            return False

    def _resolve_image_local_path(self, event: AstrMessageEvent, image_path: str) -> str:
        path = self._image_input_to_local_path(image_path).strip()
        if not path:
            raise Exception("图片路径为空")
        if os.path.isabs(path):
            return path

        workspace_root = self._workspace_root_for_event(event)
        if workspace_root is None:
            raise Exception("无法获取当前会话 workspace，不能使用相对图片路径")

        candidate = (workspace_root / path).resolve(strict=False)
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            raise Exception("相对图片路径不能超出当前 workspace")
        return str(candidate)

    async def _download_image_url_for_workflow(self, url: str, index: int) -> str:
        """下载 workflow 输入图片 URL 到临时文件。"""
        parsed = urlparse(url)
        ext = os.path.splitext(parsed.path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            ext = ".png"
        filename = f"workflow_url_{uuid.uuid4().hex}_{index + 1}{ext}"
        path = await self._download_to_temp(url, filename, cleanup=False)
        if not path or not os.path.exists(path):
            raise Exception("图片下载失败")
        if os.path.getsize(path) < 10240:
            raise Exception("图片文件过小")
        return path

    async def _copy_image_path_for_workflow(self, event: AstrMessageEvent,
                                            image_path: str, index: int) -> str:
        """复制 workflow 本地输入图片，避免原始文件被任务清理。"""
        src = self._resolve_image_local_path(event, image_path)
        if not os.path.exists(src):
            raise Exception("图片文件不存在")
        if not os.path.isfile(src):
            raise Exception("图片路径不是文件")
        if os.path.getsize(src) < 10240:
            raise Exception("图片文件过小")

        ext = os.path.splitext(src)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            ext = ".png"
        tmp_dir = self.data_dir / "temp"
        tmp_dir.mkdir(exist_ok=True)
        dst = tmp_dir / f"workflow_path_{uuid.uuid4().hex}_{index + 1}{ext}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.copy2(src, dst))
        return str(dst)

    async def _collect_workflow_image_paths(self, event: AstrMessageEvent, wfn: str,
                                            cfg: dict, image_inputs: Any = None) -> Tuple[List[str], Optional[str]]:
        """收集 workflow 输入图片：优先使用传入图片输入，未传则从当前消息/引用上下文取图。"""
        if not cfg.get("input_nodes"):
            return [], None

        eng = self.engine
        uid = str(event.get_sender_id())
        image_paths = []
        try:
            inputs = self._parse_image_inputs(image_inputs)
        except Exception as e:
            return [], str(e)

        if inputs:
            for i, image_input in enumerate(inputs):
                try:
                    if self._is_http_image_input(image_input):
                        ip = await self._download_image_url_for_workflow(image_input, i)
                    else:
                        ip = await self._copy_image_path_for_workflow(event, image_input, i)
                    if eng.enable_auto_save:
                        saved = await eng.save_input_image_permanently(ip, "workflow", uid, wfn, i)
                        if saved and saved != ip:
                            self._schedule_cleanup(ip)
                        image_paths.append(saved or ip)
                    else:
                        image_paths.append(ip)
                except Exception as e:
                    return [], f"第{i+1}张图片失败：{str(e)[:200]}"
            return image_paths, None

        msgs = event.get_messages()
        img_segs = [m for m in msgs if isinstance(m, Image)]
        reply = next((s for s in msgs if isinstance(s, Reply)), None)
        if not img_segs and reply and reply.chain:
            img_segs = [s for s in reply.chain if isinstance(s, Image)]
        if not img_segs:
            return [], "此workflow需要图片输入"

        for i, seg in enumerate(img_segs):
            try:
                ip = await seg.convert_to_file_path()
                if not os.path.exists(ip):
                    raise Exception("文件下载失败")
                fs = os.path.getsize(ip)
                if fs < 10240:
                    raise Exception("文件过小")
                if eng.enable_auto_save:
                    saved = await eng.save_input_image_permanently(ip, "workflow", uid, wfn, i)
                    image_paths.append(saved or ip)
                else:
                    image_paths.append(ip)
            except Exception as e:
                err = str(e)
                if "PERMANENT_ERROR:" in err:
                    return [], err.replace("PERMANENT_ERROR:", "")
                return [], f"第{i+1}张图片失败：{err[:200]}"
        return image_paths, None

    async def _get_audio_duration(self, audio_path: str) -> Optional[float]:
        """获取音频时长"""
        try:
            import subprocess
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, lambda: subprocess.run(
                ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                capture_output=True, text=True, timeout=10
            ))
            return float(r.stdout.strip()) if r.returncode == 0 else None
        except Exception:
            return None

    async def _send_with_auto_recall(self, event: AstrMessageEvent, message_content: Any) -> Optional[int]:
        """发送消息并根据配置自动撤回（仅撤回纯文本消息，含图片/文件时不撤回）"""
        if not self.enable_auto_recall:
            await event.send(message_content)
            return None

        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
            if not isinstance(event, AiocqhttpMessageEvent):
                await event.send(message_content)
                return None

            # 检查是否包含图片/文件 → 直接发送不撤回
            if self._has_image_or_file(message_content):
                await event.send(message_content)
                return None

            # 纯文本 → 用适配器的 _parse_onebot_json 序列化后通过 call_action 发送
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.api.message_components import Plain

            if isinstance(message_content, str):
                chain = [Plain(message_content)]
            elif isinstance(message_content, MessageChain):
                chain = message_content.chain
            elif hasattr(message_content, '__iter__') and not isinstance(message_content, str):
                chain = list(message_content)
            else:
                chain = [Plain(str(message_content))]

            obmsg = await event._parse_onebot_json(MessageChain(chain=chain))
            bot = event.bot
            group_id = event.get_group_id()
            sender_id = event.get_sender_id()

            # 构造 routing_params（参考 aiocqhttp 适配器的做法）
            routing_params = {}
            if hasattr(event.message_obj, 'raw_message') and isinstance(event.message_obj.raw_message, dict):
                sid = event.message_obj.raw_message.get('self_id')
                if sid:
                    routing_params['self_id'] = sid

            if group_id:
                result = await bot.call_action(
                    'send_group_msg',
                    group_id=int(group_id),
                    message=obmsg,
                    **routing_params,
                )
            else:
                result = await bot.call_action(
                    'send_private_msg',
                    user_id=int(sender_id),
                    message=obmsg,
                    **routing_params,
                )

            # 提取 message_id
            mid = None
            if isinstance(result, dict):
                mid = result.get('message_id') or result.get('id')
            elif isinstance(result, (int, float)):
                mid = int(result)
            elif isinstance(result, str):
                try:
                    mid = int(result)
                except ValueError:
                    pass

            if mid is not None:
                asyncio.create_task(
                    self._delayed_recall_by_id(bot, int(mid), routing_params)
                )
                logger.info(f"已安排 {self.auto_recall_delay}s 后撤回消息 ID: {mid}")
            return result
        except Exception as e:
            logger.warning(f"_send_with_auto_recall 异常: {e}")
            await event.send(message_content)
            return None

    @staticmethod
    def _has_image_or_file(content: Any) -> bool:
        """检查消息内容中是否包含图片或文件（含则跳过撤回）"""
        if isinstance(content, str):
            return False
        from astrbot.core.message.message_event_result import MessageChain
        if isinstance(content, MessageChain):
            for component in content.chain:
                if hasattr(component, '__class__'):
                    class_name = component.__class__.__name__
                    if 'Image' in class_name or 'File' in class_name:
                        return True
            return False
        if hasattr(content, '__iter__') and not isinstance(content, str):
            for component in content:
                if hasattr(component, '__class__'):
                    class_name = component.__class__.__name__
                    if 'Image' in class_name or 'File' in class_name:
                        return True
        return False

    async def _delayed_recall_by_id(self, bot, message_id: int, routing_params: dict):
        """延迟后撤回指定 ID 的消息（参考 aiocqhttp 适配器的 call_action + routing_params 模式）"""
        try:
            await asyncio.sleep(self.auto_recall_delay)
            if message_id is not None:
                await bot.call_action('delete_msg', message_id=message_id, **routing_params)
                logger.info(f"已自动撤回消息 ID: {message_id}")
        except Exception as e:
            logger.warning(f"自动撤回失败: {e}")

    # ========== 帮助系统 ==========

    async def _send_help_as_image(self, event: AstrMessageEvent):
        """发送图片格式帮助"""
        server_url = None
        img_path = None
        try:
            server_url = await self._start_help_server()
            img_path = await self._html_to_image(server_url)
            await event.send(event.image_result(img_path))
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"发送帮助图片失败: {e}")
            await self._send_help_as_text(event)
        finally:
            if img_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception:
                    pass
            if server_url:
                try:
                    await self._stop_help_server()
                except Exception:
                    pass

    async def _send_help_as_text(self, event: AstrMessageEvent):
        """发送文本帮助（与原版 main1.py 一致）"""
        eng = self.engine
        current_queue = eng.task_queue.qsize()
        total_tasks = sum(eng.user_task_counts.values())

        model_details = []
        if eng.model_name_map:
            seen = set()
            for _, (fn, desc) in eng.model_name_map.items():
                if desc not in seen:
                    seen.add(desc)
                    model_details.append(f"  • {desc} (文件: {fn})")
        else:
            model_details.append("  • 暂无可用模型")
        model_help = "\n🎨 可用模型列表：\n" + "\n".join(model_details) + "\n\n模型使用说明：\n  - 格式：model:描述（描述对应配置中的模型描述）\n  - 例：model:写实风格"

        lora_details = []
        if eng.lora_name_map:
            seen = set()
            for _, (fn, desc) in eng.lora_name_map.items():
                if desc not in seen:
                    seen.add(desc)
                    lora_details.append(f"  • {desc} (文件: {fn})")
        else:
            lora_details.append("  • 暂无可用LoRA")
        lora_help = "\n✨ 可用LoRA列表：\n" + "\n".join(lora_details) + "\n\nLoRA使用说明：\n  - 基础格式：lora:描述（使用默认强度1.0/1.0）\n  - 仅模型强度：lora:描述:0.8\n  - 仅CLIP强度：lora:描述!1.0\n  - 双强度：lora:描述:0.8!1.3\n  - 多LoRA：空格分隔多个lora参数（例：lora:儿童 lora:学生:0.9）"

        server_info = await self._build_server_info_text(eng)
        workflow_help = eng.generate_workflow_text_help()

        help_text = f"""
🎯 ComfyUI AI绘画帮助

⏰ 开放时间：{eng._get_open_time_desc()}

📊 实时状态：
• 当前排队任务：{current_queue} 个
• 总任务：{total_tasks} 个
• 队列容量：{eng.max_task_queue} 个
• 活跃用户数：{len(eng.user_task_counts)} 个

📝 使用说明：
• 文生图：发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填
  例：aimg girl 宽512,高768 批量2 model:写实风格 lora:儿童:0.8 lora:可爱!1.0

• 图生图：发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息
  例：img2img 猫咪 噪声:0.7 批量2 model:动漫风格 lora:动物:1.2!0.9 + 图片/引用图片消息

• 输出压缩包：发送「comfyuioutput」获取今天生成的图片压缩包（需开启自动保存）
• 小番茄解密：发送「小番茄图片解密」获取图片解密工具（支持批量解密小番茄混淆加密的图片）

• 自定义Workflow：发送「<前缀> [参数名:值 ...]」+ 图片（如需要），支持中英文参数名
  例：encrypt 模式:decrypt 或 t2l 提示词:可爱女孩 种子:123 采样器:euler

• 帮助信息：单独输入 aimg 或 img2img

⚙️ 默认配置：
• 文生图默认批量数：{eng.txt2img_batch_size}
• 图生图默认批量数：{eng.img2img_batch_size}
• 默认噪声系数：{eng.default_denoise}
• 默认模型：{eng.ckpt_name}
• 自动保存图片：{'开启' if eng.enable_auto_save else '关闭'}
• 输出压缩包：{'开启' if eng.enable_output_zip else '关闭'}
• 每日下载限制：{self.daily_download_limit} 次
• 仅限自己图片：{'是' if eng.only_own_images else '否'}

📏 参数限制：
• 文生图最大批量：{eng.max_txt2img_batch}
• 图生图最大批量：{eng.max_img2img_batch}
• 分辨率范围：{eng.min_width}~{eng.max_width} x {eng.min_height}~{eng.max_height}
• 任务队列最大：{eng.max_task_queue}个
• 每用户最大并发：{eng.max_concurrent_tasks_per_user}个
{server_info}
{model_help}
{lora_help}
{workflow_help}
        """
        await self._send_with_auto_recall(event, event.plain_result(help_text.strip()))

    @staticmethod
    def _build_model_help_text(eng: WorkflowEngine) -> str:
        if not eng.model_name_map:
            return "\n🎨 可用模型列表：\n  暂无可用模型"
        seen = set()
        items = []
        for _, (fn, desc) in eng.model_name_map.items():
            if desc not in seen:
                seen.add(desc)
                items.append(f"  • {desc} (文件: {fn})")
        return "\n🎨 可用模型列表：\n" + "\n".join(items) + "\n\n模型使用说明：\n  - 格式：model:描述\n  - 例：model:写实风格"

    @staticmethod
    def _build_lora_help_text(eng: WorkflowEngine) -> str:
        if not eng.lora_name_map:
            return "\n✨ 可用LoRA列表：\n  暂无可用LoRA"
        seen = set()
        items = []
        for _, (fn, desc) in eng.lora_name_map.items():
            if desc not in seen:
                seen.add(desc)
                items.append(f"  • {desc} (文件: {fn})")
        return "\n✨ 可用LoRA列表：\n" + "\n".join(items) + "\n\nLoRA使用说明：\n  - 格式：lora:描述[:强度][!CLIP强度]"

    async def _build_server_info_text(self, eng: WorkflowEngine) -> str:
        parts = ["\n🌐 服务器信息："]
        for srv in eng.comfyui_servers:
            if srv.healthy:
                parts.append(f"\n📊 【{srv.name}】")
                info = await eng._get_server_system_info(srv)
                if info:
                    sys_data = info.get("system", {})
                    devs = info.get("devices", [])
                    ram_t = sys_data.get("ram_total", 0) / (1024**3)
                    ram_f = sys_data.get("ram_free", 0) / (1024**3)
                    parts.append(f"  🖥️  OS: {sys_data.get('os','未知')}")
                    parts.append(f"  💾 内存：{ram_t-ram_f:.1f}GB / {ram_t:.1f}GB")
                    for i, d in enumerate(devs):
                        vt = d.get("vram_total", 0) / (1024**3)
                        vf = d.get("vram_free", 0) / (1024**3)
                        parts.append(f"  🎮 GPU{i+1}: {d.get('name','未知')} ({vt-vf:.1f}/{vt:.1f}GB)")
                else:
                    parts.append("  ❌ 无法获取系统信息")
            else:
                parts.append(f"\n📊 【{srv.name}】\n  ❌ 服务器不可用")
        return "".join(parts)

    async def _start_help_server(self) -> str:
        """启动帮助 HTTP 服务器"""
        if self.help_server_runner is not None:
            return f"http://localhost:{self.actual_help_port}"

        async def _find_port(sp):
            for p in range(sp, sp + 100):
                try:
                    _, w = await asyncio.wait_for(
                        asyncio.open_connection('localhost', p), timeout=1.0
                    )
                    w.close()
                    await w.wait_closed()
                except Exception:
                    return p
            return random.randint(49152, 65535)

        self.actual_help_port = await _find_port(self.help_server_port)
        eng = self.engine

        def gen_html():
            model_items = ""
            seen_m = set()
            for _, (fn, desc) in eng.model_name_map.items():
                if desc not in seen_m:
                    seen_m.add(desc)
                    model_items += f'<li>{desc} (文件: {fn})</li>'
            lora_items = ""
            seen_l = set()
            for _, (fn, desc) in eng.lora_name_map.items():
                if desc not in seen_l:
                    seen_l.add(desc)
                    lora_items += f'<li>{desc} (文件: {fn})</li>'
            server_items = "".join(
                f'<li>{s.name}</li>' for s in eng.comfyui_servers if s.healthy
            )
            return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><title>ComfyUI AI绘画帮助</title>
<style>
body{{font-family:'Microsoft YaHei',Arial,sans-serif;line-height:1.8;margin:0;padding:20px;
background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh}}
.container{{max-width:800px;margin:0 auto;background:#fff;border-radius:15px;box-shadow:0 10px 30px rgba(0,0,0,.2);overflow:hidden}}
.header{{background:linear-gradient(45deg,#4a90e2,#357abd);color:#fff;padding:30px;text-align:center}}
.header h1{{margin:0;font-size:2.5em}}
.content{{padding:30px}}
.section{{margin-bottom:30px;padding:20px;background:#f8f9fa;border-radius:10px;border-left:4px solid #4a90e2}}
.section h2{{margin-top:0;color:#333}}
.section ul{{margin:10px 0;padding-left:20px}}
.section li{{margin:8px 0;color:#555}}
.footer{{background:#333;color:#fff;text-align:center;padding:20px}}
</style></head><body>
<div class="container"><div class="header"><h1>🎨 ComfyUI AI绘画帮助</h1></div>
<div class="content">
<div class="section"><h2>🎯 主要功能</h2><ul>
<li>文生图: 发送「aimg &lt;提示词&gt; [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」</li>
<li>图生图: 发送「img2img &lt;提示词&gt; [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片</li>
<li>帮助信息: 单独输入 aimg 或 img2img</li></ul></div>
<div class="section"><h2>⚙️ 基本配置</h2><ul>
<li>默认模型: {eng.ckpt_name or '未配置'}</li>
<li>文生图批量: {eng.txt2img_batch_size}｜图生图批量: {eng.img2img_batch_size}</li>
<li>默认噪声: {eng.default_denoise}｜开放时间: {eng.open_time_ranges}</li></ul></div>
<div class="section"><h2>📏 参数限制</h2><ul>
<li>分辨率: {eng.min_width}~{eng.max_width} x {eng.min_height}~{eng.max_height}</li>
<li>文生图最大批量: {eng.max_txt2img_batch}｜图生图最大批量: {eng.max_img2img_batch}</li>
<li>任务队列: {eng.max_task_queue}｜每用户最大并发: {eng.max_concurrent_tasks_per_user}</li></ul></div>
<div class="section"><h2>🎨 可用模型列表</h2><ul>{model_items}</ul></div>
<div class="section"><h2>✨ 可用LoRA列表</h2><ul>{lora_items}</ul></div>
<div class="section"><h2>🌐 服务器信息</h2><ul>{server_items}</ul></div>
<div class="section"><h2>🔧 可用Workflow列表</h2><ul>{eng.generate_workflow_html_items()}</ul></div>
</div><div class="footer"><p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p></div></div></body></html>"""

        async def handle(req):
            return web.Response(text=gen_html(), content_type='text/html')

        app = web.Application()
        app.router.add_get('/', handle)
        self.help_server_runner = web.AppRunner(app)
        await self.help_server_runner.setup()
        self.help_server_site = web.TCPSite(self.help_server_runner, 'localhost', self.actual_help_port)
        await self.help_server_site.start()
        logger.info(f"帮助图片服务器已启动: http://localhost:{self.actual_help_port}")
        return f"http://localhost:{self.actual_help_port}"

    async def _html_to_image(self, html_url: str) -> str:
        """HTML 转图片（PIL 渲染）"""
        try:
            from PIL import Image as PILImage, ImageDraw, ImageFont
            try:
                fp = os.path.join(self.plugin_dir, "1.ttf")
                if os.path.exists(fp):
                    tf = ImageFont.truetype(fp, 52)
                    nf = ImageFont.truetype(fp, 32)
                    sf = ImageFont.truetype(fp, 24)
                else:
                    tf = nf = sf = ImageFont.load_default()
            except Exception:
                tf = nf = sf = ImageFont.load_default()

            eng = self.engine
            sections = []
            qsize = eng.task_queue.qsize()
            utc = sum(eng.user_task_counts.values())

            sections.append(("🎯 主要功能", [
                "• 文生图: 发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填",
                "• 图生图: 发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息",
                "• 输出压缩包: comfyuioutput",
                "• 小番茄解密：发送「小番茄图片解密」",
                "• 帮助信息: 单独输入 aimg 或 img2img"
            ]))
            sections.append(("📊 实时状态", [
                f"• 当前排队任务: {qsize} 个",
                f"• 总任务: {utc} 个",
                f"• 队列容量: {eng.max_task_queue} 个",
                f"• 活跃用户数: {len(eng.user_task_counts)} 个"
            ]))
            sections.append(("⚙️ 基本配置", [
                f"• 默认模型: {eng.ckpt_name or '未配置'}",
                f"• 文生图批量: {eng.txt2img_batch_size}",
                f"• 图生图批量: {eng.img2img_batch_size}",
                f"• 默认噪声: {eng.default_denoise}",
                f"• 自动保存: {'开启' if eng.enable_auto_save else '关闭'}",
                f"• 输出压缩包: {'开启' if eng.enable_output_zip else '关闭'}",
                f"• 每日下载限制: {eng.daily_download_limit} 次",
                f"• 仅限自己图片: {'是' if eng.only_own_images else '否'}",
                f"• 开放时间: {eng.open_time_ranges}"
            ]))
            sections.append(("📏 参数限制", [
                f"• 分辨率: {eng.min_width}~{eng.max_width} x {eng.min_height}~{eng.max_height}",
                f"• 文生图最大批量: {eng.max_txt2img_batch}",
                f"• 图生图最大批量: {eng.max_img2img_batch}",
                f"• 任务队列最大: {eng.max_task_queue}",
                f"• 每用户最大并发: {eng.max_concurrent_tasks_per_user}"
            ]))
            seen_m = set()
            ms = ["格式: model:描述"]
            if eng.model_name_map:
                for _, (fn, desc) in eng.model_name_map.items():
                    if desc not in seen_m:
                        seen_m.add(desc)
                        ms.append(f"• {desc} (文件: {fn})")
            else:
                ms.append("• 暂无可用模型")
            sections.append(("🎨 可用模型列表", ms))
            seen_l = set()
            ls = ["格式: lora:描述[:强度][!CLIP强度]"]
            if eng.lora_name_map:
                for _, (fn, desc) in eng.lora_name_map.items():
                    if desc not in seen_l:
                        seen_l.add(desc)
                        ls.append(f"• {desc} (文件: {fn})")
            else:
                ls.append("• 暂无可用LoRA")
            sections.append(("✨ 可用LoRA列表", ls))

            # 服务器信息（与原版完全一致）
            server_items = []
            for srv in eng.comfyui_servers:
                if srv.healthy:
                    server_items.append(f"📊 【{srv.name}】")
                    info = await eng._get_server_system_info(srv)
                    if info:
                        sd = info.get("system", {})
                        dd = info.get("devices", [])
                        rt = sd.get("ram_total", 0) / (1024**3)
                        rf = sd.get("ram_free", 0) / (1024**3)
                        ru = rt - rf
                        server_items.append(f"  系统: {sd.get('os','未知')}")
                        server_items.append(f"  PyTorch: {sd.get('pytorch_version','未知')}")
                        server_items.append(f"  内存: {ru:.1f}GB / {rt:.1f}GB")
                        if dd:
                            for i, d in enumerate(dd):
                                dn = d.get("name", "未知设备")
                                dt = d.get("type", "未知")
                                vt = d.get("vram_total", 0) / (1024**3)
                                vf = d.get("vram_free", 0) / (1024**3)
                                vu = vt - vf
                                server_items.append(f"  GPU{i+1}: {dn}")
                                server_items.append(f"    类型: {dt}")
                                server_items.append(f"    显存: {vu:.1f}GB / {vt:.1f}GB")
                        else:
                            server_items.append(f"  GPU: 未检测到GPU设备")
                    else:
                        server_items.append(f"  ❌ 无法获取系统信息")
                else:
                    server_items.append(f"📊 【{srv.name}】")
                    server_items.append(f"  ❌ 服务器不可用")
            sections.append(("🌐 服务器信息", server_items))

            # Workflow 列表
            wf_items = ["格式: <前缀> [参数名:值 ...]"]
            if eng.workflows:
                for wfn, info in eng.workflows.items():
                    cfg = info["config"]
                    name = cfg.get("name", wfn)
                    prefix = cfg.get("prefix", "")
                    desc = cfg.get("description", "")
                    if desc:
                        wf_items.append(f"• {name} (前缀: {prefix}) - {desc}")
                    else:
                        wf_items.append(f"• {name} (前缀: {prefix})")
            else:
                wf_items.append("• 暂无可用Workflow")
            sections.append(("🔧 可用Workflow列表", wf_items))

            width = 1200
            base_h = 120
            bh = 80
            ss = 30
            th = 45
            ih = 35
            th2 = base_h + bh
            for _, items in sections:
                th2 += th + len(items) * ih + ss
            height = max(800, th2)
            img = PILImage.new('RGB', (width, height), '#ffffff')
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, width, 80], fill='#4a90e2')
            tw = draw.textbbox((0, 0), "ComfyUI AI绘画帮助", font=tf)
            tx = (width - (tw[2] - tw[0])) // 2
            draw.text((tx, 25), "ComfyUI AI绘画帮助", fill='white', font=tf)
            yo = base_h
            for title, items in sections:
                draw.text((50, yo), title, fill='#333', font=nf)
                yo += th
                for item in items:
                    draw.text((80, yo), item, fill='#666', font=sf)
                    yo += ih
                yo += ss
            draw.rectangle([0, height - 80, width, height], fill='#f5f5f5')
            draw.text((50, height - 60),
                      f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                      fill='#999', font=sf)
            draw.text((50, height - 35),
                      "https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax",
                      fill='#666', font=sf)
            try:
                ap = os.path.join(self.plugin_dir, "Astrbot.png")
                if os.path.exists(ap):
                    ai = PILImage.open(ap)
                    ath = 60
                    atw = int(ath * ai.width / ai.height)
                    ar = ai.resize((atw, ath), PILImage.Resampling.LANCZOS)
                    img.paste(ar, (width - atw - 10, height - ath - 10),
                              ar if ar.mode == 'RGBA' else None)
            except Exception:
                pass
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            op = os.path.join(self.plugin_dir, f"help_{ts}.png")
            img.save(op, 'PNG', quality=95)
            logger.info(f"帮助图片已生成: {op}")
            return op
        except Exception as e:
            logger.error(f"HTML转图片失败: {e}")
            raise

    async def _stop_help_server(self):
        try:
            if self.help_server_site:
                await self.help_server_site.stop()
                self.help_server_site = None
            if self.help_server_runner:
                await self.help_server_runner.cleanup()
                self.help_server_runner = None
            logger.info("帮助服务器已停止")
        except Exception as e:
            logger.error(f"停止帮助服务器失败: {e}")

    def _filter_server_urls(self, text: str) -> str:
        return self.engine._filter_server_urls(text)

    def _check_group_whitelist(self, event: AstrMessageEvent) -> bool:
        if not self.group_whitelist:
            return True
        gid = event.get_group_id()
        if gid is None:
            return True
        return str(gid) in self.group_whitelist

    def _parse_command(self, full_text: str, keyword: str) -> Tuple[str, list]:
        mention_pat = r'@[\w\d]+'
        text = re.sub(mention_pat, '', full_text).strip()
        parts = text.split()
        if not parts or parts[0] != keyword:
            return ("", [])
        mentions = " ".join(re.findall(mention_pat, full_text))
        return (mentions, parts[1:])

    def _truncate_prompt(self, prompt: str, max_len: int = 8) -> str:
        return prompt[:max_len] + "..." if len(prompt) > max_len else prompt

    def _resolve_workflow(self, workflow_ref: str) -> Tuple[Optional[str], Optional[dict], Optional[str]]:
        """按目录名、前缀或显示名称查找 workflow。"""
        eng = self.engine
        ref = (workflow_ref or "").strip()
        if not ref:
            return None, None, "workflow不能为空"
        if ref in eng.workflows:
            return ref, eng.workflows[ref], None
        if ref in eng.workflow_prefixes:
            wfn = eng.workflow_prefixes[ref]
            return wfn, eng.workflows[wfn], None

        lowered = ref.lower()
        matched = []
        for wfn, info in eng.workflows.items():
            cfg = info.get("config", {})
            names = [wfn, str(cfg.get("name", "")), str(cfg.get("prefix", ""))]
            if any(lowered == n.lower() for n in names if n):
                matched.append(wfn)
        if len(matched) == 1:
            wfn = matched[0]
            return wfn, eng.workflows[wfn], None
        if len(matched) > 1:
            return None, None, f"workflow匹配到多个结果：{', '.join(matched)}"
        return None, None, f"未找到workflow：{ref}"

    def _workflow_param_docs(self, cfg: dict) -> List[dict]:
        docs = []
        for nid, node_cfg in cfg.get("node_configs", {}).items():
            for pname, pinfo in node_cfg.items():
                docs.append({
                    "node": nid,
                    "name": pname,
                    "aliases": pinfo.get("aliases", []),
                    "type": pinfo.get("type", "string"),
                    "required": bool(pinfo.get("required", False)),
                    "default": pinfo.get("default"),
                    "description": pinfo.get("description", ""),
                    "options": pinfo.get("options", []),
                    "min": pinfo.get("min"),
                    "max": pinfo.get("max"),
                })
        return docs

    def _format_workflow_list_for_ai(self, workflow_ref: str = "") -> str:
        eng = self.engine
        if workflow_ref:
            wfn, info, err = self._resolve_workflow(workflow_ref)
            if err:
                return err
            items = [(wfn, info)]
        else:
            items = list(eng.workflows.items())
        if not items:
            return "暂无可用Workflow"

        blocks = []
        for wfn, info in items:
            cfg = info.get("config", {})
            lines = [
                f"- id: {wfn}",
                f"  name: {cfg.get('name', wfn)}",
                f"  prefix: {cfg.get('prefix', wfn)}",
                f"  description: {cfg.get('description', '暂无') or '暂无'}",
                f"  needs_image: {'yes' if cfg.get('input_nodes') else 'no'}",
            ]
            params = self._workflow_param_docs(cfg)
            if params:
                lines.append("  parameters:")
                for p in params:
                    aliases = f" aliases={p['aliases']}" if p["aliases"] else ""
                    default = f" default={p['default']}" if p["default"] is not None else ""
                    required = "required" if p["required"] else "optional"
                    limits = []
                    if p["options"]:
                        limits.append(f"options={p['options']}")
                    if p["min"] is not None:
                        limits.append(f"min={p['min']}")
                    if p["max"] is not None:
                        limits.append(f"max={p['max']}")
                    limit_text = f" {' '.join(limits)}" if limits else ""
                    desc = f" - {p['description']}" if p["description"] else ""
                    lines.append(f"    - {p['name']} ({p['type']}, {required}){default}{aliases}{limit_text}{desc}")
            else:
                lines.append("  parameters: none")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _parse_workflow_param_text(self, workflow_text: str, cfg: dict) -> dict:
        """解析聊天式 workflow 文本，支持裸提示词自动映射到“提示词”。"""
        params_text = (workflow_text or "").strip()
        node_configs = cfg.get("node_configs", {})
        known_keys = set()
        for _, nc in node_configs.items():
            for pn, pi in nc.items():
                known_keys.add(pn)
                known_keys.update(pi.get("aliases", []))

        args = []
        if known_keys:
            param_pat = '|'.join(re.escape(k) for k in known_keys)
            regex = rf'(?<!\S)({param_pat})(?=\s*:)'
            matches = list(re.finditer(regex, params_text))
            prompt_parts = []
            last_end = 0
            for idx, m in enumerate(matches):
                ps = m.start()
                if ps > last_end:
                    pp = params_text[last_end:ps].strip()
                    if pp:
                        prompt_parts.append(pp)
                cp = params_text.find(':', ps)
                if cp == -1:
                    continue
                nps = len(params_text)
                if idx + 1 < len(matches):
                    nps = matches[idx + 1].start()
                val = params_text[cp + 1:nps].strip()
                args.append(f"{m.group(1)}:{val}")
                last_end = nps
            if last_end < len(params_text):
                pp = params_text[last_end:].strip()
                if pp:
                    prompt_parts.append(pp)
            prompt_text = ' '.join(prompt_parts).strip()
            if prompt_text:
                args.insert(0, f"提示词:{prompt_text}")
        elif params_text:
            args.append(f"提示词:{params_text}")
        return self.engine.parse_workflow_params(args, cfg)

    def _parse_llm_workflow_parameters(self, parameters: Any, cfg: dict) -> dict:
        """解析 LLM 工具参数，支持 JSON 对象或 参数:值 文本。"""
        if isinstance(parameters, dict):
            raw = parameters
        else:
            text = (parameters or "").strip()
            raw = None
            if text:
                try:
                    decoded = json.loads(text)
                    if isinstance(decoded, dict):
                        raw = decoded
                except Exception:
                    raw = None
            if raw is None:
                return self._parse_workflow_param_text(text, cfg)

        args = []
        for key, value in raw.items():
            if value is None:
                continue
            args.append(f"{key}:{value}")
        return self.engine.parse_workflow_params(args, cfg)

    # ========== 事件处理器 ==========

    # --- 文生图 ---
    @filter.custom_filter(ImgGenerateFilter)
    async def generate_image(self, event: AstrMessageEvent):
        eng = self.engine
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result("❌ 当前群聊不在白名单中！"))
            return
        if not eng._is_in_open_time():
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n当前未开放～开放时间：{eng._get_open_time_desc()}"))
            return
        if not eng._get_any_healthy_server():
            await self._send_with_auto_recall(event, event.plain_result("\n所有ComfyUI服务器均不可用！"))
            return

        full_msg = event.message_obj.message_str.strip()
        _, params = self._parse_command(full_msg, "aimg")
        if not params:
            await self.send_help(event)
            return
        try:
            params, selected_model = eng.parse_model_params(params)
            params, lora_list = eng.parse_lora_params(params)
        except ValueError as e:
            await self._send_with_auto_recall(event, event.plain_result(f"\n参数解析失败：{self._filter_server_urls(str(e))}"))
            return

        prompt_text = " ".join(params)
        res_m = re.search(r'宽(\d+),高(\d+)', prompt_text)
        batch_m = re.search(r'批量(\d+)', prompt_text)
        w, h = eng.default_width, eng.default_height
        bs = eng.txt2img_batch_size
        pure = prompt_text
        if res_m:
            try:
                iw, ih = int(res_m.group(1)), int(res_m.group(2))
                if not (eng.min_width <= iw <= eng.max_width):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n宽度{iw}非法！"))
                    return
                if not (eng.min_height <= ih <= eng.max_height):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n高度{ih}非法！"))
                    return
                w, h = iw, ih
                pure = re.sub(r'宽\d+,高\d+', '', pure).strip()
            except Exception as e:
                await self._send_with_auto_recall(event, event.plain_result(f"\n宽高解析失败：{e}"))
                return
        if batch_m:
            try:
                ib = int(batch_m.group(1))
                if not (1 <= ib <= eng.max_txt2img_batch):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n批量数{ib}非法！"))
                    return
                bs = ib
                pure = re.sub(r'批量\d+', '', pure).strip()
            except Exception:
                pass
        if not pure:
            await self._send_with_auto_recall(event, event.plain_result("\n提示词不能为空！"))
            return
        try:
            seed = random.randint(1, 18446744073709551615) if (eng.seed == "随机" or not eng.seed) else int(eng.seed)
        except (ValueError, TypeError):
            seed = random.randint(1, 2147483647)

        uid = str(event.get_sender_id())
        if not await eng._increment_user_task_count(uid):
            await self._send_with_auto_recall(event, event.plain_result(f"\n您当前任务数已达上限（{eng.max_concurrent_tasks_per_user}个）！"))
            return

        if eng.task_queue.full():
            await eng._decrement_user_task_count(uid)
            await self._send_with_auto_recall(event, event.plain_result(f"\n任务队列已满（{eng.max_task_queue}个）！"))
            return

        task_id = await eng.generate_task_id("txt2img")
        submitted = await eng.submit_task({
            "task_id": task_id,
            "prompt": pure, "current_seed": seed,
            "current_width": w, "current_height": h,
            "current_batch_size": bs, "lora_list": lora_list,
            "selected_model": selected_model, "user_id": uid,
            "is_ai_initiated": False,
            "callback": self._make_result_callback(event, "txt2img")
        })
        if not submitted:
            await eng._decrement_user_task_count(uid)
            await self._send_with_auto_recall(event, event.plain_result(f"\n任务队列已满（{eng.max_task_queue}个）！"))
            return

        mf = ""
        if selected_model:
            md = next((d for _, (fn, d) in eng.model_name_map.items() if fn == selected_model), "自定义模型")
            mf = f"\n使用模型：{md}"

        await self._send_with_auto_recall(event, event.plain_result(
            f"\n文生图任务已加入队列（任务ID：{task_id}，排队：{eng.task_queue.qsize()}个）\n"
            f"提示词：{self._truncate_prompt(pure)}\nSeed：{seed}\n"
            f"分辨率：{w}x{h}\n批量数：{bs}"
            + mf
            + (f"\n可用服务器：{'、'.join(s.name for s in eng.comfyui_servers if s.healthy)}"
               if any(s.healthy for s in eng.comfyui_servers) else "")
        ))

    # --- 图生图 ---
    @filter.custom_filter(Img2ImgFilter)
    async def handle_img2img(self, event: AstrMessageEvent):
        eng = self.engine
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result("❌ 当前群聊不在白名单中！"))
            return
        if not eng._is_in_open_time():
            await self._send_with_auto_recall(event, event.plain_result(f"\n当前未开放～"))
            return
        if not eng._get_any_healthy_server():
            await self._send_with_auto_recall(event, event.plain_result("\n所有ComfyUI服务器均不可用！"))
            return

        full_msg = event.message_obj.message_str.strip()
        _, params = self._parse_command(full_msg, "img2img")
        try:
            params, selected_model = eng.parse_model_params(params)
            params, lora_list = eng.parse_lora_params(params)
        except ValueError as e:
            await self._send_with_auto_recall(event, event.plain_result(f"\n参数解析失败：{self._filter_server_urls(str(e))}"))
            return

        prompt_text = " ".join(params)
        noise_m = re.search(r'噪声:([0-9.]+)', prompt_text)
        batch_m = re.search(r'批量(\d+)', prompt_text)
        denoise = float(noise_m.group(1)) if noise_m else eng.default_denoise
        bs = eng.img2img_batch_size
        pure = re.sub(r'噪声:[0-9.]+', '', prompt_text).strip()
        if batch_m:
            try:
                ib = int(batch_m.group(1))
                if not (1 <= ib <= eng.max_img2img_batch):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n批量数{ib}非法！"))
                    return
                bs = ib
                pure = re.sub(r'批量\d+', '', pure).strip()
            except Exception:
                pass
        if not pure:
            await self._send_with_auto_recall(event, event.plain_result("\n提示词不能为空！"))
            return

        msgs = event.get_messages()
        img_seg = next((m for m in msgs if isinstance(m, Image)), None)
        if not img_seg:
            reply = next((s for s in msgs if isinstance(s, Reply)), None)
            if reply and reply.chain:
                img_seg = next((s for s in reply.chain if isinstance(s, Image)), None)
        if not img_seg:
            await self._send_with_auto_recall(event, event.plain_result("\n请同时发送图片或引用图片消息！"))
            return

        try:
            img_path = await img_seg.convert_to_file_path()
            if not os.path.exists(img_path):
                raise Exception("图片下载失败")
            fs = os.path.getsize(img_path)
            if fs < 10240:
                raise Exception("图片文件过小")
        except Exception as e:
            await self._send_with_auto_recall(event, event.plain_result(f"\n图片下载失败：{str(e)[:200]}"))
            return

        if eng.enable_auto_save:
            saved = await eng.save_input_image_permanently(img_path, "img2img", str(event.get_sender_id()))
            if saved:
                img_path = saved

        try:
            seed = random.randint(1, 18446744073709551615) if (eng.seed == "随机" or not eng.seed) else int(eng.seed)
        except (ValueError, TypeError):
            seed = random.randint(1, 2147483647)

        uid = str(event.get_sender_id())
        if not await eng._increment_user_task_count(uid):
            await self._send_with_auto_recall(event, event.plain_result(f"\n并发任务数已达上限！"))
            return
        if eng.task_queue.full():
            await eng._decrement_user_task_count(uid)
            await self._send_with_auto_recall(event, event.plain_result(f"\n任务队列已满！"))
            return

        task_id = await eng.generate_task_id("img2img")
        submitted = await eng.submit_task({
            "task_id": task_id,
            "prompt": pure, "current_seed": seed,
            "current_width": eng.default_width, "current_height": eng.default_height,
            "current_batch_size": bs, "lora_list": lora_list,
            "selected_model": selected_model, "user_id": uid,
            "img_path": img_path, "denoise": denoise,
            "is_ai_initiated": False,
            "callback": self._make_result_callback(event, "img2img")
        })
        if not submitted:
            await eng._decrement_user_task_count(uid)
            await self._send_with_auto_recall(event, event.plain_result(f"\n任务队列已满！"))
            return

        await self._send_with_auto_recall(event, event.plain_result(
            f"\n图生图任务已加入队列（任务ID：{task_id}，排队：{eng.task_queue.qsize()}个）\n"
            f"提示词：{self._truncate_prompt(pure)}\nSeed：{seed}\n噪声：{denoise}"
        ))

    # --- Workflow ---
    @filter.custom_filter(WorkflowFilter)
    async def handle_workflow(self, event: AstrMessageEvent):
        eng = self.engine
        full_text = event.message_obj.message_str.strip()
        words = full_text.split()
        if not words:
            return
        prefix = words[0]
        if prefix not in eng.workflow_prefixes:
            await self._send_with_auto_recall(event, event.plain_result(f"未知前缀: {prefix}"))
            return
        if len(words) >= 2 and words[1].lower() == "help":
            await self._send_workflow_help(event, prefix)
            return

        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result("❌ 不在白名单中"))
            return
        if not eng._is_in_open_time():
            await self._send_with_auto_recall(event, event.plain_result(f"当前不在开放时间"))
            return
        if not eng._get_any_healthy_server():
            await self._send_with_auto_recall(event, event.plain_result("\n所有服务器不可用"))
            return

        wfn = eng.workflow_prefixes[prefix]
        wf_info = eng.workflows[wfn]
        cfg = wf_info["config"]
        wf_data = wf_info["workflow"]

        # 参数解析
        params_text = full_text[len(prefix):].strip()
        params = self._parse_workflow_param_text(params_text, cfg)
        missing = eng.validate_required_params(cfg, params)
        if missing:
            await self._send_with_auto_recall(event, event.plain_result(f"缺少必需参数：{'、'.join(missing)}"))
            return
        errs = eng.validate_param_values(cfg, params)
        if errs:
            await self._send_with_auto_recall(event, event.plain_result(f"参数错误：\n" + "\n".join(errs)))
            return

        # 图片下载
        image_paths, image_err = await self._collect_workflow_image_paths(event, wfn, cfg)
        if image_err:
            await self._send_with_auto_recall(event, event.plain_result(image_err))
            return

        # 构建 workflow
        final_wf = eng.build_workflow(wf_data, cfg, params, [])

        # 入队
        uid = str(event.get_sender_id())
        if not await eng._increment_user_task_count(uid):
            await self._send_with_auto_recall(event, event.plain_result(f"并发任务数已达上限"))
            return
        if eng.task_queue.full():
            await eng._decrement_user_task_count(uid)
            await self._send_with_auto_recall(event, event.plain_result("队列已满"))
            return

        task_id = await eng.generate_task_id(wfn)
        submitted = await eng.submit_task({
            "task_id": task_id,
            "prompt": final_wf,
            "workflow_name": wfn, "user_id": uid,
            "is_workflow": True, "image_paths": image_paths,
            "workflow_config": cfg,
            "is_ai_initiated": False,
            "callback": self._make_result_callback(event, "workflow")
        })
        if not submitted:
            await eng._decrement_user_task_count(uid)
            await self._send_with_auto_recall(event, event.plain_result("队列已满"))
            return
        await self._send_with_auto_recall(event, event.plain_result(
            f"Workflow「{cfg['name']}」已加入队列（任务ID：{task_id}，排队：{eng.task_queue.qsize()}个）"
        ))

    async def _send_workflow_help(self, event: AstrMessageEvent, prefix: str):
        """发送 workflow 帮助"""
        eng = self.engine
        try:
            wfn = eng.workflow_prefixes[prefix]
            wf_info = eng.workflows[wfn]
            cfg = wf_info["config"]
            if self.enable_help_image:
                success = await self._send_workflow_help_image(event, wfn, prefix, cfg)
                if not success:
                    await self._send_workflow_help_text(event, prefix, cfg)
            else:
                await self._send_workflow_help_text(event, prefix, cfg)
        except Exception as e:
            await self._send_with_auto_recall(event, event.plain_result(f"获取帮助失败：{str(e)}"))

    async def _send_workflow_help_image(self, event, wfn, prefix, cfg):
        try:
            wf_dir = self.engine.workflow_dir / wfn
            hip = wf_dir / "help.png"
            if os.path.exists(hip):
                await event.send(event.image_result(str(hip)))
                return True
            help_text = self._generate_workflow_help_text(prefix, cfg)
            title = cfg.get("name", "工作流帮助")
            img_data = self._create_help_image(help_text, title)
            if img_data:
                wf_dir.mkdir(parents=True, exist_ok=True)
                with open(hip, 'wb') as f:
                    f.write(img_data)
                await event.send(event.image_result(str(hip)))
                return True
            return False
        except Exception:
            return False

    async def _send_workflow_help_text(self, event, prefix, cfg):
        text = self._generate_workflow_help_text(prefix, cfg)
        await self._send_with_auto_recall(event, event.plain_result(text))

    def _generate_workflow_help_text(self, prefix, cfg):
        lines = []
        lines.append(f"🔧 {cfg.get('name', 'Unknown')} 详细帮助")
        lines.append("=" * 50)
        lines.append(f"调用前缀: {prefix}")
        lines.append(f"描述: {cfg.get('description', '暂无')}")
        lines.append("")
        lines.append("📝 使用格式:")
        lines.append(f"  {prefix} [参数名:值 ...]")
        if cfg.get("input_nodes"):
            lines.append("  + 图片（必需）")
        lines.append("")
        nc = cfg.get("node_configs", {})
        if nc:
            lines.append("⚙️ 参数详细说明:")
            lines.append("-" * 30)
            for _, nc2 in nc.items():
                for pn, pi in nc2.items():
                    lines.append(f"🔸 {pn}")
                    lines.append(f"   类型: {pi.get('type','未知')}")
                    lines.append(f"   描述: {pi.get('description','暂无')}")
                    lines.append(f"   必需: {'是' if pi.get('required') else '否'}")
                    lines.append(f"   默认值: {pi.get('default','无')}")
                    if pi.get("type") == "number":
                        mn = pi.get("min")
                        mx = pi.get("max")
                        if mn is not None: lines.append(f"   最小值: {mn}")
                        if mx is not None: lines.append(f"   最大值: {mx}")
                    if pi.get("type") == "select":
                        opts = pi.get("options", [])
                        if opts: lines.append(f"   可选值: {'、'.join(opts)}")
                    al = pi.get("aliases", [])
                    if al: lines.append(f"   别名: {'、'.join(al)}")
                    lines.append("")
        lines.append("💡 使用示例:")
        for ex in self._generate_workflow_examples(prefix, cfg):
            lines.append(f"  {ex}")
        lines.append("")
        lines.append("⚠️ 注意事项:")
        lines.append("  • 参数格式: 参数名:值")
        lines.append("  • 支持中英文参数名和别名")
        lines.append("  • 多个参数用空格分隔")
        return "\n".join(lines)

    def _generate_workflow_examples(self, prefix, cfg):
        examples = []
        nc = cfg.get("node_configs", {})
        common = {}
        for _, nc2 in nc.items():
            for pn, pi in nc2.items():
                if pi.get("default") is not None:
                    common[pn] = pi["default"]
        if common:
            parts = [prefix]
            samples = list(common.items())[:3]
            parts.extend(f"{k}:{v}" for k, v in samples)
            examples.append(" ".join(parts))
        examples.append(prefix)
        has_prompt = False
        for _, nc2 in nc.items():
            for pn in nc2:
                if "提示" in pn or "prompt" in pn.lower():
                    examples.append(f"{prefix} {pn}:可爱女孩")
                    has_prompt = True
                    break
            if has_prompt:
                break
        return examples

    def _create_help_image(self, text: str, title: str = "工作流帮助") -> Optional[bytes]:
        try:
            from PIL import Image as PILImage, ImageDraw, ImageFont
            width = 1200
            pad = 50
            lh = 35
            fst = 52
            fsn = 32
            fss = 24
            bh = 120
            bth = 80
            try:
                fp = os.path.join(self.plugin_dir, "1.ttf")
                tf = ImageFont.truetype(fp, fst) if os.path.exists(fp) else ImageFont.load_default()
                nf = ImageFont.truetype(fp, fsn) if os.path.exists(fp) else ImageFont.load_default()
                sf = ImageFont.truetype(fp, fss) if os.path.exists(fp) else ImageFont.load_default()
            except Exception:
                tf = nf = sf = ImageFont.load_default()
            lines = text.split('\n')
            ch = len(lines) * lh + 50
            height = max(800, bh + ch + bth)
            img = PILImage.new('RGB', (width, height), '#ffffff')
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, width, 80], fill='#4a90e2')
            tt = f"🎨 ComfyUI AI绘画 - {title}"
            tb = draw.textbbox((0, 0), tt, font=tf)
            draw.text(((width - (tb[2] - tb[0])) // 2, 25), tt, fill='white', font=tf)
            yo = bh
            for line in lines:
                if line.startswith('🔧'):
                    draw.text((pad, yo), line, fill='#333', font=nf)
                elif line.startswith('='):
                    draw.text((pad, yo), line, fill='#34495e', font=nf)
                elif line.startswith(('📝', '⚙️', '💡', '⚠️')):
                    draw.text((pad, yo), line, fill='#2980b9' if line.startswith('📝') else '#27ae60', font=nf)
                elif line.startswith('🔸'):
                    draw.text((pad, yo), line, fill='#8e44ad', font=sf)
                elif line.startswith(('   ', '  ')):
                    draw.text((pad, yo), line, fill='#34495e' if line.startswith('   ') else '#16a085', font=sf)
                else:
                    draw.text((pad, yo), line, fill='#666', font=sf)
                yo += lh
            draw.rectangle([0, height - 80, width, height], fill='#f5f5f5')
            draw.text((50, height - 60), f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", fill='#999', font=sf)
            draw.text((50, height - 35), "https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax", fill='#666', font=sf)
            try:
                ap = os.path.join(self.plugin_dir, "Astrbot.png")
                if os.path.exists(ap):
                    ai = PILImage.open(ap)
                    ath = 60
                    atw = int(ath * ai.width / ai.height)
                    ar = ai.resize((atw, ath), PILImage.Resampling.LANCZOS)
                    img.paste(ar, (width - atw - 10, height - ath - 10), ar if ar.mode == 'RGBA' else None)
            except Exception:
                pass
            buf = io.BytesIO()
            img.save(buf, format='PNG', quality=95)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"创建工作流帮助图片失败: {e}")
            return None

    # --- 帮助 ---
    @filter.custom_filter(HelpFilter)
    async def send_help(self, event: AstrMessageEvent):
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result("❌ 不在白名单中"))
            return
        if self.enable_help_image:
            await self._send_help_as_image(event)
        else:
            await self._send_help_as_text(event)

    # --- 输出压缩包 ---
    @filter.custom_filter(OutputZipFilter)
    async def handle_output_zip(self, event: AstrMessageEvent):
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result("❌ 不在白名单中"))
            return
        uid = str(event.get_sender_id())
        can, cur = await self.engine.check_download_limit(uid)
        if not can:
            await self._send_with_auto_recall(event, event.plain_result(f"\n今日下载次数已达上限（{self.daily_download_limit}次）！"))
            return
        files = await self.engine.get_today_images(uid)
        if not files:
            await self._send_with_auto_recall(event, event.plain_result("\n今天还没有生成的图片！"))
            return
        zip_path = await self.engine.create_zip(files, uid)
        if not zip_path or not os.path.exists(zip_path):
            await self._send_with_auto_recall(event, event.plain_result("\n压缩包创建失败！"))
            return
        await self._increment_download_count(uid)
        await self._upload_zip_file(event, zip_path)
        self._schedule_cleanup(zip_path)

    async def _increment_download_count(self, user_id: str):
        await self.engine.increment_download_count(user_id)

    def _schedule_cleanup(self, path: str, delay: int = 10):
        async def _cl():
            await asyncio.sleep(delay)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        asyncio.create_task(_cl())

    async def _upload_zip_file(self, event: AstrMessageEvent, zip_path: str) -> bool:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                return False
            gid = event.get_group_id()
            uid = event.get_sender_id()
            fn = os.path.basename(zip_path)
            if gid:
                await event.bot.upload_group_file(group_id=gid, file=zip_path, name=fn)
            else:
                await event.bot.upload_private_file(user_id=int(uid), file=zip_path, name=fn)
            return True
        except Exception as e:
            logger.error(f"上传压缩包失败: {e}")
            return False

    # --- 小番茄解密 ---
    @filter.custom_filter(TomatoDecryptFilter)
    async def handle_tomato_decrypt(self, event: AstrMessageEvent):
        html_path = os.path.join(self.plugin_dir, "解密.html")
        if not os.path.exists(html_path):
            await self._send_with_auto_recall(event, event.plain_result("解密工具未找到"))
            return
        await self._upload_html_file(event, html_path)

    async def _upload_html_file(self, event, html_path: str) -> bool:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                return False
            gid = event.get_group_id()
            uid = event.get_sender_id()
            fn = os.path.basename(html_path)
            if gid:
                await event.bot.upload_group_file(group_id=gid, file=html_path, name=fn)
            else:
                await event.bot.upload_private_file(user_id=int(uid), file=html_path, name=fn)
            return True
        except Exception as e:
            logger.error(f"上传HTML文件失败: {e}")
            return False

    # --- teeee（转发消息分析） ---
    @filter.custom_filter(TeeeFilter)
    async def handle_teee(self, event: AstrMessageEvent):
        """处理teeee指令，获取并分析转发消息内容（与原版 main1.py 一致）"""
        try:
            messages = event.get_messages()
            output_lines = ["📋 转发消息元信息分析：\n"]

            # 获取基本信息
            user_id = getattr(event, 'user_id', getattr(event.message_obj, 'sender_id', getattr(event.message_obj, 'user_id', 'Unknown')))
            group_id = getattr(event, 'group_id', getattr(event.message_obj, 'group_id', None))
            message_id = getattr(event.message_obj, 'message_id', getattr(event.message_obj, 'message_seq', 'Unknown'))
            time_raw = getattr(event.message_obj, 'time', getattr(event.message_obj, 'timestamp', 'Unknown'))

            for msg in messages:
                if hasattr(msg, 'sender_id') and msg.sender_id != 'Unknown':
                    user_id = msg.sender_id
                if hasattr(msg, 'time') and msg.time != 'Unknown':
                    time_raw = msg.time
                if hasattr(msg, 'qq') and msg.qq != 'Unknown':
                    user_id = msg.qq

            if time_raw != 'Unknown' and isinstance(time_raw, (int, float)):
                import time as time_module
                time_str = time_module.strftime('%Y-%m-%d %H:%M:%S', time_module.localtime(time_raw))
            else:
                time_str = str(time_raw) if time_raw != 'Unknown' else 'Unknown'

            output_lines.append(f"📤 发送者ID: {user_id}")
            if group_id:
                output_lines.append(f"👥 群聊ID: {group_id}")
            output_lines.append(f"🆔 消息ID: {message_id}")
            output_lines.append(f"⏰ 时间戳: {time_str}")
            output_lines.append("")

            output_lines.append(f"🔍 消息组件分析 (共{len(messages)}个组件)：")
            forward_content_found = False

            for i, msg in enumerate(messages, 1):
                output_lines.append(f"\n--- 组件 {i} ---")
                output_lines.append(f"📦 类型: {type(msg).__name__}")
                output_lines.append(f"🏷️  模块名: {msg.__class__.__module__}")

                attributes = {}
                for attr_name in dir(msg):
                    if not attr_name.startswith('_'):
                        try:
                            attr_value = getattr(msg, attr_name)
                            if not callable(attr_value):
                                attributes[attr_name] = attr_value
                        except Exception:
                            continue

                for attr_name, attr_value in attributes.items():
                    if isinstance(attr_value, str) and len(attr_value) > 100:
                        attr_value = attr_value[:100] + "..."
                    elif isinstance(attr_value, (list, tuple)) and len(attr_value) > 5:
                        attr_value = f"{type(attr_value).__name__}(长度:{len(attr_value)})"
                    output_lines.append(f"  {attr_name}: {attr_value}")

                if hasattr(msg, 'type') and hasattr(msg, 'chain') and msg.chain:
                    output_lines.append(f"  📋 转发链长度: {len(msg.chain)}")
                    for j, chain_msg in enumerate(msg.chain, 1):
                        output_lines.append(f"    链节点{j}: {type(chain_msg).__name__}")
                        if hasattr(chain_msg, 'type') and hasattr(chain_msg.type, 'value') and chain_msg.type.value == 'Forward':
                            forward_content_found = True
                            forward_id = getattr(chain_msg, 'id', None)
                            if forward_id:
                                output_lines.append(f"      📤 转发消息ID: {forward_id}")
                                try:
                                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                                    if isinstance(event, AiocqhttpMessageEvent):
                                        fr = await event.bot.api.call_action("get_forward_msg", message_id=forward_id)
                                        if fr:
                                            fms = None
                                            if "messages" in fr: fms = fr["messages"]
                                            elif isinstance(fr.get("data"), dict) and "messages" in fr["data"]: fms = fr["data"]["messages"]
                                            elif isinstance(fr.get("data"), list): fms = fr["data"]
                                            elif isinstance(fr, list): fms = fr
                                            if fms:
                                                output_lines.append(f"      📨 转发消息数量: {len(fms)}")
                                                output_lines.append("\n🎯 转发消息内容详情：")
                                                output_lines.append("=" * 60)
                                                for k, fmsg in enumerate(fms, 1):
                                                    output_lines.append(f"\n【转发消息 {k}】")
                                                    sn, sid, mt = "Unknown", "Unknown", "Unknown"
                                                    if isinstance(fmsg, dict):
                                                        si = fmsg.get('sender', {})
                                                        if isinstance(si, dict):
                                                            sn = si.get('nickname', si.get('card', si.get('name', 'Unknown')))
                                                            sid = si.get('user_id', si.get('uid', 'Unknown'))
                                                        mt = fmsg.get('time', fmsg.get('timestamp', 'Unknown'))
                                                    output_lines.append(f"👤 发送者: {sn} ({sid})")
                                                    if mt != "Unknown" and isinstance(mt, (int, float)):
                                                        import time as tm
                                                        output_lines.append(f"⏰ 时间: {tm.strftime('%Y-%m-%d %H:%M:%S', tm.localtime(mt))}")
                                                    else:
                                                        output_lines.append(f"⏰ 时间: {mt}")
                                                    mc = ""
                                                    if isinstance(fmsg, dict):
                                                        mc = fmsg.get('message', fmsg.get('content', ''))
                                                    if mc:
                                                        output_lines.append("📝 内容:")
                                                        if isinstance(mc, str):
                                                            if len(mc) <= 200:
                                                                output_lines.append(f"  🔧 CQ: {mc}")
                                                            else:
                                                                output_lines.append(f"  🔧 CQ: {mc[:200]}...[截断]")
                                                        elif isinstance(mc, list):
                                                            output_lines.append(f"  📋 列表, {len(mc)}项")
                                                            for seg in mc:
                                                                if isinstance(seg, dict):
                                                                    st = seg.get('type', '?')
                                                                    sd = seg.get('data', {})
                                                                    if st == 'text':
                                                                        txt = sd.get('text', '')[:200]
                                                                        output_lines.append(f"    📄 {txt}")
                                                                    else:
                                                                        output_lines.append(f"    📎 {st}")
                                                    if k < len(fms):
                                                        output_lines.append("-" * 40)
                                            else:
                                                output_lines.append(f"      ❌ 无法提取转发消息")
                                except Exception as e:
                                    output_lines.append(f"      ⚠️ 获取转发内容失败: {str(e)[:100]}")

            if not forward_content_found:
                output_lines.append("\n💡 提示：没有检测到转发消息（Forward类型）组件")
                output_lines.append("💡 请回复包含转发消息的聊天记录")

            output_text = "\n".join(output_lines)
            # teeee 是纯文本，走 _send_with_auto_recall 支持撤回
            await self._send_with_auto_recall(event, event.plain_result(output_text[:5000]))
        except Exception as e:
            logger.error(f"teeee处理失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(f"teeee处理失败: {str(e)[:200]}"))

    # --- 添加服务器 ---
    @filter.custom_filter(AddServerFilter)
    async def handle_add_server(self, event: AstrMessageEvent):
        text = event.message_obj.message_str.strip()
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await self._send_with_auto_recall(event, event.plain_result("格式：添加服务器 URL,名称"))
            return
        server_str = parts[1] + parts[2] if len(parts) == 3 else parts[1]
        idx = len(self.engine.comfyui_servers) + len(self.engine.temp_servers)
        if "," not in server_str:
            await self._send_with_auto_recall(event, event.plain_result("格式错误，需要 URL,名称"))
            return
        url, name = server_str.split(",", 1)
        url, name = url.strip(), name.strip()
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        srv = WorkflowEngine.ServerState(url, name, idx)
        self.engine.temp_servers.append(srv)
        self.engine.comfyui_servers.append(srv)
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        if isinstance(event, AiocqhttpMessageEvent):
            await event.send(event.plain_result(f"✅ 已添加临时服务器：{name} ({url})"))
        else:
            await self._send_with_auto_recall(event, event.plain_result(f"✅ 已添加临时服务器：{name} ({url})"))

    # ========== LLM 工具 ==========

    @llm_tool(name="comfyui_txt2img")
    async def comfyui_txt2img(self, event: AstrMessageEvent, prompt: str,
                              img_width: int = None, img_height: int = None):
        """AI painting based on the prompts entered by the user.
        Args:
            prompt(string): A prompt for text to image, if the user inputs Chinese prompts, they need to be translated into English.
            img_width(number): The width of the generated image. Optional.
            img_height(number): The height of the generated image. Optional.

        Returns:
            string: Submission status. On success, includes a task ID for later reference.
        """
        eng = self.engine
        if not self._check_group_whitelist(event):
            return "❌ 当前群聊不在白名单中！"
        if not eng._is_in_open_time():
            return f"当前未开放～开放时间：{eng._get_open_time_desc()}"
        if not prompt or prompt.strip() == "":
            return "提示词不能为空！"
        prompt = prompt.replace(" ", "_")
        w = img_width if img_width and eng.min_width <= img_width <= eng.max_width else eng.default_width
        h = img_height if img_height and eng.min_height <= img_height <= eng.max_height else eng.default_height
        try:
            seed = random.randint(1, 18446744073709551615) if (eng.seed == "随机" or not eng.seed) else int(eng.seed)
        except (ValueError, TypeError):
            seed = random.randint(1, 2147483647)
        if not any(s.healthy for s in eng.comfyui_servers):
            return "当前没有可用的ComfyUI服务器。"
        uid = str(event.get_sender_id())
        if not await eng._increment_user_task_count(uid):
            return f"您的并发任务数已达上限（{eng.max_concurrent_tasks_per_user}个）"
        if eng.task_queue.full():
            await eng._decrement_user_task_count(uid)
            return f"任务队列已满（{eng.max_task_queue}个上限）"
        task_id = await eng.generate_task_id("txt2img")
        submitted = await eng.submit_task({
            "task_id": task_id,
            "prompt": prompt, "current_seed": seed,
            "current_width": w, "current_height": h,
            "current_batch_size": 1, "lora_list": [],
            "selected_model": None, "user_id": uid,
            "is_ai_initiated": True,
            "callback": self._make_result_callback(event, "llm_tool")
        })
        if not submitted:
            await eng._decrement_user_task_count(uid)
            return f"任务队列已满（{eng.max_task_queue}个上限）"
        servers = [s.name for s in eng.comfyui_servers if s.healthy]
        sf = f"\n可用服务器：{'、'.join(servers)}" if servers else "\n当前无可用服务器，任务将在服务器恢复后处理"
        return f"文生图任务已加入队列（任务ID：{task_id}，排队：{eng.task_queue.qsize()}个）\n提示词：{prompt}\nSeed：{seed}\n分辨率：{w}x{h}{sf}"

    @llm_tool(name="comfyui_queue_status")
    async def comfyui_queue_status(self, event: AstrMessageEvent):
        """Get current ComfyUI plugin queue and server status."""
        eng = self.engine
        if not self._check_group_whitelist(event):
            return "❌ 当前群聊不在白名单中！"

        queued = eng.queued_task_count()
        total_user_tasks = sum(eng.user_task_counts.values())
        current_user_id = str(event.get_sender_id())
        current_user_tasks = eng.user_task_counts.get(current_user_id, 0)
        healthy_count = sum(1 for srv in eng.comfyui_servers if srv.healthy)
        busy_count = sum(1 for srv in eng.comfyui_servers if srv.busy)

        lines = [
            "ComfyUI队列状态：",
            f"- 当前排队任务：{queued}",
            f"- 队列容量：{eng.max_task_queue}",
            f"- 当前用户并发任务：{current_user_tasks}/{eng.max_concurrent_tasks_per_user}",
            f"- 全部用户进行中任务：{total_user_tasks}",
            f"- 活跃用户数：{len(eng.user_task_counts)}",
            f"- 服务器：{healthy_count}/{len(eng.comfyui_servers)} 可用，{busy_count} 忙碌",
            f"- 当前开放状态：{'开放' if eng._is_in_open_time() else '未开放'}",
        ]

        if eng.comfyui_servers:
            lines.append("服务器详情：")
            for srv in eng.comfyui_servers:
                state = "可用" if srv.healthy else "不可用"
                busy = "忙碌" if srv.busy else "空闲"
                directed_queued = eng.server_queued_task_count(srv)
                retry_after = ""
                if srv.retry_after:
                    retry_after = f"，重试时间：{srv.retry_after.strftime('%Y-%m-%d %H:%M:%S')}"
                lines.append(
                    f"- {srv.name}：{state}，{busy}，定向排队：{directed_queued}，"
                    f"失败次数：{srv.failure_count}{retry_after}"
                )
        else:
            lines.append("服务器详情：暂无配置")

        return "\n".join(lines)

    @llm_tool(name="comfyui_workflow_list")
    async def comfyui_workflow_list(self, event: AstrMessageEvent, workflow: str = ""):
        """Get available ComfyUI workflows and their parameters.
        Args:
            workflow(string): Optional workflow id, prefix, or name. Leave empty to list all workflows.
        """
        if not self._check_group_whitelist(event):
            return "❌ 当前群聊不在白名单中！"
        return self._format_workflow_list_for_ai(workflow)

    @llm_tool(name="comfyui_workflow_run")
    async def comfyui_workflow_run(self, event: AstrMessageEvent, workflow: str,
                                   parameters: str = "", input_images: list = None,
                                   server_name: str = ""):
        """Run a selected ComfyUI workflow with parameters.
        Args:
            workflow(string): Workflow id, prefix, or name from comfyui_workflow_list.
            parameters(string): Prefer a JSON object string, e.g. "{\"提示词\":\"cat with blue eyes\", \"宽\":1024}". Text like "提示词:cat 宽:1024" is accepted only for compatibility. Use parameter names or aliases shown by comfyui_workflow_list.
            input_images(list): Optional image input array for workflows that need images. Use a JSON array, e.g. ["https://example.com/a.png", "/AstrBot/data/temp/a.png"]. Each item can be an http/https URL or a local path. Local paths support AstrBot temp directory files and non-sandbox workspace files. AstrBot temp directory paths usually contain data/temp; files retrieved from the sandbox with astrbot_download_file are automatically placed in the temp directory. If omitted, images are read from the current message or replied message context.
            server_name(string): Optional exact ComfyUI server name. When provided, the workflow waits for and runs only on that server. Leave empty to use the normal shared queue.

        Returns:
            string: Submission status. On success, includes a task ID for later reference.
        """
        eng = self.engine
        if not self._check_group_whitelist(event):
            return "❌ 当前群聊不在白名单中！"
        if not eng._is_in_open_time():
            return f"当前未开放～开放时间：{eng._get_open_time_desc()}"

        selected_server = None
        requested_server_name = str(server_name or "").strip()
        if requested_server_name:
            selected_server = next(
                (srv for srv in eng.comfyui_servers
                 if srv.name.casefold() == requested_server_name.casefold()),
                None
            )
            if selected_server is None:
                configured_names = "、".join(srv.name for srv in eng.comfyui_servers) or "暂无"
                return f"未找到服务器「{requested_server_name}」。可选服务器：{configured_names}"
            if (not selected_server.healthy
                    or (selected_server.retry_after
                        and datetime.now() < selected_server.retry_after)):
                return f"服务器「{selected_server.name}」当前不可用，请稍后重试或不指定 server_name。"
        elif not eng._get_any_healthy_server():
            return "当前没有可用的ComfyUI服务器。"

        wfn, wf_info, err = self._resolve_workflow(workflow)
        if err:
            return err
        cfg = wf_info["config"]
        wf_data = wf_info["workflow"]

        try:
            params = self._parse_llm_workflow_parameters(parameters, cfg)
        except Exception as e:
            return f"参数解析失败：{self._filter_server_urls(str(e))}"

        missing = eng.validate_required_params(cfg, params)
        if missing:
            return f"缺少必需参数：{'、'.join(missing)}\n可先调用 comfyui_workflow_list 查看参数。"
        errs = eng.validate_param_values(cfg, params)
        if errs:
            return "参数错误：\n" + "\n".join(errs)

        image_paths, image_err = await self._collect_workflow_image_paths(event, wfn, cfg, input_images)
        if image_err:
            return image_err

        final_wf = eng.build_workflow(wf_data, cfg, params, [])

        # 后处理：遍历 workflow 所有节点，替换 seed=-1 为随机种子（ComfyUI 要求 seed ≥ 0）
        for node_id, node_data in final_wf.items():
            if isinstance(node_data, dict) and "inputs" in node_data:
                seed_val = node_data["inputs"].get("seed")
                if seed_val is not None:
                    try:
                        if int(seed_val) == -1:
                            node_data["inputs"]["seed"] = random.randint(1, 18446744073709551615)
                    except (ValueError, TypeError):
                        pass

        uid = str(event.get_sender_id())
        if not await eng._increment_user_task_count(uid):
            return f"您的并发任务数已达上限（{eng.max_concurrent_tasks_per_user}个）"
        if eng.queued_task_count() >= eng.max_task_queue:
            await eng._decrement_user_task_count(uid)
            return f"任务队列已满（{eng.max_task_queue}个上限）"

        task_id = await eng.generate_task_id(wfn)
        submitted = await eng.submit_task({
            "task_id": task_id,
            "prompt": final_wf,
            "workflow_name": wfn,
            "user_id": uid,
            "is_workflow": True,
            "image_paths": image_paths,
            "workflow_config": cfg,
            "is_ai_initiated": True,
            "callback": self._make_result_callback(event, "workflow")
        }, server=selected_server)
        if not submitted:
            await eng._decrement_user_task_count(uid)
            return f"任务队列已满（{eng.max_task_queue}个上限）"
        title = cfg.get("name", wfn)
        if selected_server:
            return (
                f"Workflow「{title}」已加入服务器「{selected_server.name}」的定向队列"
                f"（任务ID：{task_id}，该服务器定向排队："
                f"{eng.server_queued_task_count(selected_server)}个）"
            )
        servers = [s.name for s in eng.comfyui_servers if s.healthy]
        sf = f"\n可用服务器：{'、'.join(servers)}" if servers else ""
        return f"Workflow「{title}」已加入队列（任务ID：{task_id}，排队：{eng.task_queue.qsize()}个）{sf}"

    # ========== 清理 ==========

    async def cleanup(self) -> None:
        """资源清理"""
        eng = self.engine
        try:
            if hasattr(eng, 'server_monitor_task') and eng.server_monitor_task and not eng.server_monitor_task.done():
                eng.server_monitor_task.cancel()
                try:
                    await eng.server_monitor_task
                except asyncio.CancelledError:
                    pass
            if hasattr(eng, 'history_cleanup_task') and eng.history_cleanup_task and not eng.history_cleanup_task.done():
                eng.history_cleanup_task.cancel()
                try:
                    await eng.history_cleanup_task
                except asyncio.CancelledError:
                    pass
            for srv in eng.comfyui_servers:
                if srv.worker and not srv.worker.done():
                    srv.worker.cancel()
                    try:
                        await srv.worker
                    except asyncio.CancelledError:
                        pass
                    srv.worker = None
            if self.gui_server:
                self.gui_server.stop()
            await self._stop_help_server()
            logger.info("清理完成")
        except Exception as e:
            logger.error(f"清理时出错: {e}")

    async def terminate(self) -> None:
        """AstrBot 热重载/卸载生命周期入口。"""
        await self.cleanup()

    # ========== 消息发送工具（文件上传） ==========

    async def _send_video(self, event: AstrMessageEvent, video_path: str, filename: str, idx: int):
        """发送视频到 QQ"""
        try:
            fs = os.path.getsize(video_path)
            fsmb = fs / (1024 * 1024)
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if fsmb > self.engine.max_upload_size:
                if isinstance(event, AiocqhttpMessageEvent):
                    gid = event.get_group_id()
                    uid = event.get_sender_id()
                    if gid:
                        await event.bot.upload_group_file(group_id=gid, file=video_path, name=filename)
                    else:
                        await event.bot.upload_private_file(user_id=int(uid), file=video_path, name=filename)
            else:
                await event.send(event.chain_result([Video.fromFileSystem(video_path)]))
        except Exception as e:
            logger.error(f"视频{idx}发送失败: {e}")

    async def _upload_audio_file(self, event: AstrMessageEvent, audio_path: str, filename: str, duration: Optional[float] = None) -> bool:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                return False
            client = event.bot
            gid = event.get_group_id()
            uid = event.get_sender_id()
            if self.enable_audio_to_voice and duration is not None and duration <= 30:
                wav = audio_path.rsplit('.', 1)[0] + '.wav'
                if await self._convert_to_wav(audio_path, wav):
                    try:
                        msg = Record(file=wav, url=wav)
                        if gid:
                            await client.send_group_msg(group_id=int(gid), message=msg)
                        else:
                            await client.send_private_msg(user_id=int(uid), message=msg)
                        self._schedule_cleanup(wav)
                        return True
                    except Exception:
                        pass
            if gid:
                await client.upload_group_file(group_id=gid, file=audio_path, name=filename)
            else:
                await client.upload_private_file(user_id=int(uid), file=audio_path, name=filename)
            return True
        except Exception as e:
            logger.error(f"上传音频失败: {e}")
            return False

    async def _convert_to_wav(self, input_path: str, output_path: str) -> bool:
        try:
            import subprocess
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: subprocess.run(
                ['ffmpeg', '-y', '-i', input_path, '-acodec', 'pcm_s16le', '-ar', '24000', '-ac', '1', output_path],
                capture_output=True, text=True, timeout=30
            ))
            return result.returncode == 0
        except Exception:
            return False

    async def _upload_file(self, event: AstrMessageEvent, file_path: str, filename: str) -> bool:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                return False
            gid = event.get_group_id()
            uid = event.get_sender_id()
            if gid:
                await event.bot.upload_group_file(group_id=gid, file=file_path, name=filename)
            else:
                await event.bot.upload_private_file(user_id=int(uid), file=file_path, name=filename)
            return True
        except Exception:
            return False

    async def _upload_3d_model_file(self, event: AstrMessageEvent, model_path: str, filename: str) -> bool:
        """以文件形式上传 3D 模型。"""
        return await self._upload_file(event, model_path, filename)

    # ========== 伪造转发 ==========

    async def send_fake_forward_message(self, event: AstrMessageEvent, merged_chain: list, image_count: int):
        """发送伪装转发消息。仅 QQ(aiocqhttp) 平台支持，其他平台直接走通用发送。"""
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        is_qq = isinstance(event, AiocqhttpMessageEvent)
        if not is_qq or not self.enable_fake_forward or image_count < self.fake_forward_threshold:
            await event.send(event.chain_result(merged_chain))
            return
        try:
            fake_qq = self.fake_forward_qq
            use_default = False
            if not fake_qq or fake_qq == "":
                use_default = True
                fake_qq = ""
            elif fake_qq == "0":
                fake_qq = str(event.get_sender_id())
            elif fake_qq == "1":
                fake_qq = str(getattr(event.message_obj, 'self_id', '123456'))

            if use_default:
                nickname = "Astrbot"
            else:
                nickname = await self._get_qq_nickname(event, fake_qq)

            # 只保留 Plain 和 Image 组件
            node_content = [c for c in merged_chain if isinstance(c, (Plain, Image))]

            if use_default:
                bot_qq = str(getattr(event.message_obj, 'self_id', '123456'))
                node = Node(uin=int(bot_qq), name=nickname, content=node_content)
            else:
                node = Node(uin=int(fake_qq) if fake_qq.isdigit() else 123456,
                            name=nickname, content=node_content)

            await event.send(event.chain_result([Nodes(nodes=[node])]))
            logger.info(f"已使用伪装转发消息发送，QQ号: {fake_qq}, 昵称: {nickname}, 图片数量: {image_count}")
        except Exception as e:
            logger.error(f"发送伪装转发消息失败，使用普通发送方式: {e}")
            await event.send(event.chain_result(merged_chain))

    async def _get_qq_nickname(self, event: AstrMessageEvent, qq_number: str) -> str:
        """获取QQ昵称（通过 bot API，无需外部接口）"""
        if not qq_number or qq_number == "0" or qq_number == "":
            return "Astrbot"
        if qq_number.isdigit():
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    info = await event.bot.get_stranger_info(user_id=int(qq_number))
                    if isinstance(info, dict):
                        name = info.get("nickname") or info.get("name")
                        if name:
                            return str(name)
            except Exception:
                pass
        return f"用户{qq_number}"
