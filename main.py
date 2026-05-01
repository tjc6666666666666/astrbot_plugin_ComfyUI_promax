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
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import CustomFilter
from astrbot.api.message_components import Image, Node, Nodes, Plain, Record, Reply, Video
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import llm_tool
from aiohttp import web

import sys
import os
# AstrBot 通过 __import__ 导入插件时，当前目录不一定在 sys.path 中
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)
from workflow_engine import WorkflowEngine, WorkflowResult
from gui_server import GuiServer

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

        # 1. 创建引擎（构造函数是同步的，内部用 create_task 启动异步组件）
        self.engine = WorkflowEngine(config=config, plugin_dir=self.plugin_dir)

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
        self.data_dir = StarTools.get_data_dir()
        self.data_dir.mkdir(exist_ok=True)

        # 4. 设置模块级引擎引用（供 WorkflowFilter 直接读取 workflow_prefixes）
        global _workflow_engine_ref
        _workflow_engine_ref = self.engine

        # 5. GUI 服务器
        self.gui_server: Optional[GuiServer] = None
        self._init_gui(config)

        # 激活 LLM 工具
        self.context.activate_llm_tool("comfyui_txt2img")

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

    def _make_result_callback(self, event: AstrMessageEvent, task_type: str = "txt2img",
                               metadata: dict = None):
        """创建任务完成后的回调函数，负责将 WorkflowResult 发回给用户"""
        async def _send_result(result: WorkflowResult):
            try:
                if not result.success:
                    await self._send_with_auto_recall(
                        event, event.plain_result(f"\n❌ 生成失败：{result.error or '未知错误'}")
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
                    merged_chain.append(Plain(f"\n\n第{idx}/{len(result.images) + len(result.videos) + len(result.audios) + len(result.models_3d)}张："))
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

                # 添加3D模型
                for idx, minfo in enumerate(result.models_3d,
                                            len(result.images) + len(result.videos) + len(result.audios) + 1):
                    merged_chain.append(Plain(f"\n\n第{idx}个3D模型：正在上传..."))
                    try:
                        mp = await self._download_to_temp(minfo["url"], minfo["filename"])
                        if mp:
                            ok = await self._upload_3d_model_file(event, mp, minfo["filename"])
                            merged_chain.append(Plain(f"\n{'✅' if ok else '❌'} 3D模型{idx}上传{'成功' if ok else '失败'}"))
                    except Exception as e:
                        merged_chain.append(Plain(f"\n❌ 3D模型{idx}上传失败: {str(e)}"))

                # 发送（优先伪造转发）
                await self.send_fake_forward_message(event, merged_chain, len(result.images))

            except Exception as e:
                logger.error(f"发送结果失败: {e}")
                try:
                    await self._send_with_auto_recall(event, event.plain_result(f"\n发送结果时出错：{str(e)[:200]}"))
                except Exception:
                    pass

        return _send_result

    async def _download_to_temp(self, url: str, filename: str) -> Optional[str]:
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
            self._schedule_cleanup(str(path))
            return str(path)
        except Exception as e:
            logger.warning(f"下载文件失败: {e}")
            return None

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
        """发送消息并根据配置自动撤回（仅撤回文本消息，与原版一致）"""
        if not self.enable_auto_recall:
            await event.send(message_content)
            return None

        has_non_text = False
        if hasattr(message_content, '__iter__') and not isinstance(message_content, str):
            try:
                for component in message_content:
                    if hasattr(component, '__class__'):
                        class_name = component.__class__.__name__
                        if 'Image' in class_name or 'File' in class_name:
                            has_non_text = True
                            break
            except Exception:
                has_non_text = True

        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if isinstance(event, AiocqhttpMessageEvent) and not has_non_text:
                client = event.bot
                group_id = event.get_group_id() if event.get_group_id() else None
                sender_id = event.get_sender_id()
                message_to_send = self._convert_to_cq_code(message_content)
                if not message_to_send or not message_to_send.strip():
                    await event.send(message_content)
                    return None
                if group_id:
                    result = await client.send_group_msg(group_id=int(group_id), message=message_to_send)
                else:
                    result = await client.send_private_msg(user_id=int(sender_id), message=message_to_send)
                asyncio.create_task(self._delayed_recall(event, result))
                return result
            else:
                await event.send(message_content)
                return None
        except Exception as e:
            await event.send(message_content)
            return None

    def _convert_to_cq_code(self, content: Any) -> str:
        """将消息内容转换为 CQ 码格式（与原版一致，找到即返回）"""
        if isinstance(content, str):
            return content.strip()
        # 方法1: 检查 message 属性/方法
        if hasattr(content, 'message'):
            attr = getattr(content, 'message')
            if callable(attr):
                try:
                    msg = attr()
                    text = self._extract_text_from_content(msg)
                    if text:
                        return text.strip()
                except Exception:
                    pass
            else:
                text = self._extract_text_from_content(attr)
                if text:
                    return text.strip()
        # 方法2: 检查 chain 属性
        if hasattr(content, 'chain'):
            text = self._extract_text_from_content(content.chain)
            if text:
                return text.strip()
        # 方法3: 可迭代对象
        if hasattr(content, '__iter__') and not isinstance(content, str):
            text = self._extract_text_from_content(content)
            if text:
                return text.strip()
        # 方法4: 直接转字符串
        try:
            return str(content).strip()
        except Exception:
            return ""

    @staticmethod
    def _extract_text_from_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if hasattr(content, '__iter__') and not isinstance(content, str):
            parts = []
            for comp in content:
                if hasattr(comp, 'type') and hasattr(comp.type, 'value') and comp.type.value == 'Plain':
                    parts.append(getattr(comp, 'text', ''))
                elif hasattr(comp, 'text'):
                    parts.append(comp.text)
                elif isinstance(comp, str):
                    parts.append(comp)
                else:
                    try:
                        parts.append(str(comp))
                    except Exception:
                        pass
            return ''.join(parts)
        return getattr(content, 'text', str(content) if not hasattr(content, '__iter__') else '')

    async def _delayed_recall(self, event, sent_message):
        try:
            await asyncio.sleep(self.auto_recall_delay)
            mid = None
            if hasattr(sent_message, 'message_id'):
                mid = sent_message.message_id
            elif isinstance(sent_message, int):
                mid = sent_message
            elif hasattr(sent_message, 'id'):
                mid = sent_message.id
            elif isinstance(sent_message, dict):
                for k in ('message_id', 'id', 'msg_id'):
                    if k in sent_message:
                        mid = sent_message[k]
                        break
            elif isinstance(sent_message, str):
                try:
                    mid = int(sent_message)
                except ValueError:
                    pass
            if mid is not None:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    await event.bot.delete_msg(message_id=mid)
                    logger.info(f"已自动撤回消息 ID: {mid}")
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

        await eng.submit_task({
            "prompt": pure, "current_seed": seed,
            "current_width": w, "current_height": h,
            "current_batch_size": bs, "lora_list": lora_list,
            "selected_model": selected_model, "user_id": uid,
            "callback": self._make_result_callback(event, "txt2img")
        })

        mf = ""
        if selected_model:
            md = next((d for _, (fn, d) in eng.model_name_map.items() if fn == selected_model), "自定义模型")
            mf = f"\n使用模型：{md}"

        await self._send_with_auto_recall(event, event.plain_result(
            f"\n文生图任务已加入队列（排队：{eng.task_queue.qsize()}个）\n"
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

        await eng.submit_task({
            "prompt": pure, "current_seed": seed,
            "current_width": eng.default_width, "current_height": eng.default_height,
            "current_batch_size": bs, "lora_list": lora_list,
            "selected_model": selected_model, "user_id": uid,
            "img_path": img_path, "denoise": denoise,
            "callback": self._make_result_callback(event, "img2img")
        })

        await self._send_with_auto_recall(event, event.plain_result(
            f"\n图生图任务已加入队列（排队：{eng.task_queue.qsize()}个）\n"
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
        node_configs = cfg.get("node_configs", {})
        known_keys = set()
        for _, nc in node_configs.items():
            for pn, pi in nc.items():
                known_keys.add(pn)
                known_keys.update(pi.get("aliases", []))
        param_pat = '|'.join(re.escape(k) for k in known_keys)
        regex = rf'(?<!\S)({param_pat})(?=\s*:)'
        matches = list(re.finditer(regex, params_text))
        args = []
        prompt_parts = []
        last_end = 0
        for m in matches:
            ps = m.start()
            if ps > last_end:
                pp = params_text[last_end:ps].strip()
                if pp:
                    prompt_parts.append(pp)
            cp = params_text.find(':', ps)
            if cp == -1:
                continue
            nps = len(params_text)
            idx = matches.index(m)
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

        params = eng.parse_workflow_params(args, cfg)
        missing = eng.validate_required_params(cfg, params)
        if missing:
            await self._send_with_auto_recall(event, event.plain_result(f"缺少必需参数：{'、'.join(missing)}"))
            return
        errs = eng.validate_param_values(cfg, params)
        if errs:
            await self._send_with_auto_recall(event, event.plain_result(f"参数错误：\n" + "\n".join(errs)))
            return

        # 图片下载
        image_paths = []
        if cfg.get("input_nodes"):
            msgs = event.get_messages()
            img_segs = [m for m in msgs if isinstance(m, Image)]
            reply = next((s for s in msgs if isinstance(s, Reply)), None)
            if not img_segs and reply and reply.chain:
                img_segs = [s for s in reply.chain if isinstance(s, Image)]
            if not img_segs:
                await self._send_with_auto_recall(event, event.plain_result("此workflow需要图片输入"))
                return
            uid = str(event.get_sender_id())
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
                        await self._send_with_auto_recall(event, event.plain_result(err.replace("PERMANENT_ERROR:", "")))
                    else:
                        await self._send_with_auto_recall(event, event.plain_result(f"第{i+1}张图片失败：{err[:200]}"))
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

        await eng.submit_task({
            "prompt": final_wf,
            "workflow_name": wfn, "user_id": uid,
            "is_workflow": True, "image_paths": image_paths,
            "workflow_config": cfg,
            "callback": self._make_result_callback(event, "workflow")
        })
        await self._send_with_auto_recall(event, event.plain_result(
            f"Workflow「{cfg['name']}」已加入队列（排队：{eng.task_queue.qsize()}个）"
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
        await eng.submit_task({
            "prompt": prompt, "current_seed": seed,
            "current_width": w, "current_height": h,
            "current_batch_size": 1, "lora_list": [],
            "selected_model": None, "user_id": uid,
            "callback": self._make_result_callback(event, "llm_tool")
        })
        servers = [s.name for s in eng.comfyui_servers if s.healthy]
        sf = f"\n可用服务器：{'、'.join(servers)}" if servers else "\n当前无可用服务器，任务将在服务器恢复后处理"
        return f"文生图任务已加入队列（排队：{eng.task_queue.qsize()}个）\n提示词：{prompt}\nSeed：{seed}\n分辨率：{w}x{h}{sf}"

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
            for srv in eng.comfyui_servers:
                if srv.worker and not srv.worker.done():
                    srv.worker.cancel()
                    try:
                        await srv.worker
                    except asyncio.CancelledError:
                        pass
                    srv.worker = None
            await self._stop_help_server()
            logger.info("清理完成")
        except Exception as e:
            logger.error(f"清理时出错: {e}")

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

    async def _upload_3d_model_file(self, event: AstrMessageEvent, model_path: str, filename: str) -> bool:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                return False
            gid = event.get_group_id()
            uid = event.get_sender_id()
            if gid:
                await event.bot.upload_group_file(group_id=gid, file=model_path, name=filename)
            else:
                await event.bot.upload_private_file(user_id=int(uid), file=model_path, name=filename)
            return True
        except Exception:
            return False

    # ========== 伪造转发 ==========

    async def send_fake_forward_message(self, event: AstrMessageEvent, merged_chain: list, image_count: int):
        """发送伪造转发消息（与原版 main1.py 一致）"""
        if not self.enable_fake_forward or image_count < self.fake_forward_threshold:
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
            logger.info(f"已使用伪造转发消息发送，QQ号: {fake_qq}, 昵称: {nickname}, 图片数量: {image_count}")
        except Exception as e:
            logger.error(f"发送伪造转发消息失败，使用普通发送方式: {e}")
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
