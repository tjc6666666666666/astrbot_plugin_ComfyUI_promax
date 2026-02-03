from astrbot.api.message_components import Plain, Image, Reply, Video, Record
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event.filter import CustomFilter
from astrbot.api import logger, llm_tool
from astrbot.core.config import AstrBotConfig
import aiohttp
import asyncio
import random
import json
import uuid
import re
import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union
from urllib.parse import quote
from datetime import datetime, timedelta
from PIL import Image as PILImage, ImageDraw, ImageFont
from aiohttp import web
import threading
import socket
import sqlite3
import zipfile
import shutil
import aiosqlite
import copy
import glob

import io
import base64
import requests
from io import BytesIO
import subprocess
import tempfile

# GUI配置管理界面相关导入
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file, session, send_from_directory

from functools import wraps
import time

# 伪造转发消息相关导入
from astrbot.api.message_components import Node, Nodes


@register(
    "mod-comfyui",
    "",
    "使用多服务器ComfyUI文生图/图生图（支持模型选择、LoRA、自定义Workflow和服务器轮询）。\n开放时间：{open_time_ranges}\n文生图：发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填（例：/aimg girl 宽512,高768 批量2 model:写实风格 lora:儿童:0.8 lora:可爱!1.0）\n图生图：发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息（例：img2img 猫咪 噪声:0.7 批量2 model:动漫风格 lora:动物:1.2!0.9 + 图片/引用图片消息）\n自定义Workflow：发送「<前缀> [参数名:值 ...]」+ 图片（如需要），支持中英文参数名（例：encrypt 模式:decrypt 或 t2l 提示词:可爱女孩 种子:123 采样器:euler）\n输出压缩包：发送「comfyuioutput」获取今天生成的图片压缩包（需开启自动保存）\nLLM工具：可通过AI助手调用comfyui_txt2img工具进行文生图（支持英文提示词，空格自动转换为下划线）\n模型使用说明：\n  - 格式：model:描述（描述对应配置中的模型描述）\n  - 例：model:写实风格\nLoRA使用说明：\n  - 基础格式：lora:描述（使用默认强度1.0/1.0，描述对应配置中的LoRA描述）\n  - 仅模型强度：lora:描述:0.8（strength_model=0.8）\n  - 仅CLIP强度：lora:描述!1.0（strength_clip=1.0）\n  - 双强度：lora:描述:0.8!1.3（model=0.8, clip=1.3）\n  - 多LoRA：空格分隔多个lora参数（例：lora:儿童 lora:学生:0.9）\nWorkflow参数说明：\n  - 支持中英文参数名和别名（如：width/宽度/w，sampler_name/采样器/sampler）\n  - 参数格式：参数名:值（例：宽度:800 或 采样器:euler）\n  - 具体支持的参数名请查看各workflow的配置说明\n多服务器轮询处理，所有生成图片将合并为一条消息发送，未指定参数则用默认配置（文生图默认批量数：{txt2img_batch_size}，图生图默认批量数：{img2img_batch_size}，默认噪声系数：{default_denoise}，默认模型：{ckpt_name}）。\n限制说明：文生图最大批量{max_txt2img_batch}，图生图最大批量{max_img2img_batch}，分辨率范围{min_width}~{max_width}x{min_height}~{max_height}，任务队列最大{max_task_queue}个，每用户最大并发{max_concurrent_tasks_per_user}个\n可用模型列表：\n{model_list_desc}\n可用LoRA列表：\n{lora_list_desc}\n可用Workflow列表：\n{workflow_list_desc}",
    "3.4"  # 版本更新：支持LLM工具comfyui_txt2img功能
)
class ModComfyUI(Star):
    # 服务器状态类（增加worker引用）
    class ServerState:
        def __init__(self, url: str, name: str, server_id: int):
            self.url = url.rstrip("/")
            self.name = name
            self.server_id = server_id  # 唯一标识服务器
            self.busy = False  # 是否忙碌
            self.last_checked = None  # 最后检查时间
            self.healthy = True  # 是否健康
            self.failure_count = 0  # 连续失败次数
            self.retry_after = None  # 重试时间
            self.worker: Optional[asyncio.Task] = None  # 关联的worker任务

    # 过滤器类（不变）
    class ImgGenerateFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            messages = event.get_messages()
            has_image = any(isinstance(msg, Image) for msg in messages)
            if has_image:
                return False
            full_text = event.message_obj.message_str.strip()
            # 检查是否以aimg开头（支持空格前缀），但排除单独的aimg
            return (full_text.startswith("aimg") or full_text.startswith("aimg ")) and full_text != "aimg"

    class Img2ImgFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            messages = event.get_messages()
            has_image = any(isinstance(msg, Image) for msg in messages)
            has_image_in_reply = False
            reply_seg = next((seg for seg in messages if isinstance(seg, Reply)), None)
            if reply_seg and reply_seg.chain:
                has_image_in_reply = any(isinstance(seg, Image) for seg in reply_seg.chain)
            full_text = event.message_obj.message_str.strip()
            # 检查是否以img2img开头（支持空格前缀），但排除单独的img2img
            starts_with_img2img = (full_text.startswith("img2img") or full_text.startswith("img2img ")) and full_text != "img2img"
            return starts_with_img2img and (has_image or has_image_in_reply)

    class TomatoDecryptFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            full_text = event.message_obj.message_str.strip()
            return full_text == "小番茄图片解密"

    class TeeeFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            full_text = event.message_obj.message_str.strip()
            return full_text == "teeee"

    class AddServerFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            full_text = event.message_obj.message_str.strip()
            # 检查是否以"添加服务器"开头
            return full_text.startswith("添加服务器")

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        # 1. 加载配置
        self.comfyui_servers = self._parse_comfyui_servers(config.get("comfyui_url", []))
        # 临时服务器列表（仅在本次运行中有效，不写入配置文件）
        self.temp_servers = []  # 存储临时添加的ServerState对象
        self.ckpt_name = config.get("ckpt_name")
        self.sampler_name = config.get("sampler_name")
        self.scheduler = config.get("scheduler")
        
        # 采样器配置列表
        self.available_samplers = [
            "euler",
            "euler_cfg_pp", 
            "euler_ancestral",
            "euler_ancestral_cfg_pp",
            "heun",
            "heunpp2",
            "dpm_2",
            "dpm_2_ancestral",
            "lms",
            "dpm_fast",
            "dpm_adaptive",
            "dpmpp_2s_ancestral",
            "dpmpp_2s_ancestral_cfg_pp",
            "dpmpp_sde",
            "dpmpp_sde_gpu",
            "dpmpp_2m",
            "dpmpp_2m_cfg_pp",
            "dpmpp_2m_sde",
            "dpmpp_2m_sde_gpu",
            "dpmpp_2m_sde_heun",
            "dpmpp_2m_sde_heun_gpu",
            "dpmpp_3m_sde",
            "dpmpp_3m_sde_gpu",
            "ddpm",
            "lcm",
            "ipndm",
            "ipndm_v",
            "deis",
            "res_multistep",
            "res_multistep_cfg_pp",
            "res_multistep_ancestral",
            "res_multistep_ancestral_cfg_pp",
            "gradient_estimation",
            "gradient_estimation_cfg_pp",
            "er_sde",
            "seeds_2",
            "seeds_3",
            "sa_solver",
            "sa_solver_pece",
            "ddim",
            "uni_pc",
            "uni_pc_bh2"
        ]
        
        # 调度器配置列表
        self.available_schedulers = [
            "simple",
            "sgm_uniform",
            "karras",
            "exponential",
            "ddim_uniform",
            "beta",
            "normal",
            "linear_quadratic",
            "kl_optimal"
        ]
        self.cfg = config.get("cfg")
        self.negative_prompt = config.get("negative_prompt", "")
        self.default_width = config.get("default_width")
        self.default_height = config.get("default_height")
        self.num_inference_steps = config.get("num_inference_steps")
        self.seed = config.get("seed")
        self.enable_translation = config.get("enable_translation")
        self.default_denoise = config.get("default_denoise", 0.7)
        self.open_time_ranges = config.get("open_time_ranges", "7:00-8:00,11:00-14:00,17:00-24:00")
        self.enable_image_encrypt = config.get("enable_image_encrypt", True)
        self.txt2img_batch_size = config.get("txt2img_batch_size", 1)
        self.img2img_batch_size = config.get("img2img_batch_size", 1)
        self.max_txt2img_batch = config.get("max_txt2img_batch", 6)
        self.max_img2img_batch = config.get("max_img2img_batch", 6)
        self.max_task_queue = config.get("max_task_queue", 10)
        self.min_width = config.get("min_width", 64)
        self.max_width = config.get("max_width", 2000)
        self.min_height = config.get("min_height", 64)
        self.max_height = config.get("max_height", 2000)
        self.queue_check_delay = config.get("queue_check_delay", 30)
        self.queue_check_interval = config.get("queue_check_interval", 5)
        self.empty_queue_max_retry = config.get("empty_queue_max_retry", 2)
        self.server_check_interval = 60
        self.max_failure_count = 3
        self.retry_delay = 300
        self.last_poll_index = -1
        self.lora_config = config.get("lora_config", [])
        self.default_lora_strength_model = 1.0
        self.default_lora_strength_clip = 1.0
        self.max_lora_count = 10
        self.min_lora_strength = 0.0
        self.max_lora_strength = 2.0
        self.lora_name_map = self._parse_lora_config()
        self.lora_list_desc = self._generate_lora_list_desc()
        
        # 模型配置
        self.model_config = config.get("model_config", [])
        self.model_name_map = self._parse_model_config()
        self.model_list_desc = self._generate_model_list_desc()
        
        self.parsed_time_ranges = self._parse_time_ranges()
        
        # HTML转图片配置
        self.enable_help_image = config.get("enable_help_image", True)
        self.help_server_port = config.get("help_server_port", 8080)
        self.help_server_thread: Optional[threading.Thread] = None
        self.help_server_runner: Optional[web.AppRunner] = None
        self.help_server_site: Optional[web.TCPSite] = None
        self.actual_help_port = self.help_server_port

        # 获取插件数据目录
        self.data_dir = StarTools.get_data_dir()
        self.data_dir.mkdir(exist_ok=True)
        
        # 自动保存图片配置
        self.enable_auto_save = config.get("enable_auto_save", False)
        auto_save_config = config.get("auto_save_directory", config.get("auto_save_dir", "output"))
        if os.path.isabs(auto_save_config):
            # 绝对路径：使用用户配置
            self.auto_save_dir = auto_save_config
        else:
            # 相对路径：基于插件数据目录
            self.auto_save_dir = self.data_dir / auto_save_config
        
        # 确保自动保存目录存在，处理文件冲突
        self._ensure_directory_exists(self.auto_save_dir, "auto_save_dir")
        
        # 输出压缩包配置
        self.enable_output_zip = config.get("enable_output_zip", True)
        self.daily_download_limit = config.get("daily_download_limit", 1)  # 每天下载次数限制
        self.only_own_images = config.get("only_own_images", False)  # 是否只能下载自己生成的图片
        
        # 数据库配置 - 数据库与图片存储在同一目录
        self.db_dir = self.auto_save_dir
        self.db_path = os.path.join(self.db_dir, "user.db")  # 数据库路径

        # 用户队列限制配置
        self.max_concurrent_tasks_per_user = config.get("max_concurrent_tasks_per_user", 3)
        
        # 主动撤回配置
        self.enable_auto_recall = config.get("enable_auto_recall", False)
        self.auto_recall_delay = config.get("auto_recall_delay", 20)
        
        # 调试信息
        logger.info(f"主动撤回配置: 启用={self.enable_auto_recall}, 延迟={self.auto_recall_delay}秒")

        # 视频发送配置
        self.max_upload_size = config.get("max_upload_size", 100)  # 最大直接发送视频大小（MB）
        
        # 音频转语音配置
        self.enable_audio_to_voice = config.get("enable_audio_to_voice", True)  # 是否启用音频转语音功能

        # GUI配置管理界面配置
        self.enable_gui = config.get("enable_gui", False)
        self.gui_port = config.get("gui_port", 7777)
        self.gui_username = config.get("gui_username", "123")
        self.gui_password = config.get("gui_password", "123")
        
        # 伪造转发消息配置
        self.enable_fake_forward = config.get("enable_fake_forward", False)
        self.fake_forward_threshold = config.get("fake_forward_threshold", 2)  # 图片数量阈值
        self.fake_forward_qq = config.get("fake_forward_qq", "")  # 伪造的QQ号：0=发送者QQ，1=机器人QQ，其他=指定QQ号
        
        # Flask应用和GUI相关
        self.app = None
        self.gui_thread: Optional[threading.Thread] = None
        self.gui_running = False
        
        # Web API配置
        self.enable_web_api = config.get("enable_web_api", False)
        self.web_api_port = config.get("web_api_port", 7778)
        self.web_api_allow_register = config.get("web_api_allow_register", True)
        self.web_api_image_proxy = config.get("web_api_image_proxy", True)  # 是否启用图片代理
        
        # Web API Flask应用
        self.web_api_app = None
        self.web_api_thread: Optional[threading.Thread] = None
        self.web_api_running = False
        
        # 配置路径
        self.config_dir = Path(__file__).parent
        self.workflow_dir = self.config_dir / "workflow"
        self.main_config_file = self.config_dir / "config.json"
        
        # 群聊白名单配置
        self.group_whitelist = config.get("group_whitelist", [])
        # 转换为字符串列表，确保比较时类型一致
        self.group_whitelist = [str(group_id) for group_id in self.group_whitelist]
        
        # 如果启用GUI，则初始化Flask应用
        if self.enable_gui:
            self._init_gui()
        
        # 如果启用Web API，则初始化Web API应用
        if self.enable_web_api:
            self._init_web_api()

        # Workflow模块配置
        self.workflows: Dict[str, Dict[str, Any]] = {}
        self.workflow_prefixes: Dict[str, str] = {}  # prefix -> workflow_name
        # 将在初始化任务中异步加载
        asyncio.create_task(self._load_workflows_and_generate_desc())

        # 2. 状态管理
        self.task_queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_task_queue)
        self.server_monitor_task: Optional[asyncio.Task] = None  # 服务器监控任务
        self.server_monitor_running: bool = False
        
        # 3. 用户队列计数管理
        self.user_task_counts: Dict[str, int] = {}  # 记录每个用户的当前任务数
        self.user_task_lock = asyncio.Lock()  # 保护用户任务计数的锁
        
        # 4. 服务器轮询索引锁
        self.server_poll_lock = asyncio.Lock()  # 保护last_poll_index的并发访问
        
        # 5. 服务器状态锁
        self.server_state_lock = asyncio.Lock()  # 保护服务器状态的并发访问

        # 3. 验证配置
        self._validate_config()
        
        # 4. 初始化数据库
        asyncio.create_task(self._init_database())
        
        # 启动ComfyUI服务器监控（将在监控中启动worker）
        self.server_monitor_task = asyncio.create_task(self._start_server_monitor())
        
        # 启动GUI服务器（如果启用）
        if self.enable_gui:
            self._start_gui_server()
        
        # 启动Web API服务器（如果启用）
        if self.enable_web_api:
            self._start_web_api_server()
        
        # 激活LLM工具
        self.context.activate_llm_tool("comfyui_txt2img")

    # LLM工具：文生图功能
    @llm_tool(name="comfyui_txt2img")
    async def comfyui_txt2img(self, event: AstrMessageEvent, prompt: str, img_width: int = None, img_height: int = None) -> MessageEventResult:
        '''AI painting based on the prompts entered by the user.

        Args:
            prompt(string): A prompt for text to image,if the user inputs Chinese prompts, they need to be translated into English prompts that are closely aligned with the specialized terms used for AI painting, such as the prompts used when creating AI art with Midjourney.字符串，AI绘画的正向提示词（需将中文翻译为英文专业术语，如"猫毛"→"fluffy cat fur, photorealistic"）如果生图词空格的话，请用下划线_代替，而不是空格。
            img_width(number): The width of the image generated by AI painting. Optional parameter, this does not need to be parsed when there is no specified information about the image width.
            img_height(number): The height of the image generated by AI painting. Optional parameter, this does not need to be parsed when there is no specified information about the image height.
        '''
        logger.info(f"LLM Tool prompt:{prompt}")
        
        # 检查群聊白名单
        if not self._check_group_whitelist(event):
            return "❌ 当前群聊不在白名单中，无法使用此功能！"
        
        # 检查开放时间
        if not self._is_in_open_time():
            open_desc = self._get_open_time_desc()
            return f"当前未开放图片生成服务～\n开放时间：{open_desc}\n请在开放时间段内提交任务！"
        
        # 检查提示词
        if not prompt or prompt.strip() == "":
            return "提示词不能为空！请提供有效的AI绘画提示词。"
        
        # 处理提示词中的空格，替换为下划线
        processed_prompt = prompt.replace(" ", "_")
        
        # 设置默认尺寸
        current_width = img_width if img_width and self.min_width <= img_width <= self.max_width else self.default_width
        current_height = img_height if img_height and self.min_height <= img_height <= self.max_height else self.default_height
        
        # 生成随机种子
        try:
            current_seed = random.randint(1, 18446744073709551615) if (self.seed == "随机" or not self.seed) else int(self.seed)
        except (ValueError, TypeError):
            current_seed = random.randint(1, 2147483647)
        
        # 检查是否有可用的ComfyUI服务器
        available_servers = [s for s in self.comfyui_servers if s.healthy]
        if not available_servers:
            return "当前没有可用的ComfyUI服务器，请稍后再试。"
        
        # 检查用户任务数限制
        user_id = str(event.get_sender_id())
        if not await self._increment_user_task_count(user_id):
            return f"您当前同时进行的任务数已达上限（{self.max_concurrent_tasks_per_user}个），请等待当前任务完成后再提交新任务！"
        task_decremented = False  # 标记任务计数是否已递减
        
        # 检查任务队列是否已满
        if self.task_queue.full():
            await self._decrement_user_task_count(user_id)
            task_decremented = True
            return f"当前任务队列已满（{self.max_task_queue}个任务上限），请稍后再试！"
        
        try:
            # 将任务加入队列
            await self.task_queue.put({
                "event": event,
                "prompt": processed_prompt,
                "current_seed": current_seed,
                "current_width": current_width,
                "current_height": current_height,
                "current_batch_size": 1,  # LLM工具固定为1张
                "lora_list": [],  # LLM工具暂不支持LoRA
                "selected_model": None,  # 使用默认模型
                "user_id": user_id,
                "is_llm_tool": True  # 标记这是LLM工具任务
            })
            
            available_servers = [s.name for s in self.comfyui_servers if s.healthy]
            server_feedback = f"可用服务器：{', '.join(available_servers)}" if available_servers else "当前无可用服务器，任务将在服务器恢复后处理"
            
            # 返回任务已加入队列的消息
            return f"文生图任务已加入队列（当前排队：{self.task_queue.qsize()}个）\n提示词：{processed_prompt}\nSeed：{current_seed}\n分辨率：{current_width}x{current_height}\n{server_feedback}"
            

                        
        except Exception as e:
            await self._decrement_user_task_count(user_id)
            task_decremented = True
            logger.error(f"LLM tool comfyui_txt2img error: {e}")
            return f"图片生成失败：{str(e)}"
        finally:
            # 注意：这里不递减任务计数，因为任务会被队列处理器处理
            # 任务计数会在队列处理完成后递减
            pass

    def _ensure_directory_exists(self, dir_path: Union[str, Path], dir_name: str = "directory") -> None:
        """确保目录存在，如果存在为文件则备份并创建目录"""
        dir_path_str = str(dir_path)
        
        if os.path.exists(dir_path_str):
            if not os.path.isdir(dir_path_str):
                # 路径存在但不是目录，需要处理
                backup_path = f"{dir_path_str}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                logger.warning(f"{dir_name}路径 {dir_path_str} 已存在为文件，将重命名为 {backup_path} 并创建目录")
                os.rename(dir_path_str, backup_path)
                os.makedirs(dir_path_str, exist_ok=True)
        else:
            # 路径不存在，直接创建目录
            os.makedirs(dir_path_str, exist_ok=True)

    async def _send_with_auto_recall(self, event: AstrMessageEvent, message_content: Any) -> Optional[int]:
        """发送消息并根据配置自动撤回（仅撤回文本消息）"""
        logger.info(f"发送消息: enable_auto_recall={self.enable_auto_recall}")
        
        if not self.enable_auto_recall:
            # 如果未启用自动撤回，直接发送消息
            await event.send(message_content)
            return None
        
        # 检查要发送的消息内容是否包含图片或文件
        has_non_text = False
        if hasattr(message_content, '__iter__') and not isinstance(message_content, str):
            # 如果是消息链，检查是否包含图片或文件
            try:
                for component in message_content:
                    if hasattr(component, '__class__'):
                        class_name = component.__class__.__name__
                        if 'Image' in class_name or 'File' in class_name:
                            has_non_text = True
                            break
            except Exception:
                # 如果检查失败，保守处理，不撤回
                has_non_text = True
        
        # 尝试使用 AiocqhttpMessageEvent 的直接发送方法来获取消息ID
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            logger.info(f"事件类型检查: {type(event)}, 是否为 AiocqhttpMessageEvent: {isinstance(event, AiocqhttpMessageEvent)}")
            logger.info(f"消息内容检查: has_non_text={has_non_text}")
            
            if isinstance(event, AiocqhttpMessageEvent) and not has_non_text:
                # QQ 平台：使用 bot 的直接发送方法获取消息ID进行撤回
                client = event.bot
                
                # 获取发送者和群组信息
                group_id = event.get_group_id() if event.get_group_id() else None
                user_id = event.get_sender_id()
                logger.info(f"发送信息: group_id={group_id}, user_id={user_id}")
                
                # 准备消息 - 需要转换为 CQ 码格式
                message_to_send = self._convert_to_cq_code(message_content)
                logger.info(f"转换后的消息: {message_to_send}")
                
                # 检查转换是否成功
                if not message_to_send or message_to_send.strip() == "":
                    logger.warning("消息转换失败或结果为空，回退到普通发送方式")
                    await event.send(message_content)
                    return None
                
                # 发送消息并获取消息ID
                if group_id:
                    # 群聊消息
                    result = await client.send_group_msg(group_id=int(group_id), message=message_to_send)
                else:
                    # 私聊消息
                    result = await client.send_private_msg(user_id=int(user_id), message=message_to_send)
                
                logger.info(f"直接发送消息返回结果: {type(result)}, 内容: {result}")
                
                # 创建延迟撤回任务
                asyncio.create_task(self._delayed_recall(event, result))
                return result
            else:
                # 非 QQ 平台或包含非文本内容：使用通用发送方式
                if not isinstance(event, AiocqhttpMessageEvent):
                    logger.info(f"非 QQ 平台事件类型: {type(event)}，使用通用发送方式（不支持自动撤回）")
                else:
                    logger.info("消息包含非文本内容，使用通用发送方式（不支持自动撤回）")
                
                await event.send(message_content)
                return None
                
        except Exception as e:
            import traceback
            logger.warning(f"使用直接发送方法失败，回退到普通发送: {e}")
            logger.warning(f"详细错误信息: {traceback.format_exc()}")
            await event.send(message_content)
            return None

    def _convert_to_cq_code(self, message_content: Any) -> str:
        """将 AstrBot 消息组件转换为 CQ 码格式"""
        logger.info(f"开始转换消息内容: {type(message_content)}")
        
        # 如果是字符串，直接返回
        if isinstance(message_content, str):
            logger.info(f"消息内容是字符串: {message_content}")
            return message_content.strip()  # 去除首尾空白字符
        
        # 尝试多种方式提取文本内容
        text_result = ""
        
        # 方法1: 检查是否有 message 属性，并且是可调用的方法
        if hasattr(message_content, 'message'):
            msg_attr = getattr(message_content, 'message')
            logger.info(f"检查 message 属性: {type(msg_attr)}, 是否可调用: {callable(msg_attr)}")
            
            if callable(msg_attr):
                # 如果是方法，调用它
                try:
                    msg_content = msg_attr()
                    logger.info(f"调用 message 方法后获取内容: {type(msg_content)}")
                    text_result = self._extract_text_from_content(msg_content)
                    if text_result:
                        logger.info(f"从调用 message 方法提取的文本: {text_result}")
                        return text_result.strip()  # 去除首尾空白字符
                except Exception as e:
                    logger.warning(f"调用 message 方法失败: {e}")
            else:
                # 如果是属性，直接使用
                msg_content = msg_attr
                logger.info(f"通过 message 属性获取内容: {type(msg_content)}")
                text_result = self._extract_text_from_content(msg_content)
                if text_result:
                    logger.info(f"从 message 属性提取的文本: {text_result}")
                    return text_result.strip()  # 去除首尾空白字符
        
        # 方法2: 检查是否有 chain 属性
        if hasattr(message_content, 'chain'):
            msg_content = message_content.chain
            logger.info(f"通过 chain 属性获取内容: {type(msg_content)}")
            text_result = self._extract_text_from_content(msg_content)
            if text_result:
                logger.info(f"从 chain 属性提取的文本: {text_result}")
                return text_result.strip()  # 去除首尾空白字符
        
        # 方法3: 如果是可迭代的消息链
        if hasattr(message_content, '__iter__') and not isinstance(message_content, str):
            logger.info(f"作为消息链处理: {type(message_content)}")
            text_result = self._extract_text_from_content(message_content)
            if text_result:
                logger.info(f"从消息链提取的文本: {text_result}")
                return text_result.strip()  # 去除首尾空白字符
        
        # 方法4: 最后尝试直接转换为字符串
        try:
            text_result = str(message_content)
            logger.info(f"直接转换为字符串: {text_result}")
            return text_result.strip()  # 去除首尾空白字符
        except Exception as e:
            logger.warning(f"无法转换消息内容为字符串: {e}")
            return ""
    
    def _extract_text_from_content(self, content: Any) -> str:
        """从消息内容中提取纯文本"""
        if isinstance(content, str):
            return content
        
        if hasattr(content, '__iter__') and not isinstance(content, str):
            text_parts = []
            for component in content:
                # 检查是否是 Plain 组件
                if hasattr(component, 'type'):
                    try:
                        if component.type.value == 'Plain':
                            if hasattr(component, 'text'):
                                text_parts.append(component.text)
                        # 如果是其他组件，忽略（因为我们只想要纯文本）
                    except AttributeError:
                        # 如果没有 type 属性，尝试其他方法
                        pass
                
                # 检查是否有 text 属性
                elif hasattr(component, 'text'):
                    text_parts.append(component.text)
                
                # 如果组件本身就是字符串
                elif isinstance(component, str):
                    text_parts.append(component)
                
                # 其他情况，尝试转换为字符串
                else:
                    try:
                        text_parts.append(str(component))
                    except:
                        pass
            
            return ''.join(text_parts)
        
        # 单个组件的情况
        if hasattr(content, 'text'):
            return content.text
        
        # 最后尝试转换为字符串
        try:
            return str(content)
        except:
            return ""

    async def _delayed_recall(self, event, sent_message) -> None:
        """延迟撤回消息"""
        try:
            logger.info(f"准备在{self.auto_recall_delay}秒后撤回消息")
            # 等待指定的延迟时间
            await asyncio.sleep(self.auto_recall_delay)
            
            # 尝试获取消息ID
            message_id = None
            if sent_message is None:
                logger.warning("sent_message 为 None")
                return
                
            logger.info(f"尝试解析消息ID: {type(sent_message)}, 内容: {sent_message}")
            
            # 尝试多种方式获取消息ID
            if hasattr(sent_message, 'message_id'):
                message_id = sent_message.message_id
                logger.info(f"通过 message_id 属性获取: {message_id}")
            elif isinstance(sent_message, int):
                message_id = sent_message
                logger.info(f"直接是整数: {message_id}")
            elif hasattr(sent_message, 'id'):
                message_id = sent_message.id
                logger.info(f"通过 id 属性获取: {message_id}")
            elif isinstance(sent_message, dict):
                # 如果是字典，尝试常见的键
                for key in ['message_id', 'id', 'msg_id']:
                    if key in sent_message:
                        message_id = sent_message[key]
                        logger.info(f"通过字典键 {key} 获取: {message_id}")
                        break
            elif isinstance(sent_message, str):
                # 如果是字符串，尝试解析为整数
                try:
                    message_id = int(sent_message)
                    logger.info(f"字符串转整数: {message_id}")
                except ValueError:
                    pass
            
            if message_id is not None:
                # 尝试撤回消息
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    if isinstance(event, AiocqhttpMessageEvent):
                        client = event.bot
                        await client.delete_msg(message_id=message_id)
                        logger.info(f"已自动撤回消息 ID: {message_id}")
                    else:
                        logger.warning(f"事件类型不是 AiocqhttpMessageEvent: {type(event)}")
                except Exception as delete_error:
                    logger.warning(f"撤回消息时出错: {delete_error}")
            else:
                logger.warning(f"无法获取消息ID: {type(sent_message)}, 内容: {sent_message}")
                
        except Exception as e:
            logger.warning(f"自动撤回消息失败: {e}")

    async def _init_database(self) -> None:
        """初始化用户下载记录数据库"""
        try:
            # 确保数据库目录存在，处理文件冲突
            db_dir = os.path.dirname(self.db_path)
            loop = asyncio.get_event_loop()
            
            # 检查路径是否存在以及类型
            if os.path.exists(db_dir):
                if not os.path.isdir(db_dir):
                    # 路径存在但不是目录，需要处理
                    backup_path = f"{db_dir}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    logger.warning(f"路径 {db_dir} 已存在为文件，将重命名为 {backup_path} 并创建目录")
                    await loop.run_in_executor(None, os.rename, db_dir, backup_path)
                    await loop.run_in_executor(None, os.makedirs, db_dir, True)
            else:
                # 路径不存在，直接创建目录
                await loop.run_in_executor(None, os.makedirs, db_dir, True)
            
            async with aiosqlite.connect(self.db_path) as conn:
                # 创建用户下载记录表
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_downloads (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        download_date TEXT NOT NULL,
                        download_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, download_date)
                    )
                ''')
                
                # 创建图片生成记录表（用于记录图片的生成者）
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS image_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        generate_date TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                await conn.commit()
                logger.info(f"数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    async def _check_download_limit(self, user_id: str) -> Tuple[bool, int]:
        """检查用户今日下载次数限制"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                # 查询或插入用户今日下载记录
                await conn.execute('''
                    INSERT OR IGNORE INTO user_downloads (user_id, download_date, download_count)
                    VALUES (?, ?, 0)
                ''', (user_id, today))
                await conn.commit()  # 确保插入操作被提交
                
                # 查询当前下载次数
                cursor = await conn.execute('''
                    SELECT download_count FROM user_downloads
                    WHERE user_id = ? AND download_date = ?
                ''', (user_id, today))
                
                result = await cursor.fetchone()
                current_count = result[0] if result else 0
                
                # 检查是否超过限制
                can_download = current_count < self.daily_download_limit
                return can_download, current_count
        except Exception as e:
            logger.error(f"检查下载限制失败: {e}")
            return False, 0

    async def _increment_download_count(self, user_id: str) -> None:
        """增加用户下载次数"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                # 确保记录存在，如果不存在则插入
                await conn.execute('''
                    INSERT OR IGNORE INTO user_downloads (user_id, download_date, download_count)
                    VALUES (?, ?, 0)
                ''', (user_id, today))
                
                # 更新下载次数
                await conn.execute('''
                    UPDATE user_downloads 
                    SET download_count = download_count + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND download_date = ?
                ''', (user_id, today))
                await conn.commit()
        except Exception as e:
            logger.error(f"更新下载次数失败: {e}")

    async def _record_image_generation(self, filename: str, user_id: str) -> None:
        """记录图片生成信息"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''
                    INSERT INTO image_records (filename, user_id, generate_date)
                    VALUES (?, ?, ?)
                ''', (filename, user_id, today))
                await conn.commit()
        except Exception as e:
            logger.error(f"记录图片生成信息失败: {e}")

    async def _get_user_images_today(self, user_id: str) -> List[str]:
        """获取用户今日生成的图片列表"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT filename FROM image_records
                    WHERE user_id = ? AND generate_date = ?
                ''', (user_id, today))
                return [row[0] for row in await cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取用户图片列表失败: {e}")
            return []

    def _parse_comfyui_servers(self, server_configs: list) -> List[ServerState]:
        servers = []
        if not isinstance(server_configs, list):
            logger.warning(f"ComfyUI服务器配置格式错误，应为列表类型，当前为{type(server_configs)}")
            return servers
        for idx, config in enumerate(server_configs):
            if not isinstance(config, str) or "," not in config:
                logger.warning(f"服务器配置项格式错误（索引{idx}）：{config}，跳过该配置（正确格式：URL,名称）")
                continue
            url, name = config.split(",", 1)
            url = url.strip()
            name = name.strip()
            if not url.startswith(("http://", "https://")):
                logger.warning(f"服务器URL格式错误（索引{idx}）：{url}，需以http://或https://开头，已自动添加http://")
                url = f"http://{url}"
            # 增加server_id标识
            servers.append(self.ServerState(url, name or f"服务器{idx+1}", idx))
            logger.info(f"已添加ComfyUI服务器：{name} ({url})")
        return servers

    def _filter_server_urls(self, text: str) -> str:
        if not text or not self.comfyui_servers:
            return text
        filtered_text = text
        for server in self.comfyui_servers:
            if server.url in filtered_text:
                filtered_text = filtered_text.replace(server.url, f"[{server.name}地址已隐藏]")
            url_without_prefix = server.url.replace("http://", "").replace("https://", "")
            if url_without_prefix in filtered_text and server.url not in filtered_text:
                filtered_text = filtered_text.replace(url_without_prefix, f"[{server.name}地址已隐藏]")
        return filtered_text
    
    def _check_group_whitelist(self, event: AstrMessageEvent) -> bool:
        """检查群聊是否在白名单中"""
        # 如果白名单为空，则允许所有群聊
        if not self.group_whitelist:
            return True
        
        # 获取群组ID
        group_id = event.get_group_id()
        if group_id is None:
            # 私聊消息，默认允许
            return True
        
        # 检查群组是否在白名单中
        group_id_str = str(group_id)
        is_allowed = group_id_str in self.group_whitelist
        
        if not is_allowed:
            logger.info(f"群聊 {group_id} 不在白名单中，拒绝访问")
        
        return is_allowed

    def _parse_lora_config(self) -> Dict[str, Tuple[str, str]]:
        lora_map = {}
        duplicate_descriptions = set()
        for idx, lora_item in enumerate(self.lora_config):
            if not isinstance(lora_item, str) or "," not in lora_item:
                logger.warning(f"LoRA配置项格式错误（索引{idx}）：{lora_item}，跳过该配置（正确格式：文件名,描述）")
                continue
            filename, desc = lora_item.split(",", 1)
            filename = filename.strip()
            desc = desc.strip()
            if not filename:
                logger.warning(f"LoRA配置项缺少文件名（索引{idx}）：{lora_item}，跳过")
                continue
            if not desc:
                logger.warning(f"LoRA配置项缺少描述（索引{idx}）：{lora_item}，跳过（需指定描述用于引用）")
                continue
            desc_lower = desc.lower()
            if desc_lower in lora_map:
                existing_filename, _ = lora_map[desc_lower]
                logger.warning(f"LoRA描述重复（索引{idx}）：{desc}，已覆盖原有配置（原文件：{existing_filename} → 新文件：{filename}）")
                duplicate_descriptions.add(desc_lower)
            lora_map[desc_lower] = (filename, desc)
            filename_prefix = os.path.splitext(filename)[0].strip().lower()
            if filename_prefix not in lora_map and filename_prefix not in duplicate_descriptions:
                lora_map[filename_prefix] = (filename, desc)
                logger.debug(f"LoRA兼容映射：文件名前缀「{filename_prefix}」→ 描述「{desc}」")
        return lora_map

    def _get_unique_lora_descriptions(self) -> List[str]:
        """获取唯一的LoRA描述列表（用于Web API）"""
        if not self.lora_name_map:
            return []
        
        # 使用集合来确保LoRA描述的唯一性，按原始配置顺序返回
        seen_descriptions = set()
        unique_descriptions = []
        
        for key, (filename, desc) in self.lora_name_map.items():
            # 只添加第一次出现的每个描述
            if desc not in seen_descriptions:
                seen_descriptions.add(desc)
                # 格式：描述，文件名
                unique_descriptions.append(f"{desc}，{filename}")
        
        return unique_descriptions

    def _generate_lora_list_desc(self) -> str:
        if not self.lora_name_map:
            return "  暂无可用LoRA"
        unique_loras = {}
        for key, (filename, desc) in self.lora_name_map.items():
            desc_lower = desc.lower()
            if desc_lower not in unique_loras:
                unique_loras[desc_lower] = (filename, desc)
        desc_list = []
        for desc_lower, (filename, desc) in unique_loras.items():
            desc_list.append(f"  - {desc}（文件：{filename}）")
        return "\n".join(desc_list)

    def _parse_model_config(self) -> Dict[str, Tuple[str, str]]:
        """解析模型配置，返回描述到文件名的映射"""
        model_map = {}
        duplicate_descriptions = set()
        for idx, model_item in enumerate(self.model_config):
            if not isinstance(model_item, str) or "," not in model_item:
                logger.warning(f"模型配置项格式错误（索引{idx}）：{model_item}，跳过该配置（正确格式：文件名,描述）")
                continue
            filename, desc = model_item.split(",", 1)
            filename = filename.strip()
            desc = desc.strip()
            if not filename:
                logger.warning(f"模型配置项缺少文件名（索引{idx}）：{model_item}，跳过")
                continue
            if not desc:
                logger.warning(f"模型配置项缺少描述（索引{idx}）：{model_item}，跳过（需指定描述用于引用）")
                continue
            desc_lower = desc.lower()
            if desc_lower in model_map:
                existing_filename, _ = model_map[desc_lower]
                logger.warning(f"模型描述重复（索引{idx}）：{desc}，已覆盖原有配置（原文件：{existing_filename} → 新文件：{filename}）")
                duplicate_descriptions.add(desc_lower)
            model_map[desc_lower] = (filename, desc)
            filename_prefix = os.path.splitext(filename)[0].strip().lower()
            if filename_prefix not in model_map and filename_prefix not in duplicate_descriptions:
                model_map[filename_prefix] = (filename, desc)
                logger.debug(f"模型兼容映射：文件名前缀「{filename_prefix}」→ 描述「{desc}」")
        return model_map

    def _get_unique_model_descriptions(self) -> List[str]:
        """获取唯一的模型描述列表（用于Web API）"""
        if not self.model_name_map:
            return []
        
        # 使用集合来确保模型描述的唯一性，按原始配置顺序返回
        seen_descriptions = set()
        unique_descriptions = []
        
        for key, (filename, desc) in self.model_name_map.items():
            # 只添加第一次出现的每个描述
            if desc not in seen_descriptions:
                seen_descriptions.add(desc)
                # 格式：描述，文件名
                unique_descriptions.append(f"{desc}，{filename}")
        
        return unique_descriptions

    def _generate_model_list_desc(self) -> str:
        """生成模型列表描述"""
        if not self.model_name_map:
            return "  暂无可用模型"
        # 使用集合来确保模型描述的唯一性
        seen_descriptions = set()
        desc_list = []
        
        for key, (filename, desc) in self.model_name_map.items():
            # 只添加第一次出现的每个描述
            if desc not in seen_descriptions:
                seen_descriptions.add(desc)
                desc_list.append(f"  - {desc}（文件：{filename}）")
        
        return "\n".join(desc_list)

    async def _load_workflows_and_generate_desc(self) -> None:
        """异步加载workflows并生成描述"""
        await self._load_workflows()
        self.workflow_list_desc = self._generate_workflow_list_desc()

    def _generate_workflow_list_desc(self) -> str:
        """生成workflow列表描述"""
        if not self.workflows:
            return "  暂无可用Workflow"
        
        desc_list = []
        for workflow_name, workflow_info in self.workflows.items():
            config = workflow_info["config"]
            name = config.get("name", workflow_name)
            prefix = config.get("prefix", "")
            description = config.get("description", "")
            desc_list.append(f"  - {name}（前缀：{prefix}）{f' - {description}' if description else ''}")
        
        return "\n".join(desc_list)

    def _generate_workflow_html_items(self) -> str:
        """生成HTML格式的workflow列表"""
        if not self.workflows:
            return '<li>暂无可用Workflow</li>'
        
        html_items = []
        for workflow_name, workflow_info in self.workflows.items():
            config = workflow_info["config"]
            name = config.get("name", workflow_name)
            prefix = config.get("prefix", "")
            description = config.get("description", "")
            if description:
                html_items.append(f'<li>{name} (前缀: {prefix}) - {description}</li>')
            else:
                html_items.append(f'<li>{name} (前缀: {prefix})</li>')
        
        return "\n".join(html_items)

    def _generate_workflow_text_help(self) -> str:
        """生成文本格式的workflow帮助信息"""
        if not self.workflows:
            return "\n\n🔧 可用Workflow列表：\n  • 暂无可用Workflow"
        
        workflow_details = []
        for workflow_name, workflow_info in self.workflows.items():
            config = workflow_info["config"]
            name = config.get("name", workflow_name)
            prefix = config.get("prefix", "")
            description = config.get("description", "")
            if description:
                workflow_details.append(f"  • {name} (前缀: {prefix}) - {description}")
            else:
                workflow_details.append(f"  • {name} (前缀: {prefix})")
        
        workflow_help = f"\n\n🔧 可用Workflow列表：\n" + "\n".join(workflow_details)
        workflow_help += "\n\nWorkflow使用说明：\n  - 格式：<前缀> [参数名:值 ...]\n  - 支持中英文参数名和别名（如：width/宽度/w，sampler_name/采样器/sampler）\n  - 参数格式：参数名:值（例：宽度:800 或 采样器:euler）\n  - 具体支持的参数名请查看各workflow的配置说明"
        
        return workflow_help

    async def _send_workflow_help(self, event: AstrMessageEvent, prefix: str) -> None:
        """发送特定workflow的详细帮助信息"""
        try:
            workflow_name = self.workflow_prefixes[prefix]
            workflow_info = self.workflows[workflow_name]
            config = workflow_info["config"]
            
            # 检查是否启用图片帮助
            if self.enable_help_image:
                # 尝试发送图片格式的帮助
                success = await self._send_workflow_help_image(event, workflow_name, prefix, config)
                if not success:
                    # 图片生成失败，回退到文本格式
                    logger.warning(f"生成workflow帮助图片失败，回退到文本格式: {workflow_name}")
                    await self._send_workflow_help_text(event, prefix, config)
            else:
                # 发送文本格式的帮助
                await self._send_workflow_help_text(event, prefix, config)
            
        except Exception as e:
            logger.error(f"发送workflow帮助失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(f"获取帮助信息失败: {str(e)}"))

    async def _send_workflow_help_image(self, event: AstrMessageEvent, workflow_name: str, prefix: str, config: Dict[str, Any]) -> bool:
        """发送图片格式的workflow帮助信息"""
        try:
            # 检查是否已有缓存的帮助图片
            workflow_dir = self.workflow_dir / workflow_name
            help_image_path = workflow_dir / "help.png"
            
            if os.path.exists(help_image_path):
                # 使用缓存的图片，传递文件路径
                await event.send(event.image_result(str(help_image_path)))
                return True
            
            # 生成帮助图片
            help_text = self._generate_workflow_help_text(prefix, config)
            workflow_title = config.get("name", "工作流帮助")
            image_data = self._create_help_image(help_text, workflow_title)
            
            if image_data:
                # 保存图片到缓存
                workflow_dir.mkdir(parents=True, exist_ok=True)
                def write_help_image():
                    with open(help_image_path, 'wb') as f:
                        f.write(image_data)
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, write_help_image)
                
                # 发送图片，传递文件路径
                await event.send(event.image_result(str(help_image_path)))
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"生成workflow帮助图片失败: {e}")
            return False

    async def _send_workflow_help_text(self, event: AstrMessageEvent, prefix: str, config: Dict[str, Any]) -> None:
        """发送文本格式的workflow帮助信息"""
        help_text = self._generate_workflow_help_text(prefix, config)
        await self._send_with_auto_recall(event, event.plain_result(help_text))

    def _generate_workflow_help_text(self, prefix: str, config: Dict[str, Any]) -> str:
        """生成workflow帮助文本内容"""
        help_lines = []
        
        # 标题和基本信息
        help_lines.append(f"🔧 {config.get('name', 'Unknown')} 详细帮助")
        help_lines.append("=" * 50)
        help_lines.append(f"调用前缀: {prefix}")
        help_lines.append(f"描述: {config.get('description', '暂无描述')}")
        help_lines.append(f"版本: {config.get('version', '未知')}")
        help_lines.append(f"作者: {config.get('author', '未知')}")
        help_lines.append("")
        
        # 使用格式
        help_lines.append("📝 使用格式:")
        help_lines.append(f"  {prefix} [参数名:值 ...]")
        
        # 检查是否需要图片输入
        input_nodes = config.get("input_nodes", [])
        if input_nodes:
            help_lines.append("  + 图片（必需）")
        help_lines.append("")
        
        # 参数说明
        node_configs = config.get("node_configs", {})
        if node_configs:
            help_lines.append("⚙️ 参数详细说明:")
            help_lines.append("-" * 30)
            
            for node_id, node_config in node_configs.items():
                for param_name, param_info in node_config.items():
                    # 参数基本信息
                    param_type = param_info.get("type", "未知")
                    default_value = param_info.get("default", "无")
                    description = param_info.get("description", "暂无描述")
                    required = param_info.get("required", False)
                    
                    help_lines.append(f"🔸 {param_name}")
                    help_lines.append(f"   类型: {param_type}")
                    help_lines.append(f"   描述: {description}")
                    help_lines.append(f"   必需: {'是' if required else '否'}")
                    help_lines.append(f"   默认值: {default_value}")
                    
                    # 数值范围（如果有）
                    if param_type == "number":
                        min_val = param_info.get("min")
                        max_val = param_info.get("max")
                        if min_val is not None and max_val is not None:
                            help_lines.append(f"   范围: {min_val} ~ {max_val}")
                        elif min_val is not None:
                            help_lines.append(f"   最小值: {min_val}")
                        elif max_val is not None:
                            help_lines.append(f"   最大值: {max_val}")
                    
                    # 选项（如果是select类型）
                    if param_type == "select":
                        options = param_info.get("options", [])
                        if options:
                            help_lines.append(f"   可选值: {', '.join(options)}")
                    
                    # 别名
                    aliases = param_info.get("aliases", [])
                    if aliases:
                        help_lines.append(f"   别名: {', '.join(aliases)}")
                    
                    help_lines.append("")
        
        # 使用示例
        help_lines.append("💡 使用示例:")
        examples = self._generate_workflow_examples(prefix, config)
        for example in examples:
            help_lines.append(f"  {example}")
        help_lines.append("")
        
        # 注意事项
        help_lines.append("⚠️ 注意事项:")
        help_lines.append("  • 参数格式为: 参数名:值")
        help_lines.append("  • 支持中英文参数名和别名")
        help_lines.append("  • 多个参数用空格分隔")
        help_lines.append("  • 如需图片，请同时发送图片或引用图片消息")
        help_lines.append("")
        
        return "\n".join(help_lines)

    def _create_help_image(self, text: str, title: str = "工作流帮助") -> Optional[bytes]:
        """创建帮助图片，使用与主帮助图片相同的页眉页尾样式"""
        try:
            # 图片设置
            width = 1200
            padding = 50
            line_height = 35
            font_size_title = 52
            font_size_normal = 32
            font_size_small = 24
            base_height = 120  # 顶部标题区域
            bottom_height = 80  # 底部信息区域
            
            # 尝试加载字体
            try:
                font_path = os.path.join(os.path.dirname(__file__), "1.ttf")
                if os.path.exists(font_path):
                    title_font = ImageFont.truetype(font_path, font_size_title)
                    normal_font = ImageFont.truetype(font_path, font_size_normal)
                    small_font = ImageFont.truetype(font_path, font_size_small)
                else:
                    title_font = ImageFont.load_default()
                    normal_font = ImageFont.load_default()
                    small_font = ImageFont.load_default()
            except:
                title_font = ImageFont.load_default()
                normal_font = ImageFont.load_default()
                small_font = ImageFont.load_default()
            
            # 计算所需高度
            lines = text.split('\n')
            content_height = len(lines) * line_height + 50  # 额外50像素用于间距
            height = max(800, base_height + content_height + bottom_height)
            
            # 创建图片
            img = PILImage.new('RGB', (width, height), color='#ffffff')
            draw = ImageDraw.Draw(img)
            
            # 绘制页眉背景（与主帮助图片相同的样式）
            draw.rectangle([0, 0, width, 80], fill='#4a90e2')
            
            # 绘制页眉标题
            title_text = f"🎨 ComfyUI AI绘画 - {title}"
            title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
            title_width = title_bbox[2] - title_bbox[0]
            title_x = (width - title_width) // 2
            draw.text((title_x, 25), title_text, fill='white', font=title_font)
            
            # 绘制内容区域
            y_offset = base_height
            
            for line in lines:
                if line.startswith('🔧'):
                    # 标题行
                    draw.text((padding, y_offset), line, fill='#333333', font=normal_font)
                elif line.startswith('='):
                    # 分隔线
                    draw.text((padding, y_offset), line, fill='#34495e', font=normal_font)
                elif line.startswith('📝'):
                    # 章节标题
                    draw.text((padding, y_offset), line, fill='#2980b9', font=normal_font)
                elif line.startswith('⚙️') or line.startswith('💡') or line.startswith('⚠️'):
                    # 章节标题
                    draw.text((padding, y_offset), line, fill='#27ae60', font=normal_font)
                elif line.startswith('🔸'):
                    # 参数名
                    draw.text((padding, y_offset), line, fill='#8e44ad', font=small_font)
                elif line.startswith('   '):
                    # 参数说明
                    draw.text((padding, y_offset), line, fill='#34495e', font=small_font)
                elif line.startswith('  '):
                    # 示例
                    draw.text((padding, y_offset), line, fill='#16a085', font=small_font)
                else:
                    # 普通文本
                    draw.text((padding, y_offset), line, fill='#666666', font=small_font)
                
                y_offset += line_height
            
            # 绘制页脚背景（与主帮助图片相同的样式）
            draw.rectangle([0, height-80, width, height], fill='#f5f5f5')
            
            # 绘制页脚信息
            footer_text = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            draw.text((50, height-60), footer_text, fill='#999999', font=small_font)
            
            # 在左下角添加GitHub链接
            github_text = "https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax"
            draw.text((50, height-35), github_text, fill='#666666', font=small_font)
            
            # 在右下角添加Astrbot.png图片（与主帮助图片相同的样式）
            try:
                astrbot_path = os.path.join(os.path.dirname(__file__), "Astrbot.png")
                if os.path.exists(astrbot_path):
                    astrbot_img = PILImage.open(astrbot_path)
                    
                    # 调整图片大小
                    target_height = 60
                    aspect_ratio = astrbot_img.width / astrbot_img.height
                    target_width = int(target_height * aspect_ratio)
                    
                    astrbot_resized = astrbot_img.resize((target_width, target_height), PILImage.Resampling.LANCZOS)
                    
                    # 计算右下角位置
                    x_position = width - target_width - 10
                    y_position = height - target_height - 10
                    
                    # 粘贴图片
                    img.paste(astrbot_resized, (x_position, y_position), astrbot_resized if astrbot_resized.mode == 'RGBA' else None)
                    
                    logger.info(f"已将Astrbot.png添加到工作流帮助图片右下角，位置: ({x_position}, {y_position})")
                else:
                    logger.warning(f"Astrbot.png文件不存在: {astrbot_path}")
            except Exception as e:
                logger.error(f"添加Astrbot.png到工作流帮助图片失败: {e}")
            
            # 转换为bytes
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG', quality=95)
            img_buffer.seek(0)
            
            return img_buffer.getvalue()
            
        except Exception as e:
            logger.error(f"创建工作流帮助图片失败: {e}")
            return None

    def _generate_workflow_examples(self, prefix: str, config: Dict[str, Any]) -> List[str]:
        """生成workflow使用示例"""
        examples = []
        node_configs = config.get("node_configs", {})
        
        # 收集常用参数
        common_params = {}
        for node_id, node_config in node_configs.items():
            for param_name, param_info in node_config.items():
                if param_info.get("default") is not None:
                    common_params[param_name] = param_info["default"]
        
        # 基础示例（使用默认值）
        if common_params:
            example_parts = [prefix]
            # 选择几个常用参数作为示例
            sample_params = []
            for param_name, default_value in list(common_params.items())[:3]:
                sample_params.append(f"{param_name}:{default_value}")
            if sample_params:
                example_parts.extend(sample_params)
                examples.append(" ".join(example_parts))
        
        # 简单示例（仅前缀）
        examples.append(prefix)
        
        # 如果有提示词参数，添加提示词示例
        has_prompt = False
        for node_id, node_config in node_configs.items():
            for param_name, param_info in node_config.items():
                if "提示" in param_name or "prompt" in param_name.lower():
                    examples.append(f"{prefix} {param_name}:可爱女孩")
                    has_prompt = True
                    break
            if has_prompt:
                break
        
        return examples

    def _validate_config(self) -> None:
        # 注释掉ComfyUI服务器强制检测，允许插件在没有配置服务器的情况下加载
        # if not self.comfyui_servers:
        #     raise ValueError("未配置有效的ComfyUI服务器，请检查comfyui_url配置")
        required_configs = [
            ("ckpt_name", str), ("sampler_name", str), ("scheduler", str),
            ("cfg", (int, float)), ("default_width", int), ("default_height", int),
            ("txt2img_batch_size", int), ("img2img_batch_size", int), ("max_txt2img_batch", int),
            ("max_img2img_batch", int), ("max_task_queue", int), ("min_width", int), ("max_width", int),
            ("min_height", int), ("max_height", int), ("num_inference_steps", int),
            ("default_denoise", (int, float)), ("open_time_ranges", str),
            ("queue_check_delay", int), ("queue_check_interval", int), ("empty_queue_max_retry", int),
            ("lora_config", list), ("max_concurrent_tasks_per_user", int)
        ]
        
        # 验证自动保存配置
        if not isinstance(self.enable_auto_save, bool):
            raise ValueError(f"配置项错误：enable_auto_save（需为bool类型）")
#        if not isinstance(self.auto_save_dir, str) or not self.auto_save_dir.strip():
  #          raise ValueError(f"配置项错误：auto_save_dir（需为非空字符串）")
        

        for cfg_key, cfg_type in required_configs:
            cfg_value = getattr(self, cfg_key)
            if not isinstance(cfg_value, cfg_type):
                raise ValueError(f"配置项错误：{cfg_key}（需为{cfg_type.__name__}类型）")
            if cfg_key not in ["lora_config", "enable_translation"] and not cfg_value:
                raise ValueError(f"配置项错误：{cfg_key}（非空）")






        if not self.parsed_time_ranges:
            logger.warning(f"开放时间格式错误：{self.open_time_ranges}，已自动使用默认时间段")
            self.open_time_ranges = "7:00-8:00,11:00-14:00,17:00-24:00"
            self.parsed_time_ranges = self._parse_time_ranges()

    def _parse_time_ranges(self) -> List[Tuple[int, int]]:
        parsed = []
        ranges = [r.strip() for r in self.open_time_ranges.split(",") if r.strip()]
        for r in ranges:
            if "-" not in r:
                logger.warning(f"时间段格式错误：{r}（需为HH:MM-HH:MM格式）")
                continue
            start_str, end_str = r.split("-", 1)
            start_min = self._time_to_minutes(start_str)
            end_min = self._time_to_minutes(end_str)
            if start_min is None or end_min is None:
                logger.warning(f"时间解析失败：{r}")
                continue
            parsed.append((start_min, end_min))
        return parsed

    def _time_to_minutes(self, time_str: str) -> Optional[int]:
        try:
            if ":" in time_str:
                hh, mm = time_str.split(":", 1)
                hh = int(hh.strip())
                mm = int(mm.strip())
            else:
                hh = int(time_str.strip())
                mm = 0
            if not (0 <= hh <= 24 and 0 <= mm <= 59):
                return None
            return (hh % 24) * 60 + mm
        except (ValueError, IndexError):
            return None

    def _is_in_open_time(self) -> bool:
        if not self.parsed_time_ranges:
            return True
        now = datetime.now()
        current_min = now.hour * 60 + now.minute
        for start_min, end_min in self.parsed_time_ranges:
            if start_min <= end_min:
                if start_min <= current_min <= end_min:
                    return True
            else:
                if current_min >= start_min or current_min <= end_min:
                    return True
        return False

    def _get_open_time_desc(self) -> str:
        return "、".join([r.strip() for r in self.open_time_ranges.split(",") if r.strip()])

    async def _start_server_monitor(self) -> None:
        """服务器监控，负责检查健康状态并管理worker生命周期"""
        if self.server_monitor_running:
            return
        self.server_monitor_running = True
        logger.info(f"启动服务器监控，检查间隔：{self.server_check_interval}秒")
        
        # 初始启动所有健康服务器的worker
        for server in self.comfyui_servers:
            await self._manage_worker_for_server(server)
            
        while self.server_monitor_running:
            for server in self.comfyui_servers:
                if server.retry_after and datetime.now() < server.retry_after:
                    continue
                is_healthy = await self._check_server_health(server)
                async with self.server_state_lock:
                    if is_healthy != server.healthy:
                        server.healthy = is_healthy
                        status = "恢复正常" if is_healthy else "异常"
                        logger.info(f"服务器{server.name}({server.url})状态变化：{status}")
                        # 状态变化时管理worker
                        await self._manage_worker_for_server(server)
                    server.last_checked = datetime.now()
            await asyncio.sleep(self.server_check_interval)

    async def _manage_worker_for_server(self, server: ServerState) -> None:
        """根据服务器健康状态管理worker"""
        # 如果服务器健康且没有worker，启动新worker
        if server.healthy and (not server.worker or server.worker.done()):
            if server.worker and not server.worker.done():
                # 先标记为不健康，让worker优雅退出
                server.healthy = False
                # 给worker时间完成当前任务
                try:
                    await asyncio.wait_for(server.worker, timeout=30)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    # 超时或取消，直接取消worker
                    server.worker.cancel()
                    try:
                        await server.worker
                    except asyncio.CancelledError:
                        pass
            
            logger.info(f"为服务器{server.name}启动worker")
            server.worker = asyncio.create_task(
                self._worker_loop(f"worker-{server.server_id}", server)
            )
        
        # 如果服务器不健康但有活跃worker，不要立即终止
        # 让worker自己检测到不健康状态后优雅退出，完成当前任务
        elif not server.healthy and server.worker and not server.worker.done():
            logger.info(f"服务器{server.name}异常，worker将完成当前任务后退出")
            # 不立即取消worker，让它自己检测状态并退出
        
        # 检查是否所有服务器都不健康，如果是则清空任务队列
        await self._check_and_clear_queue_if_no_healthy_servers()
    
    async def _check_and_clear_queue_if_no_healthy_servers(self) -> None:
        """检查是否所有服务器都不健康，如果是则清空任务队列并通知用户"""
        has_healthy_server = any(server.healthy for server in self.comfyui_servers)
        
        if not has_healthy_server and self.task_queue.qsize() > 0:
            logger.warning(f"所有服务器均不健康，清空任务队列（{self.task_queue.qsize()}个任务）")
            # 清空队列中的所有任务并通知用户
            while not self.task_queue.empty():
                try:
                    task_data = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                    event = task_data.get("event")
                    user_id = task_data.get("user_id")
                    if event:
                        try:
                            await self._send_with_auto_recall(
                                event, 
                                event.plain_result(
                                    "\n❌ 所有ComfyUI服务器均不可用，任务已取消。\n"
                                    "请稍后重新提交任务。"
                                )
                            )
                        except Exception as e:
                            logger.error(f"通知用户任务取消失败：{e}")
                    # 减少用户任务计数
                    if user_id:
                        await self._decrement_user_task_count(user_id)
                    self.task_queue.task_done()
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    logger.error(f"清空任务队列时出错：{e}")

    async def _check_server_health(self, server: ServerState) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.url}/system_stats", timeout=10) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.warning(f"服务器{server.name}健康检查失败：{str(e)}")
            return False

    async def _get_server_system_info(self, server: ServerState) -> Optional[Dict[str, Any]]:
        """获取服务器系统信息"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.url}/system_stats", timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data
                    else:
                        logger.warning(f"获取服务器{server.name}系统信息失败，HTTP状态码：{resp.status}")
                        return None
        except Exception as e:
            logger.warning(f"获取服务器{server.name}系统信息异常：{str(e)}")
            return None



    async def _get_next_available_server(self) -> Optional[ServerState]:
        """轮询获取下一个可用服务器（多worker并发安全）"""
        if not self.comfyui_servers:
            return None
        
        # 使用两个锁来保护轮询索引和服务器状态的并发访问
        async with self.server_poll_lock, self.server_state_lock:
            # 先遍历所有服务器，优先选择空闲的
            for _ in range(len(self.comfyui_servers)):
                self.last_poll_index = (self.last_poll_index + 1) % len(self.comfyui_servers)
                server = self.comfyui_servers[self.last_poll_index]
                now = datetime.now()
                if (server.healthy and 
                    not server.busy and 
                    (not server.retry_after or now >= server.retry_after)):
                    # 抢到服务器后立即标记为忙碌（避免被其他worker抢走）
                    server.busy = True
                    return server
        return None

    def _get_any_healthy_server(self) -> Optional[ServerState]:
        if not self.comfyui_servers:
            return None
        for server in self.comfyui_servers:
            now = datetime.now()
            if server.healthy and (not server.retry_after or now >= server.retry_after):
                return server
        return None

    async def _mark_server_busy(self, server: ServerState, busy: bool) -> None:
        async with self.server_state_lock:
            server.busy = busy
            logger.debug(f"服务器{server.name}状态更新：{'忙碌' if busy else '空闲'}")

    async def _handle_server_failure(self, server: ServerState) -> None:
        async with self.server_state_lock:
            server.failure_count += 1
            logger.warning(f"服务器{server.name}失败次数：{server.failure_count}/{self.max_failure_count}")
            if server.failure_count >= self.max_failure_count:
                server.healthy = False
                server.retry_after = datetime.now() + timedelta(seconds=self.retry_delay)
                logger.warning(f"服务器{server.name}连续失败{self.max_failure_count}次，将在{self.retry_delay}秒后重试")
            else:
                server.retry_after = datetime.now() + timedelta(seconds=10)

    async def _reset_server_failure(self, server: ServerState) -> None:
        async with self.server_state_lock:
            if server.failure_count > 0:
                server.failure_count = 0
                server.retry_after = None
                if not server.healthy:
                    server.healthy = True
                    logger.info(f"服务器{server.name}恢复健康状态")

    async def _worker_loop(self, worker_name: str, server: ServerState) -> None:
        """单个worker的任务处理循环，绑定到特定服务器"""
        logger.info(f"{worker_name}已启动，绑定到服务器{server.name}，开始监听任务队列")
        try:
            while True:
                # 检查服务器是否健康，如果不健康则退出循环
                if not server.healthy:
                    logger.info(f"{worker_name}检测到服务器{server.name}不健康，停止接收新任务，退出循环")
                    return

                try:
                    # 等待获取任务（超时10秒，避免无限阻塞）
                    task_data = await asyncio.wait_for(self.task_queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    # 超时后检查服务器健康状态，不健康则退出
                    if not server.healthy:
                        logger.info(f"{worker_name}检测到服务器{server.name}不健康，停止接收新任务，退出循环")
                        return
                    continue

                try:
                    # 检查任务是否已经获取后服务器才变不健康
                    if not server.healthy:
                        logger.info(f"{worker_name}检测到服务器{server.name}不健康，将任务重新放回队列")
                        await self.task_queue.put(task_data)
                        return

                    # 使用绑定的服务器处理任务
                    await self._process_comfyui_task_with_server(server, **task_data)
                except Exception as e:
                    event = task_data.get("event")
                    user_id = task_data.get("user_id")
                    # 检查是否是服务器不健康导致的错误，如果是则将任务重新放回队列
                    if not server.healthy or ("服务器.*不健康" in str(e) or "故障转移" in str(e)):
                        logger.info(f"{worker_name}检测到服务器{server.name}故障，将任务重新放回队列")
                        await self.task_queue.put(task_data)
                        return
                    # 其他错误，通知用户
                    if event:
                        err_msg = f"\n图片生成失败：{str(e)[:1000]}"
                        await self._send_with_auto_recall(event, event.plain_result(err_msg))
                    logger.error(f"{worker_name}处理任务失败：{str(e)}")
                finally:
                    self.task_queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"{worker_name}被取消")
        except Exception as e:
            logger.error(f"{worker_name}异常退出：{str(e)}")
        finally:
            logger.info(f"{worker_name}已停止")

    def _parse_lora_params(self, params: List[str]) -> Tuple[List[str], List[Dict[str, Any]]]:
        lora_pattern = r"^lora:([^:!]+)(?::([0-9.]+))?(?:!([0-9.]+))?$"
        non_lora_params = []
        lora_list = []
        for param in params:
            match = re.match(lora_pattern, param.strip(), re.IGNORECASE)
            if not match:
                non_lora_params.append(param)
                continue
            user_input = match.group(1).strip().lower()
            strength_model_str = match.group(2)
            strength_clip_str = match.group(3)
            matched_lora = None
            exact_match_key = None
            fuzzy_match_keys = []
            if user_input in self.lora_name_map:
                exact_match_key = user_input
                matched_lora = self.lora_name_map[user_input]
            else:
                fuzzy_match_keys = [
                    key for key, (_, desc) in self.lora_name_map.items()
                    if user_input in key or user_input in desc.lower()
                ]
                if len(fuzzy_match_keys) == 1:
                    exact_match_key = fuzzy_match_keys[0]
                    matched_lora = self.lora_name_map[exact_match_key]
                elif len(fuzzy_match_keys) > 1:
                    matched_descs = [self.lora_name_map[key][1] for key in fuzzy_match_keys]
                    raise ValueError(
                        f"找到多个匹配的LoRA：{', '.join(matched_descs)}（输入关键词：{user_input}）"
                        f"\n请使用精确描述引用，可用LoRA：{', '.join([v[1] for v in self.lora_name_map.values() if v[1].lower() not in ['', None]])}"
                    )
            if not matched_lora:
                available_descs = list({v[1] for v in self.lora_name_map.values() if v[1].lower() not in ['', None]})
                raise ValueError(
                    f"未找到LoRA：{user_input}（可用LoRA描述：{', '.join(available_descs)}）"
                    f"\n提示：可直接使用描述引用，例如 lora:{available_descs[0]}"
                )
            filename, desc = matched_lora
            def parse_strength(s: Optional[str], default: float, desc: str) -> float:
                if s is None:
                    return default
                try:
                    val = float(s.strip())
                    if not (self.min_lora_strength <= val <= self.max_lora_strength):
                        raise ValueError(f"{desc}需在{self.min_lora_strength}-{self.max_lora_strength}之间")
                    return val
                except ValueError as e:
                    raise ValueError(f"LoRA强度解析失败：{desc}={s}（{str(e)}）")
            strength_model = parse_strength(strength_model_str, self.default_lora_strength_model, "model强度")
            strength_clip = parse_strength(strength_clip_str, self.default_lora_strength_clip, "CLIP强度")
            lora_list.append({
                "name": desc,
                "filename": filename,
                "strength_model": strength_model,
                "strength_clip": strength_clip
            })
        if len(lora_list) > self.max_lora_count:
            raise ValueError(f"单次最多支持{self.max_lora_count}个LoRA，当前传入{len(lora_list)}个")
        return non_lora_params, lora_list

    def _parse_model_params(self, params: List[str]) -> Tuple[List[str], Optional[str]]:
        """解析模型参数，返回剩余参数和选中的模型文件名"""
        model_pattern = r"^model:([^:]+)$"
        non_model_params = []
        selected_model = None
        
        for param in params:
            match = re.match(model_pattern, param.strip(), re.IGNORECASE)
            if not match:
                non_model_params.append(param)
                continue
                
            user_input = match.group(1).strip().lower()
            matched_model = None
            exact_match_key = None
            fuzzy_match_keys = []
            
            if user_input in self.model_name_map:
                exact_match_key = user_input
                matched_model = self.model_name_map[user_input]
            else:
                fuzzy_match_keys = [
                    key for key, (_, desc) in self.model_name_map.items()
                    if user_input in key or user_input in desc.lower()
                ]
                if len(fuzzy_match_keys) == 1:
                    exact_match_key = fuzzy_match_keys[0]
                    matched_model = self.model_name_map[exact_match_key]
                elif len(fuzzy_match_keys) > 1:
                    # 使用集合来确保模型描述的唯一性
                    matched_descs_set = set()
                    for key in fuzzy_match_keys:
                        desc = self.model_name_map[key][1]
                        matched_descs_set.add(desc)
                    matched_descs = list(matched_descs_set)
                    # 使用集合来确保可用模型描述的唯一性
                    available_descs_set = set()
                    for v in self.model_name_map.values():
                        if v[1] and v[1].lower() not in ['', None]:
                            available_descs_set.add(v[1])
                    available_descs = list(available_descs_set)
                    
                    raise ValueError(
                        f"找到多个匹配的模型：{', '.join(matched_descs)}（输入关键词：{user_input}）"
                        f"\n请使用精确描述引用，可用模型：{', '.join(available_descs)}"
                    )
            
            if not matched_model:
                # 使用集合来确保可用模型描述的唯一性
                available_descs_set = set()
                for v in self.model_name_map.values():
                    if v[1] and v[1].lower() not in ['', None]:
                        available_descs_set.add(v[1])
                available_descs = list(available_descs_set)
                raise ValueError(
                    f"未找到模型：{user_input}（可用模型描述：{', '.join(available_descs)}）"
                    f"\n提示：可直接使用描述引用，例如 model:{available_descs[0] if available_descs else '模型名'}"
                )
            
            filename, desc = matched_model
            selected_model = filename
            
        return non_model_params, selected_model

    def _is_model_not_found_error(self, error_msg: str) -> bool:
        """检查错误是否是模型不存在的错误"""
        return ("value_not_in_list" in error_msg and 
                "ckpt_name" in error_msg and 
                "not in" in error_msg)

    def _is_node_not_found_error(self, error_msg: str) -> bool:
        """检查错误是否是节点不存在的错误"""
        error_msg_lower = error_msg.lower()
        return (
            ("invalid_prompt" in error_msg or "does not exist" in error_msg_lower) and 
            "node" in error_msg_lower and
            ("does not exist" in error_msg_lower or "not found" in error_msg_lower)
        )

    def _extract_node_name_from_error(self, error_msg: str) -> Optional[str]:
        """从错误信息中提取节点名称"""
        import re
        
        # 尝试多种匹配模式
        patterns = [
            # 模式1: "node Eff. Loader SDXL does not exist"
            r"node\s+([^\s]+(?:\s+[^\s]+)*?)\s+does not exist",
            # 模式2: "Cannot execute because node Eff. Loader SDXL does not exist"
            r"because\s+node\s+([^\s]+(?:\s+[^\s]+)*?)\s+does not exist",
            # 模式3: 在message字段中查找节点名称
            r"node\s+([^.]+)\s+does not exist",
            # 模式4: 更宽松的匹配，包含特殊字符
            r"node\s+([^.\n]+?)\s+does not exist"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, error_msg, re.IGNORECASE)
            if match:
                node_name = match.group(1).strip()
                # 清理多余的空格和特殊字符
                node_name = re.sub(r'\s+', ' ', node_name).strip()
                if node_name:
                    return node_name
        
        return None

    def _extract_model_name_from_error(self, error_msg: str) -> Optional[str]:
        """从错误信息中提取模型名称"""
        import re
        # 匹配类似 "ckpt_name: '234334' not in" 的模式
        match = re.search(r"ckpt_name:\s*['\"]([^'\"]+)['\"]\s*not in", error_msg)
        if match:
            return match.group(1)
        return None

    def _get_available_models_from_error(self, error_msg: str) -> Optional[str]:
        """从错误信息中提取可用模型列表"""
        import re
        # 匹配类似 "not in ['model1.safetensors', 'model2.safetensors']" 的模式
        match = re.search(r"not in\s*\[([^\]]+)\]", error_msg)
        if match:
            models_str = match.group(1)
            # 移除引号并分割
            models = [model.strip().strip("'\"") for model in models_str.split(",")]
            return ", ".join(models)
        return None

    def _is_lora_not_found_error(self, error_msg: str) -> bool:
        """检查错误是否是LoRA不存在的错误"""
        return ("value_not_in_list" in error_msg and 
                "lora_name" in error_msg and 
                "not in" in error_msg)

    def _extract_lora_name_from_error(self, error_msg: str) -> Optional[str]:
        """从错误信息中提取LoRA名称"""
        import re
        # 匹配类似 "lora_name: '1234' not in" 的模式
        match = re.search(r"lora_name:\s*['\"]([^'\"]+)['\"]\s*not in", error_msg)
        if match:
            return match.group(1)
        return None

    def _get_available_loras_from_error(self, error_msg: str) -> Optional[str]:
        """从错误信息中提取可用LoRA列表"""
        import re
        # 匹配类似 "not in ['lora1.safetensors', 'lora2.safetensors']" 的模式
        match = re.search(r"not in\s*\[([^\]]+)\]", error_msg)
        if match:
            loras_str = match.group(1)
            # 移除引号并分割
            loras = [lora.strip().strip("'\"") for lora in loras_str.split(",")]
            return ", ".join(loras)
        return None

    def _build_comfyui_prompt(
        self,
        prompt: str,
        current_seed: int,
        current_width: int,
        current_height: int,
        image_filename: Optional[str] = None,
        denoise: float = 0.7,
        current_batch_size: int = 1,
        lora_list: List[Dict[str, Any]] = [],
        selected_model: Optional[str] = None
    ) -> Dict[str, Any]:
        nodes = {
            "6": {
                "inputs": {"text": prompt, "clip": ["30", 1]},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode (Positive Prompt)"}
            },
            "8": {
                "inputs": {"samples": ["31", 0], "vae": ["30", 2]},
                "class_type": "VAEDecode",
                "_meta": {"title": "VAE Decode"}
            },
            "30": {
                "inputs": {"ckpt_name": selected_model or self.ckpt_name},
                "class_type": "CheckpointLoaderSimple",
                "_meta": {"title": "Load Checkpoint"}
            },
            "31": {
                "inputs": {
                    "seed": current_seed,
                    "steps": self.num_inference_steps,
                    "cfg": self.cfg,
                    "sampler_name": self.sampler_name,
                    "scheduler": self.scheduler,
                    "denoise": denoise,
                    "model": ["30", 0],
                    "positive": ["6", 0],
                    "negative": ["33", 0],
                    "latent_image": ["54", 0] if image_filename else ["36", 0]
                },
                "class_type": "KSampler",
                "_meta": {"title": "KSampler"}
            },
            "33": {
                "inputs": {"text": self.negative_prompt, "clip": ["30", 1]},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode (Negative Prompt)"}
            }
        }
        
        # 根据配置决定是否添加图片混淆节点
        if self.enable_image_encrypt:
            # 启用图片混淆时的节点配置
            nodes["44"] = {
                "inputs": {"mode": "encrypt", "enable": True, "image": ["8", 0]},
                "class_type": "HilbertImageEncrypt",
                "_meta": {"title": "希尔伯特曲线图像加密"}
            }
            nodes["save_image_websocket_node"] = {
                "inputs": {"images": ["44", 0]},
                "class_type": "SaveImageWebsocket",
                "_meta": {"title": "保存图像（网络接口）"}
            }
            nodes["9"] = {
                "inputs": {"filename_prefix": "comfyui_gen", "images": ["44", 0]},
                "class_type": "SaveImage",
                "_meta": {"title": "Save Image"}
            }
        else:
            # 未启用图片混淆时，直接连接VAE解码输出到保存节点
            nodes["save_image_websocket_node"] = {
                "inputs": {"images": ["8", 0]},
                "class_type": "SaveImageWebsocket",
                "_meta": {"title": "保存图像（网络接口）"}
            }
            nodes["9"] = {
                "inputs": {"filename_prefix": "comfyui_gen", "images": ["8", 0]},
                "class_type": "SaveImage",
                "_meta": {"title": "Save Image"}
            }
        if lora_list:
            current_model_node = "30"
            current_clip_node = "30"
            current_model_output_idx = 0
            current_clip_output_idx = 1
            lora_node_ids = [str(100 + i) for i in range(len(lora_list))]
            for idx, lora in enumerate(lora_list):
                node_id = lora_node_ids[idx]
                nodes[node_id] = {
                    "inputs": {
                        "lora_name": lora["filename"],
                        "strength_model": lora["strength_model"],
                        "strength_clip": lora["strength_clip"],
                        "model": [current_model_node, current_model_output_idx],
                        "clip": [current_clip_node, current_clip_output_idx]
                    },
                    "class_type": "LoraLoader",
                    "_meta": {"title": f"Load LoRA: {lora['name']}"}
                }
                current_model_node = node_id
                current_clip_node = node_id
                current_model_output_idx = 0
                current_clip_output_idx = 1
            nodes["31"]["inputs"]["model"] = [current_model_node, current_model_output_idx]
            nodes["6"]["inputs"]["clip"] = [current_clip_node, current_clip_output_idx]
            nodes["33"]["inputs"]["clip"] = [current_clip_node, current_clip_output_idx]
        if image_filename:
            nodes.update({
                "51": {
                    "inputs": {"image": image_filename},
                    "class_type": "LoadImage",
                    "_meta": {"title": "加载图像"}
                },
                "53": {
                    "inputs": {
                        "max_width": 1600,
                        "max_height": 1600,
                        "min_width": 800,
                        "min_height": 800,
                        "image": ["51", 0]
                    },
                    "class_type": "ImageCompressor",
                    "_meta": {"title": "图像压缩器"}
                },
                "54": {
                    "inputs": {
                        "pixels": ["53", 0],
                        "vae": ["30", 2]
                    },
                    "class_type": "VAEEncode",
                    "_meta": {"title": "VAE编码"}
                },
                "55": {
                    "inputs": {
                        "samples": ["54", 0],
                        "amount": current_batch_size
                    },
                    "class_type": "RepeatLatentBatch",
                    "_meta": {"title": "批量复制Latent"}
                }
            })
            nodes["31"]["inputs"]["positive"] = ["6", 0]
            nodes["31"]["inputs"]["negative"] = ["33", 0]
            nodes["31"]["inputs"]["latent_image"] = ["55", 0]
        else:
            nodes["36"] = {
                "inputs": {
                    "width": current_width,
                    "height": current_height,
                    "batch_size": current_batch_size
                },
                "class_type": "EmptyLatentImage",
                "_meta": {"title": "空Latent图像"}
            }
        return nodes

    async def _check_user_task_limit(self, user_id: str) -> bool:
        """检查用户是否超过任务数限制"""
        async with self.user_task_lock:
            current_count = self.user_task_counts.get(user_id, 0)
            return current_count < self.max_concurrent_tasks_per_user

    async def _increment_user_task_count(self, user_id: str) -> bool:
        """增加用户任务计数，返回是否成功"""
        async with self.user_task_lock:
            current_count = self.user_task_counts.get(user_id, 0)
            if current_count < self.max_concurrent_tasks_per_user:
                self.user_task_counts[user_id] = current_count + 1
                logger.debug(f"用户 {user_id} 任务计数增加到 {self.user_task_counts[user_id]}")
                return True
            return False

    async def _decrement_user_task_count(self, user_id: str) -> None:
        """减少用户任务计数"""
        async with self.user_task_lock:
            current_count = self.user_task_counts.get(user_id, 0)
            if current_count > 0:
                self.user_task_counts[user_id] = current_count - 1
                logger.debug(f"用户 {user_id} 任务计数减少到 {self.user_task_counts[user_id]}")
                # 如果计数为0，从字典中删除以节省内存
                if self.user_task_counts[user_id] == 0:
                    del self.user_task_counts[user_id]

    async def _process_comfyui_task_with_server(self, server: ServerState,** task_data) -> None:
        """使用指定服务器处理任务，带重试机制和故障转移"""
        max_retries = 2  # 单服务器重试次数
        retry_count = 0
        last_error = None
        user_id = task_data.get("user_id")  # 获取用户ID
        is_workflow = task_data.get("is_workflow", False)
        is_web_api = task_data.get("is_web_api", False)
        is_llm_tool = task_data.get("is_llm_tool", False)
        task_id = task_data.get("task_id")
        should_retry_on_other_server = False  # 是否需要在其他服务器上重试
        
        try:
            while retry_count <= max_retries:
                try:
                    # 检查服务器是否健康
                    if not server.healthy:
                        raise Exception(f"服务器{server.name}已不健康，无法处理任务")
                        
                    # 处理任务
                    if is_web_api:
                        # Web API任务处理
                        await self._process_web_api_task(server, task_data)
                    elif is_workflow:
                        # 过滤掉 is_workflow 参数，避免传递给 _process_workflow_task
                        workflow_task_data = {k: v for k, v in task_data.items() if k != 'is_workflow'}
                        await self._process_workflow_task(server, **workflow_task_data)
                    elif is_llm_tool:
                        # 过滤掉 is_llm_tool 参数，避免传递给 _process_llm_tool_task
                        llm_task_data = {k: v for k, v in task_data.items() if k != 'is_llm_tool'}
                        await self._process_llm_tool_task(server, **llm_task_data)
                    else:
                        await self._process_comfyui_task(server, **task_data)
                    await self._reset_server_failure(server)
                    return
                except Exception as e:
                    last_error = e
                    error_msg = str(e)
                    
                    # 检查是否是永久性错误（用户端问题，不需要重试服务器）
                    if ("URL已过期" in error_msg or "下载失败" in error_msg or 
                        "文件过小" in error_msg or "上传失败" in error_msg or
                        "PERMANENT_ERROR:" in error_msg):
                        # 这些是用户端问题，直接抛出，不要重试或转移到其他服务器
                        # 清理PERMANENT_ERROR前缀
                        if "PERMANENT_ERROR:" in error_msg:
                            error_msg = error_msg.replace("PERMANENT_ERROR:", "")
                            raise Exception(error_msg)
                        raise
                    
                    # 检查是否是模型不存在的错误，如果是则不重试
                    if self._is_model_not_found_error(error_msg):
                        model_name = self._extract_model_name_from_error(error_msg)
                        available_models = self._get_available_models_from_error(error_msg)
                        if model_name and available_models:
                            raise Exception(f"模型「{model_name}」未安装到服务器【{server.name}】上，请安装该模型。\n可用模型：{available_models}")
                        else:
                            raise Exception(f"指定的模型未安装到服务器【{server.name}】上，请检查模型配置。")
                    
                    # 检查是否是LoRA不存在的错误，如果是则不重试
                    if self._is_lora_not_found_error(error_msg):
                        lora_name = self._extract_lora_name_from_error(error_msg)
                        available_loras = self._get_available_loras_from_error(error_msg)
                        if lora_name and available_loras:
                            # 提取可用的LoRA描述名称
                            available_lora_descs = []
                            for lora_file in available_loras.split(", "):
                                lora_file = lora_file.strip()
                                # 从self.lora_name_map中查找对应的描述
                                for filename, desc in self.lora_name_map.values():
                                    if filename == lora_file and desc:
                                        available_lora_descs.append(desc)
                                        break
                                else:
                                    # 如果找不到描述，使用文件名
                                    available_lora_descs.append(lora_file)
                            
                            error_msg = f"LoRA「{lora_name}」未安装到服务器【{server.name}】上。\n"
                            if available_lora_descs:
                                error_msg += f"可用LoRA：{', '.join(available_lora_descs)}\n"
                                error_msg += f"使用方法：lora:<LoRA描述名>（如：lora:{available_lora_descs[0]}）"
                            else:
                                error_msg += "请检查LoRA配置。"
                            raise Exception(error_msg)
                        else:
                            raise Exception(f"指定的LoRA未安装到服务器【{server.name}】上，请检查LoRA配置。")
                    
                    # 检查是否是节点不存在的错误，如果是则不重试
                    if self._is_node_not_found_error(error_msg):
                        node_name = self._extract_node_name_from_error(error_msg)
                        if node_name:
                            raise Exception(f"节点「{node_name}」未安装到服务器【{server.name}】上，请安装该节点。")
                        else:
                            raise Exception(f"指定的节点未安装到服务器【{server.name}】上，请检查节点配置。")
                    
                    logger.error(f"服务器{server.name}处理任务失败（重试{retry_count}/{max_retries}）：{error_msg}")
                    await self._handle_server_failure(server)
                    
                    # 如果服务器已被标记为不健康，不再重试当前服务器，标记需要转移到其他服务器
                    if not server.healthy:
                        should_retry_on_other_server = True
                        break
                        
                    retry_count += 1
                    if retry_count <= max_retries:
                        await asyncio.sleep(2)  # 短暂延迟后重试
                
            # 所有重试失败
            if last_error:
                filtered_error = self._filter_server_urls(str(last_error)[:1000])
                raise Exception(f"服务器{server.name}处理任务失败：{filtered_error}")
            else:
                raise Exception(f"服务器{server.name}处理任务失败，原因未知")
                
        except Exception as e:
            # 如果需要转移到其他服务器，将任务重新放回队列
            if should_retry_on_other_server:
                logger.info(f"服务器{server.name}不健康，将任务重新放回队列供其他服务器处理")
                # 将任务重新放回队列（不减少用户任务计数，因为任务还在队列中）
                await self.task_queue.put(task_data)
            else:
                # 其他错误，正常抛出异常
                raise
        finally:
            # 如果不是故障转移的情况，才减少用户任务计数和释放服务器
            if not should_retry_on_other_server:
                # 无论成功还是失败，都确保减少用户任务计数
                if user_id:
                    await self._decrement_user_task_count(user_id)
                # 确保释放服务器
                await self._mark_server_busy(server, False)

    async def _process_web_api_task(self, server: ServerState, task_data: Dict[str, Any]) -> None:
        """处理Web API任务"""
        task_id = task_data.get("task_id")
        task_type = task_data.get("type")
        user_id = task_data.get("user_id")
        
        try:
            if task_type == "txt2img":
                result = await self._process_web_api_txt2img(server, task_data)
            elif task_type == "img2img":
                result = await self._process_web_api_img2img(server, task_data)
            elif task_type == "workflow":
                result = await self._process_web_api_workflow(server, task_data)
            else:
                result = {
                    'error': f'不支持的任务类型: {task_type}'
                }
            
            # 存储任务结果
            if not hasattr(self, '_task_results'):
                self._task_results = {}
            self._task_results[task_id] = result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"处理Web API任务失败: {error_msg}")
            
            # 尝试解析JSON格式的错误信息
            try:
                if error_msg.startswith('{') and error_msg.endswith('}'):
                    error_json = json.loads(error_msg)
                    if 'error' in error_json:
                        error_details = error_json['error']
                        if isinstance(error_details, dict):
                            error_type = error_details.get('type', '')
                            error_message = error_details.get('message', '')
                            node_errors = error_details.get('node_errors', {})
                            
                            # 如果有节点错误，提取第一个节点的错误
                            if node_errors and isinstance(node_errors, dict):
                                first_node_id = list(node_errors.keys())[0]
                                first_node_error = node_errors[first_node_id]
                                if 'errors' in first_node_error and first_node_error['errors']:
                                    first_error = first_node_error['errors'][0]
                                    error_type = first_error.get('type', error_type)
                                    error_message = first_error.get('message', error_message)
                            
                            # 更新错误消息用于后续处理
                            if error_type or error_message:
                                error_msg = f"{error_type}: {error_message}" if error_type and error_message else (error_type or error_message)
            except:
                # 如果JSON解析失败，使用原始错误消息
                pass
            
            # 检查是否是模型不存在的错误，提供更友好的错误信息
            if self._is_model_not_found_error(error_msg):
                model_name = self._extract_model_name_from_error(error_msg)
                available_models = self._get_available_models_from_error(error_msg)
                if model_name and available_models:
                    friendly_error = f"❌ 模型错误：模型「{model_name}」未安装到服务器【{server.name}】\n\n📋 可用模型列表：\n{available_models}\n\n💡 请使用上述可用模型之一重试。"
                else:
                    friendly_error = f"❌ 模型错误：指定的模型未安装到服务器【{server.name}】\n\n💡 请检查模型配置或联系管理员。"
                self._task_results[task_id] = {'error': friendly_error}
            # 检查是否是LoRA不存在的错误
            elif self._is_lora_not_found_error(error_msg):
                lora_name = self._extract_lora_name_from_error(error_msg)
                available_loras = self._get_available_loras_from_error(error_msg)
                if lora_name and available_loras:
                    friendly_error = f"❌ LoRA错误：LoRA「{lora_name}」未安装到服务器【{server.name}】\n\n📋 可用LoRA列表：\n{available_loras}\n\n💡 请使用上述可用LoRA之一重试，或移除LoRA参数。"
                else:
                    friendly_error = f"❌ LoRA错误：指定的LoRA未安装到服务器【{server.name}】\n\n💡 请检查LoRA配置或移除LoRA参数重试。"
                self._task_results[task_id] = {'error': friendly_error}
            # 检查是否是节点不存在的错误
            elif self._is_node_not_found_error(error_msg):
                node_name = self._extract_node_name_from_error(error_msg)
                if node_name:
                    friendly_error = f"❌ 节点错误：节点「{node_name}」不存在于服务器【{server.name}】\n\n💡 请检查ComfyUI是否安装了相应的自定义节点。"
                else:
                    friendly_error = f"❌ 节点错误：工作流中的某些节点不存在于服务器【{server.name}】\n\n💡 请检查ComfyUI是否安装了所需的自定义节点。"
                self._task_results[task_id] = {'error': friendly_error}
            # 检查是否是无效提示词错误
            elif "invalid_prompt" in error_msg.lower() or "prompt" in error_msg.lower() and "validation" in error_msg.lower():
                friendly_error = f"❌ 提示词错误：输入的提示词格式不正确或包含无效内容\n\n🖥️ 服务器：{server.name}\n\n💡 请检查提示词格式，避免使用特殊字符或过长的文本。"
                self._task_results[task_id] = {'error': friendly_error}
            else:
                # 其他错误，保持原样但提供更多上下文
                friendly_error = f"❌ 任务失败：{error_msg}\n\n🖥️ 服务器：{server.name}\n\n💡 如果问题持续存在，请检查参数设置或联系管理员。"
                self._task_results[task_id] = {'error': friendly_error}
            
            # 确保任务结果被存储
            if not hasattr(self, '_task_results'):
                self._task_results = {}
            if task_id not in self._task_results:
                self._task_results[task_id] = {'error': f'任务处理失败：{error_msg}'}

    async def _process_web_api_txt2img(self, server: ServerState, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理Web API文生图任务"""
        prompt = task_data.get("prompt")
        width = task_data.get("width")
        height = task_data.get("height")
        batch_size = task_data.get("batch_size")
        seed = task_data.get("seed")
        model = task_data.get("model")
        lora_list = task_data.get("lora_list", [])
        user_id = task_data.get("user_id")
        
        # 构建ComfyUI提示词
        comfy_prompt = self._build_comfyui_prompt(
            prompt, seed, width, height, None, 1, batch_size, lora_list, model
        )
        
        # 发送到ComfyUI
        prompt_id = await self._send_comfyui_prompt(server, comfy_prompt)
        
        # 轮询任务状态
        history_data = await self._poll_task_status(server, prompt_id)
        if not history_data or history_data.get("status", {}).get("completed") is False:
            raise Exception("任务超时或未完成（超时10分钟）")
        
        # 提取图片信息
        image_info_list = self._extract_batch_image_info(history_data)
        if not image_info_list or len(image_info_list) == 0:
            raise Exception("未从ComfyUI历史数据中找到图片")
        
        # 获取图片URL（使用代理模式）
        image_urls = []
        for image_info in image_info_list:
            # 先保存文件，获取保存后的文件名
            saved_filename = await self._save_image_locally(
                server, 
                image_info["filename"], 
                "aimg", 
                user_id,
                image_info.get("subfolder", ""),
                image_info.get("type", "output")
            )
            
            # 如果启用了保存，使用保存后的文件名；否则使用原始文件名
            filename_for_url = saved_filename if saved_filename else image_info["filename"]
            image_url = await self._get_image_url(
                server=server, 
                filename=filename_for_url, 
                use_proxy=True,
                subfolder=image_info.get("subfolder", ""),
                file_type=image_info.get("type", "output")
            )
            image_urls.append(image_url)
            
            # 记录图片生成（如果已经保存过就不重复记录）
            if saved_filename:
                await self._record_image_generation(saved_filename, user_id)
            else:
                await self._record_image_generation(image_info["filename"], user_id)
        
        return {
            'success': True,
            'task_id': task_data.get("task_id"),
            'prompt': prompt,
            'width': width,
            'height': height,
            'seed': seed,
            'model': model,
            'lora_count': len(lora_list),
            'image_count': len(image_urls),
            'images': image_urls
        }

    async def _process_web_api_img2img(self, server: ServerState, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理Web API图生图任务"""
        prompt = task_data.get("prompt")
        image_base64 = task_data.get("image")
        denoise = task_data.get("denoise")
        batch_size = task_data.get("batch_size")
        seed = task_data.get("seed")
        model = task_data.get("model")
        lora_list = task_data.get("lora_list", [])
        user_id = task_data.get("user_id")
        
        # 直接上传图片到ComfyUI服务器
        image_data = base64.b64decode(image_base64)
        
        # 使用内存中的数据上传到ComfyUI
        form_data = aiohttp.FormData()
        form_data.add_field("image", image_data, filename="upload.png", content_type="image/png")
        
        upload_url = f"{server.url}/upload/image"
        async with aiohttp.ClientSession() as session:
            async with session.post(upload_url, data=form_data) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    filtered_resp = self._filter_server_urls(resp_text[:50])
                    raise Exception(f"图片上传失败（HTTP {resp.status}）：{filtered_resp}")
                resp_data = await resp.json()
                image_filename = resp_data.get("name", "")
                if not image_filename:
                    raise Exception("图片上传成功但未返回文件名")
        
        try:
            # 构建ComfyUI提示词
            comfy_prompt = self._build_comfyui_prompt(
                prompt, seed, 512, 512, image_filename, denoise, batch_size, lora_list, model
            )
            
            # 发送到ComfyUI
            prompt_id = await self._send_comfyui_prompt(server, comfy_prompt)
            
            # 轮询任务状态
            history_data = await self._poll_task_status(server, prompt_id)
            if not history_data or history_data.get("status", {}).get("completed") is False:
                raise Exception("任务超时或未完成（超时10分钟）")
            
            # 提取图片信息
            image_info_list = self._extract_batch_image_info(history_data)
            if not image_info_list or len(image_info_list) == 0:
                raise Exception("未从ComfyUI历史数据中找到图片")
            
            # 获取图片URL（使用代理模式）
            image_urls = []
            for image_info in image_info_list:
                # 先保存文件，获取保存后的文件名
                saved_filename = await self._save_image_locally(
                    server, 
                    image_info["filename"], 
                    "img2img", 
                    user_id,
                    image_info.get("subfolder", ""),
                    image_info.get("type", "output")
                )
                
                # 如果启用了保存，使用保存后的文件名；否则使用原始文件名
                filename_for_url = saved_filename if saved_filename else image_info["filename"]
                image_url = await self._get_image_url(
                server=server, 
                filename=filename_for_url, 
                use_proxy=True,
                subfolder=image_info.get("subfolder", ""),
                file_type=image_info.get("type", "output")
            )
                image_urls.append(image_url)
                
                # 记录图片生成（如果已经保存过就不重复记录）
                if saved_filename:
                    await self._record_image_generation(saved_filename, user_id)
                else:
                    await self._record_image_generation(image_info["filename"], user_id)
            
            return {
                'success': True,
                'task_id': task_data.get("task_id"),
                'prompt': prompt,
                'denoise': denoise,
                'seed': seed,
                'model': model,
                'lora_count': len(lora_list),
                'image_count': len(image_urls),
                'images': image_urls
            }
            
        finally:
            # 清理临时文件
            try:
                os.unlink(image_filename)
            except:
                pass

    async def _process_web_api_workflow(self, server: ServerState, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理Web API Workflow任务"""
        workflow_name = task_data.get("workflow_name")
        params = task_data.get("params")
        image_base64 = task_data.get("image")
        user_id = task_data.get("user_id")
        
        workflow_info = self.workflows[workflow_name]
        config = workflow_info["config"]
        workflow_data = workflow_info["workflow"]
        
        # 构建最终workflow
        final_workflow = copy.deepcopy(workflow_data)
        
        # 注入主配置
        self._inject_main_config(final_workflow, workflow_name)
        
        # 注入用户参数
        self._inject_user_params(final_workflow, config, params)
        
        # 处理图片输入（如果有）
        if image_base64:
            # 直接上传图片到ComfyUI服务器，而不是创建本地临时文件
            image_data = base64.b64decode(image_base64)
            
            # 使用内存中的数据上传到ComfyUI
            form_data = aiohttp.FormData()
            form_data.add_field("image", image_data, filename="upload.png", content_type="image/png")
            
            upload_url = f"{server.url}/upload/image"
            async with aiohttp.ClientSession() as session:
                async with session.post(upload_url, data=form_data) as resp:
                    if resp.status != 200:
                        resp_text = await resp.text()
                        filtered_resp = self._filter_server_urls(resp_text[:50])
                        raise Exception(f"图片上传失败（HTTP {resp.status}）：{filtered_resp}")
                    resp_data = await resp.json()
                    image_filename = resp_data.get("name", "")
                    if not image_filename:
                        raise Exception("图片上传成功但未返回文件名")
            
            # 将图片注入到workflow中
            self._inject_image_to_workflow(final_workflow, config, image_filename)
        
        # 发送到ComfyUI
        prompt_id = await self._send_comfyui_prompt(server, final_workflow)
        
        # 轮询任务状态
        history_data = await self._poll_task_status(server, prompt_id)
        if not history_data or history_data.get("status", {}).get("completed") is False:
            raise Exception("任务超时或未完成（超时10分钟）")
        
        # 提取输出图片
        output_nodes = config.get("output_nodes", [])
        output_mappings = config.get("output_mappings", {})
        image_urls = []
        
        for node_id in output_nodes:
            if node_id in output_mappings:
                outputs = history_data.get("outputs", {})
                node_output = outputs.get(node_id)
                if node_output and node_output.get("images"):
                    for image_info in node_output["images"]:
                        # 先保存图片，获取保存后的文件名
                        saved_filename = await self._save_image_locally(
                            server, 
                            image_info["filename"], 
                            f"workflow_{workflow_name}", 
                            user_id,
                            image_info.get("subfolder", ""),
                            image_info.get("type", "output")
                        )
                        
                        # 如果启用了保存，使用保存后的文件名；否则使用原始文件名
                        filename_for_url = saved_filename if saved_filename else image_info["filename"]
                        image_url = await self._get_image_url(
                server=server, 
                filename=filename_for_url, 
                use_proxy=True,
                subfolder=image_info.get("subfolder", ""),
                file_type=image_info.get("type", "output")
            )
                        image_urls.append(image_url)
                        
                        # 记录图片生成（如果已经保存过就不重复记录）
                        if saved_filename:
                            await self._record_image_generation(saved_filename, user_id)
                        else:
                            await self._record_image_generation(image_info["filename"], user_id)
        
        return {
            'success': True,
            'task_id': task_data.get("task_id"),
            'workflow_name': workflow_name,
            'params': params,
            'image_count': len(image_urls),
            'images': image_urls
        }

    def _truncate_prompt(self, prompt: str) -> str:
        max_display_len = 8
        if len(prompt) > max_display_len:
            return prompt[:max_display_len] + "..."
        return prompt

    async def _process_llm_tool_task(
        self,
        server: ServerState,
        event: AstrMessageEvent,
        prompt: str,
        current_seed: int,
        current_width: int,
        current_height: int,
        current_batch_size: int = 1,
        lora_list: List[Dict[str, Any]] = [],
        selected_model: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> None:
        """处理LLM工具任务"""
        try:
            # 构建ComfyUI提示词
            comfy_prompt = self._build_comfyui_prompt(
                prompt, current_seed, current_width, current_height, None, 1, current_batch_size, lora_list, selected_model
            )
            
            # 发送到ComfyUI
            prompt_id = await self._send_comfyui_prompt(server, comfy_prompt)
            
            # 轮询任务状态
            history_data = await self._poll_task_status(server, prompt_id)
            if not history_data or history_data.get("status", {}).get("completed") is False:
                raise Exception("任务超时或未完成")
            
            # 提取图片信息
            image_info_list = self._extract_batch_image_info(history_data)
            if not image_info_list or len(image_info_list) == 0:
                raise Exception("未找到生成的图片")
            
            # 下载第一张图片
            image_info = image_info_list[0]
            filename = image_info["filename"]
            subfolder = image_info.get("subfolder", "")
            file_type = image_info.get("type", "output")
            
            image_url = await self._get_image_url(
                server, 
                filename, 
                use_proxy=False,
                subfolder=subfolder,
                file_type=file_type
            )
            
            # 使用统一的方法静悄悄保存文件
            saved_filename = await self._save_image_locally(
                server, filename, prompt, user_id, subfolder, file_type
            )
            
            # 下载图片到临时位置用于发送
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        
                        # 创建临时文件用于发送
                        current_file_path = os.path.abspath(__file__)
                        current_directory = os.path.dirname(current_file_path)
                        temp_dir = os.path.join(current_directory, 'temp')
                        os.makedirs(temp_dir, exist_ok=True)
                        img_path = os.path.join(temp_dir, f'llm_temp_{filename}')
                        
                        with open(img_path, 'wb') as fp:
                            fp.write(image_data)
                        
                        # 返回图片
                        chain = [Image.fromFileSystem(img_path)]
                        
                        # 创建临时文件清理任务
                        asyncio.create_task(self._cleanup_temp_file(img_path))
                        # 先发送消息给用户
                        await event.send(event.chain_result(chain))
                        
        except Exception as e:
            err_msg = f"LLM工具图片生成失败：{str(e)}"
            await self._send_with_auto_recall(event, event.plain_result(err_msg))
            logger.error(f"LLM tool task error: {e}")
            raise

    async def _process_comfyui_task(
        self,
        server: ServerState,
        event: AstrMessageEvent,
        prompt: str,
        current_seed: int,
        current_width: int,
        current_height: int,
        image_filename: Optional[str] = None,
        img_path: Optional[str] = None,
        denoise: float = 1,
        current_batch_size: int = 1,
        lora_list: List[Dict[str, Any]] = [],
        selected_model: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> None:
        # 如果有本地图片路径，先上传到ComfyUI服务器
        image_filename = image_filename  # 保持默认值（可能从Web API传来）
        local_img_path = img_path  # 新增：本地图片路径

        if local_img_path:
            # 图生图任务：上传本地图片到ComfyUI服务器
            try:
                # 验证文件是否存在
                if not os.path.exists(local_img_path):
                    raise Exception(f"PERMANENT_ERROR:图片文件不存在：{local_img_path}")
                file_size = os.path.getsize(local_img_path)
                if file_size < 10240:  # 小于10KB
                    logger.warning(f"图片文件过小（{file_size}字节），可能已损坏")
                    raise Exception(f"PERMANENT_ERROR:图片文件过小，可能已损坏")

                # 上传图片到ComfyUI服务器
                image_filename = await self._upload_image_to_comfyui(server, local_img_path)

                if not image_filename:
                    raise Exception(f"PERMANENT_ERROR:图片上传失败")

                logger.info(f"成功上传图片到ComfyUI服务器: {image_filename}")

                # 上传完成后，如果图片是临时文件，清理临时文件
                if local_img_path and os.path.exists(local_img_path):
                    # 检查是否是临时文件（不包含img2img_inputs路径）
                    if "img2img_inputs" not in local_img_path:
                        try:
                            # 延迟清理临时文件
                            asyncio.create_task(self._cleanup_temp_file(local_img_path))
                            logger.info(f"已安排清理临时图片文件: {local_img_path}")
                        except Exception as e:
                            logger.warning(f"安排清理临时文件失败: {e}")

            except Exception as e:
                error_msg = str(e)
                # 检查是否是永久性错误（不需要重试的错误）
                if error_msg.startswith("PERMANENT_ERROR:"):
                    # 直接抛出永久性错误，不要重试
                    raise Exception(error_msg.replace("PERMANENT_ERROR:", ""))
                else:
                    raise Exception(f"图片上传失败：{error_msg[:1000]}")

        comfy_prompt = self._build_comfyui_prompt(
            prompt, current_seed, current_width, current_height, image_filename, denoise, current_batch_size, lora_list, selected_model
        )
        prompt_id = await self._send_comfyui_prompt(server, comfy_prompt)
        if image_filename:
            task_type = "图生图"
            extra_info = f"噪声系数：{denoise}\n批量数：{current_batch_size}\n图片：{image_filename[:20]}..."
        else:
            task_type = "文生图"
            extra_info = f"分辨率：{current_width}x{current_height}\n批量数：{current_batch_size}"
        if lora_list:
            lora_info = "\n使用LoRA：" + " | ".join([
                f"{lora['name']}（model:{lora['strength_model']}, clip:{lora['strength_clip']}）"
                for lora in lora_list
            ])
            extra_info += lora_info
  #      await self._send_with_auto_recall(event, event.plain_result(
   #         f"\n{task_type}任务已下发至服务器【{server.name}】：\n提示词：{self._truncate_prompt(prompt)}\nSeed：{current_seed}\n{extra_info}\n任务ID：{prompt_id[:8]}..."
  #      ))
        history_data = await self._poll_task_status(server, prompt_id)
        if not history_data or history_data.get("status", {}).get("completed") is False:
            raise Exception("任务超时或未完成（超时10分钟）")
        image_info_list = self._extract_batch_image_info(history_data)
        if not image_info_list or len(image_info_list) == 0:
            raise Exception("未从ComfyUI历史数据中找到图片或3D模型")
        
        # 检查是否为3D模型文件
        is_3d_model = any(info.get("filename", "").lower().endswith('.glb') for info in image_info_list)
        
        image_urls = []
        model_3d_files = []  # 存储3D模型文件信息
        
        for idx, image_info in enumerate(image_info_list, 1):
            filename = image_info["filename"]
            subfolder = image_info.get("subfolder", "")
            
            # 检查是否为3D模型文件
            if filename.lower().endswith('.glb'):
                # 构建3D模型文件URL
                if subfolder:
                    model_3d_url = f"{server.url}/view?filename={filename}&type=output&subfolder={subfolder}"
                else:
                    model_3d_url = f"{server.url}/view?filename={filename}&type=output"
                
                model_3d_files.append({
                    "filename": filename,
                    "url": model_3d_url,
                    "subfolder": subfolder
                })
            else:
                # 处理图片文件
                image_url = await self._get_image_url(
                    server, 
                    filename, 
                    use_proxy=False,
                    subfolder=subfolder,
                    file_type=image_info.get("type", "output")
                )
                image_urls.append((idx, image_url))
                
                # 静悄悄保存文件
                await self._save_image_locally(
                    server, 
                    filename, 
                    prompt, 
                    user_id or "",
                    subfolder,
                    image_info.get("type", "output")
                )
        # 构建结果消息
        result_parts = []
        if image_urls:
            result_parts.append(f"{len(image_urls)}张图片")
        if model_3d_files:
            result_parts.append(f"{len(model_3d_files)}个3D模型")
        
        if image_filename:
            result_text = f"提示词：{self._truncate_prompt(prompt)}\nSeed：{current_seed}\n噪声系数：{denoise}\n批量数：{current_batch_size}\n{task_type}生成完成！"
        else:
            result_text = f"提示词：{self._truncate_prompt(prompt)}\nSeed：{current_seed}\n分辨率：{current_width}x{current_height}\n批量数：{current_batch_size}\n{task_type}生成完成！"
        if lora_list:
            lora_result_info = "\n使用LoRA：" + " | ".join([
                f"{lora['name']}（model:{lora['strength_model']}, clip:{lora['strength_clip']}）"
                for lora in lora_list
            ])
            result_text += lora_result_info
        
        # 构建合并的消息链
        merged_chain = []
        merged_chain.append(Plain(result_text))
        merged_chain.append(Plain(f"\n\n共{'、'.join(result_parts)}："))
        
        # 添加图片
        for idx, img_url in image_urls:
            merged_chain.append(Plain(f"\n\n第{idx}/{len(image_urls) + len(model_3d_files)}张："))
            merged_chain.append(Image.fromURL(img_url))
        
        # 添加3D模型（作为文件上传）
        if model_3d_files:
            for idx, model_3d_info in enumerate(model_3d_files):
                model_3d_idx = len(image_urls) + idx + 1
                merged_chain.append(Plain(f"\n\n第{model_3d_idx}/{len(image_urls) + len(model_3d_files)}个3D模型：正在上传文件..."))
                
                # 下载并上传3D模型文件
                try:
                    model_3d_path = await self._download_and_upload_3d_model(event, server, model_3d_info)
                    if model_3d_path:
                        merged_chain.append(Plain(f"\n✅ 3D模型{model_3d_idx}已上传为文件"))
                    else:
                        merged_chain.append(Plain(f"\n❌ 3D模型{model_3d_idx}上传失败"))
                except Exception as e:
                    logger.error(f"3D模型上传失败: {e}")
                    merged_chain.append(Plain(f"\n❌ 3D模型{model_3d_idx}上传失败: {str(e)}"))
        
        # 使用伪造转发消息发送（如果启用且图片数量足够）
        await self.send_fake_forward_message(event, merged_chain, len(image_urls))

    async def _process_workflow_task(
        self,
        server: ServerState,
        event: AstrMessageEvent,
        prompt: Dict[str, Any],
        workflow_name: str,
        user_id: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
        workflow_config: Optional[Dict[str, Any]] = None
    ) -> None:
        """处理workflow任务"""
        workflow_info = self.workflows[workflow_name]
        config = workflow_config if workflow_config is not None else workflow_info["config"]
        
        # 如果有已下载的图片需要上传，直接上传
        images = []
        if image_paths and config.get("input_nodes"):
            logger.info(f"开始上传 {len(image_paths)} 张已下载图片到服务器 {server.name}")
            
            for i, img_path in enumerate(image_paths):
                try:
                    # 验证文件是否存在
                    if not os.path.exists(img_path):
                        raise Exception(f"PERMANENT_ERROR:第 {i+1} 张图片文件不存在：{img_path}")
                    file_size = os.path.getsize(img_path)
                    if file_size < 10240:  # 小于10KB
                        logger.warning(f"第 {i+1} 张图片文件过小（{file_size}字节），可能已损坏")
                        raise Exception(f"PERMANENT_ERROR:第 {i+1} 张图片文件过小，可能已损坏")
                    
                    # 直接上传图片到ComfyUI服务器
                    image_filename = await self._upload_image_to_comfyui(server, img_path)
                    
                    if not image_filename:
                        raise Exception(f"PERMANENT_ERROR:第 {i+1} 张图片上传失败")
                    
                    images.append(image_filename)
                    logger.info(f"成功上传第 {i+1} 张图片: {image_filename}")
                except Exception as e:
                    error_msg = str(e)
                    # 检查是否是永久性错误（不需要重试的错误）
                    if error_msg.startswith("PERMANENT_ERROR:"):
                        # 直接抛出永久性错误，不要重试
                        raise Exception(error_msg.replace("PERMANENT_ERROR:", ""))
                    else:
                        raise Exception(f"图片上传失败：{error_msg[:1000]}")
            
            # 使用与 _build_workflow 相同的逻辑更新 prompt 中的图片节点
            if images:
                input_nodes = config.get("input_nodes", [])
                input_mappings = config.get("input_mappings", {})
                
                # 用于跟踪已分配的图片索引
                used_image_indices = set()
                
                # 首先处理指定了image_index的节点
                for node_id in input_nodes:
                    if node_id in input_mappings and node_id in prompt:
                        mapping = input_mappings[node_id]
                        param_name = mapping.get("parameter_name", "image")
                        if param_name == "image":
                            image_index = mapping.get("image_index")
                            if image_index is not None:
                                # 有明确指定image_index的节点
                                image_mode = mapping.get("image_mode", "single")
                                
                                if image_mode == "single":
                                    if image_index < len(images):
                                        prompt[node_id]["inputs"][param_name] = images[image_index]
                                        used_image_indices.add(image_index)
                                        logger.info(f"节点 {node_id} 使用指定索引 {image_index} 的图片")
                                    else:
                                        prompt[node_id]["inputs"][param_name] = images[0]
                                        used_image_indices.add(0)
                                        logger.info(f"节点 {node_id} 索引超出范围，使用第一张图片")
                                elif image_mode == "list":
                                    prompt[node_id]["inputs"][param_name] = images
                                    logger.info(f"节点 {node_id} 使用所有图片列表")
                                elif image_mode == "all":
                                    prompt[node_id]["inputs"][param_name] = images
                                    logger.info(f"节点 {node_id} 使用全部图片")
                
                # 然后处理未指定image_index的节点，自动分配未使用的图片
                current_image_index = 0
                for node_id in input_nodes:
                    if node_id in input_mappings and node_id in prompt:
                        mapping = input_mappings[node_id]
                        param_name = mapping.get("parameter_name", "image")
                        if param_name == "image":
                            image_index = mapping.get("image_index")
                            if image_index is None:
                                # 未指定image_index的节点，自动分配
                                # 找到下一个未使用的图片索引
                                while current_image_index in used_image_indices and current_image_index < len(images):
                                    current_image_index += 1
                                
                                if current_image_index < len(images):
                                    prompt[node_id]["inputs"][param_name] = images[current_image_index]
                                    used_image_indices.add(current_image_index)
                                    logger.info(f"节点 {node_id} 自动分配索引 {current_image_index} 的图片")
                                    current_image_index += 1
                                else:
                                    # 没有更多图片了，使用第一张图片
                                    prompt[node_id]["inputs"][param_name] = images[0]
                                    used_image_indices.add(0)
                                    logger.info(f"节点 {node_id} 没有足够图片，使用第一张图片")
            
            # 上传完成后，如果图片是临时文件且启用了永久保存，清理临时文件
            # (永久保存已经在下载时完成，这里只需要清理临时文件)
            for i, img_path in enumerate(image_paths):
                if img_path and os.path.exists(img_path):
                    # 检查是否是临时文件（不包含workflow_inputs路径）
                    if "workflow_inputs" not in img_path:
                        try:
                            # 延迟清理临时文件，给用户一些时间查看错误信息
                            asyncio.create_task(self._cleanup_temp_file(img_path))
                            logger.info(f"已安排清理临时图片文件: {img_path}")
                        except Exception as e:
                            logger.warning(f"安排清理临时文件失败: {e}")
        
        # 发送workflow到ComfyUI
        prompt_id = await self._send_comfyui_prompt(server, prompt)
        
       # await self._send_with_auto_recall(event, event.plain_result(
        #    f"\nWorkflow任务「{config['name']}」已下发至服务器【{server.name}】：\n任务ID：{prompt_id[:8]}..."
   #     ))
        
        # 轮询任务状态
        history_data = await self._poll_task_status(server, prompt_id)
        if not history_data or history_data.get("status", {}).get("completed") is False:
            raise Exception("任务超时或未完成（超时10分钟）")
        
        # 提取输出图片、视频、音频和3D模型
        output_nodes = config.get("output_nodes", [])
        output_mappings = config.get("output_mappings", {})
        image_urls = []
        video_files = []  # 存储视频文件信息
        audio_files = []  # 存储音频文件信息
        model_3d_files = []  # 存储3D模型文件信息
        
        for node_id in output_nodes:
            if node_id in output_mappings:
                outputs = history_data.get("outputs", {})
                node_output = outputs.get(node_id)
                
                # 处理图片输出
                if node_output and node_output.get("images"):
                    for idx, file_info in enumerate(node_output["images"]):
                        filename = file_info["filename"]
                        subfolder = file_info.get("subfolder", "")
                        
                        # 检查是否为视频文件
                        is_video = (filename.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm')) or 
                                   node_output.get("animated", False))
                        
                        if is_video:
                            # 处理视频文件
                            if subfolder:
                                video_url = f"{server.url}/view?filename={filename}&type=output&subfolder={subfolder}"
                            else:
                                video_url = f"{server.url}/view?filename={filename}&type=output"
                            
                            video_files.append({
                                "filename": filename,
                                "url": video_url,
                                "subfolder": subfolder
                            })
                        else:
                            # 处理图片文件
                            image_url = await self._get_image_url(
                                server, 
                                filename, 
                                use_proxy=False,
                                subfolder=subfolder,
                                file_type=file_info.get("type", "output")
                            )
                            image_urls.append((len(image_urls) + len(video_files) + len(audio_files) + 1, image_url))
                            
                            # 静悄悄保存文件
                            await self._save_image_locally(
                                server, 
                                filename, 
                                f"workflow_{workflow_name}", 
                                user_id or "",
                                subfolder,
                                file_info.get("type", "output")
                            )
                
                # 处理音频输出
                if node_output and node_output.get("audio"):
                    for idx, file_info in enumerate(node_output["audio"]):
                        filename = file_info["filename"]
                        subfolder = file_info.get("subfolder", "")
                        
                        # 构建音频文件URL
                        if subfolder:
                            audio_url = f"{server.url}/view?filename={filename}&type=output&subfolder={subfolder}"
                        else:
                            audio_url = f"{server.url}/view?filename={filename}&type=output"
                        
                        audio_files.append({
                            "filename": filename,
                            "url": audio_url,
                            "subfolder": subfolder
                        })
                
                # 处理3D模型输出
                if node_output and node_output.get("3d"):
                    for idx, file_info in enumerate(node_output["3d"]):
                        filename = file_info["filename"]
                        subfolder = file_info.get("subfolder", "")
                        
                        # 构建3D模型文件URL
                        if subfolder:
                            model_3d_url = f"{server.url}/view?filename={filename}&type=output&subfolder={subfolder}"
                        else:
                            model_3d_url = f"{server.url}/view?filename={filename}&type=output"
                        
                        model_3d_files.append({
                            "filename": filename,
                            "url": model_3d_url,
                            "subfolder": subfolder
                        })
        
        if not image_urls and not video_files and not audio_files and not model_3d_files:
            # 检查是否有其他类型的输出
            await self._send_with_auto_recall(event, event.plain_result(f"Workflow「{config['name']}」执行完成，但未检测到图片、视频、音频或3D模型输出"))
            return
        
        # 构建结果消息
        result_parts = []
        if image_urls:
            result_parts.append(f"{len(image_urls)}张图片")
        if video_files:
            result_parts.append(f"{len(video_files)}个视频")
        if audio_files:
            result_parts.append(f"{len(audio_files)}个音频")
        if model_3d_files:
            result_parts.append(f"{len(model_3d_files)}个3D模型")
        
        result_text = f"Workflow「{config['name']}」执行完成！\n共{'、'.join(result_parts)}："
        
        # 构建合并的消息链
        merged_chain = []
        merged_chain.append(Plain(result_text))
        
        # 添加图片
        for idx, img_url in image_urls:
            merged_chain.append(Plain(f"\n\n第{idx}/{len(image_urls) + len(video_files) + len(audio_files) + len(model_3d_files)}张图片："))
            merged_chain.append(Image.fromURL(img_url))
        
        # 添加视频（根据大小决定发送方式）
        if video_files:
            for idx, video_info in enumerate(video_files):
                video_idx = len(image_urls) + idx + 1
                filename = video_info["filename"]
                
                # 下载视频文件
                try:
                    temp_video_path = await self._download_video_file(event, server, video_info)
                    if temp_video_path:
                        # 使用新的发送逻辑（小于100MB直接发送，大于100MB上传为群文件）
                        await self._send_video(event, temp_video_path, filename, video_idx)
                        merged_chain.append(Plain(f"\n✅ 视频{video_idx}处理完成"))
                    else:
                        merged_chain.append(Plain(f"\n❌ 视频{video_idx}下载失败"))
                except Exception as e:
                    logger.error(f"视频处理失败: {e}")
                    merged_chain.append(Plain(f"\n❌ 视频{video_idx}处理失败: {str(e)}"))
        
        # 添加音频（作为文件上传）
        if audio_files:
            for idx, audio_info in enumerate(audio_files):
                audio_idx = len(image_urls) + len(video_files) + idx + 1
                merged_chain.append(Plain(f"\n\n第{audio_idx}/{len(image_urls) + len(video_files) + len(audio_files) + len(model_3d_files)}个音频：正在上传文件..."))
                
                # 下载并上传音频文件
                try:
                    audio_result = await self._download_and_upload_audio(event, server, audio_info)
                    if audio_result:
                        if audio_result["duration"] and audio_result["duration"] <= 30:
                            merged_chain.append(Plain(f"\n✅ 音频{audio_idx}({audio_result['duration_info']})已发送为语音消息"))
                        else:
                            merged_chain.append(Plain(f"\n✅ 音频{audio_idx}({audio_result['duration_info']})已上传为文件"))
                    else:
                        merged_chain.append(Plain(f"\n❌ 音频{audio_idx}上传失败"))
                except Exception as e:
                    logger.error(f"音频上传失败: {e}")
                    merged_chain.append(Plain(f"\n❌ 音频{audio_idx}上传失败: {str(e)}"))
        
        # 添加3D模型（作为文件上传）
        if model_3d_files:
            for idx, model_3d_info in enumerate(model_3d_files):
                model_3d_idx = len(image_urls) + len(video_files) + len(audio_files) + idx + 1
                merged_chain.append(Plain(f"\n\n第{model_3d_idx}/{len(image_urls) + len(video_files) + len(audio_files) + len(model_3d_files)}个3D模型：正在上传文件..."))
                
                # 下载并上传3D模型文件
                try:
                    model_3d_path = await self._download_and_upload_3d_model(event, server, model_3d_info)
                    if model_3d_path:
                        merged_chain.append(Plain(f"\n✅ 3D模型{model_3d_idx}已上传为文件"))
                    else:
                        merged_chain.append(Plain(f"\n❌ 3D模型{model_3d_idx}上传失败"))
                except Exception as e:
                    logger.error(f"3D模型上传失败: {e}")
                    merged_chain.append(Plain(f"\n❌ 3D模型{model_3d_idx}上传失败: {str(e)}"))
        
        # 使用伪造转发消息发送（如果启用且图片数量足够）
        await self.send_fake_forward_message(event, merged_chain, len(image_urls))

    async def _send_comfyui_prompt(self, server: ServerState, comfy_prompt: Dict[str, Any]) -> str:
        url = f"{server.url}/prompt"
        headers = {"Content-Type": "application/json"}
        payload = {"client_id": str(uuid.uuid4()), "prompt": comfy_prompt}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    filtered_resp = self._filter_server_urls(resp_text[:50000])
                    raise Exception(f"任务下发失败（HTTP {resp.status}）：{filtered_resp}")
                resp_data = await resp.json()
                return resp_data.get("prompt_id", "")

    async def _check_queue_empty(self, server: ServerState) -> bool:
        """检查服务器队列是否为空，失败时触发服务器故障转移"""
        url = f"{server.url}/api/queue"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        # 将队列查询失败视为服务器故障，触发故障处理
                        logger.warning(f"队列状态查询失败（HTTP {resp.status}），触发服务器故障处理")
                        await self._handle_server_failure(server)
                        # 返回 True 让外层逻辑跳过队列检查，避免无限等待
                        return True
                    resp_data = await resp.json()
                    if not isinstance(resp_data, dict) or "queue_running" not in resp_data or "queue_pending" not in resp_data:
                        logger.warning(f"队列返回格式异常：{resp_data}，触发服务器故障处理")
                        await self._handle_server_failure(server)
                        return True
                    running_empty = len(resp_data["queue_running"]) == 0
                    pending_empty = len(resp_data["queue_pending"]) == 0
                    return running_empty and pending_empty
        except Exception as e:
            # 队列查询异常也视为服务器故障
            logger.warning(f"队列检查异常：{str(e)}，触发服务器故障处理")
            await self._handle_server_failure(server)
            return True

    async def _poll_task_status(self, server: ServerState, prompt_id: str, timeout: int = 600, interval: int = 3) -> Dict[str, Any]:
        """轮询任务状态，支持服务器故障转移"""
        url = f"{server.url}/history/{prompt_id}"
        start_time = asyncio.get_event_loop().time()
        empty_queue_retry_count = 0
        queue_check_start_time = start_time + self.queue_check_delay
        async with aiohttp.ClientSession() as session:
            while True:
                current_time = asyncio.get_event_loop().time()
                elapsed_time = current_time - start_time
                
                # 检查服务器是否健康，不健康则立即失败
                if not server.healthy:
                    raise Exception(f"服务器【{server.name}】不健康，无法完成任务，触发故障转移")
                
                if elapsed_time > timeout:
                    raise Exception(f"任务超时（{timeout}秒未完成）")
                try:
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200:
                            resp_data = await resp.json()
                            task_data = resp_data.get(prompt_id)
                            if task_data and task_data.get("status", {}).get("completed"):
                                empty_queue_retry_count = 0
                                return task_data
                        else:
                            # 历史查询失败，触发服务器故障处理
                            logger.warning(f"历史状态查询失败（HTTP {resp.status}），触发服务器故障处理")
                            await self._handle_server_failure(server)
                except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
                    error_msg = str(e) if str(e) else f"{type(e).__name__}（错误信息为空）"
                    logger.warning(f"历史状态查询异常：{error_msg}")
                    # 检查是否是服务器不健康导致的异常
                    if not server.healthy:
                        raise
                    # 对于超时或网络连接错误，触发服务器故障处理
                    if isinstance(e, (asyncio.TimeoutError, aiohttp.ClientConnectorError, 
                                      aiohttp.ClientOSError, aiohttp.ServerDisconnectedError)):
                        logger.warning(f"检测到网络连接问题，触发服务器故障处理")
                        await self._handle_server_failure(server)
                        # 检查服务器是否已不健康，如果是则抛出异常触发故障转移
                        if not server.healthy:
                            raise
                    # 其他异常继续轮询
                if current_time >= queue_check_start_time:
                    if int(elapsed_time) % self.queue_check_interval == 0:
                        is_queue_empty = await self._check_queue_empty(server)
                        if is_queue_empty:
                            empty_queue_retry_count += 1
                            logger.warning(f"检测到服务器【{server.name}】队列为空（连续{empty_queue_retry_count}/{self.empty_queue_max_retry}次），prompt_id：{prompt_id[:8]}")
                            if empty_queue_retry_count >= self.empty_queue_max_retry:
                                raise Exception(
                                    self._filter_server_urls(
                                        f"任务失败：服务器【{server.name}】队列已为空（running/pending均无任务），"
                                        f"但历史记录中未找到任务结果（prompt_id：{prompt_id[:8]}）。"
                                        f"可能是任务被强制终止或ComfyUI服务异常。"
                                    )
                                )
                        else:
                            empty_queue_retry_count = 0
                await asyncio.sleep(interval)

    def _extract_batch_image_info(self, history_data: Dict[str, Any]) -> List[Dict[str, str]]:
        outputs = history_data.get("outputs", {})
        save_node_data = outputs.get("9") or outputs.get("50")
        
        # 首先检查图片输出
        if save_node_data and save_node_data.get("images"):
            return save_node_data["images"]
        
        # 检查各种类型的输出文件（3D模型、音频、视频等）
        for node_id, node_data in outputs.items():
            # 检查3D模型输出
            if node_data.get("3d"):
                model_files = []
                for model_info in node_data["3d"]:
                    model_files.append({
                        "filename": model_info["filename"],
                        "subfolder": model_info.get("subfolder", ""),
                        "type": model_info.get("type", "output"),
                        "file_type": "3d"
                    })
                return model_files
            
            # 检查音频输出
            if node_data.get("audio"):
                audio_files = []
                for audio_info in node_data["audio"]:
                    audio_files.append({
                        "filename": audio_info["filename"],
                        "subfolder": audio_info.get("subfolder", ""),
                        "type": audio_info.get("type", "output"),
                        "file_type": "audio"
                    })
                return audio_files
            
            # 检查视频输出
            if node_data.get("video"):
                video_files = []
                for video_info in node_data["video"]:
                    video_files.append({
                        "filename": video_info["filename"],
                        "subfolder": video_info.get("subfolder", ""),
                        "type": video_info.get("type", "output"),
                        "file_type": "video"
                    })
                return video_files
            
            # 检查其他可能的输出类型
            for output_type in ["mesh", "model", "file", "output"]:
                if node_data.get(output_type):
                    output_files = []
                    for file_info in node_data[output_type]:
                        output_files.append({
                            "filename": file_info["filename"],
                            "subfolder": file_info.get("subfolder", ""),
                            "type": file_info.get("type", "output"),
                            "file_type": output_type
                        })
                    return output_files
        
        raise Exception("未找到任何输出文件（图片、3D模型、音频、视频等）")

    async def _get_image_url(self, server: ServerState, filename: str, use_proxy: bool = False, subfolder: str = "", file_type: str = "output") -> str:
        """获取文件URL（支持图片、3D模型、音频、视频等）
        
        Args:
            server: ComfyUI服务器状态
            filename: 文件名
            use_proxy: 是否使用代理模式（用于Web API）
            subfolder: 子文件夹路径
            file_type: 文件类型（output、input等）
        """
        if use_proxy and self.enable_web_api and self.web_api_image_proxy:
            # Web API模式：返回相对路径，让浏览器自动适配当前域名
            # 这样可以避免暴露127.0.0.1地址，外部用户可以通过Web服务器访问文件
            return f"/api/image/{filename}"
        else:
            # 默认模式：直接返回ComfyUI地址（用于内部机器人消息）
            url_params = {"filename": filename, "type": file_type, "subfolder": subfolder}
            # 对于图片文件，添加preview参数
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')):
                url_params["preview"] = "true"
            query_str = "&".join([f"{k}={quote(v)}" for k, v in url_params.items()])
            return f"{server.url}/view?{query_str}"

    async def _save_img2img_image_permanently(self, img_path: str, user_id: str) -> Optional[str]:
        """将图生图输入图片永久保存到本地

        Args:
            img_path: 临时图片文件路径
            user_id: 用户ID

        Returns:
            保存后的文件路径，如果保存失败则返回None
        """
        try:
            # 创建保存目录
            now = datetime.now()
            # 如果是绝对路径，直接使用；如果是相对路径，则在插件目录下创建
            auto_save_path = Path(self.auto_save_dir)
            if auto_save_path.is_absolute():
                save_dir = auto_save_path / "img2img_inputs" / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            else:
                save_dir = Path(os.path.dirname(__file__)) / self.auto_save_dir / "img2img_inputs" / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            save_dir.mkdir(parents=True, exist_ok=True)

            # 生成带时间戳的文件名（不包含用户信息）
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            original_name = os.path.basename(img_path)

            name_part, ext = os.path.splitext(original_name)
            saved_filename = f"{timestamp}_img2img{ext}"
            save_path = save_dir / saved_filename

            # 复制文件到永久位置
            def copy_file():
                import shutil
                shutil.copy2(img_path, save_path)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, copy_file)

            logger.info(f"图生图输入图片已永久保存: {save_path}")
            return str(save_path)

        except Exception as e:
            logger.error(f"永久保存图生图图片失败: {str(e)}")
            return None

    async def _save_workflow_image_permanently(self, img_path: str, workflow_name: str, user_id: str, image_index: int) -> Optional[str]:
        """将workflow输入图片永久保存到本地
        
        Args:
            img_path: 临时图片文件路径
            workflow_name: workflow名称
            user_id: 用户ID
            image_index: 图片索引
            
        Returns:
            保存后的文件路径，如果保存失败则返回None
        """
        try:
            # 创建保存目录
            now = datetime.now()
            # 如果是绝对路径，直接使用；如果是相对路径，则在插件目录下创建
            auto_save_path = Path(self.auto_save_dir)
            if auto_save_path.is_absolute():
                save_dir = auto_save_path / "workflow_inputs" / workflow_name / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            else:
                save_dir = Path(os.path.dirname(__file__)) / self.auto_save_dir / "workflow_inputs" / workflow_name / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            save_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成带时间戳的文件名（不包含用户信息）
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            original_name = os.path.basename(img_path)
            
            # 添加工作流名和图片索引信息
            name_part, ext = os.path.splitext(original_name)
            saved_filename = f"{timestamp}_{workflow_name}_img{image_index+1}{ext}"
            save_path = save_dir / saved_filename
            
            # 复制文件到永久位置
            def copy_file():
                import shutil
                shutil.copy2(img_path, save_path)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, copy_file)
            
            logger.info(f"Workflow输入图片已永久保存: {save_path}")
            return str(save_path)
            
        except Exception as e:
            logger.error(f"永久保存workflow图片失败: {str(e)}")
            return None

    async def _cleanup_temp_file(self, temp_file_path: str) -> None:
        """清理临时文件"""
        try:
            await asyncio.sleep(10)  # 等待10秒后清理
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                logger.info(f"临时文件已清理: {temp_file_path}")
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")

    async def _save_image_locally(self, server: ServerState, filename: str, prompt: str = "", user_id: str = "", subfolder: str = "", file_type: str = "output") -> Optional[str]:
        """静悄悄保存文件到本地（支持图片、3D模型、音频、视频等）
        
        Returns:
            保存后的文件名（包含时间戳前缀），如果未启用保存则返回None
        """
        if not self.enable_auto_save:
            return None
            
        try:
            # 获取文件URL
            file_url = await self._get_image_url(server, filename, use_proxy=False, subfolder=subfolder, file_type=file_type)
            
            # 下载文件
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url, timeout=30) as resp:
                    if resp.status != 200:
                        logger.warning(f"下载文件失败，HTTP状态码: {resp.status}")
                        return None
                    file_data = await resp.read()
            
            # 创建保存目录
            now = datetime.now()
            # 如果是绝对路径，直接使用；如果是相对路径，则在插件目录下创建
            auto_save_path = Path(self.auto_save_dir)
            if auto_save_path.is_absolute():
                save_dir = auto_save_path / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            else:
                save_dir = Path(os.path.dirname(__file__)) / self.auto_save_dir / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            save_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成带时间戳的文件名
            timestamp = now.strftime("%Y%m%d_%H%M%S_")
            original_name = filename
            
            # 根据文件扩展名判断文件类型，不再强制添加.png扩展名
            file_extensions = {
                # 图片格式
                '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff', '.tif',
                # 3D模型格式
                '.glb', '.gltf', '.obj', '.fbx', '.dae', '.3ds', '.blend',
                # 音频格式
                '.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma',
                # 视频格式
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.m4v',
                # 其他格式
                '.zip', '.rar', '.7z', '.tar', '.gz'
            }
            
            # 检查文件是否已有有效扩展名
            has_valid_extension = any(original_name.lower().endswith(ext) for ext in file_extensions)
            
            # 如果没有有效扩展名，根据文件类型或内容添加默认扩展名
            if not has_valid_extension:
                if file_type == "3d" or any(keyword in filename.lower() for keyword in ['mesh', 'model', '3d']):
                    original_name += '.glb'  # 3D模型默认格式
                elif file_type == "audio" or any(keyword in filename.lower() for keyword in ['audio', 'sound']):
                    original_name += '.wav'  # 音频默认格式
                elif file_type == "video" or any(keyword in filename.lower() for keyword in ['video', 'movie']):
                    original_name += '.mp4'  # 视频默认格式
                else:
                    original_name += '.png'  # 默认为图片格式
            
            saved_filename = timestamp + original_name
            save_path = save_dir / saved_filename
            
            # 保存文件 - 使用异步文件写入
            def write_file():
                with open(save_path, 'wb') as f:
                    f.write(file_data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, write_file)
            
            # 根据文件类型记录不同的日志信息
            file_type_names = {
                "3d": "3D模型", "audio": "音频", "video": "视频", 
                "mesh": "3D网格", "model": "模型", "file": "文件"
            }
            file_desc = file_type_names.get(file_type, "文件")
            logger.info(f"{file_desc}已自动保存: {save_path}")
            
            # 记录文件生成信息
            if user_id:
                asyncio.create_task(self._record_image_generation(saved_filename, user_id))
            
            return saved_filename
            
        except Exception as e:
            logger.error(f"自动保存文件失败: {str(e)}")

    async def _upload_image_to_comfyui(self, server: ServerState, img_path: str) -> str:
        url = f"{server.url}/upload/image"
        if not os.path.exists(img_path):
            raise Exception(f"图片文件不存在：{img_path}")
        
        # 使用异步文件读取
        def read_image_file():
            with open(img_path, "rb") as f:
                return f.read()
        
        # 在线程池中执行文件读取
        loop = asyncio.get_event_loop()
        img_data = await loop.run_in_executor(None, read_image_file)
        
        form_data = aiohttp.FormData()
        form_data.add_field("image", img_data, filename=os.path.basename(img_path), content_type="image/*")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form_data) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    filtered_resp = self._filter_server_urls(resp_text[:50])
                    raise Exception(f"图片上传失败（HTTP {resp.status}）：{filtered_resp}")
                resp_data = await resp.json()
                return resp_data.get("name", "")

    def _parse_command(self, full_text: str, command_keyword: str) -> Tuple[str, list]:
        mention_pattern = r'@[\w\d]+'
        text_without_mention = re.sub(mention_pattern, '', full_text).strip()
        parts = text_without_mention.split()
        if not parts or parts[0] != command_keyword:
            return ("", [])
        mention_text = re.findall(mention_pattern, full_text)
        return (" ".join(mention_text), parts[1:])

    # 文生图指令
    @filter.custom_filter(ImgGenerateFilter)
    async def generate_image(self, event: AstrMessageEvent) -> None:
        # 检查群聊白名单
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result(
                "❌ 当前群聊不在白名单中，无法使用此功能！"
            ))
            return
        
        if not self._is_in_open_time():
            open_desc = self._get_open_time_desc()
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n当前未开放图片生成服务～\n开放时间：{open_desc}\n请在开放时间段内提交任务！"
            ))
            return
        if not self._get_any_healthy_server():
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n所有ComfyUI服务器均不可用，请稍后再试！"
            ))
            return
        full_msg = event.message_obj.message_str.strip()
        _, params = self._parse_command(full_msg, "aimg")
        if not params:
            # 发送帮助信息
            await self.send_help(event)
            return
        try:
            # 先解析模型参数
            params, selected_model = self._parse_model_params(params)
            # 再解析LoRA参数
            params, lora_list = self._parse_lora_params(params)
        except ValueError as e:
            filtered_err = self._filter_server_urls(str(e))
            await self._send_with_auto_recall(event, event.plain_result(f"\n参数解析失败：{filtered_err}"))
            return
        prompt_with_params = " ".join(params)
        res_pattern = r'宽(\d+),高(\d+)'
        batch_pattern = r'批量(\d+)'
        res_match = re.search(res_pattern, prompt_with_params)
        batch_match = re.search(batch_pattern, prompt_with_params)
        current_width = self.default_width
        current_height = self.default_height
        current_batch_size = self.txt2img_batch_size
        pure_prompt = prompt_with_params
        if res_match:
            try:
                input_w = int(res_match.group(1))
                input_h = int(res_match.group(2))
                if not (self.min_width <= input_w <= self.max_width):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n宽度{input_w}非法（需{self.min_width}~{self.max_width}像素），请重新输入合法参数！"))
                    return
                if not (self.min_height <= input_h <= self.max_height):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n高度{input_h}非法（需{self.min_height}~{self.max_height}像素），请重新输入合法参数！"))
                    return
                current_width = input_w
                current_height = input_h
                pure_prompt = re.sub(res_pattern, "", pure_prompt).strip()
            except Exception as e:
                await self._send_with_auto_recall(event, event.plain_result(f"\n宽高解析失败：{str(e)}，请重新输入合法参数！"))
                return
        if batch_match:
            try:
                input_batch = int(batch_match.group(1))
                if not (1 <= input_batch <= self.max_txt2img_batch):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n批量数{input_batch}非法（文生图需1~{self.max_txt2img_batch}），请重新输入合法参数！"))
                    return
                current_batch_size = input_batch
                pure_prompt = re.sub(batch_pattern, "", pure_prompt).strip()
            except Exception as e:
                await self._send_with_auto_recall(event, event.plain_result(f"\n批量数解析失败：{str(e)}，请重新输入合法参数！"))
                return
        if not pure_prompt:
            await self._send_with_auto_recall(event, event.plain_result(f"\n提示词不能为空！使用方法：\n发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填\n文生图默认批量数：{self.txt2img_batch_size}，最大支持{self.max_txt2img_batch}"))
            return
        try:
            current_seed = random.randint(1, 18446744073709551615) if (self.seed == "随机" or not self.seed) else int(self.seed)
        except (ValueError, TypeError):
            current_seed = random.randint(1, 2147483647)
        # 检查用户任务数限制
        user_id = str(event.get_sender_id())
        if not await self._increment_user_task_count(user_id):
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n您当前同时进行的任务数已达上限（{self.max_concurrent_tasks_per_user}个），请等待当前任务完成后再提交新任务！"
            ))
            return
            
        if self.task_queue.full():
            # 如果队列已满，需要减少刚刚增加的用户任务计数
            await self._decrement_user_task_count(user_id)
            await self._send_with_auto_recall(event, event.plain_result(f"\n当前任务队列已满（{self.max_task_queue}个任务上限），请稍后再试！"))
            return
            
        await self.task_queue.put({
            "event": event,
            "prompt": pure_prompt,
            "current_seed": current_seed,
            "current_width": current_width,
            "current_height": current_height,
            "current_batch_size": current_batch_size,
            "lora_list": lora_list,
            "selected_model": selected_model,
            "user_id": str(event.get_sender_id())
        })
        model_feedback = ""
        if selected_model:
            # 找到对应的模型描述
            model_desc = "自定义模型"
            for desc, (filename, desc_text) in self.model_name_map.items():
                if filename == selected_model:
                    model_desc = desc_text
                    break
            model_feedback = f"\n使用模型：{model_desc}（文件：{selected_model}）"
        lora_feedback = ""
        if lora_list:
            lora_feedback = "\n使用LoRA：" + " | ".join([
                f"{lora['name']}（model:{lora['strength_model']}, clip:{lora['strength_clip']}）"
                for lora in lora_list
            ])
        available_servers = [s.name for s in self.comfyui_servers if s.healthy]
        server_feedback = f"\n可用服务器：{', '.join(available_servers)}" if available_servers else "\n当前无可用服务器，任务将在服务器恢复后处理"
        await self._send_with_auto_recall(event, event.plain_result(
            f"\n文生图任务已加入队列（当前排队：{self.task_queue.qsize()}个）\n"
            f"提示词：{self._truncate_prompt(pure_prompt)}\n"
            f"Seed：{current_seed}\n"
            f"分辨率：{current_width}x{current_height}（默认：{self.default_width}x{self.default_height}，范围：{self.min_width}~{self.max_width}x{self.min_height}~{self.max_height}）\n"
            f"批量数：{current_batch_size}（默认：{self.txt2img_batch_size}，最大：{self.max_txt2img_batch}）"
            + model_feedback
            + server_feedback
            + lora_feedback
        ))

    async def _is_port_available(self, port: int) -> bool:
        """检查端口是否可用"""
        try:
            future = asyncio.open_connection('localhost', port)
            _, writer = await asyncio.wait_for(future, timeout=1.0)
            writer.close()
            await writer.wait_closed()
            return False  # 连接成功，端口被占用
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return True  # 连接失败，端口可用
        except Exception:
            return False

    async def _find_available_port(self, start_port: int) -> int:
        """从指定端口开始查找可用端口"""
        port = start_port
        max_attempts = 100  # 最多尝试100个端口
        
        for _ in range(max_attempts):
            if await self._is_port_available(port):
                return port
            port += 1
        
        # 如果都不可用，随机选择一个高端口
        return random.randint(49152, 65535)

    async def _start_help_server(self) -> str:
        """启动临时HTTP服务器用于HTML转图片"""
        if self.help_server_runner is not None:
            return f"http://localhost:{self.actual_help_port}"
        
        # 查找可用端口
        self.actual_help_port = await self._find_available_port(self.help_server_port)
        
        # 生成动态HTML内容
        def generate_help_html():
            # 构建模型列表HTML
            model_items_html = ""
            if self.model_name_map:
                seen_descriptions = set()
                for _, (filename, desc) in self.model_name_map.items():
                    if desc not in seen_descriptions:
                        model_items_html += f'<li>{desc} (文件: {filename})</li>'
                        seen_descriptions.add(desc)
            else:
                model_items_html = '<li>暂无可用模型</li>'
            
            # 构建LoRA列表HTML
            lora_items_html = ""
            if self.lora_name_map:
                seen_descriptions = set()
                for _, (filename, desc) in self.lora_name_map.items():
                    if desc not in seen_descriptions:
                        lora_items_html += f'<li>{desc} (文件: {filename})</li>'
                        seen_descriptions.add(desc)
            else:
                lora_items_html = '<li>暂无可用LoRA</li>'
            
            # 构建服务器信息HTML
            server_items_html = ""
            for server in self.comfyui_servers:
                if server.healthy:
                    server_items_html += f'<li>{server.name}</li>'
            
            html_template = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ComfyUI AI绘画帮助</title>
    <style>
        body {{
            font-family: 'Microsoft YaHei', Arial, sans-serif;
            line-height: 1.8;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(45deg, #4a90e2, #357abd);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 2.5em;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }}
        .content {{
            padding: 30px;
        }}
        .section {{
            margin-bottom: 30px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 10px;
            border-left: 4px solid #4a90e2;
        }}
        .section h2 {{
            margin-top: 0;
            color: #333;
            font-size: 1.5em;
        }}
        .section ul {{
            margin: 10px 0;
            padding-left: 20px;
        }}
        .section li {{
            margin: 8px 0;
            color: #555;
        }}
        .highlight {{
            background: #e3f2fd;
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #2196f3;
            margin: 15px 0;
        }}
        .code {{
            background: #f5f5f5;
            padding: 10px;
            border-radius: 5px;
            font-family: 'Courier New', monospace;
            margin: 5px 0;
        }}
        .footer {{
            background: #333;
            color: white;
            text-align: center;
            padding: 20px;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎨 ComfyUI AI绘画帮助</h1>
        </div>
        <div class="content">
            <div class="section">
                <h2>🎯 主要功能</h2>
                <ul>
                    <li>文生图: 发送「aimg &lt;提示词&gt; [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填</li>
                    <li>图生图: 发送「img2img &lt;提示词&gt; [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息</li>
                    <li>帮助信息: 单独输入 aimg 或 img2img</li>
                </ul>
            </div>
            
            <div class="section">
                <h2>⚙️ 基本配置</h2>
                <ul>
                    <li>默认模型: {self.ckpt_name or '未配置'}</li>
                    <li>文生图批量: {self.txt2img_batch_size}</li>
                    <li>图生图批量: {self.img2img_batch_size}</li>
                    <li>默认噪声: {self.default_denoise}</li>
                    <li>开放时间: {self.open_time_ranges}</li>
                </ul>
            </div>
            
            <div class="section">
                <h2>📏 参数限制</h2>
                <ul>
                    <li>分辨率: {self.min_width}~{self.max_width} x {self.min_height}~{self.max_height}</li>
                    <li>文生图最大批量: {self.max_txt2img_batch}</li>
                    <li>图生图最大批量: {self.max_img2img_batch}</li>
                    <li>任务队列最大: {self.max_task_queue}</li>
                    <li>每用户最大并发: {self.max_concurrent_tasks_per_user}</li>
                </ul>
            </div>
            
            <div class="section">
                <h2>🎨 可用模型列表</h2>
                <div class="highlight">
                    <strong>使用格式:</strong> model:描述
                </div>
                <ul>
                    {model_items_html}
                </ul>
            </div>
            
            <div class="section">
                <h2>✨ 可用LoRA列表</h2>
                <div class="highlight">
                    <strong>使用格式:</strong> lora:描述[:强度][!CLIP强度]
                </div>
                <ul>
                    {lora_items_html}
                </ul>
            </div>
            
            <div class="section">
                <h2>🌐 服务器信息</h2>
                <ul>
                    {server_items_html}
                </ul>
            </div>
            
            <div class="section">
                <h2>🔧 可用Workflow列表</h2>
                <div class="highlight">
                    <strong>使用格式:</strong> &lt;前缀&gt; [参数名:值 ...]
                </div>
                <ul>
                    {self._generate_workflow_html_items()}
                </ul>
            </div>
        </div>
        <div class="footer">
            <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>
            """
            return html_template
        
        async def handle_help(request):
            """处理帮助页面请求"""
            try:
                content = generate_help_html()
                return web.Response(text=content, content_type='text/html')
            except Exception as e:
                logger.error(f"处理帮助页面请求失败: {e}")
                return web.Response(text="服务器错误", status=500)

        # 创建应用
        app = web.Application()
        app.router.add_get('/', handle_help)

        
        # 创建runner
        self.help_server_runner = web.AppRunner(app)
        await self.help_server_runner.setup()
        
        # 创建site
        self.help_server_site = web.TCPSite(self.help_server_runner, 'localhost', self.actual_help_port)
        await self.help_server_site.start()
        
        logger.info(f"帮助图片服务器已启动: http://localhost:{self.actual_help_port}")
        return f"http://localhost:{self.actual_help_port}"

    async def _html_to_image(self, html_url: str) -> str:
        """将HTML页面转换为图片"""
        try:
            # 尝试使用imgkit或其他HTML转图片工具
            # 这里使用PIL创建一个精美的帮助图片
            
            # 尝试加载字体
            try:
                font_path = os.path.join(os.path.dirname(__file__), "1.ttf")
                if os.path.exists(font_path):
                    title_font = ImageFont.truetype(font_path, 52)
                    normal_font = ImageFont.truetype(font_path, 32)
                    small_font = ImageFont.truetype(font_path, 24)
                else:
                    title_font = ImageFont.load_default()
                    normal_font = ImageFont.load_default()
                    small_font = ImageFont.load_default()
            except:
                title_font = ImageFont.load_default()
                normal_font = ImageFont.load_default()
                small_font = ImageFont.load_default()
            
            # 准备所有内容
            sections = []
            
            # 基本信息
            # 获取当前排队信息
            current_queue_size = self.task_queue.qsize()
            total_user_tasks = sum(self.user_task_counts.values())
            
            sections.append(("🎯 主要功能", [
                f"• 文生图: 发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填",
                f"• 图生图: 发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息",
                f"• 输出压缩包: comfyuioutput",
                f"• 小番茄解密：发送「小番茄图片解密」",
                f"• 帮助信息: 单独输入 aimg 或 img2img"
            ]))
            
            sections.append(("📊 实时状态", [
                f"• 当前排队任务: {current_queue_size} 个",
                f"• 总任务: {total_user_tasks} 个",
                f"• 队列容量: {self.max_task_queue} 个",
                f"• 活跃用户数: {len(self.user_task_counts)} 个"
            ]))
            
            sections.append(("⚙️ 基本配置", [
                f"• 默认模型: {self.ckpt_name or '未配置'}",
                f"• 文生图批量: {self.txt2img_batch_size}",
                f"• 图生图批量: {self.img2img_batch_size}",
                f"• 默认噪声: {self.default_denoise}",
                f"• 自动保存: {'开启' if self.enable_auto_save else '关闭'}",
                f"• 输出压缩包: {'开启' if self.enable_output_zip else '关闭'}",
                f"• 每日下载限制: {self.daily_download_limit} 次",
                f"• 仅限自己图片: {'是' if self.only_own_images else '否'}",
                f"• 开放时间: {self.open_time_ranges}"
            ]))
            
            sections.append(("📏 参数限制", [
                f"• 分辨率: {self.min_width}~{self.max_width} x {self.min_height}~{self.max_height}",
                f"• 文生图最大批量: {self.max_txt2img_batch}",
                f"• 图生图最大批量: {self.max_img2img_batch}",
                f"• 任务队列最大: {self.max_task_queue}",
                f"• 每用户最大并发: {self.max_concurrent_tasks_per_user}"
            ]))
            
            # 模型选择 - 显示所有可用模型
            model_items = ["格式: model:描述"]
            if self.model_name_map:
                # 使用集合去重，确保每个模型描述只显示一次
                seen_descriptions = set()
                for _, (filename, desc) in self.model_name_map.items():
                    if desc not in seen_descriptions:
                        model_items.append(f"• {desc} (文件: {filename})")
                        seen_descriptions.add(desc)
            else:
                model_items.append("• 暂无可用模型")
            sections.append(("🎨 可用模型列表", model_items))
            
            # LoRA使用 - 显示所有可用LoRA
            lora_items = ["格式: lora:描述[:强度][!CLIP强度]"]
            if self.lora_name_map:
                # 使用集合去重，确保每个LoRA描述只显示一次
                seen_descriptions = set()
                for _, (filename, desc) in self.lora_name_map.items():
                    if desc not in seen_descriptions:
                        lora_items.append(f"• {desc} (文件: {filename})")
                        seen_descriptions.add(desc)
            else:
                lora_items.append("• 暂无可用LoRA")
            sections.append(("✨ 可用LoRA列表", lora_items))
            
            # 服务器信息 - 包含详细系统信息
            server_items = []
            for server in self.comfyui_servers:
                if server.healthy:
                    server_items.append(f"📊 【{server.name}】")
                    
                    # 获取系统信息
                    system_info = await self._get_server_system_info(server)
                    if system_info:
                        system_data = system_info.get("system", {})
                        devices_data = system_info.get("devices", [])
                        
                        # 系统信息
                        os_info = system_data.get("os", "未知")
                        pytorch_version = system_data.get("pytorch_version", "未知")
                        ram_total = system_data.get("ram_total", 0) / (1024**3)  # 转换为GB
                        ram_free = system_data.get("ram_free", 0) / (1024**3)  # 转换为GB
                        ram_used = ram_total - ram_free
                        
                        server_items.append(f"  系统: {os_info}")
                        server_items.append(f"  PyTorch: {pytorch_version}")
                        server_items.append(f"  内存: {ram_used:.1f}GB / {ram_total:.1f}GB")
                        
                        # 设备信息
                        if devices_data:
                            for i, device in enumerate(devices_data):
                                device_name = device.get("name", "未知设备")
                                device_type = device.get("type", "未知")
                                vram_total = device.get("vram_total", 0) / (1024**3)  # 转换为GB
                                vram_free = device.get("vram_free", 0) / (1024**3)  # 转换为GB
                                vram_used = vram_total - vram_free
                                
                                server_items.append(f"  GPU{i+1}: {device_name}")
                                server_items.append(f"    类型: {device_type}")
                                server_items.append(f"    显存: {vram_used:.1f}GB / {vram_total:.1f}GB")
                        else:
                            server_items.append(f"  GPU: 未检测到GPU设备")
                    else:
                        server_items.append(f"  ❌ 无法获取系统信息")
                else:
                    server_items.append(f"📊 【{server.name}】")
                    server_items.append(f"  ❌ 服务器不可用")
            
            # server_items.append(f"帮助服务器: {html_url}")  # 隐藏服务器地址避免暴露
            sections.append(("🌐 服务器信息", server_items))
            
            # Workflow使用 - 显示所有可用Workflow
            workflow_items = ["格式: <前缀> [参数名:值 ...]"]
            if self.workflows:
                for workflow_name, workflow_info in self.workflows.items():
                    config = workflow_info["config"]
                    name = config.get("name", workflow_name)
                    prefix = config.get("prefix", "")
                    description = config.get("description", "")
                    if description:
                        workflow_items.append(f"• {name} (前缀: {prefix}) - {description}")
                    else:
                        workflow_items.append(f"• {name} (前缀: {prefix})")
            else:
                workflow_items.append("• 暂无可用Workflow")
            sections.append(("🔧 可用Workflow列表", workflow_items))
            
            # 计算实际需要的图片高度
            base_height = 120  # 顶部标题区域
            section_spacing = 30  # 章节间距
            title_height = 45  # 章节标题高度
            item_height = 35  # 每行内容高度
            bottom_height = 80  # 底部信息区域
            
            total_height = base_height + bottom_height
            for _, items in sections:
                total_height += title_height + len(items) * item_height + section_spacing
            
            # 额外增加两行的高度
            total_height += 2 * item_height
            
            # 创建自适应大小的图片
            width = 1200
            height = max(800, total_height)  # 最小高度800
            image = PILImage.new('RGB', (width, height), color='#ffffff')
            draw = ImageDraw.Draw(image)
            
            # 绘制标题背景
            draw.rectangle([0, 0, width, 80], fill='#4a90e2')
            
            # 绘制标题
            title_text = "ComfyUI AI绘画帮助"
            title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
            title_width = title_bbox[2] - title_bbox[0]
            title_x = (width - title_width) // 2
            draw.text((title_x, 25), title_text, fill='white', font=title_font)
            
            # 绘制内容区域
            y_offset = 120
            
            for section_title, section_items in sections:
                # 绘制章节标题
                draw.text((50, y_offset), section_title, fill='#333333', font=normal_font)
                y_offset += title_height
                
                # 绘制章节内容
                for item in section_items:

                    
                    # 处理长文本换行
                    max_width = width - 100  # 留出边距
                    words = item
                    lines = []
                    current_line = ""
                    
                    for char in words:
                        test_line = current_line + char
                        bbox = draw.textbbox((0, 0), test_line, font=small_font)
                        text_width = bbox[2] - bbox[0]
                        
                        if text_width <= max_width:
                            current_line = test_line
                        else:
                            if current_line:
                                lines.append(current_line)
                            current_line = char
                    
                    if current_line:
                        lines.append(current_line)
                    
                    # 绘制每一行
                    for line in lines:
                        draw.text((80, y_offset), line, fill='#666666', font=small_font)
                        y_offset += item_height
                
                y_offset += section_spacing
            
            # 绘制底部信息
            draw.rectangle([0, height-80, width, height], fill='#f5f5f5')
            footer_text = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            draw.text((50, height-60), footer_text, fill='#999999', font=small_font)
            
            # 在左下角添加GitHub链接
            github_text = "https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax"
            draw.text((50, height-35), github_text, fill='#666666', font=small_font)
            
            # 在右下角添加Astrbot.png图片
            try:
                # 加载Astrbot.png图片
                astrbot_path = os.path.join(os.path.dirname(__file__), "Astrbot.png")
                if os.path.exists(astrbot_path):
                    # 打开Astrbot图片
                    astrbot_img = PILImage.open(astrbot_path)
                    
                    # 调整图片大小，设置合适的高度（比如60像素，保持宽高比）
                    target_height = 60
                    aspect_ratio = astrbot_img.width / astrbot_img.height
                    target_width = int(target_height * aspect_ratio)
                    
                    # 调整图片大小
                    astrbot_resized = astrbot_img.resize((target_width, target_height), PILImage.Resampling.LANCZOS)
                    
                    # 计算右下角位置（留出10像素边距）
                    x_position = width - target_width - 10
                    y_position = height - target_height - 10
                    
                    # 将Astrbot图片粘贴到主图片上
                    image.paste(astrbot_resized, (x_position, y_position), astrbot_resized if astrbot_resized.mode == 'RGBA' else None)
                    
                    logger.info(f"已将Astrbot.png添加到帮助图片右下角，位置: ({x_position}, {y_position})")
                else:
                    logger.warning(f"Astrbot.png文件不存在: {astrbot_path}")
            except Exception as e:
                logger.error(f"添加Astrbot.png到帮助图片失败: {e}")
            
            # 保存图片 - 使用线程池避免阻塞
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = os.path.join(os.path.dirname(__file__), f"help_{timestamp}.png")
            
            def save_image():
                image.save(image_path, 'PNG', quality=95)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, save_image)
            
            logger.info(f"帮助图片已生成: {image_path}")
            return image_path
            
        except Exception as e:
            logger.error(f"HTML转图片失败: {e}")
            # 如果转换失败，返回一个错误信息的图片
            try:
                width, height = 800, 400
                image = PILImage.new('RGB', (width, height), color='#ff6b6b')
                draw = ImageDraw.Draw(image)
                
                try:
                    font_path = os.path.join(os.path.dirname(__file__), "1.ttf")
                    if os.path.exists(font_path):
                        font = ImageFont.truetype(font_path, 24)
                    else:
                        font = ImageFont.load_default()
                except:
                    font = ImageFont.load_default()
                
                error_text = f"HTML转图片失败\n错误信息: {str(e)}\n请直接访问: {html_url}"
                draw.text((50, 150), error_text, fill='white', font=font)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                image_path = os.path.join(os.path.dirname(__file__), f"help_error_{timestamp}.png")
                
                def save_error_image():
                    image.save(image_path, 'PNG')
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, save_error_image)
                
                return image_path
            except Exception as e2:
                logger.error(f"创建错误图片失败: {e2}")
                raise e

    async def _send_help_as_image(self, event: AstrMessageEvent) -> None:
        """发送帮助信息为图片形式"""
        server_url = None
        image_path = None
        try:
            # 启动临时服务器
            server_url = await self._start_help_server()
            
            try:
                # 转换HTML为图片
                image_path = await self._html_to_image(server_url)
                
                # 发送图片
                await event.send(event.image_result(image_path))
                
                # 延迟清理临时图片（确保发送完成）
                await asyncio.sleep(2)
                
            finally:
                # 确保临时图片被清理
                if image_path and os.path.exists(image_path):
                    try:
                        def remove_file():
                            os.remove(image_path)
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, remove_file)
                        logger.info(f"临时图片已清理: {image_path}")
                    except Exception as e:
                        logger.warning(f"清理临时图片失败: {e}")
                
        except Exception as e:
            logger.error(f"发送帮助图片失败: {e}")
            # 如果发送图片失败，发送文本形式的帮助
            await self._send_help_as_text(event)
        finally:
            # 确保服务器被销毁
            if server_url:
                try:
                    await self._stop_help_server()
                except Exception as e:
                    logger.warning(f"停止帮助服务器失败: {e}")

    async def _send_help_as_text(self, event: AstrMessageEvent) -> None:
        """发送帮助信息为文本形式"""
        # 构建详细的模型列表
        model_details = []
        if self.model_name_map:
            # 使用集合去重，确保每个模型描述只显示一次
            seen_descriptions = set()
            for _, (filename, desc) in self.model_name_map.items():
                if desc not in seen_descriptions:
                    model_details.append(f"  • {desc} (文件: {filename})")
                    seen_descriptions.add(desc)
        else:
            model_details.append("  • 暂无可用模型")
        
        model_help = f"\n🎨 可用模型列表：\n" + "\n".join(model_details) + "\n\n模型使用说明：\n  - 格式：model:描述（描述对应配置中的模型描述）\n  - 例：model:写实风格"
        
        # 构建详细的LoRA列表
        lora_details = []
        if self.lora_name_map:
            # 使用集合去重，确保每个LoRA描述只显示一次
            seen_descriptions = set()
            for _, (filename, desc) in self.lora_name_map.items():
                if desc not in seen_descriptions:
                    lora_details.append(f"  • {desc} (文件: {filename})")
                    seen_descriptions.add(desc)
        else:
            lora_details.append("  • 暂无可用LoRA")
        
        lora_help = f"\n✨ 可用LoRA列表：\n" + "\n".join(lora_details) + "\n\nLoRA使用说明：\n  - 基础格式：lora:描述（使用默认强度1.0/1.0，描述对应列表中的名称）\n  - 仅模型强度：lora:描述:0.8（strength_model=0.8）\n  - 仅CLIP强度：lora:描述!1.0（strength_clip=1.0）\n  - 双强度：lora:描述:0.8!1.3（model=0.8, clip=1.3）\n  - 多LoRA：空格分隔多个lora参数（例：lora:儿童 lora:学生:0.9）"
        
        # 构建服务器信息（包含系统信息）
        server_info_parts = []
        server_info_parts.append(f"\n🌐 服务器信息：")
        
        # 获取每个服务器的系统信息
        for server in self.comfyui_servers:
            if server.healthy:
                server_info_parts.append(f"\n📊 【{server.name}】")
                
                # 获取系统信息
                system_info = await self._get_server_system_info(server)
                if system_info:
                    system_data = system_info.get("system", {})
                    devices_data = system_info.get("devices", [])
                    
                    # 系统信息
                    os_info = system_data.get("os", "未知")
                    pytorch_version = system_data.get("pytorch_version", "未知")
                    ram_total = system_data.get("ram_total", 0) / (1024**3)  # 转换为GB
                    ram_free = system_data.get("ram_free", 0) / (1024**3)  # 转换为GB
                    ram_used = ram_total - ram_free
                    
                    server_info_parts.append(f"  🖥️  系统：{os_info}")
                    server_info_parts.append(f"  🔥 PyTorch：{pytorch_version}")
                    server_info_parts.append(f"  💾 内存：{ram_used:.1f}GB / {ram_total:.1f}GB (已用/总计)")
                    
                    # 设备信息
                    if devices_data:
                        for i, device in enumerate(devices_data):
                            device_name = device.get("name", "未知设备")
                            device_type = device.get("type", "未知")
                            vram_total = device.get("vram_total", 0) / (1024**3)  # 转换为GB
                            vram_free = device.get("vram_free", 0) / (1024**3)  # 转换为GB
                            vram_used = vram_total - vram_free
                            
                            server_info_parts.append(f"  🎮 GPU{i+1}：{device_name}")
                            server_info_parts.append(f"     类型：{device_type}")
                            server_info_parts.append(f"     显存：{vram_used:.1f}GB / {vram_total:.1f}GB (已用/总计)")
                    else:
                        server_info_parts.append(f"  🎮 GPU：未检测到GPU设备")
                else:
                    server_info_parts.append(f"  ❌ 无法获取系统信息")
            else:
                server_info_parts.append(f"\n📊 【{server.name}】")
                server_info_parts.append(f"  ❌ 服务器不可用")
        
        server_info = "".join(server_info_parts)
        
        # 获取当前排队信息
        current_queue_size = self.task_queue.qsize()
        total_user_tasks = sum(self.user_task_counts.values())
        
        help_text = f"""
🎯 ComfyUI AI绘画帮助

⏰ 开放时间：{self.open_time_ranges}

📊 实时状态：
• 当前排队任务：{current_queue_size} 个
• 总任务：{total_user_tasks} 个
• 队列容量：{self.max_task_queue} 个
• 活跃用户数：{len(self.user_task_counts)} 个

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
• 文生图默认批量数：{self.txt2img_batch_size}
• 图生图默认批量数：{self.img2img_batch_size}
• 默认噪声系数：{self.default_denoise}
• 默认模型：{self.ckpt_name}
• 自动保存图片：{'开启' if self.enable_auto_save else '关闭'}
• 输出压缩包：{'开启' if self.enable_output_zip else '关闭'}
• 每日下载限制：{self.daily_download_limit} 次
• 仅限自己图片：{'是' if self.only_own_images else '否'}

📏 参数限制：
• 文生图最大批量：{self.max_txt2img_batch}
• 图生图最大批量：{self.max_img2img_batch}
• 分辨率范围：{self.min_width}~{self.max_width} x {self.min_height}~{self.max_height}
• 任务队列最大：{self.max_task_queue}个
• 每用户最大并发：{self.max_concurrent_tasks_per_user}个

{server_info}
{model_help}
{lora_help}
{self._generate_workflow_text_help()}
        """
        
        await self._send_with_auto_recall(event, event.plain_result(help_text.strip()))

    # 添加帮助信息过滤器
    class HelpFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            messages = event.get_messages()
            has_image = any(isinstance(msg, Image) for msg in messages)
            if has_image:
                return False
            full_text = event.message_obj.message_str.strip()
            # 检查是否单独输入aimg或img2img（无参数）
            return full_text in ["aimg", "img2img"]

    @filter.custom_filter(HelpFilter)
    async def send_help(self, event: AstrMessageEvent) -> None:
        """发送帮助信息"""
        # 检查群聊白名单
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result(
                "❌ 当前群聊不在白名单中，无法使用此功能！"
            ))
            return
        
        if self.enable_help_image:
            await self._send_help_as_image(event)
        else:
            await self._send_help_as_text(event)

    async def _stop_help_server(self):
        """停止帮助服务器"""
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

    async def _get_today_images(self, user_id: Optional[str] = None) -> List[str]:
        """获取今天的图片文件列表"""
        try:
            now = datetime.now()
            # 构建今天的目录路径
            auto_save_path = Path(self.auto_save_dir)
            if auto_save_path.is_absolute():
                today_dir = auto_save_path / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            else:
                today_dir = Path(os.path.dirname(__file__)) / self.auto_save_dir / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            
            if not today_dir.exists():
                return []
            
            # 获取目录下所有图片文件
            image_files = []
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                image_files.extend(today_dir.glob(ext))
            
            # 如果设置了只能获取自己的图片，则过滤
            if self.only_own_images and user_id:
                user_images = set(await self._get_user_images_today(user_id))
                image_files = [f for f in image_files if f.name in user_images]
            
            return [str(f) for f in image_files]
        except Exception as e:
            logger.error(f"获取今日图片列表失败: {e}")
            return []

    async def _create_zip_archive(self, image_files: List[str], user_id: str) -> Optional[str]:
        """创建图片压缩包"""
        try:
            if not image_files:
                return None
            
            # 创建临时压缩包文件
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            zip_filename = f"comfyui_images_{user_id}_{timestamp}.zip"
            
            # 确定压缩包保存位置
            auto_save_path = Path(self.auto_save_dir)
            if auto_save_path.is_absolute():
                zip_path = auto_save_path / zip_filename
            else:
                zip_path = Path(os.path.dirname(__file__)) / self.auto_save_dir / zip_filename
            
            # 创建压缩包 - 使用线程池避免阻塞
            def create_zip():
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for image_file in image_files:
                        if os.path.exists(image_file):
                            # 使用文件名作为压缩包内的路径
                            arcname = os.path.basename(image_file)
                            zipf.write(image_file, arcname)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, create_zip)
            
            logger.info(f"压缩包创建成功: {zip_path}")
            return str(zip_path)
        except Exception as e:
            logger.error(f"创建压缩包失败: {e}")
            return None

    async def _download_video_file(self, event: AstrMessageEvent, server: ServerState, video_info: Dict[str, Any]) -> Optional[str]:
        """下载视频文件到临时目录"""
        try:
            # 获取视频URL
            video_url = video_info["url"]
            filename = video_info["filename"]
            
            # 下载视频文件
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url, timeout=120) as resp:  # 增加超时时间，视频文件较大
                    if resp.status != 200:
                        logger.warning(f"下载视频失败，HTTP状态码: {resp.status}")
                        return None
                    video_data = await resp.read()
            
            # 创建临时文件目录
            temp_dir = self.data_dir / "temp"
            temp_dir.mkdir(exist_ok=True)
            
            # 保存到临时文件
            temp_video_path = temp_dir / filename
            def write_video_file():
                with open(temp_video_path, 'wb') as f:
                    f.write(video_data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, write_video_file)
            
            logger.info(f"视频已下载到临时文件: {temp_video_path}")
            
            # 如果启用了自动保存，则永久保存视频文件
            saved_filename = None
            if self.enable_auto_save:
                try:
                    # 使用_save_image_locally方法保存文件，传入正确的参数
                    saved_filename = await self._save_image_locally(
                        server=server,
                        filename=filename,
                        prompt="视频生成",
                        user_id=event.get_sender_id() or "",
                        subfolder=video_info.get("subfolder", ""),
                        file_type=video_info.get("type", "output")
                    )
                    if saved_filename:
                        logger.info(f"视频已自动保存: {saved_filename}")
                except Exception as e:
                    logger.warning(f"视频自动保存失败: {e}")
            
            # 创建清理任务
            asyncio.create_task(self._cleanup_temp_video_file(temp_video_path))
            
            return str(temp_video_path)
            
        except Exception as e:
            logger.error(f"下载视频失败: {e}")
            return None

    async def _cleanup_temp_video_file(self, temp_video_path) -> None:
        """延迟清理临时视频文件"""
        try:
            await asyncio.sleep(10)  # 等待10秒后清理
            if temp_video_path.exists():
                temp_video_path.unlink()
                logger.info(f"临时视频文件已清理: {temp_video_path}")
        except Exception as e:
            logger.warning(f"清理临时视频文件失败: {e}")



    async def _send_video(self, event: AstrMessageEvent, video_path: str, filename: str, video_idx: int) -> None:
        """发送视频（小于100MB直接发送，大于100MB上传为群文件）"""
        try:
            # 获取文件大小
            file_size_bytes = os.path.getsize(video_path)
            file_size_mb = file_size_bytes / (1024 * 1024)
            
            # Windows路径兼容处理
            video_path_compat = os.path.abspath(video_path).replace("\\", "/")
            
            # 校验文件读取权限
            if not os.access(video_path, os.R_OK):
                raise PermissionError(f"程序无读取权限，请检查文件权限设置")
            
            # 简单校验MP4文件有效性
            with open(video_path, "rb") as f:
                file_header = f.read(4)
            if file_header not in (b'\x00\x00\x00\x18', b'\x00\x00\x00\x1C', b'ftyp'):
                logger.warning(f"⚠️  检测到非标准MP4文件头：{file_header.hex()}，可能无法正常发送")
            
            if file_size_mb > self.max_upload_size:
                # 大于100MB，上传为群文件
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    group_id = event.get_group_id()
                    
                    if group_id:
                        await client.upload_group_file(group_id=group_id, file=video_path_compat, name=filename)
                        logger.info(f"视频{video_idx}（{file_size_mb:.1f}MB）超过{self.max_upload_size}MB，已上传为群文件：{filename}")
                    else:
                        sender_qq = event.get_sender_id()
                        await client.upload_private_file(user_id=int(sender_qq), file=video_path_compat, name=filename)
                        logger.info(f"视频{video_idx}（{file_size_mb:.1f}MB）超过{self.max_upload_size}MB，已上传为私聊文件：{filename}")
                else:
                    logger.warning("非QQ平台，不支持文件上传")
            else:
                # 小于100MB，直接发送视频
                await event.send(event.chain_result([Video.fromFileSystem(video_path_compat)]))
                logger.info(f"视频{video_idx}（{file_size_mb:.1f}MB）直接发送成功！")
                
        except PermissionError as e:
            logger.error(f"❌ 视频发送失败: {str(e)}")
        except FileNotFoundError as e:
            logger.error(f"❌ 视频发送失败: {str(e)}")
        except Exception as e:
            logger.error(f"❌ 视频{video_idx}发送失败: {str(e)}")


            logger.error(f"上传视频文件失败: {e}")
            return False

    async def _download_and_upload_audio(self, event: AstrMessageEvent, server: ServerState, audio_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """下载音频文件并上传到QQ群文件或个人文件，返回处理结果信息"""
        try:
            # 获取音频URL
            audio_url = audio_info["url"]
            filename = audio_info["filename"]
            
            # 下载音频文件
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url, timeout=60) as resp:  # 音频文件通常比视频小
                    if resp.status != 200:
                        logger.warning(f"下载音频失败，HTTP状态码: {resp.status}")
                        return None
                    audio_data = await resp.read()
            
            # 创建临时文件目录
            temp_dir = self.data_dir / "temp"
            temp_dir.mkdir(exist_ok=True)
            
            # 保存到临时文件
            temp_audio_path = temp_dir / filename
            def write_audio_file():
                with open(temp_audio_path, 'wb') as f:
                    f.write(audio_data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, write_audio_file)
            
            logger.info(f"音频已下载到临时文件: {temp_audio_path}")
            
            # 如果启用了自动保存，则永久保存音频文件
            saved_filename = None
            if self.enable_auto_save:
                try:
                    # 使用_save_image_locally方法保存文件，传入正确的参数
                    saved_filename = await self._save_image_locally(
                        server=server,
                        filename=filename,
                        prompt="音频生成",
                        user_id=event.get_sender_id() or "",
                        subfolder=audio_info.get("subfolder", ""),
                        file_type=audio_info.get("type", "output")
                    )
                    if saved_filename:
                        logger.info(f"音频已自动保存: {saved_filename}")
                except Exception as e:
                    logger.warning(f"音频自动保存失败: {e}")
            
            # 检测音频时长（如果ffmpeg未安装会返回None）
            duration = await self._get_audio_duration(str(temp_audio_path))
            if duration is None:
                logger.warning("无法获取音频时长，可能是因为ffprobe未安装，将作为文件上传")
            
            # 上传音频文件到QQ
            upload_success = await self._upload_audio_file(event, str(temp_audio_path), filename, duration)
            
            # 清理临时文件
            try:
                await asyncio.sleep(2)  # 等待上传完成
                def remove_temp_file():
                    if temp_audio_path.exists():
                        temp_audio_path.unlink()
                await loop.run_in_executor(None, remove_temp_file)
                logger.info(f"临时音频文件已清理: {temp_audio_path}")
            except Exception as e:
                logger.warning(f"清理临时音频文件失败: {e}")
            
            # 返回处理结果
            if upload_success:
                duration_info = f"{duration:.2f}秒" if duration else "未知时长"
                return {
                    "success": True,
                    "duration": duration,
                    "duration_info": duration_info,
                    "temp_path": str(temp_audio_path)
                }
            else:
                return None
            
        except Exception as e:
            logger.error(f"下载并上传音频失败: {e}")
            return None

    async def _get_audio_duration(self, audio_path: str) -> Optional[float]:
        """获取音频时长（秒）"""
        try:
            # 使用ffprobe获取音频时长
            cmd = [
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', audio_path
            ]
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            ))
            
            if result.returncode == 0:
                duration = float(result.stdout.strip())
                logger.info(f"音频时长: {duration:.2f}秒")
                return duration
            else:
                logger.warning(f"获取音频时长失败: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"获取音频时长异常: {e}")
            return None

    async def _convert_to_wav(self, input_path: str, output_path: str) -> bool:
        """将音频转换为WAV格式"""
        try:
            # 使用ffmpeg转换为WAV格式
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-acodec', 'pcm_s16le', '-ar', '24000', '-ac', '1',
                output_path
            ]
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            ))
            
            if result.returncode == 0:
                logger.info(f"音频已转换为WAV格式: {output_path}")
                return True
            else:
                logger.warning(f"音频格式转换失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"音频格式转换异常: {e}")
            return False

    async def _upload_audio_file(self, event: AstrMessageEvent, audio_path: str, filename: str, duration: Optional[float] = None) -> bool:
        """上传音频文件到QQ群文件或个人文件，如果小于30秒则发送为语音消息"""
        try:
            # 检查是否为QQ平台
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if isinstance(event, AiocqhttpMessageEvent):
                client = event.bot
                
                # 获取发送者和群组信息
                group_id = event.get_group_id()
                sender_qq = event.get_sender_id()
                
                # 检查是否启用音频转语音功能和音频时长
                # 如果未启用转语音、duration为None（ffprobe未安装），直接走文件上传逻辑
                if self.enable_audio_to_voice and duration is not None and duration <= 30:
                    logger.info(f"音频时长{duration:.2f}秒 <= 30秒，启用转语音功能，尝试转换为语音消息发送")
                    
                    # 转换为WAV格式
                    wav_path = audio_path.rsplit('.', 1)[0] + '.wav'
                    if await self._convert_to_wav(audio_path, wav_path):
                        try:
                            # 创建语音消息组件并发送
                            record_msg = Record(file=wav_path, url=wav_path)
                            
                            if group_id:
                                await client.send_group_msg(
                                    group_id=int(group_id),
                                    message=record_msg
                                )
                                logger.info(f"语音消息已发送到群聊: 群ID={group_id}")
                            else:
                                await client.send_private_msg(
                                    user_id=int(sender_qq),
                                    message=record_msg
                                )
                                logger.info(f"语音消息已发送给用户: 用户QQ={sender_qq}")
                            
                            # 清理临时WAV文件
                            try:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, lambda: os.unlink(wav_path) if os.path.exists(wav_path) else None
                                )
                            except:
                                pass
                            
                            return True
                            
                        except Exception as e:
                            logger.warning(f"发送语音消息失败，转为文件上传: {e}")
                            # 如果语音发送失败，继续下面的文件上传逻辑
                    
                    else:
                        logger.warning("WAV格式转换失败，转为文件上传")
                
                # 文件上传逻辑（大于30秒、ffmpeg未安装、语音发送失败或转语音功能未启用时执行）
                if duration is None:
                    logger.info("无法获取音频时长，上传为文件")
                elif duration > 30:
                    if self.enable_audio_to_voice:
                        logger.info(f"音频时长{duration:.2f}秒 > 30秒，转语音功能已启用但超时，上传为文件")
                    else:
                        logger.info(f"音频时长{duration:.2f}秒，转语音功能未启用，直接上传为文件")
                elif not self.enable_audio_to_voice:
                    logger.info(f"音频时长{duration:.2f}秒 <= 30秒，但转语音功能未启用，上传为文件")
                else:
                    logger.info("语音发送失败或转换失败，上传为文件")
                
                if group_id:
                    # 群聊：上传到群文件
                    await client.upload_group_file(
                        group_id=int(group_id),
                        file=audio_path,
                        name=filename
                    )
                    logger.info(f"音频已上传到群文件: 群ID={group_id}, 文件={filename}")
                else:
                    # 私聊：上传到个人文件
                    await client.upload_private_file(
                        user_id=int(sender_qq),
                        file=audio_path,
                        name=filename
                    )
                    logger.info(f"音频已上传到个人文件: 用户QQ={sender_qq}, 文件={filename}")
                
                return True
            else:
                logger.warning("非QQ平台，不支持音频文件上传")
                return False
                
        except Exception as e:
            logger.error(f"上传音频文件失败: {e}")
            return False

    async def _download_and_upload_3d_model(self, event: AstrMessageEvent, server: ServerState, model_3d_info: Dict[str, Any]) -> Optional[str]:
        """下载3D模型文件并上传到QQ群文件或个人文件"""
        try:
            # 获取3D模型URL
            model_3d_url = model_3d_info["url"]
            filename = model_3d_info["filename"]
            
            # 下载3D模型文件
            async with aiohttp.ClientSession() as session:
                async with session.get(model_3d_url, timeout=120) as resp:  # 3D模型文件可能较大
                    if resp.status != 200:
                        logger.warning(f"下载3D模型失败，HTTP状态码: {resp.status}")
                        return None
                    model_3d_data = await resp.read()
            
            # 创建临时文件目录
            temp_dir = self.data_dir / "temp"
            temp_dir.mkdir(exist_ok=True)
            
            # 保存到临时文件
            temp_model_3d_path = temp_dir / filename
            def write_model_3d_file():
                with open(temp_model_3d_path, 'wb') as f:
                    f.write(model_3d_data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, write_model_3d_file)
            
            logger.info(f"3D模型已下载到临时文件: {temp_model_3d_path}")
            
            # 如果启用了自动保存，则永久保存3D模型文件
            saved_filename = None
            if self.enable_auto_save:
                try:
                    # 使用_save_image_locally方法保存文件，传入正确的参数
                    saved_filename = await self._save_image_locally(
                        server=server,
                        filename=filename,
                        prompt="3D模型生成",
                        user_id=event.get_sender_id() or "",
                        subfolder=model_3d_info.get("subfolder", ""),
                        file_type=model_3d_info.get("type", "output")
                    )
                    if saved_filename:
                        logger.info(f"3D模型已自动保存: {saved_filename}")
                except Exception as e:
                    logger.warning(f"3D模型自动保存失败: {e}")
            
            # 上传3D模型文件到QQ
            upload_success = await self._upload_3d_model_file(event, str(temp_model_3d_path), filename)
            
            # 清理临时文件
            try:
                await asyncio.sleep(2)  # 等待上传完成
                def remove_temp_file():
                    if temp_model_3d_path.exists():
                        temp_model_3d_path.unlink()
                await loop.run_in_executor(None, remove_temp_file)
                logger.info(f"临时3D模型文件已清理: {temp_model_3d_path}")
            except Exception as e:
                logger.warning(f"清理临时3D模型文件失败: {e}")
            
            return str(temp_model_3d_path) if upload_success else None
            
        except Exception as e:
            logger.error(f"下载并上传3D模型失败: {e}")
            return None

    async def _upload_3d_model_file(self, event: AstrMessageEvent, model_3d_path: str, filename: str) -> bool:
        """上传3D模型文件到群文件或个人文件"""
        try:
            # 检查是否为QQ平台
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                logger.info("非QQ平台不支持文件上传功能")
                return False
            
            # 获取群ID和发送者QQ号
            group_id = event.get_group_id()
            sender_qq = event.get_sender_id()
            
            if group_id:  # 群聊场景
                client = event.bot
                await client.upload_group_file(
                    group_id=group_id,
                    file=model_3d_path,
                    name=filename
                )
                logger.info(f"3D模型已上传到群文件: 群ID={group_id}, 文件={filename}")
            else:  # 私聊场景
                client = event.bot
                await client.upload_private_file(
                    user_id=int(sender_qq),
                    file=model_3d_path,
                    name=filename
                )
                logger.info(f"3D模型已上传到个人文件: 用户QQ={sender_qq}, 文件={filename}")
            
            return True
        except Exception as e:
            logger.error(f"上传3D模型文件失败: {e}")
            return False

    async def _upload_zip_file(self, event: AstrMessageEvent, zip_path: str) -> bool:
        """上传压缩包到群文件或个人文件"""
        try:
            # 检查是否为QQ平台
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                logger.info("非QQ平台不支持文件上传功能")
                return False
            
            # 获取群ID和发送者QQ号
            group_id = event.get_group_id()
            sender_qq = event.get_sender_id()
            
            zip_filename = os.path.basename(zip_path)
            
            if group_id:  # 群聊场景
                client = event.bot
                await client.upload_group_file(
                    group_id=group_id,
                    file=zip_path,
                    name=zip_filename
                )
                logger.info(f"压缩包已上传到群文件: 群ID={group_id}, 文件={zip_filename}")
            else:  # 私聊场景
                client = event.bot
                await client.upload_private_file(
                    user_id=int(sender_qq),
                    file=zip_path,
                    name=zip_filename
                )
                logger.info(f"压缩包已上传到个人文件: 用户QQ={sender_qq}, 文件={zip_filename}")
            
            return True
        except Exception as e:
            logger.error(f"上传压缩包失败: {e}")
            return False

    async def _upload_html_file(self, event: AstrMessageEvent, html_path: str) -> bool:
        """上传HTML文件到群文件或个人文件"""
        try:
            # 检查是否为QQ平台
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if not isinstance(event, AiocqhttpMessageEvent):
                logger.info("非QQ平台不支持文件上传功能")
                return False
            
            # 获取群ID和发送者QQ号
            group_id = event.get_group_id()
            sender_qq = event.get_sender_id()
            
            html_filename = os.path.basename(html_path)
            
            if group_id:  # 群聊场景
                client = event.bot
                await client.upload_group_file(
                    group_id=group_id,
                    file=html_path,
                    name=html_filename
                )
                logger.info(f"HTML文件已上传到群文件: 群ID={group_id}, 文件={html_filename}")
            else:  # 私聊场景
                client = event.bot
                await client.upload_private_file(
                    user_id=int(sender_qq),
                    file=html_path,
                    name=html_filename
                )
                logger.info(f"HTML文件已上传到个人文件: 用户QQ={sender_qq}, 文件={html_filename}")
            
            return True
        except Exception as e:
            logger.error(f"上传HTML文件失败: {e}")
            return False

    # 添加输出压缩包过滤器
    class OutputZipFilter(CustomFilter):
        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            messages = event.get_messages()
            has_image = any(isinstance(msg, Image) for msg in messages)
            if has_image:
                return False
            full_text = event.message_obj.message_str.strip()
            return full_text == "comfyuioutput"

    @filter.custom_filter(OutputZipFilter)
    async def handle_output_zip(self, event: AstrMessageEvent) -> None:
        """处理输出压缩包指令"""
        try:
            # 检查群聊白名单
            if not self._check_group_whitelist(event):
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 当前群聊不在白名单中，无法使用此功能！"
                ))
                return
            
            # 检查是否开启了自动保存功能
            if not self.enable_auto_save:
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 未开启图片自动保存功能，无法生成压缩包！\n"
                    "请联系管理员在配置中开启 enable_auto_save 功能。"
                ))
                return
            
            # 检查是否开启了输出压缩包功能
            if not self.enable_output_zip:
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 未开启输出压缩包功能！\n"
                    "请联系管理员在配置中开启 enable_output_zip 功能。"
                ))
                return
            
            # 获取用户ID
            user_id = str(event.get_sender_id())
            
            # 检查下载次数限制
            can_download, current_count = await self._check_download_limit(user_id)
            if not can_download:
                await self._send_with_auto_recall(event, event.plain_result(
                    f"❌ 今日下载次数已达上限！\n"
                    f"当前已下载: {current_count} 次\n"
                    f"每日限制: {self.daily_download_limit} 次\n"
                    f"请明天再试～"
                ))
                return
            
            # 获取今天的图片
            await self._send_with_auto_recall(event, event.plain_result("🔍 正在搜索今天的图片..."))
            image_files = await self._get_today_images(user_id)
            
            if not image_files:
                await self._send_with_auto_recall(event, event.plain_result(
                    "📭 今天还没有生成图片哦～\n"
                    "先使用 aimg 或 img2img 指令生成一些图片吧！"
                ))
                return
            
            await self._send_with_auto_recall(event, event.plain_result(f"📁 找到 {len(image_files)} 张图片，正在生成压缩包..."))
            
            # 创建压缩包
            zip_path = await self._create_zip_archive(image_files, user_id)
            if not zip_path:
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 压缩包创建失败，请稍后重试！"
                ))
                return
            
            # 使用 try-finally 确保压缩包被清理
            try:
                await self._send_with_auto_recall(event, event.plain_result("📦 压缩包创建完成，正在上传..."))
                
                # 上传压缩包
                upload_success = await self._upload_zip_file(event, zip_path)
                
                if upload_success:
                    # 更新下载次数
                    await self._increment_download_count(user_id)
                    
                    # 获取更新后的下载次数
                    _, new_count = await self._check_download_limit(user_id)
                    
                    await self._send_with_auto_recall(event, event.plain_result(
                        f"✅ 压缩包上传成功！\n"
                        f"📁 文件名: {os.path.basename(zip_path)}\n"
                        f"📊 包含图片: {len(image_files)} 张\n"
                        f"📈 今日已下载: {new_count}/{self.daily_download_limit} 次\n"
                        f"💡 提示: 请从群文件或私聊文件中下载"
                    ))
                else:
                    # 检查是否为平台不支持
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    if not isinstance(event, AiocqhttpMessageEvent):
                        await self._send_with_auto_recall(event, event.plain_result(
                            "❌ 当前平台不支持文件上传功能！\n"
                            "📱 压缩包上传仅支持QQ平台\n"
                            "💡 如需获取图片，请使用QQ平台发送此指令"
                        ))
                    else:
                        await self._send_with_auto_recall(event, event.plain_result(
                            "❌ 压缩包上传失败，请稍后重试！"
                        ))
            
            finally:
                # 确保临时压缩包被清理
                if zip_path and os.path.exists(zip_path):
                    try:
                        # 延迟删除，确保上传完成
                        await asyncio.sleep(5)
                        def remove_zip():
                            os.remove(zip_path)
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, remove_zip)
                        logger.info(f"临时压缩包已清理: {zip_path}")
                    except Exception as e:
                        logger.warning(f"清理临时压缩包失败: {e}")
                
        except Exception as e:
            logger.error(f"处理输出压缩包指令失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(
                "❌ 处理请求时发生错误，请稍后重试！"
            ))

    @filter.custom_filter(TomatoDecryptFilter)
    async def handle_tomato_decrypt(self, event: AstrMessageEvent) -> None:
        """处理小番茄图片解密指令"""
        try:
            # 检查群聊白名单
            if not self._check_group_whitelist(event):
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 当前群聊不在白名单中，无法使用此功能！"
                ))
                return
            
            # 获取HTML文件路径
            html_file_path = os.path.join(os.path.dirname(__file__), "解密.html")
            
            # 检查HTML文件是否存在
            if not os.path.exists(html_file_path):
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 解密工具文件不存在！请联系管理员检查文件。"
                ))
                return
            
            await self._send_with_auto_recall(event, event.plain_result("🔧 正在发送小番茄图片解密工具..."))
            
            # 上传HTML文件
            upload_success = await self._upload_html_file(event, html_file_path)
            
            if upload_success:
                await self._send_with_auto_recall(event, event.plain_result(
                    "✅ 小番茄图片解密工具发送成功！\n"
                    f"📁 文件名: 解密.html\n"
                    "💡 使用说明:\n"
                    "  1. 从群文件或私聊文件中下载解密.html\n"
                    "  2. 用浏览器打开该HTML文件\n"
                    "  3. 选择需要解密的图片文件\n"
                    "  4. 点击解密按钮即可还原图片\n"
                    "  5. 支持批量解密和自动下载"
                ))
            else:
                # 检查是否为平台不支持
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if not isinstance(event, AiocqhttpMessageEvent):
                    await self._send_with_auto_recall(event, event.plain_result(
                        "❌ 当前平台不支持文件上传功能！\n"
                        "📱 文件上传仅支持QQ平台\n"
                        "💡 如需获取解密工具，请使用QQ平台发送此指令"
                    ))
                else:
                    await self._send_with_auto_recall(event, event.plain_result(
                        "❌ 解密工具发送失败，请稍后重试！"
                    ))
                
        except Exception as e:
            logger.error(f"处理小番茄图片解密指令失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(f"❌ 处理请求失败: {str(e)}"))

    @filter.custom_filter(TeeeFilter)
    async def handle_teee(self, event: AstrMessageEvent) -> None:
        """处理teeee指令，获取并分析转发消息内容"""
        try:
            messages = event.get_messages()
            
            # 构建输出信息
            output_lines = ["📋 转发消息元信息分析：\n"]
            
            # 获取基本信息 - 修复属性名获取问题
            # 首先尝试从event对象直接获取
            user_id = getattr(event, 'user_id', getattr(event.message_obj, 'sender_id', getattr(event.message_obj, 'user_id', 'Unknown')))
            group_id = getattr(event, 'group_id', getattr(event.message_obj, 'group_id', None))
            message_id = getattr(event.message_obj, 'message_id', getattr(event.message_obj, 'message_seq', 'Unknown'))
            time_raw = getattr(event.message_obj, 'time', getattr(event.message_obj, 'timestamp', 'Unknown'))
            
            # 尝试从消息组件中获取更准确的信息（如果有Reply组件）
            for msg in messages:
                if hasattr(msg, 'sender_id') and msg.sender_id != 'Unknown':
                    user_id = msg.sender_id
                if hasattr(msg, 'time') and msg.time != 'Unknown':
                    time_raw = msg.time
                if hasattr(msg, 'qq') and msg.qq != 'Unknown':
                    user_id = msg.qq
            
            # 格式化时间戳
            if time_raw != 'Unknown' and isinstance(time_raw, (int, float)):
                try:
                    import time as time_module
                    time_str = time_module.strftime('%Y-%m-%d %H:%M:%S', time_module.localtime(time_raw))
                except:
                    time_str = str(time_raw)
            else:
                time_str = str(time_raw) if time_raw != 'Unknown' else 'Unknown'
            
            output_lines.append(f"📤 发送者ID: {user_id}")
            if group_id:
                output_lines.append(f"👥 群聊ID: {group_id}")
            output_lines.append(f"🆔 消息ID: {message_id}")
            output_lines.append(f"⏰ 时间戳: {time_str}")
            output_lines.append("")
            
            # 分析消息组件
            output_lines.append(f"🔍 消息组件分析 (共{len(messages)}个组件)：")
            
            forward_content_found = False
            
            for i, msg in enumerate(messages, 1):
                output_lines.append(f"\n--- 组件 {i} ---")
                output_lines.append(f"📦 类型: {type(msg).__name__}")
                output_lines.append(f"🏷️  模块名: {msg.__class__.__module__}")
                
                # 获取组件的所有属性
                attributes = {}
                for attr_name in dir(msg):
                    if not attr_name.startswith('_'):
                        try:
                            attr_value = getattr(msg, attr_name)
                            if not callable(attr_value):
                                attributes[attr_name] = attr_value
                        except Exception:
                            continue
                
                # 打印所有属性
                for attr_name, attr_value in attributes.items():
                    # 对于可能很长的内容，进行截断处理
                    if isinstance(attr_value, str) and len(attr_value) > 100:
                        attr_value = attr_value[:100] + "..."
                    elif isinstance(attr_value, (list, tuple)) and len(attr_value) > 5:
                        attr_value = f"{type(attr_value).__name__}(长度:{len(attr_value)})"
                    
                    output_lines.append(f"  {attr_name}: {attr_value}")
                
                # 特殊处理Reply组件，尝试获取转发内容
                if hasattr(msg, 'type') and hasattr(msg, 'chain') and msg.chain:
                    output_lines.append(f"  📋 转发链长度: {len(msg.chain)}")
                    
                    for j, chain_msg in enumerate(msg.chain, 1):
                        output_lines.append(f"    链节点{j}: {type(chain_msg).__name__}")
                        
                        # 如果是Forward组件，尝试获取转发消息内容
                        if hasattr(chain_msg, 'type') and chain_msg.type.value == 'Forward':
                            forward_content_found = True
                            forward_id = getattr(chain_msg, 'id', None)
                            
                            if forward_id:
                                output_lines.append(f"      📤 转发消息ID: {forward_id}")
                                
                                # 尝试获取转发消息内容
                                try:
                                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                                    if isinstance(event, AiocqhttpMessageEvent):
                                        client = event.bot
                                        
                                        # 调用get_forward_msg API获取转发内容
                                        forward_result = await client.api.call_action("get_forward_msg", message_id=forward_id)
                                        logger.debug(f"转发消息API返回: {forward_result}")
                                        
                                        if forward_result:
                                            # 检查不同的数据结构
                                            forward_messages = None
                                            if "messages" in forward_result:
                                                forward_messages = forward_result["messages"]
                                            elif "data" in forward_result and isinstance(forward_result["data"], dict) and "messages" in forward_result["data"]:
                                                forward_messages = forward_result["data"]["messages"]
                                            elif "data" in forward_result and isinstance(forward_result["data"], list):
                                                forward_messages = forward_result["data"]
                                            elif isinstance(forward_result, list):
                                                forward_messages = forward_result
                                            
                                            if forward_messages:
                                                output_lines.append(f"      📨 转发消息数量: {len(forward_messages)}")
                                                output_lines.append("")
                                                output_lines.append("🎯 转发消息内容详情：")
                                                output_lines.append("=" * 60)
                                                
                                                for k, forward_msg in enumerate(forward_messages, 1):
                                                    output_lines.append(f"\n【转发消息 {k}】")
                                                    
                                                    # 尝试多种方式获取发送者信息
                                                    sender_name = "Unknown"
                                                    sender_id = "Unknown"
                                                    msg_time = "Unknown"
                                                    
                                                    # 标准字段
                                                    if isinstance(forward_msg, dict):
                                                        # 输出调试信息
                                                        output_lines.append(f"🔍 调试: 消息字段 = {list(forward_msg.keys())}")
                                                        
                                                        # 获取发送者信息 - 支持多种格式
                                                        sender_info = forward_msg.get('sender', {})
                                                        if isinstance(sender_info, dict):
                                                            # 调试：显示sender字段的所有内容
                                                            output_lines.append(f"🔍 发送者字段详情: {sender_info}")
                                                            sender_name = sender_info.get('nickname', sender_info.get('card', sender_info.get('name', 'Unknown')))
                                                            sender_id = sender_info.get('user_id', sender_info.get('uid', 'Unknown'))
                                                        else:
                                                            # 调试：显示sender字段的类型和内容
                                                            output_lines.append(f"🔍 发送者字段类型: {type(sender_info)}, 内容: {sender_info}")
                                                            # 兜底：直接从顶级字段获取
                                                            sender_name = forward_msg.get('sender_name', forward_msg.get('nickname', forward_msg.get('name', str(sender_info) if sender_info else 'Unknown')))
                                                            sender_id = forward_msg.get('sender_id', forward_msg.get('user_id', 'Unknown'))
                                                        
                                                        msg_time = forward_msg.get('time', forward_msg.get('timestamp', 'Unknown'))
                                                    
                                                    output_lines.append(f"👤 发送者: {sender_name} ({sender_id})")
                                                    
                                                    # 格式化时间戳
                                                    if msg_time != "Unknown" and isinstance(msg_time, (int, float)):
                                                        try:
                                                            import time as time_module
                                                            formatted_time = time_module.strftime('%Y-%m-%d %H:%M:%S', time_module.localtime(msg_time))
                                                            output_lines.append(f"⏰ 时间: {formatted_time}")
                                                        except:
                                                            output_lines.append(f"⏰ 时间戳: {msg_time}")
                                                    else:
                                                        output_lines.append(f"⏰ 时间: {msg_time}")
                                                    
                                                    # 解析消息内容
                                                    msg_content = ""
                                                    if isinstance(forward_msg, dict):
                                                        msg_content = forward_msg.get('message', forward_msg.get('content', ''))
                                                    
                                                    if msg_content:
                                                        output_lines.append("📝 内容:")
                                                        
                                                        # 如果是字符串，尝试解析CQ码
                                                        if isinstance(msg_content, str):
                                                            # 直接显示原始CQ码用于调试
                                                            if len(msg_content) <= 200:
                                                                output_lines.append(f"  🔧 原始CQ码: {msg_content}")
                                                            else:
                                                                output_lines.append(f"  🔧 原始CQ码: {msg_content[:200]}...[截断]")
                                                            
                                                            # 尝试解析CQ码
                                                            try:
                                                                from astrbot.api.message import Message
                                                                parsed_message = Message(msg_content)
                                                                plain_text = parsed_message.extract_plain_text()
                                                                
                                                                if plain_text.strip():
                                                                    # 处理长文本，截断显示
                                                                    if len(plain_text) > 500:
                                                                        plain_text = plain_text[:500] + "...\n[文本过长，已截断]"
                                                                    output_lines.append(f"  📄 文字: {plain_text}")
                                                                
                                                                # 检查其他类型的内容
                                                                for segment in parsed_message:
                                                                    if hasattr(segment, 'type'):
                                                                        seg_type = getattr(segment.type, 'value', str(segment.type))
                                                                        if seg_type == 'image':
                                                                            output_lines.append(f"  🖼️  图片: [CQ图片]")
                                                                        elif seg_type == 'video':
                                                                            output_lines.append(f"  🎥 视频: [CQ视频]")
                                                                        elif seg_type == 'record':
                                                                            output_lines.append(f"  🎵 语音: [CQ语音]")
                                                                        elif seg_type == 'face':
                                                                            output_lines.append(f"  😊 表情: [CQ表情]")
                                                                        elif seg_type == 'at':
                                                                            output_lines.append(f"  👤 @提醒: [CQ@]")
                                                                        elif seg_type not in ['text']:
                                                                            output_lines.append(f"  📎 其他{seg_type}: [CQ{seg_type}]")
                                                                
                                                            except Exception as parse_error:
                                                                logger.debug(f"解析CQ码失败: {parse_error}")
                                                                output_lines.append(f"  ⚠️  CQ码解析失败: {str(parse_error)}")
                                                                
                                                        elif isinstance(msg_content, list):
                                                            # 如果是列表格式，直接遍历
                                                            output_lines.append(f"  📋 消息为列表格式，共{len(msg_content)}个元素:")
                                                            for j, segment in enumerate(msg_content, 1):
                                                                if isinstance(segment, dict):
                                                                    seg_type = segment.get('type', 'unknown')
                                                                    seg_data = segment.get('data', {})
                                                                    output_lines.append(f"    元素{j}: {seg_type} - {seg_data}")
                                                                
                                                                    if seg_type == 'text':
                                                                        text_content = seg_data.get('text', '')
                                                                        if text_content and len(text_content) > 500:
                                                                            text_content = text_content[:500] + "...[截断]"
                                                                        output_lines.append(f"      📄 文字: {text_content}")
                                                                    elif seg_type == 'image':
                                                                        output_lines.append(f"      🖼️  图片: [CQ图片]")
                                                                    elif seg_type == 'face':
                                                                        output_lines.append(f"      😊 表情: {seg_data.get('id', 'unknown')}")
                                                           
                                                        else:
                                                            output_lines.append(f"  📄 内容类型: {type(msg_content)}")
                                                            if len(str(msg_content)) > 300:
                                                                msg_content = str(msg_content)[:300] + "..."
                                                            output_lines.append(f"  📄 内容: {msg_content}")
                                                    else:
                                                        output_lines.append("📝 内容: [空或非字符串]")
                                                    
                                                    # 添加分隔线（除了最后一个消息）
                                                    if k < len(forward_messages):
                                                        output_lines.append("-" * 40)
                                            else:
                                                output_lines.append(f"      ❌ 无法提取转发消息: 数据结构异常")
                                                output_lines.append(f"      🔍 调试: 返回数据 = {str(forward_result)[:200]}...")
                                        else:
                                            output_lines.append(f"      ❌ API返回空结果")
                                            
                                    else:
                                        output_lines.append(f"      ❌ 当前平台不支持获取转发内容")
                                        
                                except Exception as forward_error:
                                    logger.error(f"获取转发消息内容失败: {forward_error}")
                                    output_lines.append(f"      ❌ 获取转发内容失败: {str(forward_error)}")
                            else:
                                output_lines.append(f"      ⚠️  转发消息ID为空")
                    
                    # 如果链太长，提示省略
                    if len(msg.chain) > 3:
                        output_lines.append(f"    ... 还有{len(msg.chain)-3}个节点")
            
            output_lines.append("\n" + "="*50)
            
            if forward_content_found:
                output_lines.append("✅ 元信息分析完成，已提取转发消息内容")
            else:
                output_lines.append("✅ 元信息分析完成")
                output_lines.append("💡 提示: 如果消息中包含转发内容但未显示，请确保:")
                output_lines.append("  • 消息包含有效的转发组件")
                output_lines.append("  • 机器人有权限访问转发消息")
                output_lines.append("  • 转发消息未过期或被撤回")
            
            # 发送结果
            result_text = "\n".join(output_lines)
            
            # 如果结果太长，分段发送
            if len(result_text) > 4000:
                # 分段发送，避免消息过长
                lines = result_text.split('\n')
                current_chunk = []
                current_length = 0
                
                for line in lines:
                    if current_length + len(line) + 1 > 3800:  # 留一些余量
                        if current_chunk:
                            await self._send_with_auto_recall(event, event.plain_result('\n'.join(current_chunk)))
                            current_chunk = []
                            current_length = 0
                            await asyncio.sleep(0.5)  # 避免发送过快
                    
                    current_chunk.append(line)
                    current_length += len(line) + 1
                
                if current_chunk:
                    await self._send_with_auto_recall(event, event.plain_result('\n'.join(current_chunk)))
            else:
                await self._send_with_auto_recall(event, event.plain_result(result_text))
            
        except Exception as e:
            logger.error(f"处理teeee指令失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(f"❌ 处理teeee指令失败: {str(e)}"))

    async def cleanup_temp_files(self) -> None:
        """清理临时文件"""
        try:
            temp_files = glob.glob(os.path.join(os.path.dirname(__file__), "help_*.png"))
            zip_files = glob.glob(os.path.join(os.path.dirname(__file__), "comfyui_images_*.zip"))
            
            # 使用线程池并行删除文件
            def remove_file(filepath):
                try:
                    os.remove(filepath)
                    if filepath.endswith('.png'):
                        logger.debug(f"清理临时图片: {filepath}")
                    else:
                        logger.debug(f"清理临时压缩包: {filepath}")
                except Exception as e:
                    if filepath.endswith('.png'):
                        logger.warning(f"清理临时图片失败 {filepath}: {e}")
                    else:
                        logger.warning(f"清理临时压缩包失败 {filepath}: {e}")
            
            loop = asyncio.get_event_loop()
            tasks = []
            
            for temp_file in temp_files:
                tasks.append(loop.run_in_executor(None, remove_file, temp_file))
            
            for zip_file in zip_files:
                tasks.append(loop.run_in_executor(None, remove_file, zip_file))
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
        except Exception as e:
            logger.error(f"清理临时文件时发生错误: {e}")

    async def get_qq_nickname(self, qq_number):
        """获取QQ昵称"""
        # 如果没有配置QQ号或配置为空，返回默认昵称
        if not qq_number or qq_number == "0" or qq_number == "":
            return "Astrbot"
            
        # 使用HTTPS协议的API
        url = f"https://api.mmp.cc/api/qqname?qq={qq_number}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        try:
                            data = await response.json()
                            logger.debug(f"QQ昵称API返回: {data}")
                            
                            # 根据实际API返回结构解析昵称
                            if data.get("success") and "data" in data and "name" in data["data"]:
                                nickname = data["data"]["name"]
                                logger.debug(f"成功提取昵称: {nickname}")
                                if nickname and nickname.strip():
                                    return nickname.strip()
                        except Exception as e:
                            logger.debug(f"解析昵称出错: {str(e)}")
        except Exception as e:
            logger.debug(f"请求QQ昵称API出错: {str(e)}")
        
        # 如果API调用失败，返回带有QQ号的标识而不是默认"Astrbot"
        return f"用户{qq_number}"

    async def send_fake_forward_message(self, event: AstrMessageEvent, merged_chain: List, image_count: int) -> None:
        """发送伪造转发消息"""
        if not self.enable_fake_forward or image_count < self.fake_forward_threshold:
            # 如果未启用伪造转发或图片数量不足，使用普通发送方式
            await event.send(event.chain_result(merged_chain))
            return

        try:
            # 确定伪造的QQ号
            fake_qq = self.fake_forward_qq
            use_default = False  # 标记是否使用默认设置
            
            if not fake_qq or fake_qq == "":
                # 如果没有配置或配置为空，使用默认设置
                use_default = True
                fake_qq = ""
            elif fake_qq == "0":
                # 使用发送用户的QQ号
                fake_qq = str(event.get_sender_id())
            elif fake_qq == "1":
                # 使用自己的QQ号（机器人QQ号）
                # 这里需要获取机器人自身的QQ号，暂时使用事件中的机器人ID
                fake_qq = str(getattr(event.message_obj, 'self_id', '123456'))  # 如果获取不到则使用默认值

            # 获取QQ昵称
            if use_default:
                nickname = "Astrbot"  # 默认昵称
            else:
                nickname = await self.get_qq_nickname(fake_qq)
            
            # 创建伪造节点
            node_content = []
            for component in merged_chain:
                if isinstance(component, Plain):
                    node_content.append(component)
                elif isinstance(component, Image):
                    node_content.append(component)
            
            # 创建Node，如果使用默认设置，使用机器人QQ号和默认头像
            if use_default:
                # 使用默认设置：昵称为Astrbot，QQ号为机器人ID
                bot_qq = str(getattr(event.message_obj, 'self_id', '123456'))
                node = Node(
                    uin=int(bot_qq),
                    name=nickname,
                    content=node_content
                )
            else:
                node = Node(
                    uin=int(fake_qq) if fake_qq.isdigit() else 123456,
                    name=nickname,
                    content=node_content
                )
            
            # 发送伪造转发消息
            nodes = Nodes(nodes=[node])
            await event.send(event.chain_result([nodes]))
            
            logger.info(f"已使用伪造转发消息发送，QQ号: {fake_qq}, 昵称: {nickname}, 图片数量: {image_count}")
            
        except Exception as e:
            logger.error(f"发送伪造转发消息失败，使用普通发送方式: {str(e)}")
            # 如果伪造转发失败，使用普通发送方式
            await event.send(event.chain_result(merged_chain))

    # 添加临时服务器指令
    @filter.custom_filter(AddServerFilter)
    async def handle_add_server(self, event: AstrMessageEvent) -> None:
        """处理添加临时服务器指令"""
        try:
            full_text = event.message_obj.message_str.strip()
            
            # 解析命令：添加服务器 <URL>,<名称>
            parts = full_text.split(None, 2)  # 分割为最多3部分
            if len(parts) < 2:
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 命令格式错误\n使用方法：添加服务器 <URL>,<名称>\n示例：添加服务器 http://127.0.0.1:8188,临时服务器1"
                ))
                return
            
            server_info = parts[1].strip()
            if "," not in server_info:
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 服务器信息格式错误\n使用方法：添加服务器 <URL>,<名称>\n示例：添加服务器 http://127.0.0.1:8188,临时服务器1"
                ))
                return
            
            url, name = server_info.split(",", 1)
            url = url.strip()
            name = name.strip()
            
            # 验证URL格式
            if not url.startswith(("http://", "https://")):
                await self._send_with_auto_recall(event, event.plain_result(
                    f"❌ URL格式错误：{url}\n需以http://或https://开头"
                ))
                return
            
            # 验证服务器是否可以连接
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{url}/system_stats", timeout=10) as resp:
                        if resp.status != 200:
                            await self._send_with_auto_recall(event, event.plain_result(
                                f"❌ 无法连接到服务器：{url}\nHTTP状态码：{resp.status}\n请检查服务器地址是否正确"
                            ))
                            return
            except Exception as e:
                await self._send_with_auto_recall(event, event.plain_result(
                    f"❌ 无法连接到服务器：{url}\n错误信息：{str(e)[:200]}\n请检查服务器地址是否正确"
                ))
                return
            
            # 创建新的临时服务器
            import copy
            new_server_id = len(self.comfyui_servers) + len(self.temp_servers)
            new_server = self.ServerState(url, name or f"临时服务器{new_server_id}", new_server_id)
            
            # 添加到临时服务器列表
            self.temp_servers.append(new_server)
            # 添加到主服务器列表，使其可以参与轮询
            self.comfyui_servers.append(new_server)
            
            # 启动该服务器的worker
            await self._manage_worker_for_server(new_server)
            
            logger.info(f"已添加临时ComfyUI服务器：{name} ({url})")
            await self._send_with_auto_recall(event, event.plain_result(
                f"✅ 临时服务器添加成功！\n"
                f"名称：{name}\n"
                f"地址：{self._filter_server_urls(url)}\n"
                f"提示：该服务器仅在本次运行中有效，重启插件后将失效"
            ))
            
        except Exception as e:
            logger.error(f"处理添加服务器指令失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(f"❌ 添加服务器失败：{str(e)[:500]}"))

    async def terminate(self) -> None:
        """终止插件运行，清理所有资源（包括需要持续存在的GUI服务器）"""
        try:
            logger.info("开始终止插件，清理所有资源...")
            
            # 停止GUI服务器（需要持续存在的资源）
            self._stop_gui_server()
            
            # 停止服务器监控
            if self.server_monitor_task and not self.server_monitor_task.done():
                self.server_monitor_task.cancel()
                try:
                    await self.server_monitor_task
                except asyncio.CancelledError:
                    pass
            
            # 停止所有服务器worker
            for server in self.comfyui_servers:
                if server.worker and not server.worker.done():
                    server.worker.cancel()
                    try:
                        await server.worker
                    except asyncio.CancelledError:
                        pass
                    server.worker = None
            
            # 停止帮助服务器
            await self._stop_help_server()
            
            # 停止Web API服务器
            self._stop_web_api_server()
            
            # 清理临时文件
            await self.cleanup_temp_files()
            
            logger.info("插件终止完成，所有资源已清理")
        except Exception as e:
            logger.error(f"插件终止时发生错误: {e}")



    # 图生图指令
    @filter.custom_filter(Img2ImgFilter)
    async def handle_img2img(self, event: AstrMessageEvent) -> None:
        # 检查群聊白名单
        if not self._check_group_whitelist(event):
            await self._send_with_auto_recall(event, event.plain_result(
                "❌ 当前群聊不在白名单中，无法使用此功能！"
            ))
            return
        
        if not self._is_in_open_time():
            open_desc = self._get_open_time_desc()
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n当前未开放图片生成服务～\n开放时间：{open_desc}\n请在开放时间段内提交任务！"
            ))
            return
        if not self._get_any_healthy_server():
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n所有ComfyUI服务器均不可用，请稍后再试！"
            ))
            return
        full_text = event.message_obj.message_str.strip()
        messages = event.get_messages()
        _, params = self._parse_command(full_text, "img2img")
        if not params:
            # 发送帮助信息
            await self.send_help(event)
            return
        try:
            # 先解析模型参数
            params, selected_model = self._parse_model_params(params)
            # 再解析LoRA参数
            params, lora_list = self._parse_lora_params(params)
        except ValueError as e:
            filtered_err = self._filter_server_urls(str(e))
            await self._send_with_auto_recall(event, event.plain_result(f"\n参数解析失败：{filtered_err}"))
            return
        prompt = ""
        denoise = self.default_denoise
        current_batch_size = self.img2img_batch_size
        batch_pattern = r'^批量(\d+)$'
        denoise_pattern = r'^噪声:([0-9.]+)$'
        batch_param = None
        denoise_param = None
        prompt_params = []
        for param in params:
            batch_match = re.match(batch_pattern, param.strip())
            if batch_match:
                batch_param = batch_match
                continue
            denoise_match = re.match(denoise_pattern, param.strip())
            if denoise_match:
                denoise_param = denoise_match
                continue
            prompt_params.append(param)
        if batch_param:
            try:
                input_batch = int(batch_param.group(1))
                if not (1 <= input_batch <= self.max_img2img_batch):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n批量数{input_batch}非法（图生图需1~{self.max_img2img_batch}），请重新输入合法参数！"))
                    return
                current_batch_size = input_batch
            except Exception as e:
                await self._send_with_auto_recall(event, event.plain_result(f"\n批量数解析失败：{str(e)}，请重新输入合法参数！"))
                return
        if denoise_param:
            try:
                denoise_val = float(denoise_param.group(1))
                if not (0 <= denoise_val <= 1):
                    await self._send_with_auto_recall(event, event.plain_result(f"\n噪声系数{denoise_val}非法（需0-1之间的数值），请重新输入合法参数！"))
                    return
                denoise = denoise_val
            except ValueError as e:
                await self._send_with_auto_recall(event, event.plain_result(f"\n噪声系数解析失败：{str(e)}，请重新输入合法参数！"))
                return
        prompt = " ".join(prompt_params).strip()
        if not prompt:
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n图生图提示词不能为空！使用方法：\n发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息\n例：img2img 猫咪 噪声:0.7 批量2 model:动漫风格 lora:动物:1.2!0.9 + 图片/引用图片消息\n图生图默认批量数：{self.img2img_batch_size}，最大支持{self.max_img2img_batch}\n默认噪声系数：{self.default_denoise}"
            ))
            return
        image_components = [msg for msg in messages if isinstance(msg, Image)]
        reply_image_components = []
        reply_seg = next((seg for seg in messages if isinstance(seg, Reply)), None)
        if reply_seg and reply_seg.chain:
            reply_image_components = [seg for seg in reply_seg.chain if isinstance(seg, Image)]
        if image_components:
            selected_image = image_components[0]
            image_source = "当前消息"
        elif reply_image_components:
            selected_image = reply_image_components[0]
            image_source = "引用消息"
        else:
            await self._send_with_auto_recall(event, event.plain_result("\n未检测到图片，请重新发送图文消息或引用包含图片的消息"))
            return
        upload_server = None  # 暂时移除上传逻辑，改为任务处理时上传
        user_id = str(event.get_sender_id())
        
        # 先下载图片到临时位置
        try:
            img_path = await selected_image.convert_to_file_path()
            # 检查下载的文件是否存在且大于10KB（确保图片有效）
            if not os.path.exists(img_path):
                raise Exception("图片文件下载失败")
            file_size = os.path.getsize(img_path)
            if file_size < 10240:  # 小于10KB
                logger.warning(f"QQ图片文件过小（{file_size}字节），可能是下载失败或URL过期")
                raise Exception(f"QQ图片URL已过期或下载失败（文件大小：{file_size}字节），请重新发送图片")
        except Exception as e:
            error_msg = str(e)
            # 检查是否是URL过期相关的错误
            if "download url has expired" in error_msg.lower() or "expired" in error_msg.lower() or "下载失败" in error_msg:
                await self._send_with_auto_recall(event, event.plain_result(
                    "\n❌ QQ图片URL已过期，请重新发送图片后再试"
                ))
            elif "文件过小" in error_msg or "下载失败" in error_msg:
                await self._send_with_auto_recall(event, event.plain_result(f"\n❌ {error_msg}"))
            else:
                await self._send_with_auto_recall(event, event.plain_result(f"\n图片处理失败：{error_msg[:1000]}"))
            return
        
        # 如果启用了自动保存，则复制到永久文件夹
        final_img_path = img_path  # 默认使用临时路径
        if self.enable_auto_save:
            # 保存到永久文件夹（类似于workflow的逻辑）
            saved_img_path = await self._save_img2img_image_permanently(img_path, user_id)
            if saved_img_path:
                final_img_path = saved_img_path  # 使用永久路径
                logger.info(f"图片已永久保存到: {saved_img_path}")
            else:
                logger.info(f"图片保存失败，使用临时文件: {img_path}")
        
        try:
            current_seed = random.randint(1, 18446744073709551615) if (self.seed == "随机" or not self.seed) else int(self.seed)
        except (ValueError, TypeError):
            current_seed = random.randint(1, 2147483647)
        # 检查用户任务数限制
        if not await self._increment_user_task_count(user_id):
            await self._send_with_auto_recall(event, event.plain_result(
                f"\n您当前同时进行的任务数已达上限（{self.max_concurrent_tasks_per_user}个），请等待当前任务完成后再提交新任务！"
            ))
            return
            
        if self.task_queue.full():
            # 如果队列已满，需要减少刚刚增加的用户任务计数
            await self._decrement_user_task_count(user_id)
            await self._send_with_auto_recall(event, event.plain_result(f"\n当前任务队列已满（{self.max_task_queue}个任务上限），请稍后再试！"))
            return
            
        await self.task_queue.put({
            "event": event,
            "prompt": prompt,
            "current_seed": current_seed,
            "current_width": self.default_width,
            "current_height": self.default_height,
            "img_path": final_img_path,  # 改为本地图片路径
            "denoise": denoise,
            "current_batch_size": current_batch_size,
            "lora_list": lora_list,
            "user_id": user_id
        })
        lora_feedback = ""
        if lora_list:
            lora_feedback = "\n使用LoRA：" + " | ".join([
                f"{lora['name']}（model:{lora['strength_model']}, clip:{lora['strength_clip']}）"
                for lora in lora_list
            ])
        available_servers = [s.name for s in self.comfyui_servers if s.healthy]
        server_feedback = f"\n可用服务器：{', '.join(available_servers)}" if available_servers else "\n当前无可用服务器，任务将在服务器恢复后处理"
        await self._send_with_auto_recall(event, event.plain_result(
            f"\n图生图任务已加入队列（当前排队：{self.task_queue.qsize()}个）\n"
            f"提示词：{self._truncate_prompt(prompt)}\n"
            f"Seed：{current_seed}\n"
            f"噪声系数：{denoise}（默认：{self.default_denoise}）\n"
            f"批量数：{current_batch_size}（默认：{self.img2img_batch_size}，最大：{self.max_img2img_batch}）\n"
            f"图片来源：{image_source}\n"
            f"{'图片已永久保存' if self.enable_auto_save else '图片已下载到临时位置'}"
            + server_feedback
            + lora_feedback
        ))

    async def _load_workflows(self) -> None:
        """加载workflow模块"""
        try:
            loop = asyncio.get_event_loop()
            
            # 检查并创建workflow目录
            if not await loop.run_in_executor(None, self.workflow_dir.exists):
                await loop.run_in_executor(None, self.workflow_dir.mkdir, parents=True, exist_ok=True)
                logger.info(f"创建workflow目录: {self.workflow_dir}")
                return

            # 获取workflow列表
            workflow_names = await loop.run_in_executor(None, lambda: [f.name for f in self.workflow_dir.iterdir()])
            
            for workflow_name in workflow_names:
                workflow_path = self.workflow_dir / workflow_name
                if not await loop.run_in_executor(None, workflow_path.is_dir):
                    continue

                config_file = workflow_path / "config.json"
                workflow_file = workflow_path / "workflow.json"

                # 检查文件是否存在
                config_exists = await loop.run_in_executor(None, config_file.exists)
                workflow_exists = await loop.run_in_executor(None, workflow_file.exists)
                
                if not config_exists or not workflow_exists:
                    logger.warning(f"workflow {workflow_name} 缺少必要文件，跳过")
                    continue

                try:
                    # 异步读取配置文件
                    def read_config():
                        with open(config_file, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    
                    def read_workflow():
                        with open(workflow_file, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    
                    config = await loop.run_in_executor(None, read_config)
                    workflow_data = await loop.run_in_executor(None, read_workflow)

                    # 验证配置格式
                    required_fields = ["name", "prefix", "input_nodes", "output_nodes"]
                    for field in required_fields:
                        if field not in config:
                            logger.error(f"workflow {workflow_name} 配置缺少必要字段: {field}")
                            continue

                    prefix = config["prefix"]
                    if prefix in self.workflow_prefixes:
                        logger.warning(f"workflow前缀重复: {prefix}，跳过 {workflow_name}")
                        continue

                    # 注入主程序配置到workflow
                    self._inject_main_config(config, workflow_name)

                    # 存储workflow信息
                    self.workflows[workflow_name] = {
                        "config": config,
                        "workflow": workflow_data,
                        "path": workflow_path
                    }
                    self.workflow_prefixes[prefix] = workflow_name

                    logger.info(f"已加载workflow: {config['name']} (前缀: {prefix})")

                except Exception as e:
                    logger.error(f"加载workflow {workflow_name} 失败: {e}")

            logger.info(f"共加载 {len(self.workflows)} 个workflow模块")
            
            # 更新 WorkflowFilter 的前缀集合，避免每次文件系统访问
            self.WorkflowFilter.update_prefixes(self.workflow_prefixes)

        except Exception as e:
            logger.error(f"加载workflow模块失败: {e}")

    def _inject_main_config(self, config: Dict[str, Any], workflow_name: str) -> None:
        """将主程序的model和lora配置注入到workflow配置中"""
        try:
            if "node_configs" not in config:
                return

            node_configs = config["node_configs"]
            
            # 遍历所有节点配置
            for node_id, node_config in node_configs.items():
                for param_name, param_info in node_config.items():
                    # 检查是否需要注入模型配置
                    if param_info.get("type") == "select" and param_info.get("inject_models"):
                        # 注入模型选项
                        model_options = []
                        if self.model_name_map:
                            for desc_lower, (filename, desc) in self.model_name_map.items():
                                if desc_lower == desc.lower():  # 只添加原始描述，避免重复
                                    model_options.append(desc)
                        
                        if model_options:
                            param_info["options"] = model_options
                            param_info.pop("inject_models", None)  # 移除注入标记
                            logger.debug(f"为workflow {workflow_name} 节点 {node_id} 参数 {param_name} 注入了 {len(model_options)} 个模型选项")

                    # 检查是否需要注入LoRA配置
                    elif param_info.get("type") == "text" and param_info.get("inject_loras"):
                        # 为LoRA参数添加描述信息
                        if self.lora_name_map:
                            lora_descriptions = []
                            for desc_lower, (filename, desc) in self.lora_name_map.items():
                                if desc_lower == desc.lower():  # 只添加原始描述，避免重复
                                    lora_descriptions.append(f"{desc} (文件: {filename})")
                            
                            if lora_descriptions:
                                param_info["description"] = param_info.get("description", "") + f"\n可用LoRA: {', '.join(lora_descriptions[:5])}"
                                if len(lora_descriptions) > 5:
                                    param_info["description"] += f" (共{len(lora_descriptions)}个)"
                                param_info.pop("inject_loras", None)  # 移除注入标记
                                logger.debug(f"为workflow {workflow_name} 节点 {node_id} 参数 {param_name} 注入了LoRA描述信息")

                    # 检查是否需要注入采样器配置
                    elif param_info.get("type") == "select" and param_info.get("inject_samplers"):
                        # 使用主程序的默认采样器
                        if self.sampler_name:
                            param_info["default"] = self.sampler_name
                        param_info.pop("inject_samplers", None)  # 移除注入标记
                        logger.debug(f"为workflow {workflow_name} 节点 {node_id} 参数 {param_name} 设置了默认采样器: {self.sampler_name}")

                    # 检查是否需要注入调度器配置
                    elif param_info.get("type") == "select" and param_info.get("inject_schedulers"):
                        # 使用主程序的默认调度器
                        if self.scheduler:
                            param_info["default"] = self.scheduler
                        param_info.pop("inject_schedulers", None)  # 移除注入标记
                        logger.debug(f"为workflow {workflow_name} 节点 {node_id} 参数 {param_name} 设置了默认调度器: {self.scheduler}")

        except Exception as e:
            logger.error(f"为workflow {workflow_name} 注入主程序配置失败: {e}")

    class WorkflowFilter(CustomFilter):
        # 类变量，存储所有前缀
        _prefixes_set = set()

        @classmethod
        def update_prefixes(cls, prefixes_dict):
            """更新前缀集合"""
            cls._prefixes_set = set(prefixes_dict.keys())

        def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
            full_text = event.message_obj.message_str.strip()
            if not full_text:
                return False
            
            # 检查是否匹配任何workflow前缀
            words = full_text.split()
            if not words:
                return False
            
            prefix = words[0]
            
            # 直接从类变量中检查前缀是否存在，高效且无阻塞
            return prefix in self._prefixes_set

    @filter.custom_filter(WorkflowFilter)
    async def handle_workflow(self, event: AstrMessageEvent):
        """处理workflow命令"""
        try:
            full_text = event.message_obj.message_str.strip()
            words = full_text.split()
            if not words:
                return

            prefix = words[0]
            if prefix not in self.workflow_prefixes:
                await self._send_with_auto_recall(event, event.plain_result(f"未知的workflow前缀: {prefix}"))
                return

            # 检查是否是help命令
            if len(words) >= 2 and words[1].lower() == "help":
                await self._send_workflow_help(event, prefix)
                return

            workflow_name = self.workflow_prefixes[prefix]
            workflow_info = self.workflows[workflow_name]
            config = workflow_info["config"]
            workflow_data = workflow_info["workflow"]

            # 检查群聊白名单
            if not self._check_group_whitelist(event):
                await self._send_with_auto_recall(event, event.plain_result(
                    "❌ 当前群聊不在白名单中，无法使用此功能！"
                ))
                return
            
            # 检查开放时间
            if not self._is_in_open_time():
                await self._send_with_auto_recall(event, event.plain_result(
                    f"当前不在开放时间内，开放时间：{self.open_time_ranges}"
                ))
                return

            # 检查用户并发限制
            user_id = str(event.get_sender_id())
            if not await self._check_user_task_limit(user_id):
                await self._send_with_auto_recall(event, event.plain_result(
                    f"您当前有过多任务在执行中（最大{self.max_concurrent_tasks_per_user}个），请稍后再试"
                ))
                return

            # 检查是否有健康的服务器
            if not self._get_any_healthy_server():
                await self._send_with_auto_recall(event, event.plain_result(
                    f"\n所有ComfyUI服务器均不可用，请稍后再试！"
                ))
                return

            # 改进的参数解析：使用正则表达式正确处理包含空格、引号、标点的参数值
            import re
            # 使用从后往前扫描的方法解析参数，基于已知参数名
            # 逻辑：从后往前找冒号，检查前面是否有已知参数名，有则分割
            
            # 提取前缀后的参数部分
            params_text = full_text[len(prefix):].strip()
            
            # 获取所有已知的参数名（包括别名）
            known_keys = set()
            node_configs = config.get("node_configs", {})
            for node_id, node_config in node_configs.items():
                for param_name, param_info in node_config.items():
                    # 主参数名
                    known_keys.add(param_name)
                    # 别名
                    aliases = param_info.get("aliases", [])
                    known_keys.update(aliases)
            
            # 改进的参数解析：使用正则表达式正确处理包含空格、引号、标点的参数值
            import re
            
            # 构建参数名的正则表达式模式，确保匹配完整的参数名
            param_pattern = '|'.join(re.escape(key) for key in known_keys)
            # 使用正向预查确保参数名后面是冒号，并且前面是单词边界或空格
            regex_pattern = rf'(?<!\S)({param_pattern})(?=\s*:)'
            
            # 找到所有参数名的位置
            matches = list(re.finditer(regex_pattern, params_text))
            
            args = []
            prompt_parts = []
            last_end = 0
            
            for match in matches:
                param_name = match.group(1)
                param_start = match.start()
                
                # 添加参数名之前的文本作为提示词的一部分
                if param_start > last_end:
                    prompt_part = params_text[last_end:param_start].strip()
                    if prompt_part:
                        prompt_parts.append(prompt_part)
                
                # 找到冒号位置
                colon_pos = params_text.find(':', param_start)
                if colon_pos == -1:
                    continue
                
                # 找到参数值的结束位置（下一个参数名之前或字符串结尾）
                next_param_start = len(params_text)
                for next_match in matches[matches.index(match) + 1:]:
                    next_param_start = next_match.start()
                    break
                
                # 提取参数值
                param_value = params_text[colon_pos + 1:next_param_start].strip()
                args.append(f"{param_name}:{param_value}")
                
                last_end = next_param_start
            
            # 添加最后的文本作为提示词
            if last_end < len(params_text):
                prompt_part = params_text[last_end:].strip()
                if prompt_part:
                    prompt_parts.append(prompt_part)
            
            # 合并所有提示词部分
            prompt_text = ' '.join(prompt_parts).strip()
            
            # 如果有提示词参数，将其添加到参数列表中
            if prompt_text:
                args.insert(0, f"提示词:{prompt_text}")
            
            params = self._parse_workflow_params(args, config)

            # 验证必需的参数
            missing_params = self._validate_required_params(config, params)
            if missing_params:
                param_list = ", ".join(missing_params)
                await self._send_with_auto_recall(event, event.plain_result(f"缺少必需的参数：{param_list}"))
                return

            # 验证参数值的有效性
            validation_errors = self._validate_param_values(config, params)
            if validation_errors:
                error_msg = "\n".join(validation_errors)
                await self._send_with_auto_recall(event, event.plain_result(f"参数输入有误：\n{error_msg}"))
                return

            # 处理图片输入（立即下载图片）
            image_paths = []
            if config.get("input_nodes"):
                messages = event.get_messages()
                has_image = any(isinstance(msg, Image) for msg in messages)
                
                # 检查回复中的图片
                has_image_in_reply = False
                reply_seg = next((seg for seg in messages if isinstance(seg, Reply)), None)
                if reply_seg and reply_seg.chain:
                    has_image_in_reply = any(isinstance(seg, Image) for seg in reply_seg.chain)
                
                if not has_image and not has_image_in_reply:
                    await self._send_with_auto_recall(event, event.plain_result("此workflow需要图片输入，请发送图片或引用包含图片的消息"))
                    return
                
                # 获取所有图片对象
                image_segs = []
                if has_image:
                    image_segs = [msg for msg in messages if isinstance(msg, Image)]
                else:
                    # 从回复中获取所有图片
                    image_segs = [seg for seg in reply_seg.chain if isinstance(seg, Image)]
                
                logger.info(f"检测到 {len(image_segs)} 张图片，开始立即下载")
                
                # 立即下载所有图片
                for i, image_seg in enumerate(image_segs):
                    try:
                        # 将图片转换为文件路径
                        img_path = await image_seg.convert_to_file_path()
                        
                        # 检查下载的文件是否存在且大于10KB（确保图片有效）
                        if not os.path.exists(img_path):
                            raise Exception(f"PERMANENT_ERROR:第 {i+1} 张图片文件下载失败")
                        file_size = os.path.getsize(img_path)
                        if file_size < 10240:  # 小于10KB
                            logger.warning(f"第 {i+1} 张QQ图片文件过小（{file_size}字节），可能是下载失败或URL过期")
                            raise Exception(f"PERMANENT_ERROR:第 {i+1} 张QQ图片URL已过期或下载失败（文件大小：{file_size}字节），请重新发送图片后再试")
                        
                        # 如果启用了自动保存，则复制到永久文件夹
                        if self.enable_auto_save:
                            # 为workflow图片创建特殊的永久保存
                            saved_img_path = await self._save_workflow_image_permanently(img_path, workflow_name, user_id, i)
                            if saved_img_path:
                                image_paths.append(saved_img_path)
                                logger.info(f"第 {i+1} 张图片已永久保存到: {saved_img_path}")
                            else:
                                # 保存失败，使用临时文件
                                image_paths.append(img_path)
                                logger.info(f"第 {i+1} 张图片保存失败，使用临时文件: {img_path}")
                        else:
                            # 未启用自动保存，使用临时文件
                            image_paths.append(img_path)
                            logger.info(f"第 {i+1} 张图片已下载到临时位置: {img_path}")
                            
                    except Exception as e:
                        error_msg = str(e)
                        # 检查是否是永久性错误
                        if error_msg.startswith("PERMANENT_ERROR:"):
                            await self._send_with_auto_recall(event, event.plain_result(f"图片下载失败：{error_msg.replace('PERMANENT_ERROR:', '')}"))
                            return
                        else:
                            await self._send_with_auto_recall(event, event.plain_result(f"第 {i+1} 张图片处理失败：{error_msg[:200]}"))
                            return
                
                logger.info(f"所有 {len(image_paths)} 张图片下载完成")

            # 构建workflow（图片列表为空，稍后在上传时替换）
            final_workflow = self._build_workflow(workflow_data, config, params, [])

            # 增加用户任务计数
            await self._increment_user_task_count(user_id)

            # 添加到任务队列（包含已下载的图片路径）
            if self.task_queue.full():
                await self._decrement_user_task_count(user_id)
                await self._send_with_auto_recall(event, event.plain_result(f"当前任务队列已满（{self.max_task_queue}个任务上限），请稍后再试！"))
                return

            await self.task_queue.put({
                "event": event,
                "prompt": final_workflow,
                "workflow_name": workflow_name,
                "user_id": user_id,
                "is_workflow": True,
                "image_paths": image_paths,  # 改为已下载的图片路径
                "workflow_config": config
            })

            await self._send_with_auto_recall(event, event.plain_result(
                f"Workflow任务「{config['name']}」已加入队列（当前排队：{self.task_queue.qsize()}个）"
            ))

        except Exception as e:
            logger.error(f"处理workflow命令失败: {e}")
            await self._send_with_auto_recall(event, event.plain_result(f"处理workflow命令失败: {str(e)}"))

    def _parse_workflow_params(self, args: List[str], config: Dict[str, Any]) -> Dict[str, Any]:
        """解析workflow参数，支持自定义键名"""
        params = {}
        node_configs = config.get("node_configs", {})
        
        # 构建参数名映射表（包括别名），支持多节点
        # 格式：{别名: [(node_id, param_name), ...]}
        param_mapping = {}
        for node_id, node_config in node_configs.items():
            for param_name, param_info in node_config.items():
                # 主参数名
                if param_name not in param_mapping:
                    param_mapping[param_name] = []
                param_mapping[param_name].append((node_id, param_name))
                
                # 添加别名
                aliases = param_info.get("aliases", [])
                for alias in aliases:
                    if alias not in param_mapping:
                        param_mapping[alias] = []
                    param_mapping[alias].append((node_id, param_name))
        
        # 解析参数，格式为 参数名:值
        for arg in args:
            if ":" not in arg:
                continue
            key, value = arg.split(":", 1)
            
            # 查找匹配的节点和参数
            matches = param_mapping.get(key, [])
            
            if len(matches) == 1:
                # 只有一个匹配，直接使用
                node_id, param_name = matches[0]
                # 使用 node_id:param_name 作为键，避免冲突
                params[f"{node_id}:{param_name}"] = value
            elif len(matches) > 1:
                # 多个匹配，需要更精确的匹配策略
                # 优先选择在aliases中明确包含该key的参数
                exact_matches = []
                for node_id, param_name in matches:
                    param_info = node_configs.get(node_id, {}).get(param_name, {})
                    aliases = param_info.get("aliases", [])
                    if key in aliases:
                        exact_matches.append((node_id, param_name))
                
                if len(exact_matches) == 1:
                    # 只有一个精确匹配
                    node_id, param_name = exact_matches[0]
                    params[f"{node_id}:{param_name}"] = value
                elif len(exact_matches) > 1:
                    # 多个精确匹配，选择第一个（通常配置文件中顺序是确定的）
                    node_id, param_name = exact_matches[0]
                    params[f"{node_id}:{param_name}"] = value
                else:
                    # 没有精确匹配，使用第一个匹配
                    node_id, param_name = matches[0]
                    params[f"{node_id}:{param_name}"] = value
            else:
                # 没有匹配，保持原样
                params[key] = value
        
        return params

    def _validate_required_params(self, config: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
        """验证必需的参数是否都已提供"""
        missing_params = []
        node_configs = config.get("node_configs", {})
        
        # 构建参数名映射表（包括别名），支持多节点
        param_mapping = {}
        for node_id, node_config in node_configs.items():
            for param_name, param_info in node_config.items():
                # 主参数名
                if param_name not in param_mapping:
                    param_mapping[param_name] = []
                param_mapping[param_name].append((node_id, param_name))
                
                # 添加别名
                aliases = param_info.get("aliases", [])
                for alias in aliases:
                    if alias not in param_mapping:
                        param_mapping[alias] = []
                    param_mapping[alias].append((node_id, param_name))
        
        # 检查每个必需参数
        for node_id, node_config in node_configs.items():
            for param_name, param_info in node_config.items():
                if param_info.get("required", False):
                    # 检查参数是否已提供（包括节点特定格式和别名）
                    param_provided = False
                    
                    # 检查节点特定格式
                    node_specific_key = f"{node_id}:{param_name}"
                    if node_specific_key in params:
                        param_provided = True
                    else:
                        # 检查通用参数名和别名
                        for provided_key in params.keys():
                            matches = param_mapping.get(provided_key, [])
                            for match_node_id, match_param_name in matches:
                                if match_node_id == node_id and match_param_name == param_name:
                                    param_provided = True
                                    break
                            if param_provided:
                                break
                    
                    if not param_provided:
                        # 获取参数的显示名称（优先使用第一个别名，如果没有别名则使用主参数名）
                        display_name = param_name
                        aliases = param_info.get("aliases", [])
                        if aliases:
                            display_name = aliases[0]  # 使用第一个别名作为显示名称
                        
                        missing_params.append(display_name)
        
        return missing_params

    def _validate_param_values(self, config: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
        """验证参数值的有效性，包括范围、选项等"""
        errors = []
        node_configs = config.get("node_configs", {})
        
        # 验证每个提供的参数
        for provided_key, value in params.items():
            # 检查是否是 node_id:param_name 格式
            if ":" in provided_key:
                node_id, param_name = provided_key.split(":", 1)
            else:
                # 如果不是这种格式，跳过验证（这是未知参数）
                continue
            
            # 获取参数配置
            param_info = node_configs.get(node_id, {}).get(param_name, {})
            if not param_info:
                continue  # 没有找到参数配置，跳过验证
            
            param_type = param_info.get("type")
            
            # 获取参数的显示名称（优先使用第一个别名）
            display_name = param_name
            aliases = param_info.get("aliases", [])
            if aliases:
                display_name = aliases[0]
            
            try:
                # 根据参数类型进行验证
                if param_type == "number":
                    # 尝试转换为数字
                    try:
                        num_value = float(value)
                    except (ValueError, TypeError):
                        errors.append(f"参数「{display_name}」必须是数字，当前值：{value}")
                        continue
                    
                    # 检查最小值
                    min_val = param_info.get("min")
                    if min_val is not None and num_value < min_val:
                        errors.append(f"参数「{display_name}」不能小于 {min_val}，当前值：{num_value}")
                    
                    # 检查最大值
                    max_val = param_info.get("max")
                    if max_val is not None and num_value > max_val:
                        errors.append(f"参数「{display_name}」不能大于 {max_val}，当前值：{num_value}")
                
                elif param_type == "select":
                    # 检查是否在选项列表中
                    options = param_info.get("options", [])
                    if options and value not in options:
                        options_str = "、".join(options)
                        errors.append(f"参数「{display_name}」必须是以下选项之一：{options_str}，当前值：{value}")
                
                elif param_type == "boolean":
                    # 检查布尔值
                    if isinstance(value, str):
                        lower_value = value.lower()
                        if lower_value not in ["true", "false", "1", "0", "yes", "no", "on", "off"]:
                            errors.append(f"参数「{display_name}」必须是布尔值（true/false、1/0、yes/no、on/off），当前值：{value}")
                    elif not isinstance(value, bool):
                        errors.append(f"参数「{display_name}」必须是布尔值，当前值：{value}")
            
            except Exception as e:
                errors.append(f"验证参数「{display_name}」时出错：{str(e)}")
        
        return errors

    def _build_workflow(self, workflow_data: Dict[str, Any], config: Dict[str, Any], 
                       params: Dict[str, Any], images: List[str]) -> Dict[str, Any]:
        """构建最终的workflow"""
        final_workflow = copy.deepcopy(workflow_data)
        
        # 设置图片输入
        input_nodes = config.get("input_nodes", [])
        input_mappings = config.get("input_mappings", {})
        
        # 用于跟踪已分配的图片索引
        used_image_indices = set()
        
        logger.info(f"开始为workflow配置图片输入，共 {len(images)} 张图片")
        
        # 首先处理指定了image_index的节点
        for node_id in input_nodes:
            if node_id in input_mappings and node_id in final_workflow:
                mapping = input_mappings[node_id]
                param_name = mapping.get("parameter_name", "image")
                if images and param_name == "image":
                    image_index = mapping.get("image_index")
                    if image_index is not None:
                        # 有明确指定image_index的节点
                        image_mode = mapping.get("image_mode", "single")
                        
                        if image_mode == "single":
                            if image_index < len(images):
                                final_workflow[node_id]["inputs"][param_name] = images[image_index]
                                used_image_indices.add(image_index)
                                logger.info(f"节点 {node_id} 使用指定索引 {image_index} 的图片")
                            else:
                                final_workflow[node_id]["inputs"][param_name] = images[0]
                                used_image_indices.add(0)
                                logger.info(f"节点 {node_id} 索引超出范围，使用第一张图片")
                        elif image_mode == "list":
                            final_workflow[node_id]["inputs"][param_name] = images
                            logger.info(f"节点 {node_id} 使用所有图片列表")
                        elif image_mode == "all":
                            final_workflow[node_id]["inputs"][param_name] = images
                            logger.info(f"节点 {node_id} 使用全部图片")
        
        # 然后处理未指定image_index的节点，自动分配未使用的图片
        current_image_index = 0
        for node_id in input_nodes:
            if node_id in input_mappings and node_id in final_workflow:
                mapping = input_mappings[node_id]
                param_name = mapping.get("parameter_name", "image")
                if images and param_name == "image":
                    image_index = mapping.get("image_index")
                    if image_index is None:
                        # 未指定image_index的节点，自动分配
                        # 找到下一个未使用的图片索引
                        while current_image_index in used_image_indices and current_image_index < len(images):
                            current_image_index += 1
                        
                        if current_image_index < len(images):
                            final_workflow[node_id]["inputs"][param_name] = images[current_image_index]
                            used_image_indices.add(current_image_index)
                            logger.info(f"节点 {node_id} 自动分配索引 {current_image_index} 的图片")
                            current_image_index += 1
                        else:
                            # 没有更多图片了，使用第一张图片
                            final_workflow[node_id]["inputs"][param_name] = images[0]
                            used_image_indices.add(0)
                            logger.info(f"节点 {node_id} 没有足够图片，使用第一张图片")
        
        logger.info(f"图片配置完成，使用了图片索引: {list(used_image_indices)}")
        
        # 创建参数名到别名的反向映射，用于快速查找
        param_to_aliases = {}
        node_configs = config.get("node_configs", {})
        for node_id, node_config in node_configs.items():
            for param_name, param_config in node_config.items():
                aliases = param_config.get("aliases", [])
                for alias in aliases:
                    if alias not in param_to_aliases:
                        param_to_aliases[alias] = []
                    param_to_aliases[alias].append((node_id, param_name))
        
        # 设置可配置节点参数 - 只修改配置文件中明确指定的节点
        for node_id, node_config in node_configs.items():
            if node_id not in final_workflow:
                continue
                
            for param_name, param_config in node_config.items():
                # 首先检查节点特定的参数格式 node_id:param_name
                value = None
                node_specific_key = f"{node_id}:{param_name}"
                if node_specific_key in params:
                    value = params[node_specific_key]
                else:
                    # 检查直接参数名匹配
                    if param_name in params:
                        value = params[param_name]
                    else:
                        # 检查别名匹配
                        aliases = param_config.get("aliases", [])
                        for alias in aliases:
                            if alias in params:
                                value = params[alias]
                                break
                
                # 如果找到了值，进行类型转换和设置
                if value is not None:
                    # 类型转换
                    param_type = param_config.get("type", "text")
                    
                    if param_type == "number":
                        try:
                            value = float(value)
                            if value.is_integer():
                                value = int(value)
                            # 特殊处理seed参数：如果值为-1，则生成随机种子
                            if param_name == "seed" and value == -1:
                                value = random.randint(1, 18446744073709551615)
                        except ValueError:
                            value = param_config.get("default", 0)
                    elif param_type == "boolean":
                        value = value.lower() in ("true", "1", "yes", "on")
                    elif param_type == "select":
                        options = param_config.get("options", [])
                        if value not in options:
                            value = param_config.get("default", options[0] if options else "")
                    
                    final_workflow[node_id]["inputs"][param_name] = value
                elif "default" in param_config:
                    # 使用默认值
                    value = param_config["default"]
                    # 特殊处理seed参数：如果默认值为-1，则生成随机种子
                    if param_name == "seed" and value == -1:
                        value = random.randint(1, 18446744073709551615)
                    final_workflow[node_id]["inputs"][param_name] = value
        
        # 设置全局模型配置（跟随主配置）
        if "30" in final_workflow and final_workflow["30"]["class_type"] == "CheckpointLoaderSimple":
            if self.ckpt_name and not final_workflow["30"]["inputs"].get("ckpt_name"):
                final_workflow["30"]["inputs"]["ckpt_name"] = self.ckpt_name
        
        return final_workflow

    def _inject_image_to_workflow(self, workflow: Dict[str, Any], config: Dict[str, Any], image_path: str) -> None:
        """将图片注入到workflow中"""
        try:
            # 查找图片输入节点
            input_nodes = config.get("input_nodes", [])
            for node_id in input_nodes:
                if node_id in workflow:
                    node_config = workflow[node_id]
                    class_type = node_config.get("class_type", "")
                    
                    # 常见的图片输入节点类型
                    if class_type in ["LoadImage", "ImageLoader", "ImageInput"]:
                        if "inputs" in node_config:
                            node_config["inputs"]["image"] = image_path
                        break
            else:
                # 如果没有找到明确的图片输入节点，尝试查找包含image参数的节点
                for node_id, node_data in workflow.items():
                    if "inputs" in node_data and "image" in node_data["inputs"]:
                        node_data["inputs"]["image"] = image_path
                        break
        except Exception as e:
            logger.warning(f"注入图片到workflow失败: {e}")

    def _inject_user_params(self, workflow: Dict[str, Any], config: Dict[str, Any], params: Dict[str, Any]) -> None:
        """将用户参数注入到workflow中"""
        try:
            node_configs = config.get("node_configs", {})
            
            for node_id, param_configs in node_configs.items():
                if node_id not in workflow:
                    continue
                
                for param_name, param_value in params.items():
                    # 检查参数是否在当前节点的配置中
                    if param_name in param_configs:
                        param_config = param_configs[param_name]
                        
                        # 类型转换
                        converted_value = self._convert_param_value(param_value, param_config)
                        
                        # 注入参数
                        if "inputs" not in workflow[node_id]:
                            workflow[node_id]["inputs"] = {}
                        workflow[node_id]["inputs"][param_name] = converted_value
                        
                        logger.debug(f"注入参数: {node_id}.{param_name} = {converted_value}")
        except Exception as e:
            logger.warning(f"注入用户参数到workflow失败: {e}")

    def _convert_param_value(self, value: Any, param_config: Dict[str, Any]) -> Any:
        """转换参数值到正确的类型"""
        try:
            param_type = param_config.get("type", "string")
            
            if param_type == "number":
                if "." in str(value):
                    return float(value)
                else:
                    return int(value)
            elif param_type == "boolean":
                if isinstance(value, str):
                    return value.lower() in ["true", "1", "yes", "on"]
                return bool(value)
            elif param_type == "select":
                options = param_config.get("options", [])
                if value not in options:
                    default = param_config.get("default", options[0] if options else "")
                    if default in options:
                        return default
                return value
            else:
                # 默认为字符串
                return str(value)
        except Exception as e:
            logger.warning(f"参数值转换失败: {value} -> {e}")
            return value

    def _init_gui(self) -> None:
        """初始化Flask GUI应用"""
        try:
            # 创建Flask应用
            self.app = Flask(__name__)
            # 使用动态生成密钥
            self.app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
            
            # 配置日志
            # 使用 astrabot.api.logger 记录 Flask 应用启动
            logger.info("Flask GUI 应用启动")
            
            # 设置模板目录
            template_dir = self.config_dir / "templates"
            if template_dir.exists():
                self.app.template_folder = str(template_dir)
            
            # 注册路由
            self._register_gui_routes()
            
            logger.info(f"Flask GUI应用初始化成功，端口: {self.gui_port}")
            
        except Exception as e:
            logger.error(f"初始化GUI失败: {e}")
            self.enable_gui = False

    def _register_gui_routes(self) -> None:
        """注册GUI路由"""
        
        def login_required(f):
            """登录验证装饰器"""
            @wraps(f)
            def decorated_function(*args, **kwargs):
                if 'logged_in' not in session:
                    return redirect(url_for('login'))
                return f(*args, **kwargs)
            return decorated_function

        @self.app.route('/login', methods=['GET', 'POST'])
        def login():
            """登录页面"""
            if request.method == 'POST':
                username = request.form.get('username', '')
                password = request.form.get('password', '')
                
                if username == self.gui_username and password == self.gui_password:
                    session['logged_in'] = True
                    flash('登录成功！', 'success')
                    return redirect(url_for('index'))
                else:
                    flash('用户名或密码错误！', 'error')
            
            return render_template('login.html')

        @self.app.route('/logout')
        def logout():
            """登出"""
            session.pop('logged_in', None)
            flash('已退出登录！', 'info')
            return redirect(url_for('login'))

        @self.app.route('/')
        @login_required
        def index():
            """主页 - 显示所有工作流"""
            workflows = self.config_manager.get_workflows()
            return render_template('index.html', workflows=workflows)

        @self.app.route('/main_config')
        @login_required
        def main_config():
            """主配置页面"""
            config = self.config_manager.load_main_config()
            return render_template('main_config.html', config=config)

        @self.app.route('/save_main_config', methods=['POST'])
        @login_required
        def save_main_config():
            """保存主配置"""
            try:
                config = request.form.to_dict()
                
                # 处理特殊字段
                config['cfg'] = float(config.get('cfg', 7.0))
                config['default_width'] = int(config.get('default_width', 1024))
                config['default_height'] = int(config.get('default_height', 1024))
                config['num_inference_steps'] = int(config.get('num_inference_steps', 30))
                config['default_denoise'] = float(config.get('default_denoise', 0.7))
                config['txt2img_batch_size'] = int(config.get('txt2img_batch_size', 1))
                config['img2img_batch_size'] = int(config.get('img2img_batch_size', 1))
                config['max_txt2img_batch'] = int(config.get('max_txt2img_batch', 6))
                config['max_img2img_batch'] = int(config.get('max_img2img_batch', 6))
                config['max_task_queue'] = int(config.get('max_task_queue', 10))
                config['min_width'] = int(config.get('min_width', 64))
                config['max_width'] = int(config.get('max_width', 2000))
                config['min_height'] = int(config.get('min_height', 64))
                config['max_height'] = int(config.get('max_height', 2000))
                config['queue_check_delay'] = int(config.get('queue_check_delay', 30))
                config['queue_check_interval'] = int(config.get('queue_check_interval', 5))
                config['empty_queue_max_retry'] = int(config.get('empty_queue_max_retry', 2))
                config['help_server_port'] = int(config.get('help_server_port', 8080))
                config['daily_download_limit'] = int(config.get('daily_download_limit', 1))
                config['max_concurrent_tasks_per_user'] = int(config.get('max_concurrent_tasks_per_user', 3))
                config['gui_port'] = int(config.get('gui_port', 7777))
                
                # 处理布尔字段
                config['enable_translation'] = config.get('enable_translation') == 'on'
                config['enable_image_encrypt'] = config.get('enable_image_encrypt') == 'on'
                config['enable_help_image'] = config.get('enable_help_image') == 'on'
                config['enable_auto_save'] = config.get('enable_auto_save') == 'on'
                config['enable_output_zip'] = config.get('enable_output_zip') == 'on'
                config['only_own_images'] = config.get('only_own_images') == 'on'
                config['enable_auto_recall'] = config.get('enable_auto_recall') == 'on'
                config['enable_gui'] = config.get('enable_gui') == 'on'
                
                # 处理数组字段
                comfyui_urls = request.form.getlist('comfyui_url')
                config['comfyui_url'] = [url.strip() for url in comfyui_urls if url.strip()]
                
                lora_configs = request.form.getlist('lora_config')
                config['lora_config'] = [lora.strip() for lora in lora_configs if lora.strip()]
                
                model_configs = request.form.getlist('model_config')
                config['model_config'] = [model.strip() for model in model_configs if model.strip()]
                
                if self.config_manager.save_main_config(config):
                    flash('主配置保存成功！', 'success')
                else:
                    flash('主配置保存失败！', 'error')
                    
                return redirect(url_for('main_config'))
                
            except Exception as e:
                logger.error(f"保存主配置失败: {e}")
                flash(f'保存失败: {str(e)}', 'error')
                return redirect(url_for('main_config'))

        @self.app.route('/workflow/<workflow_name>')
        @login_required
        def workflow_detail(workflow_name):
            """工作流详情页面"""
            workflows = self.config_manager.get_workflows()
            workflow = None
            
            for wf in workflows:
                if wf['name'] == workflow_name:
                    workflow = wf
                    break
            
            if not workflow:
                flash('工作流不存在！', 'error')
                return redirect(url_for('index'))
            
            return render_template('workflow_detail.html', workflow=workflow)

        @self.app.route('/workflow/<workflow_name>/edit')
        @login_required
        def workflow_edit(workflow_name):
            """编辑工作流页面"""
            workflows = self.config_manager.get_workflows()
            workflow = None
            
            for wf in workflows:
                if wf['name'] == workflow_name:
                    workflow = wf
                    break
            
            if not workflow:
                flash('工作流不存在！', 'error')
                return redirect(url_for('index'))
            
            return render_template('workflow_edit.html', workflow=workflow)

        @self.app.route('/workflow/<workflow_name>/save', methods=['POST'])
        @login_required
        def workflow_save(workflow_name):
            """保存工作流配置"""
            try:
                # 获取表单数据
                config = json.loads(request.form.get('config', '{}'))
                workflow_data = json.loads(request.form.get('workflow', '{}'))
                
                if self.config_manager.save_workflow(workflow_name, config, workflow_data):
                    flash('工作流保存成功！', 'success')
                else:
                    flash('工作流保存失败！', 'error')
                    
                return redirect(url_for('workflow_detail', workflow_name=workflow_name))
                
            except Exception as e:
                logger.error(f"保存工作流失败: {e}")
                flash(f'保存失败: {str(e)}', 'error')
                return redirect(url_for('workflow_edit', workflow_name=workflow_name))

        @self.app.route('/workflow/new')
        @login_required
        def workflow_new():
            """新建工作流页面"""
            return render_template('workflow_new.html')

        @self.app.route('/workflow/create', methods=['POST'])
        @login_required
        def workflow_create():
            """创建新工作流"""
            try:
                workflow_name = request.form.get('workflow_name', '').strip()
                
                if not workflow_name:
                    flash('工作流名称不能为空！', 'error')
                    return redirect(url_for('workflow_new'))
                
                # 检查工作流是否已存在
                workflow_path = self.workflow_dir / workflow_name
                if workflow_path.exists():
                    flash('工作流已存在！', 'error')
                    return redirect(url_for('workflow_new'))
                
                # 获取配置数据
                input_nodes = request.form.getlist('input_nodes')
                output_nodes = request.form.getlist('output_nodes')
                
                # 自动生成输入输出映射
                input_mappings = {}
                output_mappings = {}
                
                # 为每个输入节点生成映射
                for node_id in input_nodes:
                    input_mappings[node_id] = {
                        "parameter_name": "image",
                        "required": True,
                        "type": "image",
                        "description": "输入图片"
                    }
                
                # 为每个输出节点生成映射
                for node_id in output_nodes:
                    output_mappings[node_id] = {
                        "parameter_name": "images",
                        "type": "image",
                        "description": "处理后的图片"
                    }
                
                config = {
                    "name": request.form.get('name', workflow_name),
                    "prefix": request.form.get('prefix', ''),
                    "description": request.form.get('description', ''),
                    "version": request.form.get('version', '1.0.0'),
                    "author": request.form.get('author', 'ComfyUI Plugin'),
                    "input_nodes": input_nodes,
                    "output_nodes": output_nodes,
                    "input_mappings": input_mappings,
                    "output_mappings": output_mappings,
                    "configurable_nodes": request.form.getlist('configurable_nodes'),
                    "node_configs": {}
                }
                
                # 解析输入输出映射
                input_mappings = request.form.get('input_mappings', '{}')
                if input_mappings:
                    config['input_mappings'] = json.loads(input_mappings)
                    
                output_mappings = request.form.get('output_mappings', '{}')
                if output_mappings:
                    config['output_mappings'] = json.loads(output_mappings)
                
                # 解析节点配置
                node_configs = request.form.get('node_configs', '{}')
                if node_configs:
                    config['node_configs'] = json.loads(node_configs)
                
                # 创建空的 workflow 数据
                workflow_data = {}
                
                if self.config_manager.save_workflow(workflow_name, config, workflow_data):
                    flash('工作流创建成功！', 'success')
                    return redirect(url_for('workflow_detail', workflow_name=workflow_name))
                else:
                    flash('工作流创建失败！', 'error')
                    return redirect(url_for('workflow_new'))
                    
            except Exception as e:
                logger.error(f"创建工作流失败: {e}")
                flash(f'创建失败: {str(e)}', 'error')
                return redirect(url_for('workflow_new'))

        @self.app.route('/workflow/<workflow_name>/delete', methods=['POST'])
        @login_required
        def workflow_delete(workflow_name):
            """删除工作流"""
            try:
                if self.config_manager.delete_workflow(workflow_name):
                    flash('工作流删除成功！', 'success')
                else:
                    flash('工作流删除失败！', 'error')
            except Exception as e:
                logger.error(f"删除工作流失败: {e}")
                flash(f'删除失败: {str(e)}', 'error')
            
            return redirect(url_for('index'))

    def _start_gui_server(self) -> None:
        """启动GUI服务器"""
        if not self.enable_gui or not self.app:
            return
            
        def run_gui():
            try:
                logger.info(f"启动ComfyUI配置管理界面...")
                logger.info(f"配置目录: {self.config_dir}")
                logger.info(f"工作流目录: {self.workflow_dir}")
                logger.info(f"访问地址: http://0.0.0.0:{self.gui_port}")
                logger.info(f"管理员账号: {self.gui_username}")
                logger.info("=" * 50)
                
                # 尝试使用不同的WSGI服务器
                try:
                    import importlib
                    gunicorn_spec = importlib.util.find_spec("gunicorn")
                    if gunicorn_spec is None:
                        raise ImportError("Gunicorn not found")
                    from gunicorn.app.base import BaseApplication
                    
                    class GunicornApp(BaseApplication):
                        def __init__(self, app, options=None):
                            self.options = options or {}
                            self.application = app
                            super().__init__()
                        
                        def load_config(self):
                            config = {key: value for key, value in self.options.items()
                                     if key in self.cfg.settings and value is not None}
                            for key, value in config.items():
                                self.cfg.set(key.lower(), value)
                        
                        def load(self):
                            return self.application
                    
                    options = {
                        'bind': f'0.0.0.0:{self.gui_port}',
                        'workers': 2,
                        'threads': 2,
                        'worker_class': 'gthread',
                        'timeout': 120,
                        'keepalive': 2,
                        'max_requests': 1000,
                        'max_requests_jitter': 100,
                        'preload_app': True,
                        'accesslog': '-',
                        'errorlog': '-',
                        'loglevel': 'info'
                    }
                    
                    GunicornApp(self.app, options).run()
                    
                except ImportError:
                    logger.info("Gunicorn 不可用，尝试使用 Waitress...")
                    try:
                        from waitress import serve
                        logger.info("使用 Waitress WSGI 服务器")
                        serve(self.app, host='0.0.0.0', port=self.gui_port, threads=4)
                    except ImportError:
                        logger.info("未安装 Waitress，使用 Flask 开发服务器")
                        self.app.run(host='0.0.0.0', port=self.gui_port, debug=False, threaded=True)
                        
            except Exception as e:
                logger.error(f"GUI服务器启动失败: {e}")
        
        # 在新线程中启动GUI服务器
        self.gui_thread = threading.Thread(target=run_gui, daemon=True)
        self.gui_thread.start()
        self.gui_running = True
        logger.info("GUI服务器已启动")

    def _stop_gui_server(self) -> None:
        """停止GUI服务器"""
        if self.gui_running and self.gui_thread:
            logger.info("正在停止GUI服务器...")
            # 注意：由于在独立线程中运行，这里只是标记
            self.gui_running = False
            logger.info("GUI服务器已停止")

    def _init_web_api(self) -> None:
        """初始化Web API Flask应用"""
        try:
            # 创建Flask应用
            self.web_api_app = Flask(__name__)
            # 使用动态生成密钥
            self.web_api_app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
            
            # 配置日志
            # 使用 astrabot.api.logger 记录 Web API 应用启动
            logger.info("Flask Web API 应用启动")
            
            # 注册Web API路由
            self._register_web_api_routes()
            
            logger.info(f"Web API应用初始化成功，端口: {self.web_api_port}")
            
        except Exception as e:
            logger.error(f"初始化Web API失败: {e}")
            self.enable_web_api = False

    def _register_web_api_routes(self) -> None:
        """注册Web API路由"""
        
        def api_auth_required(f):
            """API认证装饰器"""
            @wraps(f)
            def decorated_function(*args, **kwargs):
                auth_header = request.headers.get('Authorization')
                if not auth_header or not auth_header.startswith('Bearer '):
                    return jsonify({'error': '缺少认证令牌'}), 401
                
                token = auth_header[7:]  # 移除 'Bearer ' 前缀
                
                # 验证token（这里使用简单的用户名密码验证）
                if not self._validate_api_token(token, request):
                    return jsonify({'error': '无效的认证令牌'}), 401
                
                return f(*args, **kwargs)
            return decorated_function

        # 前端页面路由
        @self.web_api_app.route('/')
        def index():
            """主页"""
            try:
                # 检查templates目录是否存在web_index.html
                template_path = os.path.join(os.path.dirname(__file__), 'templates', 'web_index.html')
                if os.path.exists(template_path):
                    return render_template('web_index.html')
                else:
                    # 如果模板不存在，返回简单的HTML
                    return '''
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>ComfyUI Web API</title>
                        <meta charset="utf-8">
                        <meta name="viewport" content="width=device-width, initial-scale=1">
                        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
                        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
                    </head>
                    <body>
                        <div class="container mt-5">
                            <div class="row justify-content-center">
                                <div class="col-md-8">
                                    <div class="card">
                                        <div class="card-header">
                                            <h3><i class="bi bi-palette-fill"></i> ComfyUI Web API</h3>
                                        </div>
                                        <div class="card-body">
                                            <p>欢迎使用 ComfyUI Web API 服务！</p>
                                            <h5>API 端点：</h5>
                                            <ul>
                                                <li><strong>POST /api/register</strong> - 用户注册</li>
                                                <li><strong>POST /api/login</strong> - 用户登录</li>
                                                <li><strong>POST /api/aimg</strong> - 文生图</li>
                                                <li><strong>POST /api/img2img</strong> - 图生图</li>
                                                <li><strong>POST /api/workflow/&lt;name&gt;</strong> - 执行工作流</li>
                                                <li><strong>GET /api/status</strong> - 获取状态</li>
                                            </ul>
                                            <p><strong>注意：</strong> 所有API请求都需要在Header中包含认证token：<br>
                                            <code>Authorization: Bearer &lt;your_token&gt;</code></p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </body>
                    </html>
                    '''
            except Exception as e:
                logger.error(f"渲染主页失败: {e}")
                return f"<h1>ComfyUI Web API</h1><p>服务运行中，但模板渲染失败: {str(e)}</p>"

        @self.web_api_app.route('/static/<path:filename>')
        def static_files(filename):
            """静态文件服务"""
            try:
                # 检查static目录是否存在
                static_dir = os.path.join(os.path.dirname(__file__), 'static')
                if os.path.exists(static_dir):
                    return send_from_directory(static_dir, filename)
                else:
                    return "Static files not found", 404
            except Exception as e:
                logger.error(f"提供静态文件失败: {e}")
                return f"Static file error: {str(e)}", 500

        @self.web_api_app.route('/api/register', methods=['POST'])
        def register():
            """用户注册"""
            if not self.web_api_allow_register:
                return jsonify({'error': '注册功能已禁用'}), 403
            
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': '请求数据为空'}), 400
                
                username = data.get('username', '').strip()
                password = data.get('password', '').strip()
                
                if not username or not password:
                    return jsonify({'error': '用户名和密码不能为空'}), 400
                
                # 检查用户名是否已存在
                if self._user_exists(username):
                    return jsonify({'error': '用户名已存在'}), 409
                
                # 创建用户
                user_id = self._create_user(username, password)
                if user_id:
                    # 生成API token
                    token = self._generate_api_token(username)
                    return jsonify({
                        'message': '注册成功',
                        'user_id': user_id,
                        'username': username,
                        'token': token
                    }), 201
                else:
                    return jsonify({'error': '注册失败'}), 500
                    
            except Exception as e:
                logger.error(f"用户注册失败: {e}")
                return jsonify({'error': '服务器内部错误'}), 500

        @self.web_api_app.route('/api/login', methods=['POST'])
        def login():
            """用户登录"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': '请求数据为空'}), 400
                
                username = data.get('username', '').strip()
                password = data.get('password', '').strip()
                
                if not username or not password:
                    return jsonify({'error': '用户名和密码不能为空'}), 400
                
                # 验证用户
                if self._verify_user(username, password):
                    # 生成API token
                    token = self._generate_api_token(username)
                    return jsonify({
                        'message': '登录成功',
                        'username': username,
                        'token': token
                    }), 200
                else:
                    return jsonify({'error': '用户名或密码错误'}), 401
                    
            except Exception as e:
                logger.error(f"用户登录失败: {e}")
                return jsonify({'error': '服务器内部错误'}), 500

        @self.web_api_app.route('/api/aimg', methods=['POST'])
        @api_auth_required
        def api_aimg():
            """文生图API"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': '请求数据为空'}), 400
                
                prompt = data.get('prompt', '').strip()
                if not prompt:
                    return jsonify({'error': '提示词不能为空'}), 400
                
                # 解析其他参数
                width = data.get('width', self.default_width)
                height = data.get('height', self.default_height)
                batch_size = data.get('batch_size', self.txt2img_batch_size)
                model = data.get('model', None)
                lora = data.get('lora', [])
                seed = data.get('seed', self.seed)
                
                # 创建模拟事件对象
                user_id = request.environ.get('API_USER_ID', 'web_api_user')
                
                # 异步处理图片生成
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    result = loop.run_until_complete(
                        self._process_aimg_request(prompt, width, height, batch_size, model, lora, seed, user_id)
                    )
                    return jsonify(result), 200
                finally:
                    loop.close()
                    
            except Exception as e:
                logger.error(f"文生图API处理失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.web_api_app.route('/api/img2img', methods=['POST'])
        @api_auth_required
        def api_img2img():
            """图生图API"""
            try:
                # 检查是否有文件上传
                if 'image' not in request.files:
                    return jsonify({'error': '请上传图片'}), 400
                
                file = request.files['image']
                if file.filename == '':
                    return jsonify({'error': '未选择文件'}), 400
                
                data = request.form.to_dict()
                prompt = data.get('prompt', '').strip()
                if not prompt:
                    return jsonify({'error': '提示词不能为空'}), 400
                
                # 解析其他参数
                denoise = float(data.get('denoise', self.default_denoise))
                batch_size = int(data.get('batch_size', self.img2img_batch_size))
                model = data.get('model', None)
                lora = data.get('lora', '[]')
                # 解析LoRA JSON字符串
                try:
                    if isinstance(lora, str):
                        lora = json.loads(lora)
                    elif not isinstance(lora, list):
                        lora = []
                except json.JSONDecodeError:
                    logger.error(f"图生图API LoRA参数JSON解析失败: {lora}")
                    lora = []
                seed = data.get('seed', self.seed)
                
                # 创建模拟事件对象
                user_id = request.environ.get('API_USER_ID', 'web_api_user')
                
                # 异步处理图片生成
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    # 保存上传的图片到临时文件
                    import tempfile
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
                        file.save(temp_file.name)
                        temp_image_path = temp_file.name
                    
                    result = loop.run_until_complete(
                        self._process_img2img_request(prompt, temp_image_path, denoise, batch_size, model, lora, seed, user_id)
                    )
                    return jsonify(result), 200
                finally:
                    loop.close()
                    # 清理临时文件
                    try:
                        os.unlink(temp_image_path)
                    except:
                        pass
                    
            except Exception as e:
                logger.error(f"图生图API处理失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.web_api_app.route('/api/workflow/<workflow_name>', methods=['POST'])
        @api_auth_required
        def api_workflow(workflow_name):
            """Workflow API"""
            try:
                if workflow_name not in self.workflows:
                    return jsonify({'error': f'未找到workflow: {workflow_name}'}), 404
                
                # 检查是否有文件上传
                file = None
                temp_image_path = None
                if 'image' in request.files:
                    file = request.files['image']
                    if file.filename != '':
                        import tempfile
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
                            file.save(temp_file.name)
                            temp_image_path = temp_file.name
                
                data = request.form.to_dict() if file else request.get_json()
                if not data:
                    data = {}
                
                # 创建模拟事件对象
                user_id = request.environ.get('API_USER_ID', 'web_api_user')
                
                # 异步处理workflow
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    result = loop.run_until_complete(
                        self._process_workflow_request(workflow_name, data, temp_image_path, user_id)
                    )
                    return jsonify(result), 200
                finally:
                    loop.close()
                    # 清理临时文件
                    if temp_image_path:
                        try:
                            os.unlink(temp_image_path)
                        except:
                            pass
                    
            except Exception as e:
                logger.error(f"Workflow API处理失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.web_api_app.route('/api/image/<filename>')
        def serve_image(filename):
            """提供图片代理服务
            
            优先级：
            1. 如果启用自动保存，从本地保存的文件提供（支持时间戳前缀和日期目录）
            2. 否则从ComfyUI服务器实时获取并代理
            """
            try:
                # 安全检查：防止路径遍历攻击
                if '..' in filename:
                    return jsonify({'error': '非法文件名'}), 400
                
                # 如果启用了自动保存，优先从本地保存的文件提供
                if self.enable_auto_save:
                    # 检查是否是带时间戳前缀的文件名
                    if filename.startswith(('2020', '2021', '2022', '2023', '2024', '2025')) and '_' in filename:
                        # 格式：20251128_100636_comfyui_gen_00367_.png
                        try:
                            # 解析时间戳获取日期目录
                            date_part = filename[:8]  # 20251128
                            year = date_part[:4]
                            month = int(date_part[4:6])  # 转换为整数
                            day = int(date_part[6:8])    # 转换为整数
                            
                            # 构建完整路径 - 与保存逻辑保持一致
                            auto_save_path = Path(self.auto_save_dir)
                            if auto_save_path.is_absolute():
                                # 绝对路径：直接使用
                                image_path = auto_save_path / year / f"{month:02d}" / f"{day:02d}" / filename
                            else:
                                # 相对路径：基于插件目录
                                plugin_dir = Path(os.path.dirname(__file__))
                                image_path = plugin_dir / self.auto_save_dir / year / f"{month:02d}" / f"{day:02d}" / filename
                            
                            logger.info(f"查找图片路径: {image_path}")
                            if os.path.exists(image_path) and os.path.isfile(image_path):
                                logger.info(f"从本地提供图片（带时间戳）: {filename}")
                                return send_file(str(image_path))
                        except Exception as e:
                            logger.warning(f"解析时间戳文件名失败: {e}")
                    
                    # 尝试直接在保存目录中查找（兼容非时间戳文件）
                    auto_save_path = Path(self.auto_save_dir)
                    if auto_save_path.is_absolute():
                        local_image_path = auto_save_path / filename
                    else:
                        plugin_dir = Path(os.path.dirname(__file__))
                        local_image_path = plugin_dir / self.auto_save_dir / filename
                    
                    if os.path.exists(local_image_path) and os.path.isfile(local_image_path):
                        logger.info(f"从本地提供图片: {filename}")
                        return send_file(str(local_image_path))
                
                # 如果本地没有，从ComfyUI服务器获取并代理
                # 对于带时间戳的文件名，需要提取原始文件名
                original_filename = filename
                if '_' in filename and any(filename.startswith(prefix) for prefix in ['2020', '2021', '2022', '2023', '2024', '2025']):
                    # 提取原始文件名（去掉时间戳前缀）
                    parts = filename.split('_', 2)  # 分割为最多3部分
                    if len(parts) >= 3:
                        original_filename = parts[2]  # 取第三部分及之后的内容
                
                # 尝试从健康的服务器获取图片
                for server in self.comfyui_servers:
                    if server.healthy:
                        try:
                            image_url = f"{server.url}/view?filename={original_filename}&type=output&subfolder=&preview=true"
                            logger.info(f"从服务器 {server.name} 代理图片: {original_filename}")
                            
                            # 下载图片，设置超时
                            response = requests.get(image_url, timeout=15, stream=True)
                            if response.status_code == 200:
                                # 使用流式传输，避免大文件占用内存
                                img_data = BytesIO(response.content)
                                img_data.seek(0)
                                
                                # 根据文件扩展名确定MIME类型
                                if filename.lower().endswith('.png'):
                                    mimetype = 'image/png'
                                elif filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg'):
                                    mimetype = 'image/jpeg'
                                elif filename.lower().endswith('.webp'):
                                    mimetype = 'image/webp'
                                else:
                                    mimetype = 'image/png'
                                
                                # 设置缓存头
                                response_headers = {
                                    'Cache-Control': 'public, max-age=3600',  # 缓存1小时
                                    'Access-Control-Allow-Origin': '*'
                                }
                                
                                from flask import Response
                                return Response(img_data.read(), mimetype=mimetype, headers=response_headers)
                            else:
                                logger.warning(f"服务器 {server.name} 返回状态码: {response.status_code}")
                        except requests.exceptions.Timeout:
                            logger.warning(f"从服务器 {server.name} 获取图片超时")
                            continue
                        except Exception as e:
                            logger.warning(f"从服务器 {server.name} 获取图片失败: {e}")
                            continue
                
                # 添加详细的调试信息
                logger.error(f"无法获取图片: {filename}")
                logger.error(f"自动保存启用: {self.enable_auto_save}")
                logger.error(f"自动保存目录: {self.auto_save_dir}")
                logger.error(f"保存目录类型: {'绝对路径' if Path(self.auto_save_dir).is_absolute() else '相对路径'}")
                if self.enable_auto_save:
                    auto_save_path = Path(self.auto_save_dir)
                    if auto_save_path.is_absolute():
                        base_path = auto_save_path
                    else:
                        plugin_dir = Path(os.path.dirname(__file__))
                        base_path = plugin_dir / self.auto_save_dir
                    logger.error(f"实际保存根目录: {base_path}")
                    logger.error(f"目录是否存在: {os.path.exists(base_path)}")
                
                return jsonify({'error': '图片不存在或所有服务器不可用'}), 404
                
            except Exception as e:
                logger.error(f"图片代理服务失败: {e}")
                return jsonify({'error': '服务器内部错误'}), 500

        @self.web_api_app.route('/api/status', methods=['GET'])
        @api_auth_required
        def api_status():
            """获取API状态"""
            try:
                return jsonify({
                    'status': 'running',
                    'servers': [
                        {
                            'name': server.name,
                            'url': server.url,
                            'healthy': server.healthy,
                            'busy': server.busy
                        } for server in self.comfyui_servers
                    ],
                    'workflows': list(self.workflows.keys()),
                    'models': self._get_unique_model_descriptions(),
                    'loras': self._get_unique_lora_descriptions(),
                    'samplers': self.available_samplers,
                    'schedulers': self.available_schedulers,
                }), 200
            except Exception as e:
                logger.error(f"状态API处理失败: {e}")
                return jsonify({'error': str(e)}), 500

    def _start_web_api_server(self) -> None:
        """启动Web API服务器"""
        if not self.enable_web_api or not self.web_api_app:
            return
            
        def run_web_api():
            try:
                logger.info(f"启动ComfyUI Web API服务器...")
                logger.info(f"访问地址: http://0.0.0.0:{self.web_api_port}")
                logger.info(f"允许注册: {self.web_api_allow_register}")
                logger.info("=" * 50)
                
                # 尝试使用不同的WSGI服务器
                try:
                    from gunicorn.app.base import BaseApplication
                    
                    class GunicornApp(BaseApplication):
                        def __init__(self, app, options=None):
                            self.options = options or {}
                            self.application = app
                            super().__init__()
                        
                        def load_config(self):
                            config = {
                                'bind': f'0.0.0.0:{self.web_api_port}',
                                'workers': 1,
                                'threads': 4,
                                'timeout': 120,
                                'keepalive': 2,
                                'max_requests': 1000,
                                'max_requests_jitter': 100,
                                'preload_app': True,
                            }
                            for key, value in config.items():
                                if key in self.cfg.settings:
                                    self.cfg.set(key, value)
                        
                        def load(self):
                            return self.application
                    
                    logger.info("使用 Gunicorn WSGI 服务器")
                    GunicornApp(self.web_api_app, options={
                        'bind': f'0.0.0.0:{self.web_api_port}',
                    }).run()
                    
                except ImportError:
                    logger.info("Gunicorn 不可用，尝试使用 Waitress...")
                    try:
                        from waitress import serve
                        logger.info("使用 Waitress WSGI 服务器")
                        serve(self.web_api_app, host='0.0.0.0', port=self.web_api_port, threads=4)
                    except ImportError:
                        logger.info("未安装 Waitress，使用 Flask 开发服务器")
                        self.web_api_app.run(host='0.0.0.0', port=self.web_api_port, debug=False, threaded=True)
                        
            except Exception as e:
                logger.error(f"Web API服务器启动失败: {e}")
        
        # 在新线程中启动Web API服务器
        self.web_api_thread = threading.Thread(target=run_web_api, daemon=True)
        self.web_api_thread.start()
        self.web_api_running = True
        logger.info("Web API服务器已启动")

    def _stop_web_api_server(self) -> None:
        """停止Web API服务器"""
        if self.web_api_running and self.web_api_thread:
            logger.info("正在停止Web API服务器...")
            # 注意：由于在独立线程中运行，这里只是标记
            self.web_api_running = False
            logger.info("Web API服务器已停止")

    def _user_exists(self, username: str) -> bool:
        """检查用户是否存在"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._check_user_exists(username))
                return result
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"检查用户存在性失败: {e}")
            return False

    async def _check_user_exists(self, username: str) -> bool:
        """异步检查用户是否存在"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM api_users WHERE username = ?",
                    (username,)
                )
                count = (await cursor.fetchone())[0]
                return count > 0
        except Exception as e:
            logger.error(f"查询用户失败: {e}")
            return False

    def _create_user(self, username: str, password: str) -> Optional[str]:
        """创建新用户"""
        try:
            import hashlib
            # 简单的密码哈希
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                user_id = loop.run_until_complete(self._insert_user(username, password_hash))
                return user_id
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"创建用户失败: {e}")
            return None

    async def _insert_user(self, username: str, password_hash: str) -> Optional[str]:
        """异步插入用户"""
        try:
            import uuid
            user_id = str(uuid.uuid4())
            
            async with aiosqlite.connect(self.db_path) as conn:
                # 创建api_users表（如果不存在）
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS api_users (
                        id TEXT PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                await conn.execute(
                    "INSERT INTO api_users (id, username, password_hash) VALUES (?, ?, ?)",
                    (user_id, username, password_hash)
                )
                await conn.commit()
                return user_id
        except Exception as e:
            logger.error(f"插入用户失败: {e}")
            return None

    def _verify_user(self, username: str, password: str) -> bool:
        """验证用户凭据"""
        try:
            import hashlib
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._check_user_credentials(username, password_hash))
                return result
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"验证用户失败: {e}")
            return False

    async def _check_user_credentials(self, username: str, password_hash: str) -> bool:
        """异步检查用户凭据"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM api_users WHERE username = ? AND password_hash = ?",
                    (username, password_hash)
                )
                count = (await cursor.fetchone())[0]
                return count > 0
        except Exception as e:
            logger.error(f"检查用户凭据失败: {e}")
            return False

    def _generate_api_token(self, username: str) -> str:
        """生成API token"""
        try:
            import hashlib
            import time
            # 简单的token生成（用户名+时间戳的哈希）
            token_data = f"{username}:{int(time.time())}"
            token = hashlib.sha256(token_data.encode()).hexdigest()
            
            # 存储token到数据库
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._store_token(username, token))
            finally:
                loop.close()
            
            return token
        except Exception as e:
            logger.error(f"生成API token失败: {e}")
            return ""

    async def _store_token(self, username: str, token: str) -> None:
        """存储API token"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                # 创建api_tokens表（如果不存在）
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS api_tokens (
                        token TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP
                    )
                ''')
                
                # 设置token过期时间为24小时
                import datetime
                expires_at = datetime.datetime.now() + datetime.timedelta(hours=24)
                
                await conn.execute(
                    "INSERT OR REPLACE INTO api_tokens (token, username, expires_at) VALUES (?, ?, ?)",
                    (token, username, expires_at)
                )
                await conn.commit()
        except Exception as e:
            logger.error(f"存储token失败: {e}")

    def _validate_api_token(self, token: str, request_obj) -> bool:
        """验证API token"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._check_token_valid(token, request_obj))
                return result
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"验证API token失败: {e}")
            return False

    async def _check_token_valid(self, token: str, request_obj) -> bool:
        """异步检查token有效性"""
        try:
            import datetime
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT username FROM api_tokens WHERE token = ? AND (expires_at IS NULL OR expires_at > ?)",
                    (token, datetime.datetime.now())
                )
                result = await cursor.fetchone()
                if result:
                    # 将用户名存储到请求环境中，供后续使用
                    username = result[0]
                    request_obj.environ['API_USER_ID'] = username
                    return True
                return False
        except Exception as e:
            logger.error(f"检查token有效性失败: {e}")
            return False

    async def _process_aimg_request(self, prompt: str, width: int, height: int, batch_size: int, 
                                  model: Optional[str], lora: List[str], seed: str, user_id: str) -> Dict[str, Any]:
        """处理文生图请求"""
        try:
            # 检查开放时间
            if not self._is_in_open_time():
                return {
                    'error': f'当前未开放图片生成服务，开放时间：{self.open_time_ranges}'
                }
            
            # 检查服务器状态
            if not self._get_any_healthy_server():
                return {
                    'error': '所有ComfyUI服务器均不可用，请稍后再试'
                }
            
            # 检查用户并发限制
            if not await self._check_user_task_limit(user_id):
                return {
                    'error': f'您当前有过多任务在执行中（最大{self.max_concurrent_tasks_per_user}个），请稍后再试'
                }
            
            # 验证参数
            if not (self.min_width <= width <= self.max_width):
                return {
                    'error': f'宽度必须在{self.min_width}~{self.max_width}之间'
                }
            
            if not (self.min_height <= height <= self.max_height):
                return {
                    'error': f'高度必须在{self.min_height}~{self.max_height}之间'
                }
            
            if not (1 <= batch_size <= self.max_txt2img_batch):
                return {
                    'error': f'批量数必须在1~{self.max_txt2img_batch}之间'
                }
            
            # 处理模型选择
            selected_model = None
            if model:
                # 确保model是字符串类型
                if not isinstance(model, str):
                    model = str(model)
                
                # 处理网页端传入的"描述，文件名"格式
                if '，' in model or ',' in model:
                    # 如果包含逗号，尝试分割并使用描述部分
                    parts = model.split('，' if '，' in model else ',', 1)
                    model_to_match = parts[0].strip()
                else:
                    model_to_match = model.strip()
                
                model_lower = model_to_match.lower()
                
                # 添加调试日志
                logger.info(f"Web API 接收到的模型参数: {model}")
                logger.info(f"Web API 处理后的匹配参数: {model_to_match}")
                logger.info(f"Web API 可用的模型映射: {list(self.model_name_map.keys())}")
                
                if model_lower in self.model_name_map:
                    selected_model = self.model_name_map[model_lower][0]
                    logger.info(f"Web API 成功匹配到模型: {selected_model}")
                else:
                    logger.error(f"Web API 未找到模型: {model} (处理后: {model_to_match}, 小写: {model_lower})")
                    return {
                        'error': f'未找到模型：{model}'
                    }
            
            # 处理LoRA
            lora_list = []
            
            # 验证lora参数
            if not isinstance(lora, list):
                logger.error(f"Web API LoRA参数类型错误: {type(lora)}, 期望list")
                return {
                    'error': 'LoRA参数格式错误，期望数组格式'
                }
            
            # 过滤空的LoRA项
            valid_lora_items = []
            for lora_item in lora:
                if lora_item is None:
                    continue
                if isinstance(lora_item, str) and not lora_item.strip():
                    continue
                if isinstance(lora_item, dict) and not lora_item.get('name', '').strip():
                    continue
                valid_lora_items.append(lora_item)
            
            logger.info(f"Web API 过滤后的有效LoRA项: {valid_lora_items}")
            
            for lora_item in valid_lora_items:
                # 网页端传入的是字典格式：{'name': '描述，文件名', 'strength': 0.6}
                if isinstance(lora_item, dict):
                    lora_name = lora_item.get('name', '')
                    lora_strength = lora_item.get('strength', 1.0)
                else:
                    # 兼容字符串格式
                    lora_name = str(lora_item)
                    lora_strength = 1.0
                
                # 确保lora_name是字符串类型
                if not isinstance(lora_name, str):
                    lora_name = str(lora_name)
                
                # 处理网页端传入的"描述，文件名"格式
                if '，' in lora_name or ',' in lora_name:
                    # 如果包含逗号，尝试分割并使用描述部分
                    parts = lora_name.split('，' if '，' in lora_name else ',', 1)
                    lora_to_match = parts[0].strip()
                else:
                    lora_to_match = lora_name.strip()
                
                lora_lower = lora_to_match.lower()
                
                # 添加调试日志
                logger.info(f"Web API 接收到的LoRA参数: {lora_item}")
                logger.info(f"Web API 处理后的LoRA匹配参数: {lora_to_match}")
                logger.info(f"Web API 可用的LoRA映射: {list(self.lora_name_map.keys())}")
                
                if lora_lower in self.lora_name_map:
                    lora_list.append({
                        'name': lora_to_match,  # 添加name属性，用于显示
                        'filename': self.lora_name_map[lora_lower][0],
                        'strength_model': float(lora_strength),
                        'strength_clip': float(lora_strength)
                    })
                    logger.info(f"Web API 成功匹配到LoRA: {self.lora_name_map[lora_lower][0]} (强度: {lora_strength})")
                else:
                    logger.error(f"Web API 未找到LoRA: {lora_item} (处理后: {lora_to_match}, 小写: {lora_lower})")
                    return {
                        'error': f'未找到LoRA：{lora_name}'
                    }
            
            # 处理种子
            # 确保seed是字符串类型
            if not isinstance(seed, str):
                seed = str(seed)
            if seed == "随机" or seed.lower() == "random" or seed == "-1":
                seed_value = random.randint(1, 18446744073709551615)
            else:
                try:
                    seed_value = int(seed)
                    # 确保种子值在有效范围内
                    if seed_value < 0:
                        seed_value = random.randint(1, 18446744073709551615)
                except ValueError:
                    return {
                        'error': f'种子值无效：{seed}'
                    }
            
            # 创建任务
            task_id = str(uuid.uuid4())
            task_data = {
                'type': 'txt2img',
                'prompt': prompt,
                'width': width,
                'height': height,
                'batch_size': batch_size,
                'seed': seed_value,
                'model': selected_model,
                'lora_list': lora_list,
                'user_id': user_id,
                'task_id': task_id,
                'is_web_api': True
            }
            
            # 提交任务到队列
            try:
                await self.task_queue.put(task_data)
                logger.info(f"Web API文生图任务已提交: {task_id}")
            except asyncio.QueueFull:
                return {
                    'error': '任务队列已满，请稍后再试'
                }
            
            # 等待任务完成
            result = await self._wait_for_task_completion(task_id, timeout=300)  # 5分钟超时
            
            return result
            
        except Exception as e:
            logger.error(f"处理文生图请求失败: {e}")
            return {
                'error': f'处理请求失败: {str(e)}'
            }

    async def _process_img2img_request(self, prompt: str, image_path: str, denoise: float, 
                                     batch_size: int, model: Optional[str], lora: List[str], 
                                     seed: str, user_id: str) -> Dict[str, Any]:
        """处理图生图请求"""
        try:
            # 检查开放时间
            if not self._is_in_open_time():
                return {
                    'error': f'当前未开放图片生成服务，开放时间：{self.open_time_ranges}'
                }
            
            # 检查服务器状态
            if not self._get_any_healthy_server():
                return {
                    'error': '所有ComfyUI服务器均不可用，请稍后再试'
                }
            
            # 检查用户并发限制
            if not await self._check_user_task_limit(user_id):
                return {
                    'error': f'您当前有过多任务在执行中（最大{self.max_concurrent_tasks_per_user}个），请稍后再试'
                }
            
            # 验证参数
            if not (0.0 <= denoise <= 1.0):
                return {
                    'error': f'噪声系数必须在0.0~1.0之间'
                }
            
            if not (1 <= batch_size <= self.max_img2img_batch):
                return {
                    'error': f'批量数必须在1~{self.max_img2img_batch}之间'
                }
            
            # 处理模型选择
            selected_model = None
            if model:
                # 确保model是字符串类型
                if not isinstance(model, str):
                    model = str(model)
                
                # 处理网页端传入的"描述，文件名"格式
                if '，' in model or ',' in model:
                    # 如果包含逗号，尝试分割并使用描述部分
                    parts = model.split('，' if '，' in model else ',', 1)
                    model_to_match = parts[0].strip()
                else:
                    model_to_match = model.strip()
                
                model_lower = model_to_match.lower()
                
                # 添加调试日志
                logger.info(f"Web API 接收到的模型参数: {model}")
                logger.info(f"Web API 处理后的匹配参数: {model_to_match}")
                logger.info(f"Web API 可用的模型映射: {list(self.model_name_map.keys())}")
                
                if model_lower in self.model_name_map:
                    selected_model = self.model_name_map[model_lower][0]
                    logger.info(f"Web API 成功匹配到模型: {selected_model}")
                else:
                    logger.error(f"Web API 未找到模型: {model} (处理后: {model_to_match}, 小写: {model_lower})")
                    return {
                        'error': f'未找到模型：{model}'
                    }
            
            # 处理LoRA
            lora_list = []
            
            # 验证lora参数
            if not isinstance(lora, list):
                logger.error(f"Web API LoRA参数类型错误: {type(lora)}, 期望list")
                return {
                    'error': 'LoRA参数格式错误，期望数组格式'
                }
            
            # 过滤空的LoRA项
            valid_lora_items = []
            for lora_item in lora:
                if lora_item is None:
                    continue
                if isinstance(lora_item, str) and not lora_item.strip():
                    continue
                if isinstance(lora_item, dict) and not lora_item.get('name', '').strip():
                    continue
                valid_lora_items.append(lora_item)
            
            logger.info(f"Web API 过滤后的有效LoRA项: {valid_lora_items}")
            
            for lora_item in valid_lora_items:
                # 网页端传入的是字典格式：{'name': '描述，文件名', 'strength': 0.6}
                if isinstance(lora_item, dict):
                    lora_name = lora_item.get('name', '')
                    lora_strength = lora_item.get('strength', 1.0)
                else:
                    # 兼容字符串格式
                    lora_name = str(lora_item)
                    lora_strength = 1.0
                
                # 确保lora_name是字符串类型
                if not isinstance(lora_name, str):
                    lora_name = str(lora_name)
                
                # 处理网页端传入的"描述，文件名"格式
                if '，' in lora_name or ',' in lora_name:
                    # 如果包含逗号，尝试分割并使用描述部分
                    parts = lora_name.split('，' if '，' in lora_name else ',', 1)
                    lora_to_match = parts[0].strip()
                else:
                    lora_to_match = lora_name.strip()
                
                lora_lower = lora_to_match.lower()
                
                # 添加调试日志
                logger.info(f"Web API 接收到的LoRA参数: {lora_item}")
                logger.info(f"Web API 处理后的LoRA匹配参数: {lora_to_match}")
                logger.info(f"Web API 可用的LoRA映射: {list(self.lora_name_map.keys())}")
                
                if lora_lower in self.lora_name_map:
                    lora_list.append({
                        'name': lora_to_match,  # 添加name属性，用于显示
                        'filename': self.lora_name_map[lora_lower][0],
                        'strength_model': float(lora_strength),
                        'strength_clip': float(lora_strength)
                    })
                    logger.info(f"Web API 成功匹配到LoRA: {self.lora_name_map[lora_lower][0]} (强度: {lora_strength})")
                else:
                    logger.error(f"Web API 未找到LoRA: {lora_item} (处理后: {lora_to_match}, 小写: {lora_lower})")
                    return {
                        'error': f'未找到LoRA：{lora_name}'
                    }
            
            # 处理种子
            # 确保seed是字符串类型
            if not isinstance(seed, str):
                seed = str(seed)
            if seed == "随机" or seed.lower() == "random" or seed == "-1":
                seed_value = random.randint(1, 18446744073709551615)
            else:
                try:
                    seed_value = int(seed)
                    # 确保种子值在有效范围内
                    if seed_value < 0:
                        seed_value = random.randint(1, 18446744073709551615)
                except ValueError:
                    return {
                        'error': f'种子值无效：{seed}'
                    }
            
            # 读取图片
            try:
                with open(image_path, 'rb') as f:
                    image_data = f.read()
                    image_base64 = base64.b64encode(image_data).decode('utf-8')
            except Exception as e:
                return {
                    'error': f'读取图片失败: {str(e)}'
                }
            
            # 创建任务
            task_id = str(uuid.uuid4())
            task_data = {
                'type': 'img2img',
                'prompt': prompt,
                'image': image_base64,
                'denoise': denoise,
                'batch_size': batch_size,
                'seed': seed_value,
                'model': selected_model,
                'lora_list': lora_list,
                'user_id': user_id,
                'task_id': task_id,
                'is_web_api': True
            }
            
            # 提交任务到队列
            try:
                await self.task_queue.put(task_data)
                logger.info(f"Web API图生图任务已提交: {task_id}")
            except asyncio.QueueFull:
                return {
                    'error': '任务队列已满，请稍后再试'
                }
            
            # 等待任务完成
            result = await self._wait_for_task_completion(task_id, timeout=300)  # 5分钟超时
            
            return result
            
        except Exception as e:
            logger.error(f"处理图生图请求失败: {e}")
            return {
                'error': f'处理请求失败: {str(e)}'
            }

    async def _process_workflow_request(self, workflow_name: str, params: Dict[str, Any], 
                                       image_path: Optional[str], user_id: str) -> Dict[str, Any]:
        """处理Workflow请求"""
        try:
            # 检查workflow是否存在
            if workflow_name not in self.workflows:
                return {
                    'error': f'未找到workflow: {workflow_name}'
                }
            
            workflow_info = self.workflows[workflow_name]
            config = workflow_info["config"]
            workflow_data = workflow_info["workflow"]
            
            # 检查开放时间
            if not self._is_in_open_time():
                return {
                    'error': f'当前未开放图片生成服务，开放时间：{self.open_time_ranges}'
                }
            
            # 检查服务器状态
            if not self._get_any_healthy_server():
                return {
                    'error': '所有ComfyUI服务器均不可用，请稍后再试'
                }
            
            # 检查用户并发限制
            if not await self._check_user_task_limit(user_id):
                return {
                    'error': f'您当前有过多任务在执行中（最大{self.max_concurrent_tasks_per_user}个），请稍后再试'
                }
            
            # 解析参数
            args = []
            for key, value in params.items():
                args.append(f"{key}:{value}")
            
            parsed_params = self._parse_workflow_params(args, config)
            
            # 验证必需的参数
            missing_params = self._validate_required_params(config, parsed_params)
            if missing_params:
                return {
                    'error': f'缺少必需参数: {", ".join(missing_params)}'
                }
            
            # 处理图片（如果有）
            image_base64 = None
            if image_path:
                try:
                    with open(image_path, 'rb') as f:
                        image_data = f.read()
                        image_base64 = base64.b64encode(image_data).decode('utf-8')
                except Exception as e:
                    return {
                        'error': f'读取图片失败: {str(e)}'
                    }
            
            # 创建任务
            task_id = str(uuid.uuid4())
            task_data = {
                'type': 'workflow',
                'workflow_name': workflow_name,
                'params': parsed_params,
                'image': image_base64,
                'user_id': user_id,
                'task_id': task_id,
                'is_web_api': True
            }
            
            # 提交任务到队列
            try:
                await self.task_queue.put(task_data)
                logger.info(f"Web API workflow任务已提交: {task_id}")
            except asyncio.QueueFull:
                return {
                    'error': '任务队列已满，请稍后再试'
                }
            
            # 等待任务完成
            result = await self._wait_for_task_completion(task_id, timeout=600)  # 10分钟超时
            
            return result
            
        except Exception as e:
            logger.error(f"处理workflow请求失败: {e}")
            return {
                'error': f'处理请求失败: {str(e)}'
            }

    async def _wait_for_task_completion(self, task_id: str, timeout: int = 300) -> Dict[str, Any]:
        """等待任务完成"""
        try:
            # 创建任务结果存储
            if not hasattr(self, '_task_results'):
                self._task_results = {}
            
            start_time = time.time()
            while time.time() - start_time < timeout:
                # 检查任务是否完成
                if task_id in self._task_results:
                    result = self._task_results.pop(task_id)
                    return result
                
                await asyncio.sleep(1)
            
            return {
                'error': '任务执行超时'
            }
            
        except Exception as e:
            logger.error(f"等待任务完成失败: {e}")
            return {
                'error': f'等待任务完成失败: {str(e)}'
            }

    @property
    def config_manager(self):
        """配置管理器属性"""
        if not hasattr(self, '_config_manager'):
            self._config_manager = ConfigManager(
                self.config_dir,
                self.workflow_dir,
                self.main_config_file
            )
        return self._config_manager

    async def cleanup(self) -> None:
        """普通资源清理（不包括需要持续存在的GUI服务器）"""
        try:
            # 停止服务器监控
            if self.server_monitor_task and not self.server_monitor_task.done():
                self.server_monitor_task.cancel()
                try:
                    await self.server_monitor_task
                except asyncio.CancelledError:
                    pass
            
            # 停止所有服务器worker
            for server in self.comfyui_servers:
                if server.worker and not server.worker.done():
                    server.worker.cancel()
                    try:
                        await server.worker
                    except asyncio.CancelledError:
                        pass
                    server.worker = None
            
            # 停止帮助服务器
            await self._stop_help_server()
            
            # 停止Web API服务器
            self._stop_web_api_server()
            
            # 清理临时文件
            await self.cleanup_temp_files()
            
            logger.info("普通资源清理完成")
        except Exception as e:
            logger.error(f"资源清理时发生错误: {e}")


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_dir: Path, workflow_dir: Path, main_config_file: Path):
        self.config_dir = config_dir
        self.workflow_dir = workflow_dir
        self.main_config_file = main_config_file
        
    def load_main_config(self) -> Dict[str, Any]:
        """加载主配置文件"""
        try:
            if self.main_config_file.exists():
                with open(self.main_config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                # 返回默认配置
                return self.get_default_main_config()
        except Exception as e:
            logger.error(f"加载主配置失败: {e}")
            return self.get_default_main_config()
    
    def save_main_config(self, config: Dict[str, Any]) -> bool:
        """保存主配置文件"""
        try:
            with open(self.main_config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存主配置失败: {e}")
            return False
    
    def get_default_main_config(self) -> Dict[str, Any]:
        """获取默认主配置"""
        return {
            "comfyui_url": ["http://127.0.0.1:8188,本地服务器"],
            "ckpt_name": "sd_xl_base_1.0.safetensors",
            "sampler_name": "euler",
            "scheduler": "simple",
            "cfg": 7.0,
            "negative_prompt": "bad quality,worst quality,worst detail, watermark, text",
            "default_width": 1024,
            "default_height": 1024,
            "num_inference_steps": 30,
            "seed": "随机",
            "enable_translation": False,
            "default_denoise": 0.7,
            "open_time_ranges": "7:00-8:00,11:00-14:00,17:00-24:00",
            "enable_image_encrypt": True,
            "txt2img_batch_size": 1,
            "img2img_batch_size": 1,
            "max_txt2img_batch": 6,
            "max_img2img_batch": 6,
            "max_task_queue": 10,
            "min_width": 64,
            "max_width": 2000,
            "min_height": 64,
            "max_height": 2000,
            "queue_check_delay": 30,
            "queue_check_interval": 5,
            "empty_queue_max_retry": 2,
            "lora_config": [],
            "model_config": [],
            "enable_help_image": True,
            "help_server_port": 8080,
            "enable_auto_save": False,
            "auto_save_directory": "output",
            "enable_output_zip": True,
            "daily_download_limit": 1,
            "only_own_images": False,
            "db_directory": "output",
            "max_concurrent_tasks_per_user": 3,
            "enable_auto_recall": False,
            "auto_recall_delay": 20,
            "enable_gui": False,
            "enable_web_api": False,
            "web_api_port": 7778,
            "web_api_allow_register": True,
            "gui_port": 7777,
            "gui_username": "123",
            "gui_password": "123"
        }
    
    def get_workflows(self) -> List[Dict[str, Any]]:
        """获取所有工作流列表"""
        workflows = []
        
        if not self.workflow_dir.exists():
            return workflows
            
        for workflow_name in [f.name for f in self.workflow_dir.iterdir()]:
            workflow_path = self.workflow_dir / workflow_name
            if not workflow_path.is_dir():
                continue
                
            config_file = workflow_path / "config.json"
            workflow_file = workflow_path / "workflow.json"
            
            if not config_file.exists() or not workflow_file.exists():
                continue
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                with open(workflow_file, 'r', encoding='utf-8') as f:
                    workflow_data = json.load(f)
                
                workflows.append({
                    "name": workflow_name,
                    "config": config,
                    "workflow": workflow_data,
                    "workflow_json_pretty": json.dumps(workflow_data, ensure_ascii=False, indent=2),
                    "path": str(workflow_path)
                })
            except Exception as e:
                logger.error(f"加载工作流 {workflow_name} 失败: {e}")
        
        
        return workflows
    
    def save_workflow(self, workflow_name: str, config: Dict[str, Any], 
                     workflow_data: Dict[str, Any]) -> bool:
        """保存工作流"""
        try:
            workflow_path = self.workflow_dir / workflow_name
            workflow_path.mkdir(exist_ok=True)
            
            config_file = workflow_path / "config.json"
            workflow_file = workflow_path / "workflow.json"
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            with open(workflow_file, 'w', encoding='utf-8') as f:
                json.dump(workflow_data, f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            logger.error(f"保存工作流 {workflow_name} 失败: {e}")
            return False
    
    def delete_workflow(self, workflow_name: str) -> bool:
        """删除工作流"""
        try:
            workflow_path = self.workflow_dir / workflow_name
            if workflow_path.exists() and workflow_path.is_dir():
                shutil.rmtree(workflow_path)
                return True
            return False
        except Exception as e:
            logger.error(f"删除工作流 {workflow_name} 失败: {e}")
            return False
