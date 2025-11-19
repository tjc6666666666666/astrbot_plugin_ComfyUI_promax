from astrbot.api.message_components import Plain, Image, Reply
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import CustomFilter
from astrbot.api import logger
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

import io
import base64

@register(
    "mod-comfyui",
    "",
    "使用多服务器ComfyUI文生图/图生图（支持模型选择、LoRA和服务器轮询）。\n开放时间：{open_time_ranges}\n文生图：发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填（例：/aimg girl 宽512,高768 批量2 model:写实风格 lora:儿童:0.8 lora:可爱!1.0）\n图生图：发送「img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」+ 图片或引用包含图片的消息（例：img2img 猫咪 噪声:0.7 批量2 model:动漫风格 lora:动物:1.2!0.9 + 图片/引用图片消息）\n输出压缩包：发送「comfyuioutput」获取今天生成的图片压缩包（需开启自动保存）\n模型使用说明：\n  - 格式：model:描述（描述对应配置中的模型描述）\n  - 例：model:写实风格\nLoRA使用说明：\n  - 基础格式：lora:描述（使用默认强度1.0/1.0，描述对应配置中的LoRA描述）\n  - 仅模型强度：lora:描述:0.8（strength_model=0.8）\n  - 仅CLIP强度：lora:描述!1.0（strength_clip=1.0）\n  - 双强度：lora:描述:0.8!1.3（model=0.8, clip=1.3）\n  - 多LoRA：空格分隔多个lora参数（例：lora:儿童 lora:学生:0.9）\n多服务器轮询处理，所有生成图片将合并为一条消息发送，未指定参数则用默认配置（文生图默认批量数：{txt2img_batch_size}，图生图默认批量数：{img2img_batch_size}，默认噪声系数：{default_denoise}，默认模型：{ckpt_name}）。\n限制说明：文生图最大批量{max_txt2img_batch}，图生图最大批量{max_img2img_batch}，分辨率范围{min_width}~{max_width}x{min_height}~{max_height}，任务队列最大{max_task_queue}个，每用户最大并发{max_concurrent_tasks_per_user}个\n可用模型列表：\n{model_list_desc}\n可用LoRA列表：\n{lora_list_desc}",
    "3.2"  # 版本更新：支持图片压缩包输出功能
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

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        # 1. 加载配置
        self.comfyui_servers = self._parse_comfyui_servers(config.get("comfyui_url", []))
        self.ckpt_name = config.get("ckpt_name")
        self.sampler_name = config.get("sampler_name")
        self.scheduler = config.get("scheduler")
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
        self.enable_help_image = config.get("enable_help_image", False)
        self.help_server_port = config.get("help_server_port", 8080)
        self.help_server_thread: Optional[threading.Thread] = None
        self.help_server_runner: Optional[web.AppRunner] = None
        self.help_server_site: Optional[web.TCPSite] = None
        self.actual_help_port = self.help_server_port

        # 自动保存图片配置
        self.enable_auto_save = config.get("enable_auto_save", False)
        self.auto_save_dir = config.get("auto_save_directory", config.get("auto_save_dir", "output"))
        
        # 输出压缩包配置
        self.enable_output_zip = config.get("enable_output_zip", True)
        self.daily_download_limit = config.get("daily_download_limit", 1)  # 每天下载次数限制
        self.only_own_images = config.get("only_own_images", False)  # 是否只能下载自己生成的图片
        
        # 数据库配置
        self.db_dir = config.get("db_directory", config.get("auto_save_directory", "output"))
        self.db_path = os.path.join(self.db_dir, "user.db")  # 数据库路径

        # 用户队列限制配置
        self.max_concurrent_tasks_per_user = config.get("max_concurrent_tasks_per_user", 3)

        # 2. 状态管理
        self.task_queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_task_queue)
        self.server_monitor_task: Optional[asyncio.Task] = None  # 服务器监控任务
        self.server_monitor_running: bool = False
        
        # 3. 用户队列计数管理
        self.user_task_counts: Dict[str, int] = {}  # 记录每个用户的当前任务数
        self.user_task_lock = asyncio.Lock()  # 保护用户任务计数的锁

        # 3. 验证配置
        self._validate_config()
        
        # 4. 初始化数据库
        self._init_database()
        
        # 启动ComfyUI服务器监控（将在监控中启动worker）
        self.server_monitor_task = asyncio.create_task(self._start_server_monitor())

    def _init_database(self) -> None:
        """初始化用户下载记录数据库"""
        try:
            # 确保数据库目录存在
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # 创建用户下载记录表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_downloads (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        download_date TEXT NOT NULL,
                        download_count INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, download_date)
                    )
                ''')
                
                # 创建图片生成记录表（用于记录图片的生成者）
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS image_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        generate_date TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info(f"数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    def _check_download_limit(self, user_id: str) -> Tuple[bool, int]:
        """检查用户今日下载次数限制"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # 查询或插入用户今日下载记录
                cursor.execute('''
                    INSERT OR IGNORE INTO user_downloads (user_id, download_date, download_count)
                    VALUES (?, ?, 0)
                ''', (user_id, today))
                
                # 查询当前下载次数
                cursor.execute('''
                    SELECT download_count FROM user_downloads
                    WHERE user_id = ? AND download_date = ?
                ''', (user_id, today))
                
                result = cursor.fetchone()
                current_count = result[0] if result else 0
                
                # 检查是否超过限制
                can_download = current_count < self.daily_download_limit
                return can_download, current_count
        except Exception as e:
            logger.error(f"检查下载限制失败: {e}")
            return False, 0

    def _increment_download_count(self, user_id: str) -> None:
        """增加用户下载次数"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE user_downloads 
                    SET download_count = download_count + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND download_date = ?
                ''', (user_id, today))
                conn.commit()
        except Exception as e:
            logger.error(f"更新下载次数失败: {e}")

    def _record_image_generation(self, filename: str, user_id: str) -> None:
        """记录图片生成信息"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO image_records (filename, user_id, generate_date)
                    VALUES (?, ?, ?)
                ''', (filename, user_id, today))
                conn.commit()
        except Exception as e:
            logger.error(f"记录图片生成信息失败: {e}")

    def _get_user_images_today(self, user_id: str) -> List[str]:
        """获取用户今日生成的图片列表"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT filename FROM image_records
                    WHERE user_id = ? AND generate_date = ?
                ''', (user_id, today))
                return [row[0] for row in cursor.fetchall()]
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

    def _validate_config(self) -> None:
        if not self.comfyui_servers:
            raise ValueError("未配置有效的ComfyUI服务器，请检查comfyui_url配置")
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
        if not isinstance(self.auto_save_dir, str) or not self.auto_save_dir.strip():
            raise ValueError(f"配置项错误：auto_save_dir（需为非空字符串）")
        
        # 验证数据库配置
        if not isinstance(self.db_dir, str) or not self.db_dir.strip():
            raise ValueError(f"配置项错误：db_directory（需为非空字符串）")
        for cfg_key, cfg_type in required_configs:
            cfg_value = getattr(self, cfg_key)
            if not isinstance(cfg_value, cfg_type):
                raise ValueError(f"配置项错误：{cfg_key}（需为{cfg_type.__name__}类型）")
            if cfg_key not in ["lora_config", "enable_translation"] and not cfg_value:
                raise ValueError(f"配置项错误：{cfg_key}（非空）")
        if not (0 <= self.default_denoise <= 1):
            raise ValueError(f"配置项错误：default_denoise（需为0-1之间的数值）")
        if not (1 <= self.txt2img_batch_size <= self.max_txt2img_batch):
            raise ValueError(f"配置项错误：txt2img_batch_size（需为1-{self.max_txt2img_batch}之间的整数）")
        if not (1 <= self.img2img_batch_size <= self.max_img2img_batch):
            raise ValueError(f"配置项错误：img2img_batch_size（需为1-{self.max_img2img_batch}之间的整数）")
        if not (1 <= self.max_task_queue <= 100):
            raise ValueError(f"配置项错误：max_task_queue（需为1-100之间的整数）")
        if not (64 <= self.min_width < self.max_width <= 4096):
            raise ValueError(f"配置项错误：宽度范围（min_width需≥64，max_width需≤4096，且min_width<max_width）")
        if not (64 <= self.min_height < self.max_height <= 4096):
            raise ValueError(f"配置项错误：高度范围（min_height需≥64，max_height需≤4096，且min_height<max_height）")
        if not (10 <= self.queue_check_delay <= 120):
            raise ValueError(f"配置项错误：queue_check_delay（需为10-120之间的整数，单位：秒）")
        if not (3 <= self.queue_check_interval <= 30):
            raise ValueError(f"配置项错误：queue_check_interval（需为3-30之间的整数，单位：秒）")
        if not (1 <= self.empty_queue_max_retry <= 5):
            raise ValueError(f"配置项错误：empty_queue_max_retry（需为1-5之间的整数）")
        if not (1 <= self.max_lora_count <= 20):
            raise ValueError(f"配置项错误：max_lora_count（需为1-20之间的整数）")
        if not (0.0 <= self.min_lora_strength < self.max_lora_strength <= 5.0):
            raise ValueError(f"配置项错误：LoRA强度范围（min需≥0，max需≤5，且min<max）")
        if not (1 <= self.max_concurrent_tasks_per_user <= 10):
            raise ValueError(f"配置项错误：max_concurrent_tasks_per_user（需为1-10之间的整数）")
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
                server.worker.cancel()
                try:
                    await server.worker
                except asyncio.CancelledError:
                    pass
            
            logger.info(f"为服务器{server.name}启动worker")
            server.worker = asyncio.create_task(
                self._worker_loop(f"worker-{server.server_id}", server)
            )
        
        # 如果服务器不健康但有活跃worker，终止它
        elif not server.healthy and server.worker and not server.worker.done():
            logger.info(f"服务器{server.name}异常，终止其worker")
            server.worker.cancel()
            try:
                await server.worker
            except asyncio.CancelledError:
                pass
            server.worker = None

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



    def _get_next_available_server(self) -> Optional[ServerState]:
        """轮询获取下一个可用服务器（多worker并发安全）"""
        if not self.comfyui_servers:
            return None
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

    def _mark_server_busy(self, server: ServerState, busy: bool) -> None:
        server.busy = busy
        logger.debug(f"服务器{server.name}状态更新：{'忙碌' if busy else '空闲'}")

    def _handle_server_failure(self, server: ServerState) -> None:
        server.failure_count += 1
        logger.warning(f"服务器{server.name}失败次数：{server.failure_count}/{self.max_failure_count}")
        if server.failure_count >= self.max_failure_count:
            server.healthy = False
            server.retry_after = datetime.now() + timedelta(seconds=self.retry_delay)
            logger.warning(f"服务器{server.name}连续失败{self.max_failure_count}次，将在{self.retry_delay}秒后重试")
        else:
            server.retry_after = datetime.now() + timedelta(seconds=10)

    def _reset_server_failure(self, server: ServerState) -> None:
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
                    logger.info(f"{worker_name}检测到服务器{server.name}不健康，退出循环")
                    return
                    
                try:
                    # 等待获取任务（超时10秒，避免无限阻塞）
                    task_data = await asyncio.wait_for(self.task_queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    # 超时后检查服务器健康状态，不健康则退出
                    if not server.healthy:
                        logger.info(f"{worker_name}检测到服务器{server.name}不健康，退出循环")
                        return
                    continue
                    
                try:
                    # 使用绑定的服务器处理任务
                    await self._process_comfyui_task_with_server(server, **task_data)
                except Exception as e:
                    event = task_data["event"]
                    err_msg = f"\n图片生成失败：{str(e)[:100]}"
                    await event.send(event.plain_result(err_msg))
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
            "9": {
                "inputs": {"filename_prefix": "comfyui_gen", "images": ["44", 0]},
                "class_type": "SaveImage",
                "_meta": {"title": "Save Image"}
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
            },
            "44": {
                "inputs": {"mode": "encrypt", "enable": self.enable_image_encrypt, "image": ["8", 0]},
                "class_type": "HilbertImageEncrypt",
                "_meta": {"title": "希尔伯特曲线图像加密"}
            },
            "save_image_websocket_node": {
                "inputs": {"images": ["44", 0]},
                "class_type": "SaveImageWebsocket",
                "_meta": {"title": "保存图像（网络接口）"}
            }
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
        """使用指定服务器处理任务，带重试机制"""
        max_retries = 2  # 单服务器重试次数
        retry_count = 0
        last_error = None
        user_id = task_data.get("user_id")  # 获取用户ID
        
        try:
            while retry_count <= max_retries:
                try:
                    # 检查服务器是否健康
                    if not server.healthy:
                        raise Exception(f"服务器{server.name}已不健康，无法处理任务")
                        
                    # 处理任务
                    await self._process_comfyui_task(server, **task_data)
                    self._reset_server_failure(server)
                    return
                except Exception as e:
                    last_error = e
                    logger.error(f"服务器{server.name}处理任务失败（重试{retry_count}/{max_retries}）：{str(e)}")
                    self._handle_server_failure(server)
                    
                    # 如果服务器已被标记为不健康，不再重试
                    if not server.healthy:
                        break
                        
                    retry_count += 1
                    if retry_count <= max_retries:
                        await asyncio.sleep(2)  # 短暂延迟后重试
                
            # 所有重试失败
            if last_error:
                filtered_error = self._filter_server_urls(str(last_error)[:100])
                raise Exception(f"服务器{server.name}处理任务失败：{filtered_error}")
            else:
                raise Exception(f"服务器{server.name}处理任务失败，原因未知")
                
        except Exception as e:
            # 任务处理失败，减少用户任务计数
            if user_id:
                await self._decrement_user_task_count(user_id)
            raise e
                
        finally:
            # 确保释放服务器
            self._mark_server_busy(server, False)

    def _truncate_prompt(self, prompt: str) -> str:
        max_display_len = 8
        if len(prompt) > max_display_len:
            return prompt[:max_display_len] + "..."
        return prompt

    async def _process_comfyui_task(
        self,
        server: ServerState,
        event: AstrMessageEvent,
        prompt: str,
        current_seed: int,
        current_width: int,
        current_height: int,
        image_filename: Optional[str] = None,
        denoise: float = 1,
        current_batch_size: int = 1,
        lora_list: List[Dict[str, Any]] = [],
        selected_model: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> None:
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
        await event.send(event.plain_result(
            f"\n{task_type}任务已下发至服务器【{server.name}】：\n提示词：{self._truncate_prompt(prompt)}\nSeed：{current_seed}\n{extra_info}\n任务ID：{prompt_id[:8]}..."
        ))
        history_data = await self._poll_task_status(server, prompt_id)
        if not history_data or history_data.get("status", {}).get("completed") is False:
            raise Exception("任务超时或未完成（超时10分钟）")
        image_info_list = self._extract_batch_image_info(history_data)
        if not image_info_list or len(image_info_list) == 0:
            raise Exception("未从ComfyUI历史数据中找到图片")
        image_urls = []
        for idx, image_info in enumerate(image_info_list, 1):
            image_url = await self._get_image_url(server, image_info["filename"])
            image_urls.append((idx, image_url))
            
            # 静悄悄保存图片
            await self._save_image_locally(server, image_info["filename"], prompt, user_id or "")
        if image_filename:
            result_text = f"提示词：{self._truncate_prompt(prompt)}\nSeed：{current_seed}\n噪声系数：{denoise}\n批量数：{current_batch_size}\n{task_type}生成完成！\n所有图片已合并为一条消息发送～"
        else:
            result_text = f"提示词：{self._truncate_prompt(prompt)}\nSeed：{current_seed}\n分辨率：{current_width}x{current_height}\n批量数：{current_batch_size}\n{task_type}生成完成！\n所有图片已合并为一条消息发送～"
        if lora_list:
            lora_result_info = "\n使用LoRA：" + " | ".join([
                f"{lora['name']}（model:{lora['strength_model']}, clip:{lora['strength_clip']}）"
                for lora in lora_list
            ])
            result_text += lora_result_info
        
        # 构建合并的消息链
        merged_chain = []
        merged_chain.append(Plain(result_text))
        merged_chain.append(Plain(f"\n\n共{current_batch_size}张图片："))
        
        # 添加图片
        for idx, img_url in image_urls:
            merged_chain.append(Plain(f"\n\n第{idx}/{current_batch_size}张："))
            merged_chain.append(Image.fromURL(img_url))
        
        # 一次性发送合并的消息
        await event.send(event.chain_result(merged_chain))
        
        # 任务完成，减少用户任务计数
        if user_id:
            await self._decrement_user_task_count(user_id)

    async def _send_comfyui_prompt(self, server: ServerState, comfy_prompt: Dict[str, Any]) -> str:
        url = f"{server.url}/prompt"
        headers = {"Content-Type": "application/json"}
        payload = {"client_id": str(uuid.uuid4()), "prompt": comfy_prompt}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    filtered_resp = self._filter_server_urls(resp_text[:50])
                    raise Exception(f"任务下发失败（HTTP {resp.status}）：{filtered_resp}")
                resp_data = await resp.json()
                return resp_data.get("prompt_id", "")

    async def _check_queue_empty(self, server: ServerState) -> bool:
        url = f"{server.url}/api/queue"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        logger.warning(f"队列状态查询失败（HTTP {resp.status}），跳过本次检查")
                        return False
                    resp_data = await resp.json()
                    if not isinstance(resp_data, dict) or "queue_running" not in resp_data or "queue_pending" not in resp_data:
                        logger.warning(f"队列返回格式异常：{resp_data}，跳过本次检查")
                        return False
                    running_empty = len(resp_data["queue_running"]) == 0
                    pending_empty = len(resp_data["queue_pending"]) == 0
                    return running_empty and pending_empty
        except Exception as e:
            logger.warning(f"队列检查异常：{str(e)}，跳过本次检查")
            return False

    async def _poll_task_status(self, server: ServerState, prompt_id: str, timeout: int = 600, interval: int = 3) -> Dict[str, Any]:
        url = f"{server.url}/history/{prompt_id}"
        start_time = asyncio.get_event_loop().time()
        empty_queue_retry_count = 0
        queue_check_start_time = start_time + self.queue_check_delay
        async with aiohttp.ClientSession() as session:
            while True:
                current_time = asyncio.get_event_loop().time()
                elapsed_time = current_time - start_time
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
                except Exception as e:
                    logger.warning(f"历史状态查询异常：{str(e)}，继续轮询")
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
        if not save_node_data or not save_node_data.get("images"):
            raise Exception("未找到SaveImage节点的输出图片")
        return save_node_data["images"]

    async def _get_image_url(self, server: ServerState, filename: str) -> str:
        url_params = {"filename": filename, "type": "output", "subfolder": "", "preview": "true"}
        query_str = "&".join([f"{k}={quote(v)}" for k, v in url_params.items()])
        return f"{server.url}/view?{query_str}"

    async def _save_image_locally(self, server: ServerState, filename: str, prompt: str = "", user_id: str = "") -> None:
        """静悄悄保存图片到本地"""
        if not self.enable_auto_save:
            return
            
        try:
            # 获取图片URL
            image_url = await self._get_image_url(server, filename)
            
            # 下载图片
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url, timeout=30) as resp:
                    if resp.status != 200:
                        logger.warning(f"下载图片失败，HTTP状态码: {resp.status}")
                        return
                    image_data = await resp.read()
            
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
            if not original_name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                original_name += '.png'
            saved_filename = timestamp + original_name
            save_path = save_dir / saved_filename
            
            # 保存图片
            with open(save_path, 'wb') as f:
                f.write(image_data)
            
            logger.info(f"图片已自动保存: {save_path}")
            
            # 记录图片生成信息
            if user_id:
                self._record_image_generation(saved_filename, user_id)
            
        except Exception as e:
            logger.error(f"自动保存图片失败: {str(e)}")

    async def _upload_image_to_comfyui(self, server: ServerState, img_path: str) -> str:
        url = f"{server.url}/upload/image"
        if not os.path.exists(img_path):
            raise Exception(f"图片文件不存在：{img_path}")
        with open(img_path, "rb") as f:
            img_data = f.read()
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
        if not self._is_in_open_time():
            open_desc = self._get_open_time_desc()
            await event.send(event.plain_result(
                f"\n当前未开放图片生成服务～\n开放时间：{open_desc}\n请在开放时间段内提交任务！"
            ))
            return
        if not self._get_any_healthy_server():
            await event.send(event.plain_result(
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
            await event.send(event.plain_result(f"\n参数解析失败：{filtered_err}"))
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
                    await event.send(event.plain_result(f"\n宽度{input_w}非法（需{self.min_width}~{self.max_width}像素），请重新输入合法参数！"))
                    return
                if not (self.min_height <= input_h <= self.max_height):
                    await event.send(event.plain_result(f"\n高度{input_h}非法（需{self.min_height}~{self.max_height}像素），请重新输入合法参数！"))
                    return
                current_width = input_w
                current_height = input_h
                pure_prompt = re.sub(res_pattern, "", pure_prompt).strip()
            except Exception as e:
                await event.send(event.plain_result(f"\n宽高解析失败：{str(e)}，请重新输入合法参数！"))
                return
        if batch_match:
            try:
                input_batch = int(batch_match.group(1))
                if not (1 <= input_batch <= self.max_txt2img_batch):
                    await event.send(event.plain_result(f"\n批量数{input_batch}非法（文生图需1~{self.max_txt2img_batch}），请重新输入合法参数！"))
                    return
                current_batch_size = input_batch
                pure_prompt = re.sub(batch_pattern, "", pure_prompt).strip()
            except Exception as e:
                await event.send(event.plain_result(f"\n批量数解析失败：{str(e)}，请重新输入合法参数！"))
                return
        if not pure_prompt:
            await event.send(event.plain_result(f"\n提示词不能为空！使用方法：\n发送「aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]」参数可选，非必填\n文生图默认批量数：{self.txt2img_batch_size}，最大支持{self.max_txt2img_batch}"))
            return
        try:
            current_seed = random.randint(1, 18446744073709551615) if (self.seed == "随机" or not self.seed) else int(self.seed)
        except (ValueError, TypeError):
            current_seed = random.randint(1, 2147483647)
        # 检查用户任务数限制
        user_id = str(event.get_sender_id())
        if not await self._increment_user_task_count(user_id):
            await event.send(event.plain_result(
                f"\n您当前同时进行的任务数已达上限（{self.max_concurrent_tasks_per_user}个），请等待当前任务完成后再提交新任务！"
            ))
            return
            
        if self.task_queue.full():
            # 如果队列已满，需要减少刚刚增加的用户任务计数
            await self._decrement_user_task_count(user_id)
            await event.send(event.plain_result(f"\n当前任务队列已满（{self.max_task_queue}个任务上限），请稍后再试！"))
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
        await event.send(event.plain_result(
            f"\n文生图任务已加入队列（当前排队：{self.task_queue.qsize()}个）\n"
            f"提示词：{self._truncate_prompt(pure_prompt)}\n"
            f"Seed：{current_seed}\n"
            f"分辨率：{current_width}x{current_height}（默认：{self.default_width}x{self.default_height}，范围：{self.min_width}~{self.max_width}x{self.min_height}~{self.max_height}）\n"
            f"批量数：{current_batch_size}（默认：{self.txt2img_batch_size}，最大：{self.max_txt2img_batch}）"
            + model_feedback
            + server_feedback
            + lora_feedback
        ))

    def _is_port_available(self, port: int) -> bool:
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('localhost', port))
                return result != 0
        except:
            return False

    def _find_available_port(self, start_port: int) -> int:
        """从指定端口开始查找可用端口"""
        port = start_port
        max_attempts = 100  # 最多尝试100个端口
        
        for _ in range(max_attempts):
            if self._is_port_available(port):
                return port
            port += 1
        
        # 如果都不可用，随机选择一个高端口
        import random
        return random.randint(49152, 65535)

    async def _start_help_server(self) -> str:
        """启动临时HTTP服务器用于HTML转图片"""
        if self.help_server_runner is not None:
            return f"http://localhost:{self.actual_help_port}"
        
        # 查找可用端口
        self.actual_help_port = self._find_available_port(self.help_server_port)
        
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
            
            # 保存图片
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = os.path.join(os.path.dirname(__file__), f"help_{timestamp}.png")
            image.save(image_path, 'PNG', quality=95)
            
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
                image.save(image_path, 'PNG')
                
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
            
            # 转换HTML为图片
            image_path = await self._html_to_image(server_url)
            
            # 发送图片
            await event.send(event.image_result(image_path))
            
            # 延迟清理临时图片（确保发送完成）
            await asyncio.sleep(2)
            try:
                if image_path and os.path.exists(image_path):
                    os.remove(image_path)
                    logger.info(f"临时图片已清理: {image_path}")
            except Exception as e:
                logger.warning(f"清理临时图片失败: {e}")
                
        except Exception as e:
            logger.error(f"发送帮助图片失败: {e}")
            # 如果发送图片失败，发送文本形式的帮助
            await self._send_help_as_text(event)
        finally:
            # 立即销毁服务器
            if server_url:
                await self._stop_help_server()

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
        """
        
        await event.send(event.plain_result(help_text.strip()))

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

    def _get_today_images(self, user_id: Optional[str] = None) -> List[str]:
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
                user_images = set(self._get_user_images_today(user_id))
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
            
            # 创建压缩包
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for image_file in image_files:
                    if os.path.exists(image_file):
                        # 使用文件名作为压缩包内的路径
                        arcname = os.path.basename(image_file)
                        zipf.write(image_file, arcname)
            
            logger.info(f"压缩包创建成功: {zip_path}")
            return str(zip_path)
        except Exception as e:
            logger.error(f"创建压缩包失败: {e}")
            return None

    async def _upload_zip_file(self, event: AstrMessageEvent, zip_path: str) -> bool:
        """上传压缩包到群文件或个人文件"""
        try:
            # 获取群ID和发送者QQ号
            group_id = event.get_group_id()
            sender_qq = event.get_sender_id()
            
            zip_filename = os.path.basename(zip_path)
            
            if group_id:  # 群聊场景
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                await client.upload_group_file(
                    group_id=group_id,
                    file=zip_path,
                    name=zip_filename
                )
                logger.info(f"压缩包已上传到群文件: 群ID={group_id}, 文件={zip_filename}")
            else:  # 私聊场景
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
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
            # 检查是否开启了自动保存功能
            if not self.enable_auto_save:
                await event.send(event.plain_result(
                    "❌ 未开启图片自动保存功能，无法生成压缩包！\n"
                    "请联系管理员在配置中开启 enable_auto_save 功能。"
                ))
                return
            
            # 检查是否开启了输出压缩包功能
            if not self.enable_output_zip:
                await event.send(event.plain_result(
                    "❌ 未开启输出压缩包功能！\n"
                    "请联系管理员在配置中开启 enable_output_zip 功能。"
                ))
                return
            
            # 获取用户ID
            user_id = str(event.get_sender_id())
            
            # 检查下载次数限制
            can_download, current_count = self._check_download_limit(user_id)
            if not can_download:
                await event.send(event.plain_result(
                    f"❌ 今日下载次数已达上限！\n"
                    f"当前已下载: {current_count} 次\n"
                    f"每日限制: {self.daily_download_limit} 次\n"
                    f"请明天再试～"
                ))
                return
            
            # 获取今天的图片
            await event.send(event.plain_result("🔍 正在搜索今天的图片..."))
            image_files = self._get_today_images(user_id)
            
            if not image_files:
                await event.send(event.plain_result(
                    "📭 今天还没有生成图片哦～\n"
                    "先使用 aimg 或 img2img 指令生成一些图片吧！"
                ))
                return
            
            await event.send(event.plain_result(f"📁 找到 {len(image_files)} 张图片，正在生成压缩包..."))
            
            # 创建压缩包
            zip_path = await self._create_zip_archive(image_files, user_id)
            if not zip_path:
                await event.send(event.plain_result(
                    "❌ 压缩包创建失败，请稍后重试！"
                ))
                return
            
            await event.send(event.plain_result("📦 压缩包创建完成，正在上传..."))
            
            # 上传压缩包
            upload_success = await self._upload_zip_file(event, zip_path)
            
            if upload_success:
                # 更新下载次数
                self._increment_download_count(user_id)
                
                # 获取更新后的下载次数
                _, new_count = self._check_download_limit(user_id)
                
                await event.send(event.plain_result(
                    f"✅ 压缩包上传成功！\n"
                    f"📁 文件名: {os.path.basename(zip_path)}\n"
                    f"📊 包含图片: {len(image_files)} 张\n"
                    f"📈 今日已下载: {new_count}/{self.daily_download_limit} 次\n"
                    f"💡 提示: 请从群文件或私聊文件中下载"
                ))
                
                # 延迟删除临时压缩包
                await asyncio.sleep(5)
                try:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                        logger.info(f"临时压缩包已清理: {zip_path}")
                except Exception as e:
                    logger.warning(f"清理临时压缩包失败: {e}")
            else:
                await event.send(event.plain_result(
                    "❌ 压缩包上传失败，请稍后重试！"
                ))
                
        except Exception as e:
            logger.error(f"处理输出压缩包指令失败: {e}")
            await event.send(event.plain_result(
                "❌ 处理请求时发生错误，请稍后重试！"
            ))

    def __del__(self):
        """析构函数，确保资源清理"""
        try:
            # 清理临时图片文件
            import glob
            temp_files = glob.glob(os.path.join(os.path.dirname(__file__), "help_*.png"))
            for temp_file in temp_files:
                try:
                    os.remove(temp_file)
                except:
                    pass
            
            # 清理临时压缩包文件
            zip_files = glob.glob(os.path.join(os.path.dirname(__file__), "comfyui_images_*.zip"))
            for zip_file in zip_files:
                try:
                    os.remove(zip_file)
                except:
                    pass
        except:
            pass

    # 图生图指令
    @filter.custom_filter(Img2ImgFilter)
    async def handle_img2img(self, event: AstrMessageEvent) -> None:
        if not self._is_in_open_time():
            open_desc = self._get_open_time_desc()
            await event.send(event.plain_result(
                f"\n当前未开放图片生成服务～\n开放时间：{open_desc}\n请在开放时间段内提交任务！"
            ))
            return
        if not self._get_any_healthy_server():
            await event.send(event.plain_result(
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
            await event.send(event.plain_result(f"\n参数解析失败：{filtered_err}"))
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
                    await event.send(event.plain_result(f"\n批量数{input_batch}非法（图生图需1~{self.max_img2img_batch}），请重新输入合法参数！"))
                    return
                current_batch_size = input_batch
            except Exception as e:
                await event.send(event.plain_result(f"\n批量数解析失败：{str(e)}，请重新输入合法参数！"))
                return
        if denoise_param:
            try:
                denoise_val = float(denoise_param.group(1))
                if not (0 <= denoise_val <= 1):
                    await event.send(event.plain_result(f"\n噪声系数{denoise_val}非法（需0-1之间的数值），请重新输入合法参数！"))
                    return
                denoise = denoise_val
            except ValueError as e:
                await event.send(event.plain_result(f"\n噪声系数解析失败：{str(e)}，请重新输入合法参数！"))
                return
        prompt = " ".join(prompt_params).strip()
        if not prompt:
            await event.send(event.plain_result(
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
            await event.send(event.plain_result("\n未检测到图片，请重新发送图文消息或引用包含图片的消息"))
            return
        upload_server = self._get_next_available_server() or self._get_any_healthy_server()
        if not upload_server:
            await event.send(event.plain_result("\n没有可用服务器上传图片，请稍后再试"))
            return
        try:
            img_path = await selected_image.convert_to_file_path()
            image_filename = await self._upload_image_to_comfyui(upload_server, img_path)
        except Exception as e:
            await event.send(event.plain_result(f"\n图片处理失败：{str(e)[:100]}"))
            return
        try:
            current_seed = random.randint(1, 18446744073709551615) if (self.seed == "随机" or not self.seed) else int(self.seed)
        except (ValueError, TypeError):
            current_seed = random.randint(1, 2147483647)
        # 检查用户任务数限制
        user_id = str(event.get_sender_id())
        if not await self._increment_user_task_count(user_id):
            await event.send(event.plain_result(
                f"\n您当前同时进行的任务数已达上限（{self.max_concurrent_tasks_per_user}个），请等待当前任务完成后再提交新任务！"
            ))
            return
            
        if self.task_queue.full():
            # 如果队列已满，需要减少刚刚增加的用户任务计数
            await self._decrement_user_task_count(user_id)
            await event.send(event.plain_result(f"\n当前任务队列已满（{self.max_task_queue}个任务上限），请稍后再试！"))
            return
            
        await self.task_queue.put({
            "event": event,
            "prompt": prompt,
            "current_seed": current_seed,
            "current_width": self.default_width,
            "current_height": self.default_height,
            "image_filename": image_filename,
            "denoise": denoise,
            "current_batch_size": current_batch_size,
            "lora_list": lora_list,
            "user_id": str(event.get_sender_id())
        })
        lora_feedback = ""
        if lora_list:
            lora_feedback = "\n使用LoRA：" + " | ".join([
                f"{lora['name']}（model:{lora['strength_model']}, clip:{lora['strength_clip']}）"
                for lora in lora_list
            ])
        available_servers = [s.name for s in self.comfyui_servers if s.healthy]
        server_feedback = f"\n可用服务器：{', '.join(available_servers)}" if available_servers else "\n当前无可用服务器，任务将在服务器恢复后处理"
        await event.send(event.plain_result(
            f"\n图生图任务已加入队列（当前排队：{self.task_queue.qsize()}个）\n"
            f"提示词：{self._truncate_prompt(prompt)}\n"
            f"Seed：{current_seed}\n"
            f"噪声系数：{denoise}（默认：{self.default_denoise}）\n"
            f"批量数：{current_batch_size}（默认：{self.img2img_batch_size}，最大：{self.max_img2img_batch}）\n"
            f"图片来源：{image_source}\n"
            f"上传图片：{image_filename[:20]}...（服务器：{upload_server.name}）"
            + server_feedback
            + lora_feedback
        ))

    def _is_port_available(self, port: int) -> bool:
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('localhost', port))
                return result != 0
        except:
            return False

    def _find_available_port(self, start_port: int) -> int:
        """从指定端口开始查找可用端口"""
        port = start_port
        max_attempts = 100  # 最多尝试100个端口
        
        for _ in range(max_attempts):
            if self._is_port_available(port):
                return port
            port += 1
        
        # 如果都不可用，随机选择一个高端口
        import random
        return random.randint(49152, 65535)

    async def _start_help_server(self) -> str:
        """启动临时HTTP服务器用于HTML转图片"""
        if self.help_server_runner is not None:
            return f"http://localhost:{self.actual_help_port}"
        
        # 查找可用端口
        self.actual_help_port = self._find_available_port(self.help_server_port)
        
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
            
            # 保存图片
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = os.path.join(os.path.dirname(__file__), f"help_{timestamp}.png")
            image.save(image_path, 'PNG', quality=95)
            
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
                image.save(image_path, 'PNG')
                
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
            
            # 转换HTML为图片
            image_path = await self._html_to_image(server_url)
            
            # 发送图片
            await event.send(event.image_result(image_path))
            
            # 延迟清理临时图片（确保发送完成）
            await asyncio.sleep(2)
            try:
                if image_path and os.path.exists(image_path):
                    os.remove(image_path)
                    logger.info(f"临时图片已清理: {image_path}")
            except Exception as e:
                logger.warning(f"清理临时图片失败: {e}")
                
        except Exception as e:
            logger.error(f"发送帮助图片失败: {e}")
            # 如果发送图片失败，发送文本形式的帮助
            await self._send_help_as_text(event)
        finally:
            # 立即销毁服务器
            if server_url:
                await self._stop_help_server()

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
        """
        
        await event.send(event.plain_result(help_text.strip()))

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

    def _get_today_images(self, user_id: Optional[str] = None) -> List[str]:
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
                user_images = set(self._get_user_images_today(user_id))
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
            
            # 创建压缩包
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for image_file in image_files:
                    if os.path.exists(image_file):
                        # 使用文件名作为压缩包内的路径
                        arcname = os.path.basename(image_file)
                        zipf.write(image_file, arcname)
            
            logger.info(f"压缩包创建成功: {zip_path}")
            return str(zip_path)
        except Exception as e:
            logger.error(f"创建压缩包失败: {e}")
            return None

    async def _upload_zip_file(self, event: AstrMessageEvent, zip_path: str) -> bool:
        """上传压缩包到群文件或个人文件"""
        try:
            # 获取群ID和发送者QQ号
            group_id = event.get_group_id()
            sender_qq = event.get_sender_id()
            
            zip_filename = os.path.basename(zip_path)
            
            if group_id:  # 群聊场景
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                await client.upload_group_file(
                    group_id=group_id,
                    file=zip_path,
                    name=zip_filename
                )
                logger.info(f"压缩包已上传到群文件: 群ID={group_id}, 文件={zip_filename}")
            else:  # 私聊场景
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
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
            # 检查是否开启了自动保存功能
            if not self.enable_auto_save:
                await event.send(event.plain_result(
                    "❌ 未开启图片自动保存功能，无法生成压缩包！\n"
                    "请联系管理员在配置中开启 enable_auto_save 功能。"
                ))
                return
            
            # 检查是否开启了输出压缩包功能
            if not self.enable_output_zip:
                await event.send(event.plain_result(
                    "❌ 未开启输出压缩包功能！\n"
                    "请联系管理员在配置中开启 enable_output_zip 功能。"
                ))
                return
            
            # 获取用户ID
            user_id = str(event.get_sender_id())
            
            # 检查下载次数限制
            can_download, current_count = self._check_download_limit(user_id)
            if not can_download:
                await event.send(event.plain_result(
                    f"❌ 今日下载次数已达上限！\n"
                    f"当前已下载: {current_count} 次\n"
                    f"每日限制: {self.daily_download_limit} 次\n"
                    f"请明天再试～"
                ))
                return
            
            # 获取今天的图片
            await event.send(event.plain_result("🔍 正在搜索今天的图片..."))
            image_files = self._get_today_images(user_id)
            
            if not image_files:
                await event.send(event.plain_result(
                    "📭 今天还没有生成图片哦～\n"
                    "先使用 aimg 或 img2img 指令生成一些图片吧！"
                ))
                return
            
            await event.send(event.plain_result(f"📁 找到 {len(image_files)} 张图片，正在生成压缩包..."))
            
            # 创建压缩包
            zip_path = await self._create_zip_archive(image_files, user_id)
            if not zip_path:
                await event.send(event.plain_result(
                    "❌ 压缩包创建失败，请稍后重试！"
                ))
                return
            
            await event.send(event.plain_result("📦 压缩包创建完成，正在上传..."))
            
            # 上传压缩包
            upload_success = await self._upload_zip_file(event, zip_path)
            
            if upload_success:
                # 更新下载次数
                self._increment_download_count(user_id)
                
                # 获取更新后的下载次数
                _, new_count = self._check_download_limit(user_id)
                
                await event.send(event.plain_result(
                    f"✅ 压缩包上传成功！\n"
                    f"📁 文件名: {os.path.basename(zip_path)}\n"
                    f"📊 包含图片: {len(image_files)} 张\n"
                    f"📈 今日已下载: {new_count}/{self.daily_download_limit} 次\n"
                    f"💡 提示: 请从群文件或私聊文件中下载"
                ))
                
                # 延迟删除临时压缩包
                await asyncio.sleep(5)
                try:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                        logger.info(f"临时压缩包已清理: {zip_path}")
                except Exception as e:
                    logger.warning(f"清理临时压缩包失败: {e}")
            else:
                await event.send(event.plain_result(
                    "❌ 压缩包上传失败，请稍后重试！"
                ))
                
        except Exception as e:
            logger.error(f"处理输出压缩包指令失败: {e}")
            await event.send(event.plain_result(
                "❌ 处理请求时发生错误，请稍后重试！"
            ))

    def __del__(self):
        """析构函数，确保资源清理"""
        try:
            # 清理临时图片文件
            import glob
            temp_files = glob.glob(os.path.join(os.path.dirname(__file__), "help_*.png"))
            for temp_file in temp_files:
                try:
                    os.remove(temp_file)
                except:
                    pass
            
            # 清理临时压缩包文件
            zip_files = glob.glob(os.path.join(os.path.dirname(__file__), "comfyui_images_*.zip"))
            for zip_file in zip_files:
                try:
                    os.remove(zip_file)
                except:
                    pass
        except:
            pass
