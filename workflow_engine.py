"""
ComfyUI 工作流处理引擎 — 与 AstrBot/QQ 平台解耦的纯业务逻辑层

负责:
- ComfyUI API 通信（发送 prompt、轮询、上传/下载文件）
- 工作流构建（文生图/图生图/自定义 workflow）
- 多服务器调度（健康检查、worker 循环、故障转移、负载均衡）
- 用户任务队列管理
- Workflow 配置加载
- 参数解析（LoRA、模型、workflow 参数）
- 数据库操作（下载记录、图片生成记录）
- 文件本地保存

不依赖任何聊天平台（AstrBot/QQ），返回 WorkflowResult 统一结果对象。
"""

import aiohttp
import asyncio
import copy
import json
import logging
import os
import random
import re
import sqlite3
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote

import aiosqlite

logger = logging.getLogger("WorkflowEngine")


# ===== 统一结果对象 =====

@dataclass
class WorkflowResult:
    """引擎执行工作流的统一结果"""
    success: bool = True
    images: List[Dict[str, Any]] = field(default_factory=list)  # [{url, filename, subfolder, type}]
    videos: List[Dict[str, Any]] = field(default_factory=list)
    audios: List[Dict[str, Any]] = field(default_factory=list)
    models_3d: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)  # prompt, seed, batch_size, etc.


# ===== 引擎主类 =====

class WorkflowEngine:
    """ComfyUI 工作流处理引擎 — 进程内单例"""
    
    _instance: Optional['WorkflowEngine'] = None
    _init_lock = asyncio.Lock()
    
    # ---- 采样器/调度器列表 ----
    AVAILABLE_SAMPLERS = [
        "euler", "euler_cfg_pp", "euler_ancestral", "euler_ancestral_cfg_pp",
        "heun", "heunpp2", "dpm_2", "dpm_2_ancestral", "lms",
        "dpm_fast", "dpm_adaptive", "dpmpp_2s_ancestral", "dpmpp_2s_ancestral_cfg_pp",
        "dpmpp_sde", "dpmpp_sde_gpu", "dpmpp_2m", "dpmpp_2m_cfg_pp",
        "dpmpp_2m_sde", "dpmpp_2m_sde_gpu", "dpmpp_2m_sde_heun",
        "dpmpp_2m_sde_heun_gpu", "dpmpp_3m_sde", "dpmpp_3m_sde_gpu",
        "ddpm", "lcm", "ipndm", "ipndm_v", "deis", "res_multistep",
        "res_multistep_cfg_pp", "res_multistep_ancestral", "res_multistep_ancestral_cfg_pp",
        "gradient_estimation", "gradient_estimation_cfg_pp", "er_sde",
        "seeds_2", "seeds_3", "sa_solver", "sa_solver_pece", "ddim",
        "uni_pc", "uni_pc_bh2"
    ]
    AVAILABLE_SCHEDULERS = [
        "simple", "sgm_uniform", "karras", "exponential", "ddim_uniform",
        "beta", "normal", "linear_quadratic", "kl_optimal"
    ]

    class ServerState:
        """ComfyUI 服务器状态"""
        def __init__(self, url: str, name: str, server_id: int):
            self.url = url.rstrip("/")
            self.name = name
            self.server_id = server_id
            self.busy = False
            self.last_checked: Optional[datetime] = None
            self.healthy = True
            self.failure_count = 0
            self.retry_after: Optional[datetime] = None
            self.worker: Optional[asyncio.Task] = None

    # ==================== 初始化 & 单例 ====================

    def __init__(self, config: dict, plugin_dir: Optional[str] = None):
        """
        初始化引擎实例（不应直接调用，请使用 get_instance）
        
        Args:
            config: 配置字典（来自 AstrBot 注入或从 JSON 文件读取）
            plugin_dir: 插件目录路径（用于找 workflow 等子目录）
        """
        self._init_config(config, plugin_dir)
        self._init_state()
        
        # workflows 加载完成事件（供适配层等待）
        self.workflows_loaded = asyncio.Event()
        
        # 同步加载 workflow（确保适配层能立即获取前缀）
        self._load_workflows()
        self.workflows_loaded.set()
        
        # 启动异步服务（可能无事件循环，如 CLI 测试场景）
        try:
            self.server_monitor_task = asyncio.create_task(self._start_server_monitor())
        except RuntimeError:
            # 无运行中的事件循环，跳过（如在 CLI 中测试）
            pass
        try:
            asyncio.create_task(self._init_database())
        except RuntimeError:
            pass

    def _init_config(self, config: dict, plugin_dir: Optional[str] = None):
        """从配置字典初始化所有配置项"""
        # 插件目录
        self.plugin_dir = plugin_dir or str(Path(__file__).parent)
        self.workflow_dir = Path(self.plugin_dir) / "workflow"
        self.config_dir = Path(self.plugin_dir)
        
        # 数据目录（引擎不依赖 AstrBot API，用插件目录下的 data）
        self.data_dir = Path(self.plugin_dir) / "data"
        self.data_dir.mkdir(exist_ok=True)
        
        # ---- 服务器 ----
        self.comfyui_servers = self._parse_comfyui_servers(config.get("comfyui_url", []))
        self.temp_servers: List['WorkflowEngine.ServerState'] = []
        
        # ---- 基本参数 ----
        self.ckpt_name = config.get("ckpt_name")
        self.sampler_name = config.get("sampler_name")
        self.scheduler = config.get("scheduler")
        self.cfg = config.get("cfg")
        self.negative_prompt = config.get("negative_prompt", "")
        self.default_width = config.get("default_width")
        self.default_height = config.get("default_height")
        self.num_inference_steps = config.get("num_inference_steps")
        self.seed = config.get("seed", "随机")
        self.enable_translation = config.get("enable_translation")
        self.default_denoise = config.get("default_denoise", 0.7)
        self.open_time_ranges = config.get("open_time_ranges", "7:00-8:00,11:00-14:00,17:00-24:00")
        self.enable_image_encrypt = config.get("enable_image_encrypt", True)
        self.return_original_image = config.get("return_original_image", False)
        
        # ---- 批量 ----
        self.txt2img_batch_size = config.get("txt2img_batch_size", 1)
        self.img2img_batch_size = config.get("img2img_batch_size", 1)
        self.max_txt2img_batch = config.get("max_txt2img_batch", 6)
        self.max_img2img_batch = config.get("max_img2img_batch", 6)
        self.max_task_queue = config.get("max_task_queue", 10)
        self.min_width = config.get("min_width", 64)
        self.max_width = config.get("max_width", 2000)
        self.min_height = config.get("min_height", 64)
        self.max_height = config.get("max_height", 2000)
        
        # ---- 轮询/队列 ----
        self.queue_check_delay = config.get("queue_check_delay", 30)
        self.queue_check_interval = config.get("queue_check_interval", 5)
        self.empty_queue_max_retry = config.get("empty_queue_max_retry", 2)
        self.server_check_interval = 60
        self.max_failure_count = 3
        self.retry_delay = 300
        self.last_poll_index = -1
        
        # ---- LoRA ----
        self.lora_config = config.get("lora_config", [])
        self.default_lora_strength_model = 1.0
        self.default_lora_strength_clip = 1.0
        self.max_lora_count = 10
        self.min_lora_strength = 0.0
        self.max_lora_strength = 2.0
        self.lora_name_map = self._parse_lora_config()
        
        # ---- 模型 ----
        self.model_config = config.get("model_config", [])
        self.model_name_map = self._parse_model_config()
        
        # ---- 时间 ----
        self.parsed_time_ranges = self._parse_time_ranges()
        
        # ---- 自动保存 ----
        self.enable_auto_save = config.get("enable_auto_save", False)
        auto_save_config = config.get("auto_save_directory", config.get("auto_save_dir", "output"))
        if os.path.isabs(auto_save_config):
            self.auto_save_dir = auto_save_config
        else:
            self.auto_save_dir = str(self.data_dir / auto_save_config)
        self._ensure_directory_exists(self.auto_save_dir, "auto_save_dir")
        
        # ---- 下载/输出 ----
        self.enable_output_zip = config.get("enable_output_zip", True)
        self.daily_download_limit = config.get("daily_download_limit", 1)
        self.only_own_images = config.get("only_own_images", False)
        self.max_concurrent_tasks_per_user = config.get("max_concurrent_tasks_per_user", 3)
        
        # ---- 数据库 ----
        self.db_dir = self.auto_save_dir
        self.db_path = os.path.join(self.db_dir, "user.db")
        
        # ---- 视频发送 ----
        self.max_upload_size = config.get("max_upload_size", 100)
        
        # ---- Workflow ----
        self.workflows: Dict[str, Dict[str, Any]] = {}
        self.workflow_prefixes: Dict[str, str] = {}
        
        # 验证
        self._validate_config()

    def _init_state(self):
        """初始化运行时状态"""
        self.task_queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_task_queue)
        self.server_monitor_task: Optional[asyncio.Task] = None
        self.server_monitor_running: bool = False
        self.user_task_counts: Dict[str, int] = {}
        self.user_task_lock = asyncio.Lock()
        self.server_poll_lock = asyncio.Lock()
        self.server_state_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, config: Optional[dict] = None,
                           plugin_dir: Optional[str] = None) -> 'WorkflowEngine':
        """
        获取引擎单例实例
        
        Args:
            config: 首次初始化时必须提供配置字典
            plugin_dir: 插件目录路径
        """
        if cls._instance is None:
            async with cls._init_lock:
                if cls._instance is None:
                    if config is None:
                        raise RuntimeError("首次初始化必须提供 config")
                    cls._instance = cls(config=config, plugin_dir=plugin_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试或配置热更新）"""
        if cls._instance:
            cls._instance = None

    # ==================== 配置解析 ====================

    def _parse_comfyui_servers(self, server_configs: list) -> List[ServerState]:
        servers = []
        if not isinstance(server_configs, list):
            logger.warning(f"ComfyUI服务器配置格式错误，应为列表类型")
            return servers
        for idx, config in enumerate(server_configs):
            if not isinstance(config, str) or "," not in config:
                logger.warning(f"服务器配置项格式错误（索引{idx}）：{config}")
                continue
            url, name = config.split(",", 1)
            url = url.strip()
            name = name.strip()
            if not url.startswith(("http://", "https://")):
                logger.warning(f"服务器URL格式错误（索引{idx}）：{url}")
                url = f"http://{url}"
            servers.append(self.ServerState(url, name or f"服务器{idx+1}", idx))
            logger.info(f"已添加ComfyUI服务器：{name} ({url})")
        return servers

    def _parse_lora_config(self) -> Dict[str, Tuple[str, str]]:
        lora_map = {}
        duplicate_descs = set()
        for idx, item in enumerate(self.lora_config):
            if not isinstance(item, str) or "," not in item:
                logger.warning(f"LoRA配置项格式错误（索引{idx}）：{item}")
                continue
            filename, desc = item.split(",", 1)
            filename = filename.strip()
            desc = desc.strip()
            if not filename or not desc:
                continue
            desc_lower = desc.lower()
            if desc_lower in lora_map:
                duplicate_descs.add(desc_lower)
            lora_map[desc_lower] = (filename, desc)
            fp = os.path.splitext(filename)[0].strip().lower()
            if fp not in lora_map and fp not in duplicate_descs:
                lora_map[fp] = (filename, desc)
        return lora_map

    def _parse_model_config(self) -> Dict[str, Tuple[str, str]]:
        model_map = {}
        duplicate_descs = set()
        for idx, item in enumerate(self.model_config):
            if not isinstance(item, str) or "," not in item:
                continue
            filename, desc = item.split(",", 1)
            filename = filename.strip()
            desc = desc.strip()
            if not filename or not desc:
                continue
            desc_lower = desc.lower()
            if desc_lower in model_map:
                duplicate_descs.add(desc_lower)
            model_map[desc_lower] = (filename, desc)
            fp = os.path.splitext(filename)[0].strip().lower()
            if fp not in model_map and fp not in duplicate_descs:
                model_map[fp] = (filename, desc)
        return model_map

    def _validate_config(self):
        required = [
            ("ckpt_name", str), ("sampler_name", str), ("scheduler", str),
            ("cfg", (int, float)), ("default_width", int), ("default_height", int),
            ("txt2img_batch_size", int), ("img2img_batch_size", int),
            ("max_txt2img_batch", int), ("max_img2img_batch", int),
            ("max_task_queue", int), ("min_width", int), ("max_width", int),
            ("min_height", int), ("max_height", int), ("num_inference_steps", int),
            ("default_denoise", (int, float)), ("open_time_ranges", str),
            ("queue_check_delay", int), ("queue_check_interval", int),
            ("empty_queue_max_retry", int), ("lora_config", list),
            ("max_concurrent_tasks_per_user", int)
        ]
        for key, typ in required:
            val = getattr(self, key, None)
            if not isinstance(val, typ):
                raise ValueError(f"配置项错误：{key}（需为{typ.__name__}类型）")
        if not self.parsed_time_ranges:
            logger.warning(f"开放时间格式错误，使用默认时间段")
            self.open_time_ranges = "7:00-8:00,11:00-14:00,17:00-24:00"
            self.parsed_time_ranges = self._parse_time_ranges()

    def _parse_time_ranges(self) -> List[Tuple[int, int]]:
        parsed = []
        for r in [x.strip() for x in self.open_time_ranges.split(",") if x.strip()]:
            if "-" not in r:
                continue
            start_str, end_str = r.split("-", 1)
            sm = self._time_to_minutes(start_str)
            em = self._time_to_minutes(end_str)
            if sm is not None and em is not None:
                parsed.append((sm, em))
        return parsed

    @staticmethod
    def _time_to_minutes(time_str: str) -> Optional[int]:
        try:
            if ":" in time_str:
                hh, mm = time_str.split(":", 1)
                hh, mm = int(hh.strip()), int(mm.strip())
            else:
                hh, mm = int(time_str.strip()), 0
            if 0 <= hh <= 24 and 0 <= mm <= 59:
                return (hh % 24) * 60 + mm
        except (ValueError, IndexError):
            pass
        return None

    def _ensure_directory_exists(self, dir_path: Union[str, Path], dir_name: str = "directory"):
        dp = str(dir_path)
        if os.path.exists(dp):
            if not os.path.isdir(dp):
                backup = f"{dp}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                os.rename(dp, backup)
                os.makedirs(dp, exist_ok=True)
        else:
            os.makedirs(dp, exist_ok=True)

    def _filter_server_urls(self, text: str) -> str:
        if not text or not self.comfyui_servers:
            return text
        filtered = text
        for srv in self.comfyui_servers:
            if srv.url in filtered:
                filtered = filtered.replace(srv.url, f"[{srv.name}地址已隐藏]")
            url_clean = srv.url.replace("http://", "").replace("https://", "")
            if url_clean in filtered and srv.url not in filtered:
                filtered = filtered.replace(url_clean, f"[{srv.name}地址已隐藏]")
        return filtered

    def _is_in_open_time(self) -> bool:
        if not self.parsed_time_ranges:
            return True
        now = datetime.now()
        cm = now.hour * 60 + now.minute
        for sm, em in self.parsed_time_ranges:
            if sm <= em:
                if sm <= cm <= em:
                    return True
            else:
                if cm >= sm or cm <= em:
                    return True
        return False

    def _get_open_time_desc(self) -> str:
        return "、".join([r.strip() for r in self.open_time_ranges.split(",") if r.strip()])

    # ==================== 生成列表描述 ====================

    def generate_lora_list_desc(self) -> str:
        if not self.lora_name_map:
            return "  暂无可用LoRA"
        unique = {}
        for k, (fn, desc) in self.lora_name_map.items():
            if desc.lower() not in unique:
                unique[desc.lower()] = (fn, desc)
        return "\n".join(f"  - {desc}（文件：{fn}）" for fn, desc in unique.values())

    def generate_model_list_desc(self) -> str:
        if not self.model_name_map:
            return "  暂无可用模型"
        seen = set()
        items = []
        for k, (fn, desc) in self.model_name_map.items():
            if desc not in seen:
                seen.add(desc)
                items.append(f"  - {desc}（文件：{fn}）")
        return "\n".join(items)

    def generate_workflow_list_desc(self) -> str:
        if not self.workflows:
            return "  暂无可用Workflow"
        lines = []
        for wfn, info in self.workflows.items():
            cfg = info["config"]
            name = cfg.get("name", wfn)
            prefix = cfg.get("prefix", "")
            desc = cfg.get("description", "")
            lines.append(f"  - {name}（前缀：{prefix}）{f' - {desc}' if desc else ''}")
        return "\n".join(lines)

    def generate_workflow_html_items(self) -> str:
        if not self.workflows:
            return '<li>暂无可用Workflow</li>'
        html = []
        for wfn, info in self.workflows.items():
            cfg = info["config"]
            name = cfg.get("name", wfn)
            prefix = cfg.get("prefix", "")
            desc = cfg.get("description", "")
            if desc:
                html.append(f'<li>{name} (前缀: {prefix}) - {desc}</li>')
            else:
                html.append(f'<li>{name} (前缀: {prefix})</li>')
        return "\n".join(html)

    def generate_workflow_text_help(self) -> str:
        if not self.workflows:
            return "\n\n🔧 可用Workflow列表：\n  • 暂无可用Workflow"
        details = []
        for wfn, info in self.workflows.items():
            cfg = info["config"]
            name = cfg.get("name", wfn)
            prefix = cfg.get("prefix", "")
            desc = cfg.get("description", "")
            if desc:
                details.append(f"  • {name} (前缀: {prefix}) - {desc}")
            else:
                details.append(f"  • {name} (前缀: {prefix})")
        help_text = "\n\n🔧 可用Workflow列表：\n" + "\n".join(details)
        help_text += ("\n\nWorkflow使用说明：\n  - 格式：<前缀> [参数名:值 ...]\n"
                      "  - 支持中英文参数名和别名（如：width/宽度/w，sampler_name/采样器/sampler）\n"
                      "  - 参数格式：参数名:值（例：宽度:800 或 采样器:euler）\n"
                      "  - 具体支持的参数名请查看各workflow的配置说明")
        return help_text

    # ==================== Workflow 加载 ====================

    def _load_workflows(self):
        """同步加载 workflow 模块（__init__ 中调用，确保前缀立即可用）"""
        try:
            if not self.workflow_dir.exists():
                self.workflow_dir.mkdir(parents=True, exist_ok=True)
                return

            for wfn in [f.name for f in self.workflow_dir.iterdir()]:
                wf_path = self.workflow_dir / wfn
                if not wf_path.is_dir():
                    continue
                config_file = wf_path / "config.json"
                workflow_file = wf_path / "workflow.json"
                if not config_file.exists() or not workflow_file.exists():
                    continue
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                    with open(workflow_file, 'r', encoding='utf-8') as f:
                        wf_data = json.load(f)
                    required = ["name", "prefix", "input_nodes", "output_nodes"]
                    if not all(f in config for f in required):
                        continue
                    prefix = config["prefix"]
                    if prefix in self.workflow_prefixes:
                        continue
                    self._inject_main_config(config, wfn)
                    self.workflows[wfn] = {"config": config, "workflow": wf_data, "path": wf_path}
                    self.workflow_prefixes[prefix] = wfn
                    logger.info(f"已加载workflow: {config['name']} (前缀: {prefix})")
                except Exception as e:
                    logger.error(f"加载workflow {wfn} 失败: {e}")
            logger.info(f"共加载 {len(self.workflows)} 个workflow模块")
        except Exception as e:
            logger.error(f"加载workflow模块失败: {e}")

    def _inject_main_config(self, config: dict, workflow_name: str):
        """将主程序配置注入到 workflow 配置"""
        try:
            node_configs = config.get("node_configs", {})
            if not node_configs:
                return
            for nid, nc in node_configs.items():
                for pname, pinfo in nc.items():
                    if pinfo.get("type") == "select" and pinfo.get("inject_models"):
                        opts = []
                        if self.model_name_map:
                            for dl, (fn, desc) in self.model_name_map.items():
                                if dl == desc.lower():
                                    opts.append(desc)
                        if opts:
                            pinfo["options"] = opts
                            pinfo.pop("inject_models", None)
                    elif pinfo.get("type") == "select" and pinfo.get("inject_samplers"):
                        if self.sampler_name:
                            pinfo["default"] = self.sampler_name
                        pinfo.pop("inject_samplers", None)
                    elif pinfo.get("type") == "select" and pinfo.get("inject_schedulers"):
                        if self.scheduler:
                            pinfo["default"] = self.scheduler
                        pinfo.pop("inject_schedulers", None)
        except Exception as e:
            logger.error(f"为workflow {workflow_name} 注入配置失败: {e}")

    # ==================== 任务提交与处理 ====================

    async def submit_task(self, task_data: dict) -> bool:
        """
        提交任务到引擎队列
        
        Args:
            task_data: 任务数据字典，必须包含 user_id 字段
                      根据任务类型包含不同字段
        
        Returns:
            是否成功入队
        """
        if self.task_queue.full():
            return False
        await self.task_queue.put(task_data)
        return True

    async def _increment_user_task_count(self, user_id: str) -> bool:
        async with self.user_task_lock:
            cur = self.user_task_counts.get(user_id, 0)
            if cur < self.max_concurrent_tasks_per_user:
                self.user_task_counts[user_id] = cur + 1
                return True
            return False

    async def _decrement_user_task_count(self, user_id: str):
        async with self.user_task_lock:
            cur = self.user_task_counts.get(user_id, 0)
            if cur > 0:
                self.user_task_counts[user_id] = cur - 1
                if self.user_task_counts[user_id] == 0:
                    del self.user_task_counts[user_id]

    async def _check_user_task_limit(self, user_id: str) -> bool:
        async with self.user_task_lock:
            return self.user_task_counts.get(user_id, 0) < self.max_concurrent_tasks_per_user

    def _get_any_healthy_server(self) -> Optional[ServerState]:
        for srv in self.comfyui_servers:
            now = datetime.now()
            if srv.healthy and (not srv.retry_after or now >= srv.retry_after):
                return srv
        return None

    # ==================== 服务器监控 & Worker 循环 ====================

    async def _start_server_monitor(self):
        if self.server_monitor_running:
            return
        self.server_monitor_running = True
        logger.info(f"启动服务器监控，检查间隔：{self.server_check_interval}秒")
        for srv in self.comfyui_servers:
            await self._manage_worker_for_server(srv)
        while self.server_monitor_running:
            for srv in self.comfyui_servers:
                if srv.retry_after and datetime.now() < srv.retry_after:
                    continue
                is_healthy = await self._check_server_health(srv)
                async with self.server_state_lock:
                    if is_healthy != srv.healthy:
                        srv.healthy = is_healthy
                        status = "恢复正常" if is_healthy else "异常"
                        logger.info(f"服务器{srv.name}状态变化：{status}")
                        await self._manage_worker_for_server(srv)
                    srv.last_checked = datetime.now()
            await asyncio.sleep(self.server_check_interval)

    async def _manage_worker_for_server(self, server: ServerState):
        if server.healthy and (not server.worker or server.worker.done()):
            if server.worker and not server.worker.done():
                server.healthy = False
                try:
                    await asyncio.wait_for(server.worker, timeout=30)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    server.worker.cancel()
                    try:
                        await server.worker
                    except asyncio.CancelledError:
                        pass
            logger.info(f"为服务器{server.name}启动worker")
            server.worker = asyncio.create_task(
                self._worker_loop(f"worker-{server.server_id}", server)
            )
        elif not server.healthy and server.worker and not server.worker.done():
            logger.info(f"服务器{server.name}异常，worker将完成当前任务后退出")
        await self._check_and_clear_queue_if_no_healthy_servers()

    async def _check_and_clear_queue_if_no_healthy_servers(self):
        has_healthy = any(s.healthy for s in self.comfyui_servers)
        if not has_healthy and self.task_queue.qsize() > 0:
            logger.warning(f"所有服务器均不健康，清空任务队列（{self.task_queue.qsize()}个任务）")
            while not self.task_queue.empty():
                try:
                    td = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                    uid = td.get("user_id")
                    if uid:
                        await self._decrement_user_task_count(uid)
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

    async def _get_server_system_info(self, server: ServerState) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.url}/system_stats", timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return None

    async def _get_next_available_server(self) -> Optional[ServerState]:
        if not self.comfyui_servers:
            return None
        async with self.server_poll_lock, self.server_state_lock:
            for _ in range(len(self.comfyui_servers)):
                self.last_poll_index = (self.last_poll_index + 1) % len(self.comfyui_servers)
                srv = self.comfyui_servers[self.last_poll_index]
                now = datetime.now()
                if srv.healthy and not srv.busy and (not srv.retry_after or now >= srv.retry_after):
                    srv.busy = True
                    return srv
        return None

    async def _mark_server_busy(self, server: ServerState, busy: bool):
        async with self.server_state_lock:
            server.busy = busy

    async def _handle_server_failure(self, server: ServerState):
        async with self.server_state_lock:
            server.failure_count += 1
            if server.failure_count >= self.max_failure_count:
                server.healthy = False
                server.retry_after = datetime.now() + timedelta(seconds=self.retry_delay)
                logger.warning(f"服务器{server.name}连续失败{self.max_failure_count}次，将在{self.retry_delay}秒后重试")
            else:
                server.retry_after = datetime.now() + timedelta(seconds=10)

    async def _reset_server_failure(self, server: ServerState):
        async with self.server_state_lock:
            if server.failure_count > 0:
                server.failure_count = 0
                server.retry_after = None
                if not server.healthy:
                    server.healthy = True

    async def _worker_loop(self, worker_name: str, server: ServerState):
        logger.info(f"{worker_name}已启动，绑定到服务器{server.name}")
        try:
            while True:
                if not server.healthy:
                    logger.info(f"{worker_name}检测到服务器{server.name}不健康，退出")
                    return
                try:
                    task_data = await asyncio.wait_for(self.task_queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    if not server.healthy:
                        return
                    continue
                try:
                    if not server.healthy:
                        await self.task_queue.put(task_data)
                        return
                    result = await self._process_task_on_server(server, task_data)
                    if cb := task_data.get("callback"):
                        if asyncio.iscoroutinefunction(cb):
                            asyncio.create_task(cb(result))
                        else:
                            # 同步回调直接调用
                            cb(result)
                except Exception as e:
                    uid = task_data.get("user_id")
                    if uid:
                        await self._decrement_user_task_count(uid)
                    if not server.healthy:
                        logger.info(f"{worker_name}检测到服务器{server.name}故障，将任务放回队列")
                        task_data.pop("callback", None)
                        await self.task_queue.put(task_data)
                        return
                    logger.error(f"{worker_name}处理任务失败：{str(e)[:500]}")
                    # 通过回调通知用户错误
                    err_result = WorkflowResult(success=False, error=str(e)[:1000])
                    if cb := task_data.get("callback"):
                        if asyncio.iscoroutinefunction(cb):
                            asyncio.create_task(cb(err_result))
                        else:
                            cb(err_result)
                finally:
                    self.task_queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"{worker_name}被取消")
        except Exception as e:
            logger.error(f"{worker_name}异常退出：{str(e)}")
        finally:
            logger.info(f"{worker_name}已停止")

    async def _process_task_on_server(self, server: ServerState, task_data: dict) -> WorkflowResult:
        """
        在指定服务器上处理任务
        
        Returns:
            WorkflowResult 对象
        """
        is_workflow = task_data.get("is_workflow", False)
        user_id = task_data.get("user_id")
        
        try:
            if is_workflow:
                result = await self._process_workflow_task(server, task_data)
            else:
                result = await self._process_comfyui_task(server, task_data)
            await self._reset_server_failure(server)
            return result
        except Exception as e:
            await self._handle_server_failure(server)
            raise
        finally:
            if user_id:
                await self._decrement_user_task_count(user_id)
            await self._mark_server_busy(server, False)

    # ==================== ComfyUI API ====================

    async def upload_image_to_comfyui(self, server: ServerState, img_path: str) -> str:
        """上传图片到 ComfyUI 服务器，返回上传后的文件名"""
        if not os.path.exists(img_path):
            raise Exception(f"PERMANENT_ERROR:图片文件不存在：{img_path}")
        
        loop = asyncio.get_event_loop()
        img_data = await loop.run_in_executor(None, lambda: open(img_path, "rb").read())
        
        form = aiohttp.FormData()
        form.add_field("image", img_data, filename=os.path.basename(img_path), content_type="image/*")
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{server.url}/upload/image", data=form) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"图片上传失败（HTTP {resp.status}）：{self._filter_server_urls(text[:50])}")
                data = await resp.json()
                return data.get("name", "")

    async def send_comfyui_prompt(self, server: ServerState, prompt: dict) -> str:
        """发送 prompt 到 ComfyUI，返回 prompt_id"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{server.url}/prompt",
                headers={"Content-Type": "application/json"},
                json={"client_id": str(uuid.uuid4()), "prompt": prompt}
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"任务下发失败（HTTP {resp.status}）：{self._filter_server_urls(text[:50000])}")
                data = await resp.json()
                return data.get("prompt_id", "")

    async def poll_task_status(self, server: ServerState, prompt_id: str,
                                timeout: int = 600, interval: int = 3) -> dict:
        """轮询任务状态直到完成"""
        url = f"{server.url}/history/{prompt_id}"
        start_time = asyncio.get_event_loop().time()
        empty_queue_retry = 0
        queue_check_start = start_time + self.queue_check_delay
        
        async with aiohttp.ClientSession() as session:
            while True:
                now_t = asyncio.get_event_loop().time()
                elapsed = now_t - start_time
                if not server.healthy:
                    raise Exception(f"服务器【{server.name}】不健康，无法完成任务")
                if elapsed > timeout:
                    raise Exception(f"任务超时（{timeout}秒未完成）")
                
                try:
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            task_data = data.get(prompt_id)
                            if task_data and task_data.get("status", {}).get("completed"):
                                return task_data
                        else:
                            await self._handle_server_failure(server)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if not server.healthy:
                        raise
                    if isinstance(e, (asyncio.TimeoutError, aiohttp.ClientConnectorError,
                                      aiohttp.ClientOSError, aiohttp.ServerDisconnectedError)):
                        await self._handle_server_failure(server)
                        if not server.healthy:
                            raise
                
                if now_t >= queue_check_start and int(elapsed) % self.queue_check_interval == 0:
                    is_empty = await self._check_queue_empty(server)
                    if is_empty:
                        empty_queue_retry += 1
                        if empty_queue_retry >= self.empty_queue_max_retry:
                            raise Exception(
                                self._filter_server_urls(
                                    f"任务失败：服务器【{server.name}】队列已为空，"
                                    f"但历史记录中未找到任务结果。可能是任务被强制终止。"
                                )
                            )
                    else:
                        empty_queue_retry = 0
                
                await asyncio.sleep(interval)

    async def _check_queue_empty(self, server: ServerState) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.url}/api/queue", timeout=10) as resp:
                    if resp.status != 200:
                        await self._handle_server_failure(server)
                        return True
                    data = await resp.json()
                    if not isinstance(data, dict) or "queue_running" not in data:
                        await self._handle_server_failure(server)
                        return True
                    return len(data["queue_running"]) == 0 and len(data["queue_pending"]) == 0
        except Exception as e:
            await self._handle_server_failure(server)
            return True

    def _extract_batch_image_info(self, history_data: dict) -> List[dict]:
        """从历史记录中提取输出文件信息"""
        outputs = history_data.get("outputs", {})
        # 检查 SaveImage 节点（节点9或50）
        for nid in ("9", "50"):
            nd = outputs.get(nid)
            if nd and nd.get("images"):
                return nd["images"]
        # 检查其他输出类型
        for nid, nd in outputs.items():
            for out_type in ("3d", "audio", "video", "mesh", "model", "file"):
                items = nd.get(out_type)
                if items:
                    return [{
                        "filename": i["filename"],
                        "subfolder": i.get("subfolder", ""),
                        "type": i.get("type", "output"),
                        "file_type": out_type
                    } for i in items]
        raise Exception("未找到任何输出文件")

    async def get_image_url(self, server: ServerState, filename: str,
                            subfolder: str = "", file_type: str = "output") -> str:
        """获取文件下载 URL"""
        params = {"filename": filename, "type": file_type, "subfolder": subfolder}
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')) and not self.return_original_image:
            params["preview"] = "true"
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return f"{server.url}/view?{qs}"

    # ==================== 任务处理（核心逻辑） ====================

    async def _process_comfyui_task(self, server: ServerState,
                                     task_data: dict) -> WorkflowResult:
        """处理标准文生图/图生图任务"""
        prompt = task_data.get("prompt", "")
        current_seed = task_data.get("current_seed", 0)
        current_width = task_data.get("current_width", self.default_width)
        current_height = task_data.get("current_height", self.default_height)
        current_batch_size = task_data.get("current_batch_size", 1)
        lora_list = task_data.get("lora_list", [])
        selected_model = task_data.get("selected_model")
        img_path = task_data.get("img_path")
        denoise = task_data.get("denoise", 1.0)  # 默认1.0：文生图不传denoise；图生图由适配层传入
        
        image_filename = None
        if img_path:
            if not os.path.exists(img_path):
                raise Exception(f"PERMANENT_ERROR:图片文件不存在：{img_path}")
            filesize = os.path.getsize(img_path)
            if filesize < 10240:
                raise Exception(f"PERMANENT_ERROR:图片文件过小，可能已损坏")
            image_filename = await self.upload_image_to_comfyui(server, img_path)
            if not image_filename:
                raise Exception(f"PERMANENT_ERROR:图片上传失败")
            self._schedule_cleanup(img_path)
        
        comfy_prompt = self._build_comfyui_prompt(
            prompt, current_seed, current_width, current_height,
            image_filename, denoise, current_batch_size, lora_list, selected_model
        )
        prompt_id = await self.send_comfyui_prompt(server, comfy_prompt)
        history_data = await self.poll_task_status(server, prompt_id)
        
        if not history_data or not history_data.get("status", {}).get("completed"):
            raise Exception("任务超时或未完成")
        
        image_info_list = self._extract_batch_image_info(history_data)
        if not image_info_list:
            raise Exception("未找到生成的图片或文件")
        
        result = WorkflowResult()
        result.metadata = {
            "prompt": prompt,
            "seed": current_seed,
            "width": current_width,
            "height": current_height,
            "batch_size": current_batch_size,
            "server_name": server.name,
            "prompt_id": prompt_id,
            "is_img2img": image_filename is not None,
            "denoise": denoise if image_filename else None
        }
        
        for info in image_info_list:
            fn = info["filename"]
            sf = info.get("subfolder", "")
            ft = info.get("type", "output")
            url = await self.get_image_url(server, fn, subfolder=sf, file_type=ft)
            entry = {"url": url, "filename": fn, "subfolder": sf, "type": ft}
            
            if fn.lower().endswith('.glb'):
                entry["url"] = f"{server.url}/view?filename={fn}&type=output&subfolder={sf}"
                result.models_3d.append(entry)
            else:
                result.images.append(entry)
            
            # 自动保存
            if self.enable_auto_save:
                await self.save_image_locally(server, fn,
                    prompt, task_data.get("user_id", ""), sf, ft)
        
        return result

    async def _process_workflow_task(self, server: ServerState,
                                      task_data: dict) -> WorkflowResult:
        """处理自定义 workflow 任务"""
        prompt = task_data.get("prompt", {})
        workflow_name = task_data.get("workflow_name", "")
        image_paths = task_data.get("image_paths", [])
        workflow_config = task_data.get("workflow_config")
        
        workflow_info = self.workflows.get(workflow_name)
        if not workflow_info:
            raise Exception(f"Workflow不存在: {workflow_name}")
        
        config = workflow_config if workflow_config else workflow_info["config"]
        
        # 上传图片
        uploaded_images = []
        if image_paths and config.get("input_nodes"):
            for i, ip in enumerate(image_paths):
                if not os.path.exists(ip):
                    raise Exception(f"PERMANENT_ERROR:第 {i+1} 张图片不存在")
                fn = await self.upload_image_to_comfyui(server, ip)
                if not fn:
                    raise Exception(f"PERMANENT_ERROR:第 {i+1} 张图片上传失败")
                uploaded_images.append(fn)
            
            # 将上传的图片名注入 prompt
            if uploaded_images:
                self._inject_images_to_prompt(prompt, config, uploaded_images)
            
            # 清理临时文件
            for ip in image_paths:
                if "workflow_inputs" not in ip:
                    self._schedule_cleanup(ip)
        
        # 发送 workflow
        prompt_id = await self.send_comfyui_prompt(server, prompt)
        history_data = await self.poll_task_status(server, prompt_id)
        
        if not history_data or not history_data.get("status", {}).get("completed"):
            raise Exception("任务超时或未完成")
        
        # 提取输出
        output_nodes = config.get("output_nodes", [])
        output_mappings = config.get("output_mappings", {})
        result = WorkflowResult()
        result.metadata = {
            "workflow_name": workflow_name,
            "workflow_title": config.get("name", workflow_name),
            "server_name": server.name,
            "prompt_id": prompt_id
        }
        
        for node_id in output_nodes:
            if node_id not in output_mappings:
                continue
            outputs = history_data.get("outputs", {})
            node_output = outputs.get(node_id)
            if not node_output:
                continue
            
            # 图片
            if node_output.get("images"):
                for fi in node_output["images"]:
                    fn = fi["filename"]
                    sf = fi.get("subfolder", "")
                    ft = fi.get("type", "output")
                    is_vid = (fn.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm'))
                              or node_output.get("animated", False))
                    if is_vid:
                        url = f"{server.url}/view?filename={fn}&type=output&subfolder={sf}"
                        result.videos.append({"url": url, "filename": fn, "subfolder": sf, "type": ft})
                    else:
                        url = await self.get_image_url(server, fn, subfolder=sf, file_type=ft)
                        result.images.append({"url": url, "filename": fn, "subfolder": sf, "type": ft})
                    if self.enable_auto_save:
                        await self.save_image_locally(
                            server, fn, f"workflow_{workflow_name}",
                            task_data.get("user_id", ""), sf, ft
                        )
            
            # 音频
            if node_output.get("audio"):
                for fi in node_output["audio"]:
                    fn = fi["filename"]
                    sf = fi.get("subfolder", "")
                    url = f"{server.url}/view?filename={fn}&type=output&subfolder={sf}"
                    result.audios.append({"url": url, "filename": fn, "subfolder": sf, "type": "output"})
            
            # 3D
            if node_output.get("3d"):
                for fi in node_output["3d"]:
                    fn = fi["filename"]
                    sf = fi.get("subfolder", "")
                    url = f"{server.url}/view?filename={fn}&type=output&subfolder={sf}"
                    result.models_3d.append({"url": url, "filename": fn, "subfolder": sf, "type": "output"})
        
        return result

    def _inject_images_to_prompt(self, prompt: dict, config: dict, images: List[str]):
        """将上传的图片名注入到 workflow prompt"""
        if not images:
            return
        input_nodes = config.get("input_nodes", [])
        input_mappings = config.get("input_mappings", {})
        used_indices = set()
        
        for nid in input_nodes:
            if nid not in input_mappings or nid not in prompt:
                continue
            mapping = input_mappings[nid]
            pname = mapping.get("parameter_name", "image")
            if pname != "image":
                continue
            img_idx = mapping.get("image_index")
            if img_idx is not None:
                mode = mapping.get("image_mode", "single")
                if mode == "single" and img_idx < len(images):
                    prompt[nid]["inputs"][pname] = images[img_idx]
                    used_indices.add(img_idx)
                elif mode in ("list", "all"):
                    prompt[nid]["inputs"][pname] = images
        
        cur = 0
        for nid in input_nodes:
            if nid not in input_mappings or nid not in prompt:
                continue
            mapping = input_mappings[nid]
            pname = mapping.get("parameter_name", "image")
            if pname != "image" or mapping.get("image_index") is not None:
                continue
            while cur in used_indices and cur < len(images):
                cur += 1
            if cur < len(images):
                prompt[nid]["inputs"][pname] = images[cur]
                used_indices.add(cur)
                cur += 1
            else:
                prompt[nid]["inputs"][pname] = images[0]
                used_indices.add(0)

    # ==================== Workflow 构建 ====================

    def _build_comfyui_prompt(self, prompt: str, seed: int, width: int, height: int,
                               image_filename: Optional[str] = None, denoise: float = 1.0,
                               batch_size: int = 1, lora_list: List[dict] = None,
                               selected_model: Optional[str] = None) -> dict:
        """构建标准文生图/图生图 prompt"""
        lora_list = lora_list or []
        nodes = {
            "6": {"inputs": {"text": prompt, "clip": ["30", 1]},
                  "class_type": "CLIPTextEncode", "_meta": {"title": "CLIP Text Encode (Positive)"}},
            "8": {"inputs": {"samples": ["31", 0], "vae": ["30", 2]},
                  "class_type": "VAEDecode", "_meta": {"title": "VAE Decode"}},
            "30": {"inputs": {"ckpt_name": selected_model or self.ckpt_name},
                   "class_type": "CheckpointLoaderSimple", "_meta": {"title": "Load Checkpoint"}},
            "31": {"inputs": {"seed": seed, "steps": self.num_inference_steps,
                              "cfg": self.cfg, "sampler_name": self.sampler_name,
                              "scheduler": self.scheduler, "denoise": denoise,
                              "model": ["30", 0], "positive": ["6", 0],
                              "negative": ["33", 0],
                              "latent_image": ["54", 0] if image_filename else ["36", 0]},
                   "class_type": "KSampler", "_meta": {"title": "KSampler"}},
            "33": {"inputs": {"text": self.negative_prompt, "clip": ["30", 1]},
                   "class_type": "CLIPTextEncode", "_meta": {"title": "CLIP Text Encode (Negative)"}}
        }
        # 加密
        if self.enable_image_encrypt:
            nodes["44"] = {"inputs": {"mode": "encrypt", "enable": True, "image": ["8", 0]},
                           "class_type": "HilbertImageEncrypt", "_meta": {"title": "希尔伯特曲线图像加密"}}
            nodes["save_image_websocket_node"] = {"inputs": {"images": ["44", 0]},
                                                   "class_type": "SaveImageWebsocket"}
            nodes["9"] = {"inputs": {"filename_prefix": "comfyui_gen", "images": ["44", 0]},
                          "class_type": "SaveImage", "_meta": {"title": "Save Image"}}
        else:
            nodes["save_image_websocket_node"] = {"inputs": {"images": ["8", 0]},
                                                   "class_type": "SaveImageWebsocket"}
            nodes["9"] = {"inputs": {"filename_prefix": "comfyui_gen", "images": ["8", 0]},
                          "class_type": "SaveImage", "_meta": {"title": "Save Image"}}
        # LoRA
        if lora_list:
            cur_model, cur_clip = "30", "30"
            for i, lora in enumerate(lora_list):
                nid = str(100 + i)
                nodes[nid] = {"inputs": {"lora_name": lora["filename"],
                              "strength_model": lora["strength_model"],
                              "strength_clip": lora["strength_clip"],
                              "model": [cur_model, 0], "clip": [cur_clip, 1]},
                              "class_type": "LoraLoader", "_meta": {"title": f"Load LoRA: {lora['name']}"}}
                cur_model = cur_clip = nid
            nodes["31"]["inputs"]["model"] = [cur_model, 0]
            nodes["6"]["inputs"]["clip"] = [cur_clip, 1]
            nodes["33"]["inputs"]["clip"] = [cur_clip, 1]
        # 图生图
        if image_filename:
            nodes.update({
                "51": {"inputs": {"image": image_filename}, "class_type": "LoadImage"},
                "53": {"inputs": {"min_width": 800, "min_height": 800,
                                  "max_width": 1600, "max_height": 1600,
                                  "image": ["51", 0]}, "class_type": "ImageCompressor"},
                "54": {"inputs": {"pixels": ["53", 0], "vae": ["30", 2]},
                       "class_type": "VAEEncode"},
                "55": {"inputs": {"samples": ["54", 0], "amount": batch_size},
                       "class_type": "RepeatLatentBatch"}
            })
            nodes["31"]["inputs"]["latent_image"] = ["55", 0]
        else:
            nodes["36"] = {"inputs": {"width": width, "height": height, "batch_size": batch_size},
                           "class_type": "EmptyLatentImage"}
        return nodes

    def build_workflow(self, workflow_data: dict, config: dict,
                        params: dict, images: List[str]) -> dict:
        """构建最终的 workflow（对外接口）"""
        final = copy.deepcopy(workflow_data)
        if images:
            self._inject_images_to_prompt(final, config, images)
        
        node_configs = config.get("node_configs", {})
        for nid, nc in node_configs.items():
            if nid not in final:
                continue
            for pname, pcfg in nc.items():
                value = None
                key = f"{nid}:{pname}"
                if key in params:
                    value = params[key]
                elif pname in params:
                    value = params[pname]
                else:
                    for alias in pcfg.get("aliases", []):
                        if alias in params:
                            value = params[alias]
                            break
                if value is not None:
                    value = self._convert_param_value(value, pcfg)
                    if "inputs" not in final[nid]:
                        final[nid]["inputs"] = {}
                    final[nid]["inputs"][pname] = value
                elif "default" in pcfg:
                    val = pcfg["default"]
                    if pname == "seed" and val == -1:
                        val = random.randint(1, 18446744073709551615)
                    if "inputs" not in final[nid]:
                        final[nid]["inputs"] = {}
                    final[nid]["inputs"][pname] = val
        
        # 全局模型配置
        if "30" in final and final["30"].get("class_type") == "CheckpointLoaderSimple":
            if self.ckpt_name and not final["30"]["inputs"].get("ckpt_name"):
                final["30"]["inputs"]["ckpt_name"] = self.ckpt_name
        
        return final

    def _inject_image_to_workflow(self, workflow: dict, config: dict, image_path: str):
        """将图片注入 workflow（已上传的图片名）"""
        try:
            inodes = config.get("input_nodes", [])
            for nid in inodes:
                if nid in workflow:
                    ct = workflow[nid].get("class_type", "")
                    if ct in ["LoadImage", "ImageLoader", "ImageInput"]:
                        if "inputs" in workflow[nid]:
                            workflow[nid]["inputs"]["image"] = image_path
                        break
            else:
                for nid, nd in workflow.items():
                    if "inputs" in nd and "image" in nd["inputs"]:
                        nd["inputs"]["image"] = image_path
                        break
        except Exception as e:
            logger.warning(f"注入图片到workflow失败: {e}")

    def _inject_user_params(self, workflow: dict, config: dict, params: dict):
        """将用户参数注入 workflow"""
        try:
            node_configs = config.get("node_configs", {})
            for nid, pconfigs in node_configs.items():
                if nid not in workflow:
                    continue
                for pname, pvalue in params.items():
                    if pname in pconfigs:
                        cv = self._convert_param_value(pvalue, pconfigs[pname])
                        if "inputs" not in workflow[nid]:
                            workflow[nid]["inputs"] = {}
                        workflow[nid]["inputs"][pname] = cv
        except Exception as e:
            logger.warning(f"注入用户参数失败: {e}")

    @staticmethod
    def _convert_param_value(value: Any, param_config: dict) -> Any:
        """转换参数值到正确的类型"""
        ptype = param_config.get("type", "string")
        try:
            if ptype == "number":
                if "." in str(value):
                    return float(value)
                return int(value)
            elif ptype == "boolean":
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes", "on")
                return bool(value)
            elif ptype == "select":
                opts = param_config.get("options", [])
                if value not in opts:
                    return param_config.get("default", opts[0] if opts else value)
                return value
            return str(value)
        except Exception:
            return value

    # ==================== 参数解析 ====================

    def parse_lora_params(self, params: List[str]) -> Tuple[List[str], List[dict]]:
        """LoRA 参数解析"""
        pattern = r"^lora:([^:!]+)(?::([0-9.]+))?(?:!([0-9.]+))?$"
        non_lora = []
        lora_list = []
        for param in params:
            m = re.match(pattern, param.strip(), re.IGNORECASE)
            if not m:
                non_lora.append(param)
                continue
            user_input = m.group(1).strip().lower()
            sm_str = m.group(2)
            sc_str = m.group(3)
            matched = None
            exact_key = None
            if user_input in self.lora_name_map:
                exact_key = user_input
                matched = self.lora_name_map[user_input]
            else:
                fuzzy = [k for k, (_, d) in self.lora_name_map.items()
                         if user_input in k or user_input in d.lower()]
                if len(fuzzy) == 1:
                    exact_key = fuzzy[0]
                    matched = self.lora_name_map[exact_key]
                elif len(fuzzy) > 1:
                    descs = [self.lora_name_map[k][1] for k in fuzzy]
                    raise ValueError(f"找到多个匹配的LoRA：{', '.join(descs)}（输入：{user_input}）")
            if not matched:
                avails = list(set(v[1] for v in self.lora_name_map.values() if v[1]))
                raise ValueError(f"未找到LoRA：{user_input}（可用：{', '.join(avails)}）")
            
            fn, desc = matched
            def _parse(s, default):
                if s is None:
                    return default
                v = float(s.strip())
                if not (self.min_lora_strength <= v <= self.max_lora_strength):
                    raise ValueError(f"LoRA强度需在{self.min_lora_strength}-{self.max_lora_strength}之间")
                return v
            sm = _parse(sm_str, self.default_lora_strength_model)
            sc = _parse(sc_str, self.default_lora_strength_clip)
            lora_list.append({"name": desc, "filename": fn, "strength_model": sm, "strength_clip": sc})
        
        if len(lora_list) > self.max_lora_count:
            raise ValueError(f"单次最多{self.max_lora_count}个LoRA")
        return non_lora, lora_list

    def parse_model_params(self, params: List[str]) -> Tuple[List[str], Optional[str]]:
        """模型参数解析"""
        pattern = r"^model:([^:]+)$"
        non_model = []
        selected = None
        for param in params:
            m = re.match(pattern, param.strip(), re.IGNORECASE)
            if not m:
                non_model.append(param)
                continue
            ui = m.group(1).strip().lower()
            matched = None
            if ui in self.model_name_map:
                matched = self.model_name_map[ui]
            else:
                fuzzy = [k for k, (_, d) in self.model_name_map.items()
                         if ui in k or ui in d.lower()]
                if len(fuzzy) == 1:
                    matched = self.model_name_map[fuzzy[0]]
                elif len(fuzzy) > 1:
                    descs = list(set(self.model_name_map[k][1] for k in fuzzy))
                    raise ValueError(f"找到多个匹配的模型：{', '.join(descs)}（输入：{ui}）")
            if not matched:
                avails = list(set(v[1] for v in self.model_name_map.values() if v[1]))
                raise ValueError(f"未找到模型：{ui}（可用：{', '.join(avails)}）")
            selected = matched[0]
        return non_model, selected

    def parse_workflow_params(self, args: List[str], config: dict) -> dict:
        """解析 workflow 参数"""
        params = {}
        node_configs = config.get("node_configs", {})
        mapping = {}
        for nid, nc in node_configs.items():
            for pname, pinfo in nc.items():
                if pname not in mapping:
                    mapping[pname] = []
                mapping[pname].append((nid, pname))
                for alias in pinfo.get("aliases", []):
                    if alias not in mapping:
                        mapping[alias] = []
                    mapping[alias].append((nid, pname))
        for arg in args:
            if ":" not in arg:
                continue
            key, value = arg.split(":", 1)
            matches = mapping.get(key, [])
            if len(matches) == 1:
                nid, pname = matches[0]
                params[f"{nid}:{pname}"] = value
            elif len(matches) > 1:
                exact = [(n, p) for n, p in matches
                         if key in node_configs.get(n, {}).get(p, {}).get("aliases", [])]
                if exact:
                    nid, pname = exact[0]
                else:
                    nid, pname = matches[0]
                params[f"{nid}:{pname}"] = value
            else:
                params[key] = value
        return params

    def validate_required_params(self, config: dict, params: dict) -> List[str]:
        """验证必需参数"""
        missing = []
        node_configs = config.get("node_configs", {})
        for nid, nc in node_configs.items():
            for pname, pinfo in nc.items():
                if pinfo.get("required", False):
                    key = f"{nid}:{pname}"
                    if key not in params and pname not in params:
                        exists = False
                        for pk in params:
                            if ":" in pk:
                                mn, mp = pk.split(":", 1)
                                if mn == nid and mp == pname:
                                    exists = True
                                    break
                        if not exists:
                            display = pname
                            aliases = pinfo.get("aliases", [])
                            if aliases:
                                display = aliases[0]
                            missing.append(display)
        return missing

    def validate_param_values(self, config: dict, params: dict) -> List[str]:
        """验证参数值有效性"""
        errors = []
        node_configs = config.get("node_configs", {})
        for key, value in params.items():
            if ":" not in key:
                continue
            nid, pname = key.split(":", 1)
            pinfo = node_configs.get(nid, {}).get(pname, {})
            if not pinfo:
                continue
            display = pname
            aliases = pinfo.get("aliases", [])
            if aliases:
                display = aliases[0]
            ptype = pinfo.get("type")
            try:
                if ptype == "number":
                    float(value)
                    minv = pinfo.get("min")
                    maxv = pinfo.get("max")
                    nv = float(value)
                    if minv is not None and nv < minv:
                        errors.append(f"参数「{display}」不能小于{minv}")
                    if maxv is not None and nv > maxv:
                        errors.append(f"参数「{display}」不能大于{maxv}")
                elif ptype == "select":
                    opts = pinfo.get("options", [])
                    if opts and value not in opts:
                        errors.append(f"参数「{display}」必须是：{'、'.join(opts)}")
                elif ptype == "boolean":
                    if isinstance(value, str):
                        if value.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                            errors.append(f"参数「{display}」必须是布尔值")
            except Exception as e:
                errors.append(f"验证参数「{display}」出错：{str(e)}")
        return errors

    # ==================== 文件操作 ====================

    async def save_image_locally(self, server: ServerState, filename: str,
                                  prompt: str = "", user_id: str = "",
                                  subfolder: str = "", file_type: str = "output") -> Optional[str]:
        """从 ComfyUI 下载并保存文件到本地"""
        if not self.enable_auto_save:
            return None
        try:
            url = await self.get_image_url(server, filename, subfolder=subfolder, file_type=file_type)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
            
            now = datetime.now()
            auto_path = Path(self.auto_save_dir)
            save_dir = auto_path / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            save_dir.mkdir(parents=True, exist_ok=True)
            
            ts = now.strftime("%Y%m%d_%H%M%S_")
            orig = filename
            valid_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp',
                          '.glb', '.gltf', '.obj', '.mp3', '.wav', '.ogg',
                          '.mp4', '.avi', '.mov', '.webm', '.mkv'}
            has_ext = any(orig.lower().endswith(e) for e in valid_exts)
            if not has_ext:
                orig += '.png'
            saved = ts + orig
            path = save_dir / saved
            
            def _write():
                with open(path, 'wb') as f:
                    f.write(data)
            await asyncio.get_event_loop().run_in_executor(None, _write)
            logger.info(f"文件已自动保存: {path}")
            
            if user_id:
                asyncio.create_task(self._record_image_generation(saved, user_id))
            return saved
        except Exception as e:
            logger.error(f"自动保存文件失败: {str(e)}")
            return None

    async def save_input_image_permanently(self, img_path: str, category: str,
                                            user_id: str = "",
                                            workflow_name: str = "",
                                            image_index: int = 0) -> Optional[str]:
        """永久保存输入图片到本地"""
        if not self.enable_auto_save:
            return None
        try:
            now = datetime.now()
            auto_path = Path(self.auto_save_dir)
            if category == "img2img":
                subdir = auto_path / "img2img_inputs"
            elif category == "workflow":
                subdir = auto_path / "workflow_inputs" / workflow_name
            else:
                subdir = auto_path / "inputs"
            save_dir = subdir / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            save_dir.mkdir(parents=True, exist_ok=True)
            
            ts = now.strftime("%Y%m%d_%H%M%S")
            ext = os.path.splitext(img_path)[1]
            if category == "img2img":
                saved_fn = f"{ts}_img2img{ext}"
            elif category == "workflow":
                saved_fn = f"{ts}_{workflow_name}_img{image_index+1}{ext}"
            else:
                saved_fn = f"{ts}_input{ext}"
            save_path = save_dir / saved_fn
            
            def _copy():
                shutil.copy2(img_path, save_path)
            await asyncio.get_event_loop().run_in_executor(None, _copy)
            return str(save_path)
        except Exception as e:
            logger.error(f"永久保存输入图片失败: {str(e)}")
            return None

    async def download_file(self, url: str, timeout: int = 120) -> Optional[bytes]:
        """从 URL 下载文件"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception as e:
            logger.warning(f"文件下载失败: {e}")
        return None

    async def create_zip(self, image_files: List[str], user_id: str) -> Optional[str]:
        """创建图片压缩包，返回本地路径"""
        if not image_files:
            return None
        try:
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            fn = f"comfyui_images_{user_id}_{ts}.zip"
            auto_path = Path(self.auto_save_dir)
            zip_path = auto_path / fn
            
            def _zip():
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for f in image_files:
                        if os.path.exists(f):
                            zf.write(f, os.path.basename(f))
            await asyncio.get_event_loop().run_in_executor(None, _zip)
            logger.info(f"压缩包已创建: {zip_path}")
            return str(zip_path)
        except Exception as e:
            logger.error(f"创建压缩包失败: {e}")
            return None

    def _schedule_cleanup(self, file_path: str, delay: int = 10):
        """安排延迟清理临时文件"""
        async def _cleanup():
            await asyncio.sleep(delay)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"临时文件已清理: {file_path}")
            except Exception as e:
                logger.warning(f"清理临时文件失败: {e}")
        asyncio.create_task(_cleanup())

    async def get_today_images(self, user_id: Optional[str] = None) -> List[str]:
        """获取今日生成的图片文件列表"""
        try:
            now = datetime.now()
            auto_path = Path(self.auto_save_dir)
            today_dir = auto_path / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
            if not today_dir.exists():
                return []
            files = []
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                files.extend(today_dir.glob(ext))
            if self.only_own_images and user_id:
                user_imgs = set(await self._get_user_images_today(user_id))
                files = [f for f in files if f.name in user_imgs]
            return [str(f) for f in files]
        except Exception as e:
            logger.error(f"获取今日图片列表失败: {e}")
            return []

    # ==================== 数据库 ====================

    async def _init_database(self):
        try:
            db_dir = os.path.dirname(self.db_path)
            loop = asyncio.get_event_loop()
            if os.path.exists(db_dir) and not os.path.isdir(db_dir):
                backup = f"{db_dir}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                await loop.run_in_executor(None, os.rename, db_dir, backup)
                await loop.run_in_executor(None, os.makedirs, db_dir, True)
            elif not os.path.exists(db_dir):
                await loop.run_in_executor(None, os.makedirs, db_dir, True)
            
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''CREATE TABLE IF NOT EXISTS user_downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    download_date TEXT NOT NULL,
                    download_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, download_date))''')
                await conn.execute('''CREATE TABLE IF NOT EXISTS image_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    generate_date TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                await conn.commit()
            logger.info(f"数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    async def check_download_limit(self, user_id: str) -> Tuple[bool, int]:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''INSERT OR IGNORE INTO user_downloads
                    (user_id, download_date, download_count) VALUES (?, ?, 0)''', (user_id, today))
                await conn.commit()
                cur = await conn.execute('''SELECT download_count FROM user_downloads
                    WHERE user_id=? AND download_date=?''', (user_id, today))
                row = await cur.fetchone()
                cnt = row[0] if row else 0
                return cnt < self.daily_download_limit, cnt
        except Exception as e:
            logger.error(f"检查下载限制失败: {e}")
            return False, 0

    async def increment_download_count(self, user_id: str):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''INSERT OR IGNORE INTO user_downloads
                    (user_id, download_date, download_count) VALUES (?, ?, 0)''', (user_id, today))
                await conn.execute('''UPDATE user_downloads SET download_count=download_count+1,
                    updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND download_date=?''', (user_id, today))
                await conn.commit()
        except Exception as e:
            logger.error(f"更新下载次数失败: {e}")

    async def _record_image_generation(self, filename: str, user_id: str):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute('''INSERT INTO image_records
                    (filename, user_id, generate_date) VALUES (?, ?, ?)''', (filename, user_id, today))
                await conn.commit()
        except Exception as e:
            logger.error(f"记录图片生成信息失败: {e}")

    async def _get_user_images_today(self, user_id: str) -> List[str]:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            async with aiosqlite.connect(self.db_path) as conn:
                cur = await conn.execute('''SELECT filename FROM image_records
                    WHERE user_id=? AND generate_date=?''', (user_id, today))
                return [row[0] for row in await cur.fetchall()]
        except Exception as e:
            logger.error(f"获取用户图片列表失败: {e}")
            return []

    # ==================== 错误解析 ====================

    @staticmethod
    def is_model_not_found_error(error_msg: str) -> bool:
        return "value_not_in_list" in error_msg and "ckpt_name" in error_msg and "not in" in error_msg

    @staticmethod
    def is_lora_not_found_error(error_msg: str) -> bool:
        return "value_not_in_list" in error_msg and "lora_name" in error_msg and "not in" in error_msg

    @staticmethod
    def is_node_not_found_error(error_msg: str) -> bool:
        msg = error_msg.lower()
        return ("does not exist" in msg or "not found" in msg) and "node" in msg

    @staticmethod
    def extract_model_name_from_error(error_msg: str) -> Optional[str]:
        m = re.search(r"ckpt_name:\s*['\"]([^'\"]+)['\"]\s*not in", error_msg)
        return m.group(1) if m else None

    @staticmethod
    def extract_lora_name_from_error(error_msg: str) -> Optional[str]:
        m = re.search(r"lora_name:\s*['\"]([^'\"]+)['\"]\s*not in", error_msg)
        return m.group(1) if m else None

    @staticmethod
    def extract_available_items_from_error(error_msg: str) -> Optional[str]:
        m = re.search(r"not in\s*\[([^\]]+)\]", error_msg)
        if m:
            items = [x.strip().strip("'\"") for x in m.group(1).split(",")]
            return ", ".join(items)
        return None

    @staticmethod
    def extract_node_name_from_error(error_msg: str) -> Optional[str]:
        patterns = [
            r"node\s+([^\s]+(?:\s+[^\s]+)*?)\s+does not exist",
            r"node\s+([^.]+)\s+does not exist"
        ]
        for p in patterns:
            m = re.search(p, error_msg, re.IGNORECASE)
            if m:
                return re.sub(r'\s+', ' ', m.group(1)).strip()
        return None
