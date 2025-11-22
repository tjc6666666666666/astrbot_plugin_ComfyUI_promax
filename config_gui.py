#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI AI 绘图机器人配置管理界面
Flask Web GUI for managing ComfyUI workflows and configurations
"""

import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from flask import Flask, render_template_string, request, jsonify, redirect, url_for, flash, send_file
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'comfyui_config_gui_secret_key_2024'

# 配置路径
CONFIG_DIR = Path(__file__).parent
WORKFLOW_DIR = CONFIG_DIR / "workflow"
MAIN_CONFIG_FILE = CONFIG_DIR / "config.json"

# 确保目录存在
WORKFLOW_DIR.mkdir(exist_ok=True)


class ConfigManager:
    """配置管理器"""
    
    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.workflow_dir = WORKFLOW_DIR
        self.main_config_file = MAIN_CONFIG_FILE
        
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
            "max_concurrent_tasks_per_user": 3
        }
    
    def get_workflows(self) -> List[Dict[str, Any]]:
        """获取所有工作流列表"""
        workflows = []
        
        if not self.workflow_dir.exists():
            return workflows
            
        for workflow_name in os.listdir(self.workflow_dir):
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


config_manager = ConfigManager()


@app.route('/')
def index():
    """主页 - 显示所有工作流"""
    workflows = config_manager.get_workflows()
    
    html_template = """
{% extends "base.html" %}

{% block title %}工作流管理 - ComfyUI 配置{% endblock %}

{% block page_title %}工作流管理{% endblock %}

{% block page_actions %}
    <div class="btn-group" role="group">
        <a href="{{ url_for('workflow_new') }}" class="btn btn-primary">
            <i class="bi bi-plus-circle"></i>
            新建工作流
        </a>
        <button type="button" class="btn btn-outline-secondary" onclick="location.reload()">
            <i class="bi bi-arrow-clockwise"></i>
            刷新
        </button>
    </div>
{% endblock %}

{% block content %}
<div class="row">
    {% if workflows %}
        {% for workflow in workflows %}
        <div class="col-lg-4 col-md-6 mb-4">
            <div class="card workflow-card h-100">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <h5 class="card-title mb-0">
                        <i class="bi bi-diagram-3-fill text-primary"></i>
                        {{ workflow.config.name }}
                    </h5>
                    <span class="badge bg-secondary">{{ workflow.config.version }}</span>
                </div>
                <div class="card-body">
                    <p class="card-text text-muted">{{ workflow.config.description or '暂无描述' }}</p>
                    
                    <div class="mb-3">
                        <small class="text-muted">
                            <i class="bi bi-terminal"></i>
                            前缀: <code>{{ workflow.config.prefix }}</code>
                        </small>
                    </div>
                    
                    <div class="mb-3">
                        <small class="text-muted">
                            <i class="bi bi-person"></i>
                            作者: {{ workflow.config.author }}
                        </small>
                    </div>
                    
                    <div class="mb-3">
                        <small class="text-muted">
                            <i class="bi bi-diagram-2"></i>
                            配置节点: {{ workflow.config.configurable_nodes|length }} 个
                        </small>
                    </div>
                    
                    <div class="mb-3">
                        <small class="text-muted">
                            <i class="bi bi-box-arrow-in-right"></i>
                            输入节点: {{ workflow.config.input_nodes|length }} 个
                        </small>
                    </div>
                    
                    <div class="mb-3">
                        <small class="text-muted">
                            <i class="bi bi-box-arrow-right"></i>
                            输出节点: {{ workflow.config.output_nodes|length }} 个
                        </small>
                    </div>
                </div>
                <div class="card-footer">
                    <div class="btn-group w-100" role="group">
                        <a href="{{ url_for('workflow_detail', workflow_name=workflow.name) }}" 
                           class="btn btn-outline-primary btn-sm">
                            <i class="bi bi-eye"></i>
                            查看
                        </a>
                        <a href="{{ url_for('workflow_edit', workflow_name=workflow.name) }}" 
                           class="btn btn-outline-secondary btn-sm">
                            <i class="bi bi-pencil"></i>
                            编辑
                        </a>
                        <button type="button" class="btn btn-outline-danger btn-sm" 
                                data-bs-toggle="modal" data-bs-target="#deleteModal"
                                data-workflow-name="{{ workflow.name }}"
                                data-workflow-display-name="{{ workflow.config.name }}">
                            <i class="bi bi-trash"></i>
                            删除
                        </button>
                    </div>
                </div>
            </div>
        </div>
        {% endfor %}
    {% else %}
        <div class="col-12">
            <div class="text-center py-5">
                <i class="bi bi-diagram-3 display-1 text-muted"></i>
                <h3 class="mt-3 text-muted">暂无工作流</h3>
                <p class="text-muted">
                    还没有创建任何工作流配置。<br>
                    点击"新建工作流"按钮开始创建第一个工作流。
                </p>
                <a href="{{ url_for('workflow_new') }}" class="btn btn-primary btn-lg">
                    <i class="bi bi-plus-circle"></i>
                    新建工作流
                </a>
            </div>
        </div>
    {% endif %}
</div>

<!-- 删除确认模态框 -->
<div class="modal fade" id="deleteModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">确认删除</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <p>确定要删除工作流 "<span id="deleteWorkflowDisplayName"></span>" 吗？</p>
                <p class="text-danger">
                    <i class="bi bi-exclamation-triangle"></i>
                    此操作不可撤销，工作流的所有配置和文件将被永久删除。
                </p>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                <form id="deleteForm" method="post" style="display: inline;">
                    <button type="submit" class="btn btn-danger">
                        <i class="bi bi-trash"></i>
                        确认删除
                    </button>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    const deleteModal = document.getElementById('deleteModal');
    if (deleteModal) {
        deleteModal.addEventListener('show.bs.modal', function(event) {
            const button = event.relatedTarget;
            const workflowName = button.getAttribute('data-workflow-name');
            const workflowDisplayName = button.getAttribute('data-workflow-display-name');
            
            document.getElementById('deleteWorkflowDisplayName').textContent = workflowDisplayName;
            document.getElementById('deleteForm').action = `/workflow/${workflowName}/delete`;
        });
    }
});
</script>
{% endblock %}
    """
    
    return render_template_string(html_template, workflows=workflows)


@app.route('/main_config')
def main_config():
    """主配置页面"""
    config = config_manager.load_main_config()
    
    html_template = """
{% extends "base.html" %}

{% block title %}主配置 - ComfyUI 配置{% endblock %}

{% block page_title %}主配置{% endblock %}

{% block page_actions %}
    <button type="submit" form="mainConfigForm" class="btn btn-success">
        <i class="bi bi-check-circle"></i>
        保存配置
    </button>
{% endblock %}

{% block content %}
<form id="mainConfigForm" method="post" action="{{ url_for('save_main_config') }}">
    <!-- 服务器配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-server text-primary"></i>
            ComfyUI 服务器配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">服务器列表</label>
            <div class="col-sm-10">
                <div id="serverList">
                    {% for server in config.comfyui_url %}
                    <div class="input-group mb-2">
                        <input type="text" class="form-control" name="comfyui_url" 
                               value="{{ server }}" placeholder="URL,名称 (例: http://127.0.0.1:8188,本地服务器)">
                        <button type="button" class="btn btn-outline-danger" onclick="removeServer(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                    {% endfor %}
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addServer()">
                    <i class="bi bi-plus"></i>
                    添加服务器
                </button>
            </div>
        </div>
    </div>

    <!-- 基础生成配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-image text-success"></i>
            基础生成配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">默认模型</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="ckpt_name" 
                       value="{{ config.ckpt_name or '' }}" placeholder="模型文件名">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">采样器</label>
            <div class="col-sm-10">
                <select class="form-select" name="sampler_name">
                    <option value="euler" {% if config.sampler_name == 'euler' %}selected{% endif %}>euler</option>
                    <option value="euler_ancestral" {% if config.sampler_name == 'euler_ancestral' %}selected{% endif %}>euler_ancestral</option>
                    <option value="dpmpp_2m" {% if config.sampler_name == 'dpmpp_2m' %}selected{% endif %}>dpmpp_2m</option>
                    <option value="dpmpp_sde" {% if config.sampler_name == 'dpmpp_sde' %}selected{% endif %}>dpmpp_sde</option>
                    <option value="ddim" {% if config.sampler_name == 'ddim' %}selected{% endif %}>ddim</option>
                    <option value="uni_pc" {% if config.sampler_name == 'uni_pc' %}selected{% endif %}>uni_pc</option>
                </select>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">调度器</label>
            <div class="col-sm-10">
                <select class="form-select" name="scheduler">
                    <option value="simple" {% if config.scheduler == 'simple' %}selected{% endif %}>simple</option>
                    <option value="karras" {% if config.scheduler == 'karras' %}selected{% endif %}>karras</option>
                    <option value="exponential" {% if config.scheduler == 'exponential' %}selected{% endif %}>exponential</option>
                    <option value="normal" {% if config.scheduler == 'normal' %}selected{% endif %}>normal</option>
                    <option value="sgm_uniform" {% if config.scheduler == 'sgm_uniform' %}selected{% endif %}>sgm_uniform</option>
                </select>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">CFG 系数</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="cfg" 
                       value="{{ config.cfg or 7.0 }}" step="0.1" min="1" max="30">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">采样步数</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="num_inference_steps" 
                       value="{{ config.num_inference_steps or 30 }}" min="1" max="150">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">默认宽度</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="default_width" 
                       value="{{ config.default_width or 1024 }}" min="64" max="4096">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">默认高度</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="default_height" 
                       value="{{ config.default_height or 1024 }}" min="64" max="4096">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">默认噪声系数</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="default_denoise" 
                       value="{{ config.default_denoise or 0.7 }}" step="0.1" min="0" max="1">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">随机种子</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="seed" 
                       value="{{ config.seed or '随机' }}" placeholder="随机 或 具体数字">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">负面提示词</label>
            <div class="col-sm-10">
                <textarea class="form-control" name="negative_prompt" rows="3">{{ config.negative_prompt or '' }}</textarea>
            </div>
        </div>
    </div>

    <!-- 批量配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-stack text-warning"></i>
            批量配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">文生图默认批量</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="txt2img_batch_size" 
                       value="{{ config.txt2img_batch_size or 1 }}" min="1" max="10">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">图生图默认批量</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="img2img_batch_size" 
                       value="{{ config.img2img_batch_size or 1 }}" min="1" max="10">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">文生图最大批量</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="max_txt2img_batch" 
                       value="{{ config.max_txt2img_batch or 6 }}" min="1" max="20">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">图生图最大批量</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="max_img2img_batch" 
                       value="{{ config.max_img2img_batch or 6 }}" min="1" max="20">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">任务队列最大</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="max_task_queue" 
                       value="{{ config.max_task_queue or 10 }}" min="1" max="100">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">每用户最大并发</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="max_concurrent_tasks_per_user" 
                       value="{{ config.max_concurrent_tasks_per_user or 3 }}" min="1" max="20">
            </div>
        </div>
    </div>

    <!-- 限制配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-shield-check text-info"></i>
            限制配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">最小宽度</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="min_width" 
                       value="{{ config.min_width or 64 }}" min="32" max="512">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">最大宽度</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="max_width" 
                       value="{{ config.max_width or 2000 }}" min="512" max="4096">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">最小高度</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="min_height" 
                       value="{{ config.min_height or 64 }}" min="32" max="512">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">最大高度</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="max_height" 
                       value="{{ config.max_height or 2000 }}" min="512" max="4096">
            </div>
        </div>
    </div>

    <!-- 时间配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-clock text-secondary"></i>
            时间配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">开放时间</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="open_time_ranges" 
                       value="{{ config.open_time_ranges or '7:00-8:00,11:00-14:00,17:00-24:00' }}" 
                       placeholder="格式: 7:00-8:00,11:00-14:00,17:00-24:00">
            </div>
        </div>
    </div>

    <!-- 模型和 LoRA 配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-puzzle text-danger"></i>
            模型和 LoRA 配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">模型列表</label>
            <div class="col-sm-10">
                <div id="modelList">
                    {% for model in config.model_config %}
                    <div class="input-group mb-2">
                        <input type="text" class="form-control" name="model_config" 
                               value="{{ model }}" placeholder="文件名,描述 (例: model.safetensors,写实风格)">
                        <button type="button" class="btn btn-outline-danger" onclick="removeModel(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                    {% endfor %}
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addModel()">
                    <i class="bi bi-plus"></i>
                    添加模型
                </button>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">LoRA 列表</label>
            <div class="col-sm-10">
                <div id="loraList">
                    {% for lora in config.lora_config %}
                    <div class="input-group mb-2">
                        <input type="text" class="form-control" name="lora_config" 
                               value="{{ lora }}" placeholder="文件名,描述 (例: lora.safetensors,可爱风格)">
                        <button type="button" class="btn btn-outline-danger" onclick="removeLora(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                    {% endfor %}
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addLora()">
                    <i class="bi bi-plus"></i>
                    添加 LoRA
                </button>
            </div>
        </div>
    </div>

    <!-- 功能开关 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-toggle-on text-primary"></i>
            功能开关
        </h5>
        
        <div class="row mb-3">
            <div class="col-sm-2"></div>
            <div class="col-sm-10">
                <div class="form-check form-switch mb-2">
                    <input class="form-check-input" type="checkbox" name="enable_translation" 
                           {% if config.enable_translation %}checked{% endif %}>
                    <label class="form-check-label">启用翻译功能</label>
                </div>
                
                <div class="form-check form-switch mb-2">
                    <input class="form-check-input" type="checkbox" name="enable_image_encrypt" 
                           {% if config.enable_image_encrypt %}checked{% endif %}>
                    <label class="form-check-label">启用图像加密</label>
                </div>
                
                <div class="form-check form-switch mb-2">
                    <input class="form-check-input" type="checkbox" name="enable_help_image" 
                           {% if config.enable_help_image %}checked{% endif %}>
                    <label class="form-check-label">启用帮助图片</label>
                </div>
                
                <div class="form-check form-switch mb-2">
                    <input class="form-check-input" type="checkbox" name="enable_auto_save" 
                           {% if config.enable_auto_save %}checked{% endif %}>
                    <label class="form-check-label">启用自动保存</label>
                </div>
                
                <div class="form-check form-switch mb-2">
                    <input class="form-check-input" type="checkbox" name="enable_output_zip" 
                           {% if config.enable_output_zip %}checked{% endif %}>
                    <label class="form-check-label">启用输出压缩包</label>
                </div>
                
                <div class="form-check form-switch mb-2">
                    <input class="form-check-input" type="checkbox" name="only_own_images" 
                           {% if config.only_own_images %}checked{% endif %}>
                    <label class="form-check-label">仅限自己图片</label>
                </div>
            </div>
        </div>
    </div>

    <!-- 存储配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-hdd text-success"></i>
            存储配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">自动保存目录</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="auto_save_directory" 
                       value="{{ config.auto_save_directory or 'output' }}">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">数据库目录</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="db_directory" 
                       value="{{ config.db_directory or 'output' }}">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">每日下载限制</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="daily_download_limit" 
                       value="{{ config.daily_download_limit or 1 }}" min="1" max="100">
            </div>
        </div>
    </div>

    <!-- 高级配置 -->
    <div class="config-section p-4">
        <h5 class="mb-4">
            <i class="bi bi-gear-fill text-dark"></i>
            高级配置
        </h5>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">帮助服务器端口</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="help_server_port" 
                       value="{{ config.help_server_port or 8080 }}" min="1024" max="65535">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">队列检查延迟</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="queue_check_delay" 
                       value="{{ config.queue_check_delay or 30 }}" min="10" max="120">
                <div class="form-text">秒</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">队列检查间隔</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="queue_check_interval" 
                       value="{{ config.queue_check_interval or 5 }}" min="3" max="30">
                <div class="form-text">秒</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">空队列重试次数</label>
            <div class="col-sm-10">
                <input type="number" class="form-control" name="empty_queue_max_retry" 
                       value="{{ config.empty_queue_max_retry or 2 }}" min="1" max="10">
            </div>
        </div>
    </div>
</form>
{% endblock %}

{% block scripts %}
<script>
function addServer() {
    const serverList = document.getElementById('serverList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <input type="text" class="form-control" name="comfyui_url" 
               placeholder="URL,名称 (例: http://127.0.0.1:8188,本地服务器)">
        <button type="button" class="btn btn-outline-danger" onclick="removeServer(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    serverList.appendChild(div);
}

function removeServer(button) {
    button.parentElement.remove();
}

function addModel() {
    const modelList = document.getElementById('modelList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <input type="text" class="form-control" name="model_config" 
               placeholder="文件名,描述 (例: model.safetensors,写实风格)">
        <button type="button" class="btn btn-outline-danger" onclick="removeModel(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    modelList.appendChild(div);
}

function removeModel(button) {
    button.parentElement.remove();
}

function addLora() {
    const loraList = document.getElementById('loraList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <input type="text" class="form-control" name="lora_config" 
               placeholder="文件名,描述 (例: lora.safetensors,可爱风格)">
        <button type="button" class="btn btn-outline-danger" onclick="removeLora(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    loraList.appendChild(div);
}

function removeLora(button) {
    button.parentElement.remove();
}
</script>
{% endblock %}
    """
    
    return render_template_string(html_template, config=config)


@app.route('/save_main_config', methods=['POST'])
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
        
        # 处理布尔字段
        config['enable_translation'] = config.get('enable_translation') == 'on'
        config['enable_image_encrypt'] = config.get('enable_image_encrypt') == 'on'
        config['enable_help_image'] = config.get('enable_help_image') == 'on'
        config['enable_auto_save'] = config.get('enable_auto_save') == 'on'
        config['enable_output_zip'] = config.get('enable_output_zip') == 'on'
        config['only_own_images'] = config.get('only_own_images') == 'on'
        
        # 处理数组字段
        comfyui_urls = request.form.getlist('comfyui_url')
        config['comfyui_url'] = [url.strip() for url in comfyui_urls if url.strip()]
        
        lora_configs = request.form.getlist('lora_config')
        config['lora_config'] = [lora.strip() for lora in lora_configs if lora.strip()]
        
        model_configs = request.form.getlist('model_config')
        config['model_config'] = [model.strip() for model in model_configs if model.strip()]
        
        if config_manager.save_main_config(config):
            flash('主配置保存成功！', 'success')
        else:
            flash('主配置保存失败！', 'error')
            
        return redirect(url_for('main_config'))
        
    except Exception as e:
        logger.error(f"保存主配置失败: {e}")
        flash(f'保存失败: {str(e)}', 'error')
        return redirect(url_for('main_config'))


@app.route('/workflow/<workflow_name>')
def workflow_detail(workflow_name):
    """工作流详情页面"""
    workflows = config_manager.get_workflows()
    workflow = None
    
    for wf in workflows:
        if wf['name'] == workflow_name:
            workflow = wf
            break
    
    if not workflow:
        flash('工作流不存在！', 'error')
        return redirect(url_for('index'))
    
    html_template = """
{% extends "base.html" %}

{% block title %}{{ workflow.config.name }} - 工作流详情{% endblock %}

{% block page_title %}{{ workflow.config.name }}{% endblock %}

{% block page_actions %}
    <div class="btn-group" role="group">
        <a href="{{ url_for('workflow_edit', workflow_name=workflow.name) }}" class="btn btn-outline-primary">
            <i class="bi bi-pencil"></i>
            编辑
        </a>
        <a href="{{ url_for('index') }}" class="btn btn-outline-secondary">
            <i class="bi bi-arrow-left"></i>
            返回列表
        </a>
    </div>
{% endblock %}

{% block content %}
<!-- 基本信息 -->
<div class="config-section p-4 mb-4">
    <h5 class="mb-4">
        <i class="bi bi-info-circle text-primary"></i>
        基本信息
    </h5>
    
    <div class="row">
        <div class="col-md-6">
            <table class="table table-borderless">
                <tr>
                    <td class="text-muted" style="width: 120px;">工作流名称:</td>
                    <td><strong>{{ workflow.config.name }}</strong></td>
                </tr>
                <tr>
                    <td class="text-muted">文件夹名:</td>
                    <td><code>{{ workflow.name }}</code></td>
                </tr>
                <tr>
                    <td class="text-muted">前缀:</td>
                    <td><code>{{ workflow.config.prefix }}</code></td>
                </tr>
                <tr>
                    <td class="text-muted">版本:</td>
                    <td><span class="badge bg-secondary">{{ workflow.config.version }}</span></td>
                </tr>
                <tr>
                    <td class="text-muted">作者:</td>
                    <td>{{ workflow.config.author }}</td>
                </tr>
            </table>
        </div>
        <div class="col-md-6">
            <table class="table table-borderless">
                <tr>
                    <td class="text-muted" style="width: 120px;">输入节点:</td>
                    <td>
                        {% for node in workflow.config.input_nodes %}
                            <span class="badge bg-info">{{ node }}</span>
                        {% endfor %}
                    </td>
                </tr>
                <tr>
                    <td class="text-muted">输出节点:</td>
                    <td>
                        {% for node in workflow.config.output_nodes %}
                            <span class="badge bg-success">{{ node }}</span>
                        {% endfor %}
                    </td>
                </tr>
                <tr>
                    <td class="text-muted">可配置节点:</td>
                    <td>
                        {% for node in workflow.config.configurable_nodes %}
                            <span class="badge bg-warning">{{ node }}</span>
                        {% endfor %}
                    </td>
                </tr>
            </table>
        </div>
    </div>
    
    {% if workflow.config.description %}
    <div class="mt-3">
        <h6>描述:</h6>
        <p class="text-muted">{{ workflow.config.description }}</p>
    </div>
    {% endif %}
</div>

<!-- 输入输出映射 -->
<div class="config-section p-4 mb-4">
    <h5 class="mb-4">
        <i class="bi bi-diagram-2 text-success"></i>
        输入输出映射
    </h5>
    
    <div class="row">
        <div class="col-md-6">
            <h6>输入映射</h6>
            {% if workflow.config.input_mappings %}
                {% for node_id, mapping in workflow.config.input_mappings.items() %}
                <div class="param-item mb-2">
                    <strong>节点 {{ node_id }}:</strong>
                    <br>
                    <small class="text-muted">
                        参数名: {{ mapping.parameter_name }}<br>
                        类型: {{ mapping.type }}<br>
                        {% if mapping.description %}描述: {{ mapping.description }}{% endif %}
                        {% if mapping.required %}<span class="badge bg-danger">必需</span>{% endif %}
                    </small>
                </div>
                {% endfor %}
            {% else %}
                <p class="text-muted">无输入映射</p>
            {% endif %}
        </div>
        
        <div class="col-md-6">
            <h6>输出映射</h6>
            {% if workflow.config.output_mappings %}
                {% for node_id, mapping in workflow.config.output_mappings.items() %}
                <div class="param-item mb-2">
                    <strong>节点 {{ node_id }}:</strong>
                    <br>
                    <small class="text-muted">
                        参数名: {{ mapping.parameter_name }}<br>
                        类型: {{ mapping.type }}<br>
                        {% if mapping.description %}描述: {{ mapping.description }}{% endif %}
                    </small>
                </div>
                {% endfor %}
            {% else %}
                <p class="text-muted">无输出映射</p>
            {% endif %}
        </div>
    </div>
</div>

<!-- 节点配置 -->
<div class="config-section p-4 mb-4">
    <h5 class="mb-4">
        <i class="bi bi-gear-fill text-warning"></i>
        节点配置
    </h5>
    
    {% if workflow.config.node_configs %}
        {% for node_id, node_config in workflow.config.node_configs.items() %}
        <div class="param-item mb-4">
            <h6 class="mb-3">
                <i class="bi bi-cpu"></i>
                节点 {{ node_id }}
            </h6>
            
            {% for param_name, param_config in node_config.items() %}
            <div class="mb-3">
                <div class="d-flex justify-content-between align-items-center">
                    <strong>{{ param_name }}</strong>
                    <span class="badge bg-{{ 'primary' if param_config.type == 'text' else 'success' if param_config.type == 'number' else 'info' if param_config.type == 'select' else 'warning' }}">
                        {{ param_config.type }}
                    </span>
                </div>
                
                <p class="text-muted mb-2">{{ param_config.description }}</p>
                
                <div class="row">
                    <div class="col-md-6">
                        <small class="text-muted">
                            默认值: <code>{{ param_config.default or '无' }}</code>
                        </small>
                    </div>
                    {% if param_config.required %}
                    <div class="col-md-6">
                        <small class="text-danger">
                            <i class="bi bi-asterisk"></i> 必需参数
                        </small>
                    </div>
                    {% endif %}
                </div>
                
                {% if param_config.options %}
                <div class="mt-2">
                    <small class="text-muted">可选值:</small>
                    <div class="mt-1">
                        {% for option in param_config.options %}
                            <span class="badge bg-light text-dark me-1">{{ option }}</span>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
                
                {% if param_config.min is defined or param_config.max is defined %}
                <div class="mt-2">
                    <small class="text-muted">
                        范围: 
                        {% if param_config.min is defined %}最小 {{ param_config.min }}{% endif %}
                        {% if param_config.max is defined %}最大 {{ param_config.max }}{% endif %}
                    </small>
                </div>
                {% endif %}
                
                {% if param_config.inject_models or param_config.inject_loras or param_config.inject_samplers or param_config.inject_schedulers %}
                <div class="mt-2">
                    <small class="text-muted">特殊功能:</small>
                    <div class="mt-1">
                        {% if param_config.inject_models %}
                            <span class="badge bg-info me-1">注入模型列表</span>
                        {% endif %}
                        {% if param_config.inject_loras %}
                            <span class="badge bg-success me-1">注入LoRA列表</span>
                        {% endif %}
                        {% if param_config.inject_samplers %}
                            <span class="badge bg-info me-1">注入采样器列表</span>
                        {% endif %}
                        {% if param_config.inject_schedulers %}
                            <span class="badge bg-info me-1">注入调度器列表</span>
                        {% endif %}
                    </div>
                </div>
                {% endif %}
                
                {% if param_config.aliases %}
                <div class="mt-2">
                    <small class="text-muted">别名:</small>
                    <div class="mt-1">
                        {% for alias in param_config.aliases %}
                            <span class="badge bg-secondary me-1">{{ alias }}</span>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% endfor %}
    {% else %}
        <p class="text-muted">无可配置节点</p>
    {% endif %}
</div>

<!-- Workflow JSON -->
<div class="config-section p-4">
    <h5 class="mb-4">
        <i class="bi bi-code-slash text-info"></i>
        Workflow JSON
    </h5>
    
    <div class="json-editor p-3">
        <pre><code>{{ workflow.workflow_json_pretty }}</code></pre>
    </div>
</div>

<!-- 配置文件路径 -->
<div class="config-section p-4">
    <h5 class="mb-4">
        <i class="bi bi-folder text-secondary"></i>
        文件路径
    </h5>
    
    <div class="row">
        <div class="col-md-6">
            <p><strong>配置文件:</strong></p>
            <code>{{ workflow.path }}/config.json</code>
        </div>
        <div class="col-md-6">
            <p><strong>Workflow文件:</strong></p>
            <code>{{ workflow.path }}/workflow.json</code>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
// 代码高亮
document.addEventListener('DOMContentLoaded', function() {
    const codeBlocks = document.querySelectorAll('pre code');
    codeBlocks.forEach(function(block) {
        // 简单的语法高亮
        let html = block.textContent;
        html = html.replace(/("([^"\\]|\\.)*")/g, '<span style="color: #d73a49;">$1</span>');
        html = html.replace(/(\b\d+\.?\d*\b)/g, '<span style="color: #005cc5;">$1</span>');
        html = html.replace(/\b(true|false|null)\b/g, '<span style="color: #d73a49;">$1</span>');
        block.innerHTML = html;
    });
});
</script>
{% endblock %}
    """
    
    return render_template_string(html_template, workflow=workflow)


@app.route('/workflow/<workflow_name>/edit')
def workflow_edit(workflow_name):
    """编辑工作流页面"""
    workflows = config_manager.get_workflows()
    workflow = None
    
    for wf in workflows:
        if wf['name'] == workflow_name:
            workflow = wf
            break
    
    if not workflow:
        flash('工作流不存在！', 'error')
        return redirect(url_for('index'))
    
    html_template = """
{% extends "base.html" %}

{% block title %}编辑 {{ workflow.config.name }} - 工作流配置{% endblock %}

{% block page_title %}编辑 {{ workflow.config.name }}{% endblock %}

{% block page_actions %}
    <div class="btn-group" role="group">
        <button type="submit" form="workflowForm" class="btn btn-success">
            <i class="bi bi-check-circle"></i>
            保存
        </button>
        <a href="{{ url_for('workflow_detail', workflow_name=workflow.name) }}" class="btn btn-outline-secondary">
            <i class="bi bi-x-circle"></i>
            取消
        </a>
    </div>
{% endblock %}

{% block content %}
<form id="workflowForm" method="post" action="{{ url_for('workflow_save', workflow_name=workflow.name) }}">
    <!-- 基本信息 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-info-circle text-primary"></i>
                基本信息
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div class="row mb-3">
            <label class="col-sm-2 col-form-label">工作流名称</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="name" 
                       value="{{ workflow.config.name }}" required>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">前缀</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="prefix" 
                       value="{{ workflow.config.prefix }}" required>
                <div class="form-text">用于调用此工作流的前缀，如: encrypt</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">描述</label>
            <div class="col-sm-10">
                <textarea class="form-control" name="description" rows="3">{{ workflow.config.description or '' }}</textarea>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">版本</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="version" 
                       value="{{ workflow.config.version }}">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">作者</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="author" 
                       value="{{ workflow.config.author }}">
            </div>
        </div>
        </div>
    </div>

    <!-- Workflow JSON -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-code-slash text-info"></i>
                Workflow JSON
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div class="mb-3">
            <label class="form-label">ComfyUI Workflow JSON</label>
            <textarea class="form-control json-editor" name="workflow" rows="15" id="workflowJson">{{ workflow.workflow_json_pretty|safe }}</textarea>
            <div class="form-text">
                这是 ComfyUI 的 workflow JSON 配置，可以从 ComfyUI 界面导出。
            </div>
        </div>
        
        <div class="mt-3">
            <button type="button" class="btn btn-outline-primary" onclick="parseWorkflowNodes()">
                <i class="bi bi-arrow-repeat"></i>
                解析节点
            </button>
            <div class="form-text">点击此按钮解析 JSON 中的所有节点，然后可以在下方选择节点</div>
        </div>
        </div>
    </div>

    <!-- 节点配置 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-diagram-2 text-success"></i>
                节点配置
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div class="row mb-3">
            <label class="col-sm-2 col-form-label">输入节点</label>
            <div class="col-sm-10">
                <div id="inputNodesList">
                    {% for node in workflow.config.input_nodes %}
                    <div class="input-group mb-2">
                        <select class="form-select node-selector" name="input_nodes" onchange="updateMappingPreview()">
                            <option value="">-- 选择节点 --</option>
                        </select>
                        <button type="button" class="btn btn-outline-danger" onclick="removeInputNode(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                    {% endfor %}
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addInputNode()">
                    <i class="bi bi-plus"></i>
                    添加输入节点
                </button>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">输出节点</label>
            <div class="col-sm-10">
                <div id="outputNodesList">
                    {% for node in workflow.config.output_nodes %}
                    <div class="input-group mb-2">
                        <select class="form-select node-selector" name="output_nodes" onchange="updateMappingPreview()">
                            <option value="">-- 选择节点 --</option>
                        </select>
                        <button type="button" class="btn btn-outline-danger" onclick="removeOutputNode(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                    {% endfor %}
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addOutputNode()">
                    <i class="bi bi-plus"></i>
                    添加输出节点
                </button>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">可配置节点</label>
            <div class="col-sm-10">
                <div id="configurableNodesList">
                    {% for node in workflow.config.configurable_nodes %}
                    <div class="input-group mb-2">
                        <select class="form-select node-selector" name="configurable_nodes">
                            <option value="">-- 选择节点 --</option>
                        </select>
                        <button type="button" class="btn btn-outline-danger" onclick="removeConfigurableNode(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                    {% endfor %}
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addConfigurableNode()">
                    <i class="bi bi-plus"></i>
                    添加可配置节点
                </button>
            </div>
        </div>
        </div>
    </div>

    <!-- 自动映射预览 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-arrow-left-right text-warning"></i>
                自动映射预览
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div class="alert alert-info">
            <i class="bi bi-info-circle"></i>
            <strong>自动映射规则：</strong>输入节点将自动映射为输入参数，输出节点将自动映射为输出参数。无需手动配置。
        </div>
        
        <!-- 输入映射预览 -->
        <div class="row mb-4">
            <div class="col-12">
                <h6 class="mb-3">
                    <i class="bi bi-box-arrow-in-right"></i>
                    输入映射预览
                </h6>
                <div id="inputMappingsPreview" class="border rounded p-3 bg-light">
                    <p class="text-muted">暂无输入节点</p>
                </div>
            </div>
        </div>
        
        <!-- 输出映射预览 -->
        <div class="row">
            <div class="col-12">
                <h6 class="mb-3">
                    <i class="bi bi-box-arrow-right"></i>
                    输出映射预览
                </h6>
                <div id="outputMappingsPreview" class="border rounded p-3 bg-light">
                    <p class="text-muted">暂无输出节点</p>
                </div>
            </div>
        </div>
        </div>
    </div>

    <!-- 节点参数配置 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-gear-fill text-warning"></i>
                节点参数配置
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div id="nodeConfigsContainer">
            {% for node_id, node_config in workflow.config.node_configs.items() %}
            <div class="param-item mb-4" data-node-id="{{ node_id }}">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="mb-0">
                        <i class="bi bi-cpu"></i>
                        <span class="node-title" data-node-id="{{ node_id }}">节点 {{ node_id }}</span>
                    </h6>
                    <div>
                        <button type="button" class="btn btn-outline-secondary btn-sm me-2" onclick="toggleNodeConfig(this)" data-bs-toggle="tooltip" title="展开/折叠">
                            <i class="bi bi-chevron-down"></i>
                        </button>
                        <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeNodeConfig(this)">
                            <i class="bi bi-trash"></i>
                            删除节点配置
                        </button>
                    </div>
                </div>
                
                <div class="param-content" style="display: none;">
                    <div class="row mb-3">
                        <div class="col-12">
                            <div class="param-params-container">
                                {% for param_name, param_config in node_config.items() %}
                                <div class="param-config-item mb-3 p-3 border rounded" data-param-name="{{ param_name }}">
                                    <div class="d-flex justify-content-between align-items-center mb-2">
                                        <strong>{{ param_name }}</strong>
                                        <div>
                                            <button type="button" class="btn btn-outline-secondary btn-sm me-2" onclick="toggleParamConfig(this)" data-bs-toggle="tooltip" title="展开/折叠">
                                                <i class="bi bi-chevron-down"></i>
                                            </button>
                                            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeParamConfig(this)">
                                                <i class="bi bi-trash"></i>
                                            </button>
                                        </div>
                                    </div>
                                    
                                    <div class="param-config-content" style="display: none;">
                                        <div class="row">
                                            <div class="col-md-6 mb-2">
                                                <label class="form-label">参数名</label>
                                                <input type="text" class="form-control" name="param_name" 
                                                       value="{{ param_name }}" readonly>
                                            </div>
                                            <div class="col-md-6 mb-2">
                                                <label class="form-label">类型</label>
                                                <select class="form-select" name="param_type">
                                                    <option value="text" {% if param_config.type == 'text' %}selected{% endif %}>文本</option>
                                                    <option value="number" {% if param_config.type == 'number' %}selected{% endif %}>数字</option>
                                                    <option value="select" {% if param_config.type == 'select' %}selected{% endif %}>选择</option>
                                                    <option value="boolean" {% if param_config.type == 'boolean' %}selected{% endif %}>布尔值</option>
                                                </select>
                                            </div>
                                        </div>
                                        
                                        <div class="row">
                                            <div class="col-md-6 mb-2">
                                                <label class="form-label">默认值</label>
                                                <!-- 默认值输入框，会根据类型动态变化 -->
                                                <div class="param-default-container">
                                                    <input type="text" class="form-control param-default-text" name="param_default" 
                                                           value="{{ param_config.default if param_config.type in ['text', 'select'] else param_config.default or '' }}"
                                                           {% if param_config.type not in ['text', 'select'] %}style="display:none;"{% endif %}>
                                                    <input type="number" class="form-control param-default-number" name="param_default" 
                                                           value="{{ param_config.default if param_config.type == 'number' else '' }}" 
                                                           {% if param_config.type != 'number' %}style="display:none;"{% endif %} step="any">
                                                    <div class="form-check form-switch param-default-boolean" 
                                                         {% if param_config.type != 'boolean' %}style="display:none;"{% endif %}>
                                                        <input class="form-check-input" type="checkbox" name="param_default" 
                                                               {% if param_config.default == true %}checked{% endif %} onchange="updateBooleanLabel(this)">
                                                        <label class="form-check-label boolean-label">{% if param_config.default == true %}True{% else %}False{% endif %}</label>
                                                    </div>
                                                </div>
                                            </div>
                                            <div class="col-md-6 mb-2">
                                                <div class="form-check mt-4">
                                                    <input class="form-check-input" type="checkbox" name="param_required" 
                                                           {% if param_config.required %}checked{% endif %}>
                                                    <label class="form-check-label">必需参数</label>
                                                </div>
                                            </div>
                                        </div>
                                        
                                        <div class="mb-2">
                                            <label class="form-label">描述</label>
                                            <textarea class="form-control" name="param_description" rows="2">{{ param_config.description or '' }}</textarea>
                                        </div>
                                        
                                        <!-- 数字类型的范围设置 -->
                                        <div class="param-number-options" {% if param_config.type != 'number' %}style="display:none;"{% endif %}>
                                            <div class="row">
                                                <div class="col-md-6 mb-2">
                                                    <label class="form-label">最小值</label>
                                                    <input type="number" class="form-control" name="param_min" 
                                                           value="{{ param_config.min or '' }}" step="any">
                                                </div>
                                                <div class="col-md-6 mb-2">
                                                    <label class="form-label">最大值</label>
                                                    <input type="number" class="form-control" name="param_max" 
                                                           value="{{ param_config.max or '' }}" step="any">
                                                </div>
                                            </div>
                                        </div>
                                        
                                        <!-- 选择类型的选项设置 -->
                                        <div class="param-select-options" {% if param_config.type != 'select' %}style="display:none;"{% endif %}>
                                            <div class="mb-2">
                                                <label class="form-label">选项 (用逗号分隔)</label>
                                                <input type="text" class="form-control" name="param_options" 
                                                       value="{{ param_config.options | join(',') if param_config.options else '' }}"
                                                       placeholder="选项1,选项2,选项3">
                                            </div>
                                        </div>
                                        
                                        <div class="mb-2">
                                            <div class="d-flex justify-content-between align-items-center mb-2">
                                                <label class="form-label mb-0">特殊功能</label>
                                                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSpecialFeatures(this)" data-bs-toggle="tooltip" title="展开/折叠">
                                                    <i class="bi bi-chevron-down"></i>
                                                </button>
                                            </div>
                                            <div class="special-features-content" style="display: none;">
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="param_inject_models" 
                                                           {% if param_config.inject_models %}checked{% endif %}>
                                                    <label class="form-check-label">注入可用模型列表</label>
                                                </div>
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="param_inject_loras" 
                                                           {% if param_config.inject_loras %}checked{% endif %}>
                                                    <label class="form-check-label">注入可用LoRA列表</label>
                                                </div>
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="param_inject_samplers" 
                                                           {% if param_config.inject_samplers %}checked{% endif %}>
                                                    <label class="form-check-label">注入可用采样器列表</label>
                                                </div>
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="param_inject_schedulers" 
                                                           {% if param_config.inject_schedulers %}checked{% endif %}>
                                                    <label class="form-check-label">注入可用调度器列表</label>
                                                </div>
                                            </div>
                                        </div>
                                        
                                        <div class="mb-2">
                                            <label class="form-label">别名 (用逗号分隔)</label>
                                            <input type="text" class="form-control" name="param_aliases" 
                                                   value="{{ param_config.aliases | join(',') if param_config.aliases else '' }}">
                                        </div>
                                    </div>
                                </div>
                                {% endfor %}
                            </div>
                            
                            <button type="button" class="btn btn-outline-primary btn-sm" onclick="addParamConfig(this)">
                                <i class="bi bi-plus"></i>
                                添加参数
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <button type="button" class="btn btn-outline-primary" onclick="addNodeConfig()">
            <i class="bi bi-plus"></i>
            添加节点配置
        </button>
        </div>
    </div>

    <!-- 配置文件预览 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-file-text text-danger"></i>
                完整配置文件预览 (config.json)
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div class="alert alert-warning">
                <i class="bi bi-exclamation-triangle"></i>
                <strong>注意：</strong>这是将要保存到 config.json 文件的完整配置内容。提交表单后，此配置将被写入工作流的配置文件中。
            </div>
            
            <div class="mb-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <label class="form-label">当前配置预览</label>
                    <button type="button" class="btn btn-outline-primary btn-sm" onclick="updateConfigPreview()">
                        <i class="bi bi-arrow-clockwise"></i>
                        刷新预览
                    </button>
                </div>
                <div class="border rounded bg-dark">
                    <pre><code id="configPreview" class="text-light language-json">{
  "name": "{{ workflow.config.name }}",
  "prefix": "{{ workflow.config.prefix }}",
  "description": "{{ workflow.config.description or '' }}",
  "version": "{{ workflow.config.version or '' }}",
  "author": "{{ workflow.config.author or '' }}",
  "input_nodes": {{ workflow.config.input_nodes|tojson|safe }},
  "output_nodes": {{ workflow.config.output_nodes|tojson|safe }},
  "configurable_nodes": {{ workflow.config.configurable_nodes|tojson|safe }},
  "input_mappings": {},
  "output_mappings": {},
  "node_configs": {{ workflow.config.node_configs|tojson|safe }}
}</code></pre>
                </div>
                <div class="form-text">
                    此预览会根据您在上方表单中的选择实时更新。最终的配置文件将包含所有节点参数配置。
                </div>
            </div>
        </div>
    </div>

    <!-- 隐藏字段 -->
    <input type="hidden" name="config" id="configInput">
    
    <!-- 用于传递数据的隐藏字段 -->
    <input type="hidden" id="savedInputNodes" value="{{ workflow.config.input_nodes|tojson|safe }}">
    <input type="hidden" id="savedOutputNodes" value="{{ workflow.config.output_nodes|tojson|safe }}">
    <input type="hidden" id="savedConfigurableNodes" value="{{ workflow.config.configurable_nodes|tojson|safe }}">
</form>
{% endblock %}

{% block styles %}
<style>
.param-config-item {
    transition: all 0.3s ease;
}

.param-config-item:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.btn-outline-secondary:hover {
    background-color: #6c757d;
    border-color: #6c757d;
    color: white;
}

.param-content {
    border-left: 3px solid #007bff;
    padding-left: 15px;
    margin-left: 10px;
}

.param-config-content {
    border-left: 2px solid #28a745;
    padding-left: 10px;
    margin-left: 5px;
    background-color: #f8f9fa;
    border-radius: 5px;
    padding: 10px;
}

.special-features-content {
    border-left: 2px solid #6c757d;
    padding-left: 10px;
    margin-left: 5px;
    background-color: #f8f9fa;
    border-radius: 5px;
    padding: 10px;
}

.section-content {
    border-left: 3px solid #007bff;
    padding-left: 15px;
    margin-left: 10px;
    background-color: #f8f9fa;
    border-radius: 5px;
    padding: 15px;
}

.bi-chevron-down, .bi-chevron-up {
    transition: transform 0.2s ease;
}
</style>
{% endblock %}

{% block scripts %}
<script>
// 直接在JavaScript中定义保存的数据
const savedData = {
    inputNodes: {{ workflow.config.input_nodes|tojson|safe }},
    outputNodes: {{ workflow.config.output_nodes|tojson|safe }},
    configurableNodes: {{ workflow.config.configurable_nodes|tojson|safe }}
};
// 解析 Workflow JSON 中的节点
function parseWorkflowNodes() {
    const workflowJsonElement = document.getElementById('workflowJson');
    if (!workflowJsonElement) {
        console.error('workflowJson element not found');
        return;
    }
    const workflowJson = workflowJsonElement.value;
    if (!workflowJson.trim()) {
        console.log('Workflow JSON 为空，跳过解析');
        return;
    }
    
    try {
        console.log('开始解析 Workflow JSON...');
        const workflow = JSON.parse(workflowJson);
        const nodes = [];
        
        // 提取所有节点信息
        for (const [nodeId, nodeData] of Object.entries(workflow)) {
            const title = nodeData._meta?.title || nodeData.class_type || `节点 ${nodeId}`;
            nodes.push({
                id: nodeId,
                title: title,
                class_type: nodeData.class_type
            });
        }
        
        // 按节点ID排序
        nodes.sort((a, b) => parseInt(a.id) - parseInt(b.id));
        console.log('解析到', nodes.length, '个节点:', nodes.map(n => n.id));
        
        // 更新所有下拉选择框
        updateAllNodeSelectors(nodes);
        
        // 如果有已存在的节点选择，恢复它们的值
        restoreExistingNodeValues();
        
        // 更新节点标题显示
        updateNodeTitles();
        
        // 触发自定义事件，表示节点解析完成
        document.dispatchEvent(new CustomEvent('nodesParsed'));
        console.log('节点解析完成');
        
    } catch (error) {
        console.error('JSON 格式错误：', error.message);
        console.error('JSON 内容:', workflowJson.substring(0, 200));
    }
}

// 更新所有节点选择器
function updateAllNodeSelectors(nodes) {
    const selectors = document.querySelectorAll('.node-selector');
    selectors.forEach(selector => {
        // 保存当前选中的值
        const currentValue = selector.value;
        
        // 清空并重新填充选项
        selector.innerHTML = '<option value="">-- 选择节点 --</option>';
        
        nodes.forEach(node => {
            const option = document.createElement('option');
            option.value = node.id;
            option.textContent = `${node.id} - ${node.title}`;
            if (node.class_type) {
                option.textContent += ` (${node.class_type})`;
            }
            selector.appendChild(option);
        });
        
        // 恢复之前选中的值
        if (currentValue) {
            selector.value = currentValue;
        }
    });
}

// 恢复已存在的节点值
function restoreExistingNodeValues() {
    // 这个函数会在页面加载时调用，用于恢复已保存的节点值
    const savedValues = {};
    
    // 收集所有已保存的值
    document.querySelectorAll('.node-selector').forEach(selector => {
        if (selector.value) {
            const name = selector.getAttribute('name');
            if (!savedValues[name]) savedValues[name] = [];
            savedValues[name].push(selector.value);
        }
    });
}

// 专门用于恢复已保存节点值的函数
function restoreSavedNodeValues() {
    try {
        console.log('开始恢复已保存的节点值...');
        console.log('保存的数据:', savedData);
        
        // 设置输入节点的值
        if (savedData.inputNodes && savedData.inputNodes.length > 0) {
            const inputNodes = savedData.inputNodes;
            const inputSelectors = document.querySelectorAll('select[name="input_nodes"]');
            console.log('输入节点:', inputNodes, '选择框数量:', inputSelectors.length);
            inputNodes.forEach((nodeId, index) => {
                if (inputSelectors[index]) {
                    inputSelectors[index].value = nodeId;
                    console.log('设置输入节点', index, '值为:', nodeId);
                }
            });
        }
        
        // 设置输出节点的值
        if (savedData.outputNodes && savedData.outputNodes.length > 0) {
            const outputNodes = savedData.outputNodes;
            const outputSelectors = document.querySelectorAll('select[name="output_nodes"]');
            console.log('输出节点:', outputNodes, '选择框数量:', outputSelectors.length);
            outputNodes.forEach((nodeId, index) => {
                if (outputSelectors[index]) {
                    outputSelectors[index].value = nodeId;
                    console.log('设置输出节点', index, '值为:', nodeId);
                }
            });
        }
        
        // 设置可配置节点的值
        if (savedData.configurableNodes && savedData.configurableNodes.length > 0) {
            const configurableNodes = savedData.configurableNodes;
            console.log('可配置节点:', configurableNodes);
            
            let configurableSelectors = document.querySelectorAll('select[name="configurable_nodes"]');
            console.log('当前可配置节点选择框数量:', configurableSelectors.length);
            
            // 检查是否需要创建更多的选择框
            while (configurableSelectors.length < configurableNodes.length) {
                console.log('创建新的可配置节点选择框...');
                addConfigurableNode();
                configurableSelectors = document.querySelectorAll('select[name="configurable_nodes"]');
            }
            
            // 重新获取选择框（因为可能添加了新的）
            configurableSelectors = document.querySelectorAll('select[name="configurable_nodes"]');
            
            // 设置值
            configurableNodes.forEach((nodeId, index) => {
                if (configurableSelectors[index]) {
                    configurableSelectors[index].value = nodeId;
                    console.log('设置可配置节点', index, '值为:', nodeId, '成功:', configurableSelectors[index].value === nodeId);
                    
                    // 验证选项是否存在
                    const optionExists = Array.from(configurableSelectors[index].options).some(option => option.value === nodeId);
                    console.log('节点', nodeId, '的选项是否存在:', optionExists);
                } else {
                    console.log('没有找到选择框', index);
                }
            });
        }
        
        // 更新映射预览
        updateMappingPreview();
        console.log('节点值恢复完成');
    } catch (error) {
        console.error('恢复节点值时出错:', error);
    }
}

// 添加输入节点
function addInputNode() {
    const list = document.getElementById('inputNodesList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <select class="form-select node-selector" name="input_nodes" onchange="updateMappingPreview()">
            <option value="">-- 选择节点 --</option>
        </select>
        <button type="button" class="btn btn-outline-danger" onclick="removeInputNode(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    list.appendChild(div);
    
    // 如果已经解析过节点，更新选择器
    const workflowJsonElement = document.getElementById('workflowJson');
    if (workflowJsonElement && workflowJsonElement.value.trim()) {
        parseWorkflowNodes();
    }
    
    // 更新映射预览
    updateMappingPreview();
}

function removeInputNode(button) {
    button.parentElement.remove();
    updateMappingPreview();
}

// 添加输出节点
function addOutputNode() {
    const list = document.getElementById('outputNodesList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <select class="form-select node-selector" name="output_nodes" onchange="updateMappingPreview()">
            <option value="">-- 选择节点 --</option>
        </select>
        <button type="button" class="btn btn-outline-danger" onclick="removeOutputNode(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    list.appendChild(div);
    
    // 如果已经解析过节点，更新选择器
    const workflowJsonElement = document.getElementById('workflowJson');
    if (workflowJsonElement && workflowJsonElement.value.trim()) {
        parseWorkflowNodes();
    }
    
    // 更新映射预览
    updateMappingPreview();
}

function removeOutputNode(button) {
    button.parentElement.remove();
    updateMappingPreview();
}

// 添加可配置节点
function addConfigurableNode() {
    const list = document.getElementById('configurableNodesList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <select class="form-select node-selector" name="configurable_nodes">
            <option value="">-- 选择节点 --</option>
        </select>
        <button type="button" class="btn btn-outline-danger" onclick="removeConfigurableNode(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    list.appendChild(div);
    
    // 如果已经解析过节点，更新选择器
    const workflowJsonElement = document.getElementById('workflowJson');
    if (workflowJsonElement && workflowJsonElement.value.trim()) {
        parseWorkflowNodes();
    }
    
    // 更新映射预览
    updateMappingPreview();
}

function removeConfigurableNode(button) {
    button.parentElement.remove();
}

// 更新映射预览
function updateMappingPreview() {
    // 更新输入映射预览
    const inputNodes = Array.from(document.querySelectorAll('select[name="input_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const inputPreview = document.getElementById('inputMappingsPreview');
    if (inputPreview) {
        if (inputNodes.length > 0) {
            const inputMappings = {};
            inputNodes.forEach(nodeId => {
                inputMappings[nodeId] = {
                    parameter_name: "image",
                    type: "image",
                    description: "输入的原始图像"
                };
            });
            inputPreview.innerHTML = `<pre><code>${JSON.stringify(inputMappings, null, 2)}</code></pre>`;
        } else {
            inputPreview.innerHTML = '<p class="text-muted">暂无输入节点</p>';
        }
    }
    
    // 更新输出映射预览
    const outputNodes = Array.from(document.querySelectorAll('select[name="output_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const outputPreview = document.getElementById('outputMappingsPreview');
    if (outputPreview) {
        if (outputNodes.length > 0) {
            const outputMappings = {};
            outputNodes.forEach(nodeId => {
                outputMappings[nodeId] = {
                    parameter_name: "images",
                    type: "image",
                    description: "处理后的图像"
                };
            });
            outputPreview.innerHTML = `<pre><code>${JSON.stringify(outputMappings, null, 2)}</code></pre>`;
        } else {
            outputPreview.innerHTML = '<p class="text-muted">暂无输出节点</p>';
        }
    }
    
    // 同时更新配置预览
    updateConfigPreview();
}

// 更新配置预览
function updateConfigPreview() {
    const nameInput = document.querySelector('input[name="name"]');
    const prefixInput = document.querySelector('input[name="prefix"]');
    const descriptionInput = document.querySelector('textarea[name="description"]');
    const versionInput = document.querySelector('input[name="version"]');
    const authorInput = document.querySelector('input[name="author"]');
    
    const config = {
        name: nameInput ? nameInput.value : '',
        prefix: prefixInput ? prefixInput.value : '',
        description: descriptionInput ? descriptionInput.value : '',
        version: versionInput ? versionInput.value : '',
        author: authorInput ? authorInput.value : '',
        input_nodes: Array.from(document.querySelectorAll('select[name="input_nodes"]')).map(select => select.value).filter(v => v),
        output_nodes: Array.from(document.querySelectorAll('select[name="output_nodes"]')).map(select => select.value).filter(v => v),
        configurable_nodes: Array.from(document.querySelectorAll('select[name="configurable_nodes"]')).map(select => select.value).filter(v => v),
        input_mappings: {},
        output_mappings: {},
        node_configs: {}
    };
    
    // 自动生成输入输出映射
    const inputNodes = Array.from(document.querySelectorAll('select[name="input_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const outputNodes = Array.from(document.querySelectorAll('select[name="output_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    inputNodes.forEach(nodeId => {
        config.input_mappings[nodeId] = {
            parameter_name: "image",
            type: "image",
            description: "输入的原始图像"
        };
    });
    
    outputNodes.forEach(nodeId => {
        config.output_mappings[nodeId] = {
            parameter_name: "images",
            type: "image",
            description: "处理后的图像"
        };
    });
    
    // 构建节点配置
    document.querySelectorAll('.param-item[data-node-id]').forEach(nodeItem => {
        const nodeId = nodeItem.getAttribute('data-node-id');
        config.node_configs[nodeId] = {};
        
        nodeItem.querySelectorAll('.param-config-item').forEach(paramItem => {
            const paramName = paramItem.getAttribute('data-param-name');
            const paramTypeSelect = paramItem.querySelector('select[name="param_type"]');
            const paramType = paramTypeSelect ? paramTypeSelect.value : 'text';
            
            // 根据类型获取默认值
            let defaultValue = null;
            switch(paramType) {
                case 'boolean':
                    const checkbox = paramItem.querySelector('.param-default-boolean input[name="param_default"]');
                    defaultValue = checkbox ? checkbox.checked : false;
                    break;
                case 'number':
                    const numberInput = paramItem.querySelector('.param-default-number');
                    defaultValue = numberInput && numberInput.value ? parseFloat(numberInput.value) : null;
                    break;
                default:
                    const textInput = paramItem.querySelector('.param-default-text');
                    defaultValue = textInput ? textInput.value || null : null;
            }
            
            const descriptionTextarea = paramItem.querySelector('textarea[name="param_description"]');
            const requiredCheckbox = paramItem.querySelector('input[name="param_required"]');
            const minInput = paramItem.querySelector('input[name="param_min"]');
            const maxInput = paramItem.querySelector('input[name="param_max"]');
            
            const paramConfig = {
                type: paramType,
                default: defaultValue,
                description: descriptionTextarea ? descriptionTextarea.value || '' : '',
                required: requiredCheckbox ? requiredCheckbox.checked : false
            };
            
            const minVal = minInput ? minInput.value : '';
            const maxVal = maxInput ? maxInput.value : '';
            if (minVal) paramConfig.min = parseFloat(minVal);
            if (maxVal) paramConfig.max = parseFloat(maxVal);
            
            const optionsInput = paramItem.querySelector('input[name="param_options"]');
            const options = optionsInput ? optionsInput.value : '';
            if (options) paramConfig.options = options.split(',').map(o => o.trim());
            
            const aliasesInput = paramItem.querySelector('input[name="param_aliases"]');
            const aliases = aliasesInput ? aliasesInput.value : '';
            if (aliases) paramConfig.aliases = aliases.split(',').map(a => a.trim());
            
            // 处理特殊功能
            if (paramItem.querySelector('input[name="param_inject_models"]')?.checked) {
                paramConfig.inject_models = true;
            }
            if (paramItem.querySelector('input[name="param_inject_loras"]')?.checked) {
                paramConfig.inject_loras = true;
            }
            if (paramItem.querySelector('input[name="param_inject_samplers"]')?.checked) {
                paramConfig.inject_samplers = true;
            }
            if (paramItem.querySelector('input[name="param_inject_schedulers"]')?.checked) {
                paramConfig.inject_schedulers = true;
            }
            
            config.node_configs[nodeId][paramName] = paramConfig;
        });
    });
    
    // 更新预览显示
    const configPreview = document.getElementById('configPreview');
    if (configPreview) {
        configPreview.textContent = JSON.stringify(config, null, 2);
    }
}

// 折叠/展开节点配置
function toggleNodeConfig(button) {
    const paramItem = button.closest('.param-item');
    const content = paramItem.querySelector('.param-content');
    const icon = button.querySelector('i');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
    } else {
        content.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
    }
}

// 更新节点标题显示
function updateNodeTitles() {
    const workflowJsonElement = document.getElementById('workflowJson');
    if (!workflowJsonElement || !workflowJsonElement.value.trim()) return;
    const workflowJson = workflowJsonElement.value;
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodeTitles = document.querySelectorAll('.node-title');
        
        nodeTitles.forEach(titleElement => {
            const nodeId = titleElement.getAttribute('data-node-id');
            const nodeData = workflow[nodeId];
            
            if (nodeData) {
                const nodeTitle = nodeData._meta?.title || nodeData.class_type || `节点 ${nodeId}`;
                titleElement.textContent = `${nodeId} - ${nodeTitle}`;
            }
        });
    } catch (error) {
        console.error('更新节点标题时出错:', error);
    }
}

// 折叠/展开参数配置
function toggleParamConfig(button) {
    const paramItem = button.closest('.param-config-item');
    const content = paramItem.querySelector('.param-config-content');
    const icon = button.querySelector('i');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
    } else {
        content.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
    }
}

// 折叠/展开主要部分
function toggleSection(button) {
    const section = button.closest('.config-section');
    const content = section.querySelector('.section-content');
    const icon = button.querySelector('i');
    
    if (content.style.display === 'none' || content.style.display === '') {
        content.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
    } else {
        content.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
    }
}

// 折叠/展开特殊功能
function toggleSpecialFeatures(button) {
    console.log('toggleSpecialFeatures 被调用');
    
    // 方法1: 通过父元素查找
    let content = null;
    let container = button.parentElement; // 获取直接父元素
    
    console.log('按钮的直接父元素:', container);
    
    // 从当前父元素开始向上查找 special-features-content
    while (container && !content) {
        content = container.querySelector('.special-features-content');
        console.log('在容器中查找 special-features-content:', content);
        if (!content) {
            container = container.parentElement;
            console.log('向上移动到父元素:', container);
        }
    }
    
    // 如果还是找不到，尝试从按钮的下一个兄弟元素开始
    if (!content) {
        console.log('尝试从按钮的父元素查找下一个兄弟元素');
        const buttonParent = button.parentElement;
        let nextElement = buttonParent.nextElementSibling;
        console.log('按钮父元素的下一个兄弟元素:', nextElement);
        
        while (nextElement && !content) {
            if (nextElement.classList.contains('special-features-content')) {
                content = nextElement;
                console.log('找到 special-features-content:', content);
            } else {
                content = nextElement.querySelector('.special-features-content');
                console.log('在兄弟元素中查找 special-features-content:', content);
            }
            if (!content) {
                nextElement = nextElement.nextElementSibling;
            }
        }
    }
    
    const icon = button.querySelector('i');
    console.log('找到的图标元素:', icon);
    
    if (!content) {
        console.error('无法找到 special-features-content 元素');
        // 输出完整的DOM结构用于调试
        console.log('按钮的完整父级结构:', button.parentElement.parentElement.parentElement);
        return;
    }
    
    if (content.style.display === 'none' || content.style.display === '') {
        content.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
        console.log('展开特殊功能');
    } else {
        content.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
        console.log('收缩特殊功能');
    }
}

// 添加节点配置
function addNodeConfig() {
    // 获取所有已选择的可配置节点
    const configurableNodes = Array.from(document.querySelectorAll('select[name="configurable_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    // 获取已经配置了参数的节点
    const existingConfigNodes = Array.from(document.querySelectorAll('.param-item[data-node-id]'))
        .map(item => item.getAttribute('data-node-id'));
    
    // 过滤出还未配置参数的节点
    const availableNodes = configurableNodes.filter(nodeId => !existingConfigNodes.includes(nodeId));
    
    if (availableNodes.length === 0) {
        alert('没有可配置的节点，请先在上方选择可配置节点');
        return;
    }
    
    // 创建选择对话框
    let nodeOptions = availableNodes.map(nodeId => {
        const selectElement = Array.from(document.querySelectorAll('select[name="configurable_nodes"]'))
            .find(select => select.value === nodeId);
        const optionText = selectElement ? selectElement.options[selectElement.selectedIndex].text : nodeId;
        return `<option value="${nodeId}">${optionText}</option>`;
    }).join('');
    
    // 创建模态框选择节点
    const modalHtml = `
        <div class="modal fade" id="nodeSelectModal" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">选择要配置的节点</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <select class="form-select" id="nodeSelect">
                            ${nodeOptions}
                        </select>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-primary" onclick="confirmAddNodeConfig()">确定</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // 移除已存在的模态框
    const existingModal = document.getElementById('nodeSelectModal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // 添加新模态框
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('nodeSelectModal'));
    modal.show();
}

// 确认添加节点配置
function confirmAddNodeConfig() {
    const select = document.getElementById('nodeSelect');
    const nodeId = select.value;
    
    if (!nodeId) {
        alert('请选择一个节点');
        return;
    }
    
    const container = document.getElementById('nodeConfigsContainer');
    const div = document.createElement('div');
    div.className = 'param-item mb-4';
    div.setAttribute('data-node-id', nodeId);
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h6 class="mb-0">
                <i class="bi bi-cpu"></i>
                <span class="node-title" data-node-id="${nodeId}">节点 ${nodeId}</span>
            </h6>
            <div>
                <button type="button" class="btn btn-outline-secondary btn-sm me-2" onclick="toggleNodeConfig(this)" data-bs-toggle="tooltip" title="展开/折叠">
                    <i class="bi bi-chevron-down"></i>
                </button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeNodeConfig(this)">
                    <i class="bi bi-trash"></i>
                    删除节点配置
                </button>
            </div>
        </div>
        
        <div class="param-content" style="display: none;">
            <div class="row mb-3">
                <div class="col-12">
                    <div class="param-params-container">
                        <button type="button" class="btn btn-outline-primary btn-sm" onclick="addParamConfig(this)">
                            <i class="bi bi-plus"></i>
                            添加参数
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
    container.appendChild(div);
    
    // 更新新节点的标题
    updateNodeTitles();
    
    // 关闭模态框
    const modal = bootstrap.Modal.getInstance(document.getElementById('nodeSelectModal'));
    modal.hide();
}

function removeNodeConfig(button) {
    button.closest('.param-item').remove();
}

// 添加参数配置
function addParamConfig(button) {
    // 获取当前节点ID
    const nodeItem = button.closest('.param-item');
    const nodeId = nodeItem.getAttribute('data-node-id');
    
    if (!nodeId) {
        alert('无法获取节点ID');
        return;
    }
    
    // 解析工作流JSON获取该节点的inputs
    const workflowJsonElement = document.getElementById('workflowJson');
    if (!workflowJsonElement || !workflowJsonElement.value.trim()) {
        alert('请先解析工作流JSON');
        return;
    }
    const workflowJson = workflowJsonElement.value;
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodeData = workflow[nodeId];
        
        if (!nodeData || !nodeData.inputs) {
            alert(`节点 ${nodeId} 没有找到 inputs 数据`);
            return;
        }
        
        // 获取已配置的参数名
        const container = button.parentElement;
        const existingParams = Array.from(container.querySelectorAll('.param-config-item'))
            .map(item => item.getAttribute('data-param-name'));
        
        // 获取可配置的参数（排除已配置的）
        const availableInputs = Object.keys(nodeData.inputs).filter(paramName => 
            !existingParams.includes(paramName)
        );
        
        if (availableInputs.length === 0) {
            alert('该节点的所有参数都已配置');
            return;
        }
        
        // 创建选择对话框
        let inputOptions = availableInputs.map(paramName => {
            const inputValue = nodeData.inputs[paramName];
            let valueDisplay = '';
            
            // 处理不同类型的输入值显示
            if (Array.isArray(inputValue)) {
                valueDisplay = ` [连接: ${inputValue[0]}]`;
            } else if (typeof inputValue === 'string') {
                valueDisplay = ` = "${inputValue}"`;
            } else if (typeof inputValue === 'number') {
                valueDisplay = ` = ${inputValue}`;
            } else {
                valueDisplay = ` = ${JSON.stringify(inputValue)}`;
            }
            
            return `<option value="${paramName}">${paramName}${valueDisplay}</option>`;
        }).join('');
        
        // 创建模态框选择参数
        const modalHtml = `
            <div class="modal fade" id="paramSelectModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">选择要配置的参数 - 节点 ${nodeId}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <label class="form-label">可配置的参数：</label>
                            <select class="form-select" id="paramSelect">
                                ${inputOptions}
                            </select>
                            <div class="mt-3">
                                <small class="text-muted">
                                    参数值类型：字符串显示引号，数字直接显示，数组表示连接到其他节点
                                </small>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                            <button type="button" class="btn btn-primary" onclick="confirmAddParamConfig('${nodeId}')">确定</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // 移除已存在的模态框
        const existingModal = document.getElementById('paramSelectModal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // 添加新模态框
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('paramSelectModal'));
        modal.show();
        
    } catch (error) {
        console.error('解析工作流JSON时出错:', error);
        alert('解析工作流JSON时出错，请检查JSON格式');
    }
}

// 确认添加参数配置
function confirmAddParamConfig(nodeId) {
    const select = document.getElementById('paramSelect');
    if (!select || !select.value) {
        alert('请选择一个参数');
        return;
    }
    const paramName = select.value;
    
    // 获取工作流数据以获取参数的默认值
    const workflowJsonElement = document.getElementById('workflowJson');
    if (!workflowJsonElement) {
        alert('无法找到工作流JSON元素');
        return;
    }
    const workflowJson = workflowJsonElement.value;
    let defaultValue = '';
    let paramType = 'text';
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodeData = workflow[nodeId];
        const inputValue = nodeData.inputs[paramName];
        
        // 根据输入值类型设置默认值和参数类型
        if (Array.isArray(inputValue)) {
            // 数组类型表示连接到其他节点，这种情况下可能需要特殊处理
            defaultValue = '';
            paramType = 'text';
        } else if (typeof inputValue === 'string') {
            defaultValue = inputValue;
            paramType = 'text';
        } else if (typeof inputValue === 'number') {
            defaultValue = inputValue.toString();
            paramType = 'number';
        } else if (typeof inputValue === 'boolean') {
            defaultValue = inputValue.toString();
            paramType = 'boolean';
        } else {
            defaultValue = JSON.stringify(inputValue);
            paramType = 'text';
        }
    } catch (error) {
        console.error('获取参数默认值时出错:', error);
    }
    
    const container = document.querySelector(`.param-item[data-node-id="${nodeId}"] .param-params-container`);
    const addButton = container.querySelector('button[onclick="addParamConfig(this)"]');
    
    const div = document.createElement('div');
    div.className = 'param-config-item mb-3 p-3 border rounded';
    div.setAttribute('data-param-name', paramName);
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <strong>${paramName}</strong>
            <div>
                <button type="button" class="btn btn-outline-secondary btn-sm me-2" onclick="toggleParamConfig(this)" data-bs-toggle="tooltip" title="展开/折叠">
                    <i class="bi bi-chevron-down"></i>
                </button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeParamConfig(this)">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
        
        <div class="param-config-content" style="display: none;">
            <div class="row">
                <div class="col-md-6 mb-2">
                    <label class="form-label">参数名</label>
                    <input type="text" class="form-control" name="param_name" value="${paramName}" readonly>
                </div>
                <div class="col-md-6 mb-2">
                    <label class="form-label">类型</label>
                    <select class="form-select" name="param_type">
                        <option value="text" ${paramType === 'text' ? 'selected' : ''}>文本</option>
                        <option value="number" ${paramType === 'number' ? 'selected' : ''}>数字</option>
                        <option value="select">选择</option>
                        <option value="boolean" ${paramType === 'boolean' ? 'selected' : ''}>布尔值</option>
                    </select>
                </div>
            </div>
            
            <div class="row">
                <div class="col-md-6 mb-2">
                    <label class="form-label">默认值</label>
                    <!-- 默认值输入框，会根据类型动态变化 -->
                    <div class="param-default-container">
                        <input type="text" class="form-control param-default-text" name="param_default" value="${defaultValue}">
                        <input type="number" class="form-control param-default-number" name="param_default" value="${defaultValue}" style="display:none;" step="any">
                        <div class="form-check form-switch param-default-boolean" style="display:none;">
                            <input class="form-check-input" type="checkbox" name="param_default" ${defaultValue === 'true' ? 'checked' : ''} onchange="updateBooleanLabel(this)">
                            <label class="form-check-label boolean-label">${defaultValue === 'true' ? 'True' : 'False'}</label>
                        </div>
                    </div>
                </div>
                <div class="col-md-6 mb-2">
                    <div class="form-check mt-4">
                        <input class="form-check-input" type="checkbox" name="param_required">
                        <label class="form-check-label">必需参数</label>
                    </div>
                </div>
            </div>
            
            <div class="mb-2">
                <label class="form-label">描述</label>
                <textarea class="form-control" name="param_description" rows="2"></textarea>
            </div>
            
            <!-- 数字类型的范围设置 -->
            <div class="param-number-options" style="display:none;">
                <div class="row">
                    <div class="col-md-6 mb-2">
                        <label class="form-label">最小值</label>
                        <input type="number" class="form-control" name="param_min" step="any">
                    </div>
                    <div class="col-md-6 mb-2">
                        <label class="form-label">最大值</label>
                        <input type="number" class="form-control" name="param_max" step="any">
                    </div>
                </div>
            </div>
            
            <!-- 选择类型的选项设置 -->
            <div class="param-select-options" style="display:none;">
                <div class="mb-2">
                    <label class="form-label">选项 (用逗号分隔)</label>
                    <input type="text" class="form-control" name="param_options" placeholder="选项1,选项2,选项3">
                </div>
            </div>
            
            <div class="mb-2">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <label class="form-label mb-0">特殊功能</label>
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSpecialFeatures(this)" data-bs-toggle="tooltip" title="展开/折叠">
                        <i class="bi bi-chevron-down"></i>
                    </button>
                </div>
                <div class="special-features-content" style="display: none;">
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_models">
                        <label class="form-check-label">注入可用模型列表</label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_loras">
                        <label class="form-check-label">注入可用LoRA列表</label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_samplers">
                        <label class="form-check-label">注入可用采样器列表</label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_schedulers">
                        <label class="form-check-label">注入可用调度器列表</label>
                    </div>
                </div>
            </div>
            
            <div class="mb-2">
                <label class="form-label">别名 (用逗号分隔)</label>
                <input type="text" class="form-control" name="param_aliases">
            </div>
        </div>
    `;
    container.insertBefore(div, addButton);
    
    // 初始化参数类型的动态显示
    initParamTypeControls(div);
    
    // 关闭模态框
    const modal = bootstrap.Modal.getInstance(document.getElementById('paramSelectModal'));
    modal.hide();
}

// 初始化参数类型的动态显示控制
function initParamTypeControls(paramItem) {
    const typeSelect = paramItem.querySelector('select[name="param_type"]');
    
    if (!typeSelect) {
        console.warn('param_type select not found in paramItem:', paramItem);
        return;
    }
    
    // 监听类型变化
    typeSelect.addEventListener('change', function() {
        updateParamTypeDisplay(paramItem, this.value);
        updateConfigPreview(); // 更新配置预览
    });
    
    // 为所有参数配置字段添加变化监听器
    const paramFields = [
        'textarea[name="param_description"]',
        'input[name="param_required"]',
        'input[name="param_min"]',
        'input[name="param_max"]',
        'input[name="param_options"]',
        'input[name="param_aliases"]',
        'input[name="param_inject_models"]',
        'input[name="param_inject_loras"]',
        'input[name="param_inject_samplers"]',
        'input[name="param_inject_schedulers"]',
        '.param-default-text',
        '.param-default-number',
        '.param-default-boolean input[name="param_default"]'
    ];
    
    paramFields.forEach(selector => {
        const elements = paramItem.querySelectorAll(selector);
        elements.forEach(element => {
            element.addEventListener('input', updateConfigPreview);
            element.addEventListener('change', updateConfigPreview);
        });
    });
    
    // 初始化显示状态
    updateParamTypeDisplay(paramItem, typeSelect.value);
}

// 根据参数类型更新显示状态
function updateParamTypeDisplay(paramItem, paramType) {
    // 获取各个控制元素
    const defaultText = paramItem.querySelector('.param-default-text');
    const defaultNumber = paramItem.querySelector('.param-default-number');
    const defaultBoolean = paramItem.querySelector('.param-default-boolean');
    const numberOptions = paramItem.querySelector('.param-number-options');
    const selectOptions = paramItem.querySelector('.param-select-options');
    
    // 隐藏所有选项（检查元素是否存在）
    if (defaultText) defaultText.style.display = 'none';
    if (defaultNumber) defaultNumber.style.display = 'none';
    if (defaultBoolean) defaultBoolean.style.display = 'none';
    if (numberOptions) numberOptions.style.display = 'none';
    if (selectOptions) selectOptions.style.display = 'none';
    
    // 根据类型显示相应选项
    switch(paramType) {
        case 'text':
            if (defaultText) defaultText.style.display = 'block';
            break;
            
        case 'number':
            if (defaultNumber) defaultNumber.style.display = 'block';
            if (numberOptions) numberOptions.style.display = 'block';
            break;
            
        case 'select':
            if (defaultText) defaultText.style.display = 'block';
            if (selectOptions) selectOptions.style.display = 'block';
            break;
            
        case 'boolean':
            if (defaultBoolean) {
                defaultBoolean.style.display = 'block';
                // 初始化布尔值标签显示
                const checkbox = defaultBoolean.querySelector('input[type="checkbox"]');
                if (checkbox) updateBooleanLabel(checkbox);
            }
            break;
    }
}

// 更新布尔值开关的显示文字
function updateBooleanLabel(checkbox) {
    if (!checkbox) return;
    const label = checkbox.nextElementSibling;
    if (label && label.classList.contains('boolean-label')) {
        label.textContent = checkbox.checked ? 'True' : 'False';
    }
    // 更新配置预览
    updateConfigPreview();
}

function removeParamConfig(button) {
    button.closest('.param-config-item').remove();
}

// 添加输入映射
function addInputMapping() {
    const container = document.getElementById('inputMappingsContainer');
    const nodeId = prompt('请输入节点ID:');
    if (!nodeId) return;
    
    const div = document.createElement('div');
    div.className = 'mapping-item mb-3 p-3 border rounded';
    div.setAttribute('data-node-id', nodeId);
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <strong>节点 ${nodeId}</strong>
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeInputMapping(this)">
                <i class="bi bi-trash"></i>
            </button>
        </div>
        <div class="row">
            <div class="col-md-6 mb-2">
                <label class="form-label">参数名</label>
                <input type="text" class="form-control" name="input_mapping_param_name" placeholder="如: image">
            </div>
            <div class="col-md-6 mb-2">
                <label class="form-label">类型</label>
                <select class="form-select" name="input_mapping_type">
                    <option value="image">图像</option>
                    <option value="text">文本</option>
                    <option value="number">数字</option>
                    <option value="boolean">布尔值</option>
                </select>
            </div>
        </div>
        <div class="mb-2">
            <label class="form-label">描述</label>
            <textarea class="form-control" name="input_mapping_description" rows="2"></textarea>
        </div>
    `;
    container.appendChild(div);
    
    // 更新新节点的标题
    updateNodeTitles();
}

function removeInputMapping(button) {
    button.closest('.mapping-item').remove();
}

// 添加输出映射
function addOutputMapping() {
    const container = document.getElementById('outputMappingsContainer');
    const nodeId = prompt('请输入节点ID:');
    if (!nodeId) return;
    
    const div = document.createElement('div');
    div.className = 'mapping-item mb-3 p-3 border rounded';
    div.setAttribute('data-node-id', nodeId);
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <strong>节点 ${nodeId}</strong>
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeOutputMapping(this)">
                <i class="bi bi-trash"></i>
            </button>
        </div>
        <div class="row">
            <div class="col-md-6 mb-2">
                <label class="form-label">参数名</label>
                <input type="text" class="form-control" name="output_mapping_param_name" placeholder="如: images">
            </div>
            <div class="col-md-6 mb-2">
                <label class="form-label">类型</label>
                <select class="form-select" name="output_mapping_type">
                    <option value="image">图像</option>
                    <option value="text">文本</option>
                    <option value="number">数字</option>
                    <option value="boolean">布尔值</option>
                </select>
            </div>
        </div>
        <div class="mb-2">
            <label class="form-label">描述</label>
            <textarea class="form-control" name="output_mapping_description" rows="2"></textarea>
        </div>
    `;
    container.appendChild(div);
    
    // 更新新节点的标题
    updateNodeTitles();
}

function removeOutputMapping(button) {
    button.closest('.mapping-item').remove();
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    updateMappingPreview();
    
    // 初始化Bootstrap tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // 初始化已存在的参数配置的动态显示
    document.querySelectorAll('.param-config-item').forEach(paramItem => {
        initParamTypeControls(paramItem);
    });
    
    // 为基本表单字段添加变化监听器，以便实时更新配置预览
    const basicFields = ['input[name="name"]', 'input[name="prefix"]', 'textarea[name="description"]', 
                         'input[name="version"]', 'input[name="author"]'];
    basicFields.forEach(selector => {
        const element = document.querySelector(selector);
        if (element) {
            element.addEventListener('input', updateConfigPreview);
            element.addEventListener('change', updateConfigPreview);
        }
    });
    
    // 等待一小段时间确保所有DOM元素都完全加载
    setTimeout(() => {
        // 自动解析 Workflow JSON 中的节点
        const workflowJsonElement = document.getElementById('workflowJson');
        if (workflowJsonElement && workflowJsonElement.value.trim()) {
            parseWorkflowNodes();
            
            // 在节点解析完成后立即恢复已保存的值
            setTimeout(() => {
                restoreSavedNodeValues();
                updateNodeTitles(); // 更新节点标题
                updateConfigPreview(); // 更新配置预览
            }, 200); // 确保所有选择框都已填充选项
        }
    }, 100); // 确保模板渲染的选择框已完全创建
});

// 表单提交时构建配置对象
document.getElementById('workflowForm').addEventListener('submit', function(e) {
    e.preventDefault();
    
    const nameInput = document.querySelector('input[name="name"]');
    const prefixInput = document.querySelector('input[name="prefix"]');
    const descriptionInput = document.querySelector('textarea[name="description"]');
    const versionInput = document.querySelector('input[name="version"]');
    const authorInput = document.querySelector('input[name="author"]');
    
    const config = {
        name: nameInput ? nameInput.value : '',
        prefix: prefixInput ? prefixInput.value : '',
        description: descriptionInput ? descriptionInput.value : '',
        version: versionInput ? versionInput.value : '',
        author: authorInput ? authorInput.value : '',
        input_nodes: Array.from(document.querySelectorAll('select[name="input_nodes"]')).map(select => select.value).filter(v => v),
        output_nodes: Array.from(document.querySelectorAll('select[name="output_nodes"]')).map(select => select.value).filter(v => v),
        configurable_nodes: Array.from(document.querySelectorAll('select[name="configurable_nodes"]')).map(select => select.value).filter(v => v),
        input_mappings: {},
        output_mappings: {},
        node_configs: {}
    };
    
    // 自动生成输入输出映射
    const inputNodes = Array.from(document.querySelectorAll('select[name="input_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const outputNodes = Array.from(document.querySelectorAll('select[name="output_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    inputNodes.forEach(nodeId => {
        config.input_mappings[nodeId] = {
            parameter_name: "image",
            type: "image",
            description: "输入的原始图像"
        };
    });
    
    outputNodes.forEach(nodeId => {
        config.output_mappings[nodeId] = {
            parameter_name: "images",
            type: "image",
            description: "处理后的图像"
        };
    });
    
    // 构建节点配置
    document.querySelectorAll('.param-item[data-node-id]').forEach(nodeItem => {
        const nodeId = nodeItem.getAttribute('data-node-id');
        config.node_configs[nodeId] = {};
        
        nodeItem.querySelectorAll('.param-config-item').forEach(paramItem => {
            const paramName = paramItem.getAttribute('data-param-name');
            const paramTypeSelect = paramItem.querySelector('select[name="param_type"]');
            const paramType = paramTypeSelect ? paramTypeSelect.value : 'text';
            
            // 根据类型获取默认值
            let defaultValue = null;
            switch(paramType) {
                case 'boolean':
                    const checkbox = paramItem.querySelector('.param-default-boolean input[name="param_default"]');
                    defaultValue = checkbox ? checkbox.checked : false;
                    break;
                case 'number':
                    const numberInput = paramItem.querySelector('.param-default-number');
                    defaultValue = numberInput && numberInput.value ? parseFloat(numberInput.value) : null;
                    break;
                default:
                    const textInput = paramItem.querySelector('.param-default-text');
                    defaultValue = textInput ? textInput.value || null : null;
            }
            
            const descriptionTextarea = paramItem.querySelector('textarea[name="param_description"]');
            const requiredCheckbox = paramItem.querySelector('input[name="param_required"]');
            const minInput = paramItem.querySelector('input[name="param_min"]');
            const maxInput = paramItem.querySelector('input[name="param_max"]');
            
            const paramConfig = {
                type: paramType,
                default: defaultValue,
                description: descriptionTextarea ? descriptionTextarea.value || '' : '',
                required: requiredCheckbox ? requiredCheckbox.checked : false
            };
            
            const minVal = minInput ? minInput.value : '';
            const maxVal = maxInput ? maxInput.value : '';
            if (minVal) paramConfig.min = parseFloat(minVal);
            if (maxVal) paramConfig.max = parseFloat(maxVal);
            
            const optionsInput = paramItem.querySelector('input[name="param_options"]');
            const options = optionsInput ? optionsInput.value : '';
            if (options) paramConfig.options = options.split(',').map(o => o.trim());
            
            const aliasesInput = paramItem.querySelector('input[name="param_aliases"]');
            const aliases = aliasesInput ? aliasesInput.value : '';
            if (aliases) paramConfig.aliases = aliases.split(',').map(a => a.trim());
            
            // 处理特殊功能
            if (paramItem.querySelector('input[name="param_inject_models"]')?.checked) {
                paramConfig.inject_models = true;
            }
            if (paramItem.querySelector('input[name="param_inject_loras"]')?.checked) {
                paramConfig.inject_loras = true;
            }
            if (paramItem.querySelector('input[name="param_inject_samplers"]')?.checked) {
                paramConfig.inject_samplers = true;
            }
            if (paramItem.querySelector('input[name="param_inject_schedulers"]')?.checked) {
                paramConfig.inject_schedulers = true;
            }
            
            config.node_configs[nodeId][paramName] = paramConfig;
        });
    });
    
    const configInput = document.getElementById('configInput');
    if (configInput) {
        configInput.value = JSON.stringify(config, null, 2);
    }
    
    // 提交表单
    this.submit();
});
</script>
{% endblock %}
    """
    
    return render_template_string(html_template, workflow=workflow)


@app.route('/workflow/<workflow_name>/save', methods=['POST'])
def workflow_save(workflow_name):
    """保存工作流配置"""
    try:
        # 获取表单数据
        config = json.loads(request.form.get('config', '{}'))
        workflow_data = json.loads(request.form.get('workflow', '{}'))
        
        if config_manager.save_workflow(workflow_name, config, workflow_data):
            flash('工作流保存成功！', 'success')
        else:
            flash('工作流保存失败！', 'error')
            
        return redirect(url_for('workflow_detail', workflow_name=workflow_name))
        
    except Exception as e:
        logger.error(f"保存工作流失败: {e}")
        flash(f'保存失败: {str(e)}', 'error')
        return redirect(url_for('workflow_edit', workflow_name=workflow_name))


@app.route('/workflow/new')
def workflow_new():
    """新建工作流页面"""
    html_template = """
{% extends "base.html" %}

{% block title %}新建工作流 - 工作流配置{% endblock %}

{% block page_title %}新建工作流{% endblock %}

{% block page_actions %}
    <div class="btn-group" role="group">
        <button type="submit" form="workflowForm" class="btn btn-success">
            <i class="bi bi-check-circle"></i>
            创建
        </button>
        <a href="{{ url_for('index') }}" class="btn btn-outline-secondary">
            <i class="bi bi-x-circle"></i>
            取消
        </a>
    </div>
{% endblock %}

{% block content %}
<form id="workflowForm" method="post" action="{{ url_for('workflow_create') }}">
    <!-- 基本信息 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-info-circle text-primary"></i>
                基本信息
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">工作流名称</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="workflow_name" 
                       placeholder="输入工作流名称（用于存储）" required>
                <div class="form-text">工作流的唯一标识符，用于文件存储和URL路径</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">显示名称</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="name" 
                       placeholder="输入显示名称" required>
                <div class="form-text">工作流的显示名称，可以包含中文和特殊字符</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">前缀</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="prefix" 
                       placeholder="输入调用前缀" required>
                <div class="form-text">用于调用此工作流的前缀，如: encrypt</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">描述</label>
            <div class="col-sm-10">
                <textarea class="form-control" name="description" rows="3" 
                          placeholder="输入工作流描述"></textarea>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">版本</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="version" 
                       value="1.0.0">
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">作者</label>
            <div class="col-sm-10">
                <input type="text" class="form-control" name="author" 
                       value="ComfyUI Plugin">
            </div>
        </div>
        </div>
    </div>

    <!-- Workflow JSON -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-code-slash text-info"></i>
                Workflow JSON
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
        
        <div class="mb-3">
            <label class="form-label">ComfyUI Workflow JSON</label>
            <textarea class="form-control json-editor" name="workflow" rows="15" id="workflowJson"
                      placeholder="粘贴 ComfyUI 的 workflow JSON 配置...">{}</textarea>
            <div class="form-text">
                这是 ComfyUI 的 workflow JSON 配置，可以从 ComfyUI 界面导出。
            </div>
        </div>
        
        <div class="mt-3">
            <button type="button" class="btn btn-outline-primary" onclick="parseWorkflowNodes()">
                <i class="bi bi-arrow-repeat"></i>
                解析节点
            </button>
            <div class="form-text">点击此按钮解析 JSON 中的所有节点，然后可以在下方选择节点</div>
        </div>
        </div>
    </div>

    <!-- 节点配置 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-diagram-2 text-success"></i>
                节点配置
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">输入节点</label>
            <div class="col-sm-10">
                <div id="inputNodesList">
                    <div class="input-group mb-2">
                        <select class="form-select node-selector" name="input_nodes" onchange="updateMappingPreview()">
                            <option value="">-- 选择节点 --</option>
                        </select>
                        <button type="button" class="btn btn-outline-danger" onclick="removeInputNode(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addInputNode()">
                    <i class="bi bi-plus"></i>
                    添加输入节点
                </button>
                <div class="form-text">输入节点是接收外部数据的节点，如图片输入节点</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">输出节点</label>
            <div class="col-sm-10">
                <div id="outputNodesList">
                    <div class="input-group mb-2">
                        <select class="form-select node-selector" name="output_nodes" onchange="updateMappingPreview()">
                            <option value="">-- 选择节点 --</option>
                        </select>
                        <button type="button" class="btn btn-outline-danger" onclick="removeOutputNode(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addOutputNode()">
                    <i class="bi bi-plus"></i>
                    添加输出节点
                </button>
                <div class="form-text">输出节点是最终输出结果的节点，如保存图片节点</div>
            </div>
        </div>
        
        <div class="row mb-3">
            <label class="col-sm-2 col-form-label">可配置节点</label>
            <div class="col-sm-10">
                <div id="configurableNodesList">
                    <div class="input-group mb-2">
                        <select class="form-select node-selector" name="configurable_nodes">
                            <option value="">-- 选择节点 --</option>
                        </select>
                        <button type="button" class="btn btn-outline-danger" onclick="removeConfigurableNode(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <button type="button" class="btn btn-outline-primary btn-sm" onclick="addConfigurableNode()">
                    <i class="bi bi-plus"></i>
                    添加可配置节点
                </button>
                <div class="form-text">可配置节点是用户可以在运行时修改参数的节点</div>
            </div>
        </div>
        </div>
    </div>

    <!-- 自动映射预览 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-arrow-left-right text-warning"></i>
                自动映射预览
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
        
        <div class="alert alert-info">
            <i class="bi bi-info-circle"></i>
            <strong>自动映射规则：</strong>输入节点将自动映射为输入参数，输出节点将自动映射为输出参数。无需手动配置。
        </div>
        
        <!-- 输入映射预览 -->
        <div class="row mb-4">
            <div class="col-12">
                <h6 class="mb-3">
                    <i class="bi bi-box-arrow-in-right"></i>
                    输入映射预览
                </h6>
                <div id="inputMappingsPreview" class="border rounded p-3 bg-light">
                    <p class="text-muted">暂无输入节点</p>
                </div>
            </div>
        </div>
        
        <!-- 输出映射预览 -->
        <div class="row">
            <div class="col-12">
                <h6 class="mb-3">
                    <i class="bi bi-box-arrow-right"></i>
                    输出映射预览
                </h6>
                <div id="outputMappingsPreview" class="border rounded p-3 bg-light">
                    <p class="text-muted">暂无输出节点</p>
                </div>
            </div>
        </div>
        </div>
    </div>

    <!-- 节点参数配置 -->
    <div class="config-section p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">
                <i class="bi bi-gear-fill text-warning"></i>
                节点参数配置
            </h5>
            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSection(this)" data-bs-toggle="tooltip" title="展开/折叠">
                <i class="bi bi-chevron-down"></i>
            </button>
        </div>
        
        <div class="section-content" style="display: none;">
            <div id="nodeConfigsContainer">
                <!-- 节点配置项将动态添加到这里 -->
            </div>
            
            <button type="button" class="btn btn-outline-primary" onclick="addNodeConfig()">
                <i class="bi bi-plus"></i>
                添加节点配置
            </button>
        </div>
    </div>





    <!-- 隐藏字段 -->
    <input type="hidden" name="input_mappings" id="inputMappingsInput">
    <input type="hidden" name="output_mappings" id="outputMappingsInput">
    <input type="hidden" name="node_configs" id="nodeConfigsInput">
</form>
{% endblock %}

{% block scripts %}<style>
.section-content {
    border-left: 3px solid #007bff;
    padding-left: 15px;
    margin-left: 10px;
    background-color: #f8f9fa;
    border-radius: 5px;
    padding: 15px;
}

.bi-chevron-down, .bi-chevron-up {
    transition: transform 0.2s ease;
}
</style>

<script>
// 解析 Workflow JSON 中的节点
function parseWorkflowNodes() {
    const workflowJson = document.getElementById('workflowJson').value;
    if (!workflowJson.trim()) {
        alert('请先输入 Workflow JSON');
        return;
    }
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodes = [];
        
        // 提取所有节点信息
        for (const [nodeId, nodeData] of Object.entries(workflow)) {
            const title = nodeData._meta?.title || nodeData.class_type || `节点 ${nodeId}`;
            nodes.push({
                id: nodeId,
                title: title,
                class_type: nodeData.class_type
            });
        }
        
        // 按节点ID排序
        nodes.sort((a, b) => parseInt(a.id) - parseInt(b.id));
        
        // 更新所有下拉选择框
        updateAllNodeSelectors(nodes);
        
    } catch (error) {
        console.error('JSON 格式错误：', error.message);
    }
}

// 更新所有节点选择器
function updateAllNodeSelectors(nodes) {
    const selectors = document.querySelectorAll('.node-selector');
    selectors.forEach(selector => {
        // 保存当前选中的值
        const currentValue = selector.value;
        
        // 清空并重新填充选项
        selector.innerHTML = '<option value="">-- 选择节点 --</option>';
        
        nodes.forEach(node => {
            const option = document.createElement('option');
            option.value = node.id;
            option.textContent = `${node.id} - ${node.title}`;
            if (node.class_type) {
                option.textContent += ` (${node.class_type})`;
            }
            selector.appendChild(option);
        });
        
        // 恢复之前选中的值
        if (currentValue) {
            selector.value = currentValue;
        }
    });
}

// 页面加载完成后的初始化
document.addEventListener('DOMContentLoaded', function() {
    updateMappingPreview();
});

// 添加输入节点
function addInputNode(value = '') {
    const list = document.getElementById('inputNodesList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <select class="form-select node-selector" name="input_nodes" onchange="updateMappingPreview()">
            <option value="">-- 选择节点 --</option>
        </select>
        <button type="button" class="btn btn-outline-danger" onclick="removeInputNode(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    list.appendChild(div);
    
    // 如果已经解析过节点，更新选择器
    const workflowJson = document.getElementById('workflowJson').value;
    if (workflowJson.trim()) {
        parseWorkflowNodes();
    }
    
    // 如果有预设值，设置它
    if (value) {
        setTimeout(() => {
            const selectors = list.querySelectorAll('.node-selector');
            const lastSelector = selectors[selectors.length - 1];
            if (lastSelector) {
                lastSelector.value = value;
            }
        }, 100);
    }
    
    // 更新映射预览
    updateMappingPreview();
}

function removeInputNode(button) {
    button.parentElement.remove();
    updateMappingPreview();
}

// 添加输出节点
function addOutputNode(value = '') {
    const list = document.getElementById('outputNodesList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <select class="form-select node-selector" name="output_nodes" onchange="updateMappingPreview()">
            <option value="">-- 选择节点 --</option>
        </select>
        <button type="button" class="btn btn-outline-danger" onclick="removeOutputNode(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    list.appendChild(div);
    
    // 如果已经解析过节点，更新选择器
    const workflowJson = document.getElementById('workflowJson').value;
    if (workflowJson.trim()) {
        parseWorkflowNodes();
    }
    
    // 如果有预设值，设置它
    if (value) {
        setTimeout(() => {
            const selectors = list.querySelectorAll('.node-selector');
            const lastSelector = selectors[selectors.length - 1];
            if (lastSelector) {
                lastSelector.value = value;
            }
        }, 100);
    }
    
    // 更新映射预览
    updateMappingPreview();
}

function removeOutputNode(button) {
    button.parentElement.remove();
    updateMappingPreview();
}

// 添加可配置节点
function addConfigurableNode(value = '') {
    const list = document.getElementById('configurableNodesList');
    const div = document.createElement('div');
    div.className = 'input-group mb-2';
    div.innerHTML = `
        <select class="form-select node-selector" name="configurable_nodes">
            <option value="">-- 选择节点 --</option>
        </select>
        <button type="button" class="btn btn-outline-danger" onclick="removeConfigurableNode(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    list.appendChild(div);
    
    // 如果已经解析过节点，更新选择器
    const workflowJson = document.getElementById('workflowJson').value;
    if (workflowJson.trim()) {
        parseWorkflowNodes();
    }
    
    // 如果有预设值，设置它
    if (value) {
        setTimeout(() => {
            const selectors = list.querySelectorAll('.node-selector');
            const lastSelector = selectors[selectors.length - 1];
            if (lastSelector) {
                lastSelector.value = value;
            }
        }, 100);
    }
}

function removeConfigurableNode(button) {
    button.parentElement.remove();
}

// 更新映射预览
function updateMappingPreview() {
    // 更新输入映射预览
    const inputNodes = Array.from(document.querySelectorAll('select[name="input_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const inputPreview = document.getElementById('inputMappingsPreview');
    if (inputNodes.length > 0) {
        const inputMappings = {};
        inputNodes.forEach(nodeId => {
            inputMappings[nodeId] = {
                parameter_name: "image",
                type: "image",
                description: "输入的原始图像"
            };
        });
        inputPreview.innerHTML = `<pre><code>${JSON.stringify(inputMappings, null, 2)}</code></pre>`;
    } else {
        inputPreview.innerHTML = '<p class="text-muted">暂无输入节点</p>';
    }
    
    // 更新输出映射预览
    const outputNodes = Array.from(document.querySelectorAll('select[name="output_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const outputPreview = document.getElementById('outputMappingsPreview');
    if (outputNodes.length > 0) {
        const outputMappings = {};
        outputNodes.forEach(nodeId => {
            outputMappings[nodeId] = {
                parameter_name: "images",
                type: "image",
                description: "处理后的图像"
            };
        });
        outputPreview.innerHTML = `<pre><code>${JSON.stringify(outputMappings, null, 2)}</code></pre>`;
    } else {
        outputPreview.innerHTML = '<p class="text-muted">暂无输出节点</p>';
    }
}

// 表单提交时的处理
document.getElementById('workflowForm').addEventListener('submit', function(e) {
    // 基本验证
    const workflowName = document.querySelector('input[name="workflow_name"]').value.trim();
    const displayName = document.querySelector('input[name="name"]').value.trim();
    const prefix = document.querySelector('input[name="prefix"]').value.trim();
    
    if (!workflowName) {
        e.preventDefault();
        alert('请输入工作流名称！');
        return;
    }
    
    if (!displayName) {
        e.preventDefault();
        alert('请输入显示名称！');
        return;
    }
    
    if (!prefix) {
        e.preventDefault();
        alert('请输入前缀！');
        return;
    }
    
    // 验证工作流名称只包含字母、数字、下划线和连字符
    if (!/^[a-zA-Z0-9_-]+$/.test(workflowName)) {
        e.preventDefault();
        alert('工作流名称只能包含字母、数字、下划线和连字符！');
        return;
    }
    
    // 自动生成输入输出映射
    const inputNodes = Array.from(document.querySelectorAll('select[name="input_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const outputNodes = Array.from(document.querySelectorAll('select[name="output_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    const inputMappings = {};
    inputNodes.forEach(nodeId => {
        inputMappings[nodeId] = {
            parameter_name: "image",
            type: "image",
            description: "输入的原始图像"
        };
    });
    
    const outputMappings = {};
    outputNodes.forEach(nodeId => {
        outputMappings[nodeId] = {
            parameter_name: "images",
            type: "image",
            description: "处理后的图像"
        };
    });
    
    document.getElementById('inputMappingsInput').value = JSON.stringify(inputMappings);
    document.getElementById('outputMappingsInput').value = JSON.stringify(outputMappings);
    
    // 获取 workflow JSON
    const workflowJson = document.getElementById('workflowJson').value.trim();
    if (workflowJson) {
        try {
            // 验证 JSON 格式
            JSON.parse(workflowJson);
        } catch (error) {
            e.preventDefault();
            alert('Workflow JSON 格式错误：' + error.message);
            return;
        }
    }
});

// 切换主要部分的展开/折叠状态
function toggleSection(button) {
    const section = button.closest('.config-section');
    const content = section.querySelector('.section-content');
    const icon = button.querySelector('i');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.classList.remove('bi-chevron-down');
        icon.classList.add('bi-chevron-up');
    } else {
        content.style.display = 'none';
        icon.classList.remove('bi-chevron-up');
        icon.classList.add('bi-chevron-down');
    }
}

// 添加节点配置
function addNodeConfig() {
    // 获取所有已选择的可配置节点
    const configurableNodes = Array.from(document.querySelectorAll('select[name="configurable_nodes"]'))
        .map(select => select.value.trim())
        .filter(v => v);
    
    // 获取已经配置了参数的节点
    const existingConfigNodes = Array.from(document.querySelectorAll('.param-item[data-node-id]'))
        .map(item => item.getAttribute('data-node-id'));
    
    // 过滤出还未配置参数的节点
    const availableNodes = configurableNodes.filter(nodeId => !existingConfigNodes.includes(nodeId));
    
    if (availableNodes.length === 0) {
        alert('没有可配置的节点，请先在上方选择可配置节点');
        return;
    }
    
    // 创建选择对话框
    let nodeOptions = availableNodes.map(nodeId => {
        const selectElement = Array.from(document.querySelectorAll('select[name="configurable_nodes"]'))
            .find(select => select.value === nodeId);
        const optionText = selectElement ? selectElement.options[selectElement.selectedIndex].text : nodeId;
        return `<option value="${nodeId}">${optionText}</option>`;
    }).join('');
    
    // 创建模态框选择节点
    const modalHtml = `
        <div class="modal fade" id="nodeSelectModal" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">选择要配置的节点</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <select class="form-select" id="nodeSelect">
                            ${nodeOptions}
                        </select>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-primary" onclick="confirmAddNodeConfig()">确定</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // 移除已存在的模态框
    const existingModal = document.getElementById('nodeSelectModal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // 添加新模态框
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('nodeSelectModal'));
    modal.show();
}

// 确认添加节点配置
function confirmAddNodeConfig() {
    const select = document.getElementById('nodeSelect');
    const nodeId = select.value;
    
    if (!nodeId) {
        alert('请选择一个节点');
        return;
    }
    
    const container = document.getElementById('nodeConfigsContainer');
    const div = document.createElement('div');
    div.className = 'param-item mb-4';
    div.setAttribute('data-node-id', nodeId);
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h6 class="mb-0">
                <i class="bi bi-cpu"></i>
                <span class="node-title" data-node-id="${nodeId}">节点 ${nodeId}</span>
            </h6>
            <div>
                <button type="button" class="btn btn-outline-secondary btn-sm me-2" onclick="toggleNodeConfig(this)" data-bs-toggle="tooltip" title="展开/折叠">
                    <i class="bi bi-chevron-down"></i>
                </button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeNodeConfig(this)">
                    <i class="bi bi-trash"></i>
                    删除节点配置
                </button>
            </div>
        </div>
        
        <div class="param-content" style="display: none;">
            <div class="row mb-3">
                <div class="col-12">
                    <div class="param-params-container">
                        <button type="button" class="btn btn-outline-primary btn-sm" onclick="addParamConfig(this)">
                            <i class="bi bi-plus"></i>
                            添加参数
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
    container.appendChild(div);
    
    // 更新新节点的标题
    updateNodeTitles();
    
    // 关闭模态框
    const modal = bootstrap.Modal.getInstance(document.getElementById('nodeSelectModal'));
    modal.hide();
}

function removeNodeConfig(button) {
    button.closest('.param-item').remove();
}

// 折叠/展开节点配置
function toggleNodeConfig(button) {
    const paramItem = button.closest('.param-item');
    const content = paramItem.querySelector('.param-content');
    const icon = button.querySelector('i');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
    } else {
        content.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
    }
}

// 更新节点标题显示
function updateNodeTitles() {
    const workflowJson = document.getElementById('workflowJson').value;
    if (!workflowJson.trim()) return;
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodeTitles = document.querySelectorAll('.node-title');
        
        nodeTitles.forEach(titleElement => {
            const nodeId = titleElement.getAttribute('data-node-id');
            const nodeData = workflow[nodeId];
            
            if (nodeData) {
                const title = nodeData._meta?.title || nodeData.class_type || `节点 ${nodeId}`;
                titleElement.textContent = `${nodeId} - ${title}`;
            }
        });
    } catch (error) {
        console.error('解析工作流JSON失败:', error);
    }
}

// 添加参数配置
function addParamConfig(button) {
    // 获取当前节点ID
    const nodeItem = button.closest('.param-item');
    const nodeId = nodeItem.getAttribute('data-node-id');
    
    if (!nodeId) {
        alert('无法获取节点ID');
        return;
    }
    
    // 解析工作流JSON获取该节点的inputs
    const workflowJson = document.getElementById('workflowJson').value;
    if (!workflowJson.trim()) {
        alert('请先解析工作流JSON');
        return;
    }
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodeData = workflow[nodeId];
        
        if (!nodeData || !nodeData.inputs) {
            alert(`节点 ${nodeId} 没有找到 inputs 数据`);
            return;
        }
        
        // 获取已配置的参数名
        const container = button.parentElement;
        const existingParams = Array.from(container.querySelectorAll('.param-config-item'))
            .map(item => item.getAttribute('data-param-name'));
        
        // 获取可配置的参数（排除已配置的）
        const availableInputs = Object.keys(nodeData.inputs).filter(paramName => 
            !existingParams.includes(paramName)
        );
        
        if (availableInputs.length === 0) {
            alert('该节点的所有参数都已配置');
            return;
        }
        
        // 创建选择对话框
        let inputOptions = availableInputs.map(paramName => {
            const inputValue = nodeData.inputs[paramName];
            let valueDisplay = '';
            
            // 处理不同类型的输入值显示
            if (Array.isArray(inputValue)) {
                valueDisplay = ` [连接: ${inputValue[0]}]`;
            } else if (typeof inputValue === 'string') {
                valueDisplay = ` = "${inputValue}"`;
            } else if (typeof inputValue === 'number') {
                valueDisplay = ` = ${inputValue}`;
            } else {
                valueDisplay = ` = ${JSON.stringify(inputValue)}`;
            }
            
            return `<option value="${paramName}">${paramName}${valueDisplay}</option>`;
        }).join('');
        
        // 创建模态框选择参数
        const modalHtml = `
            <div class="modal fade" id="paramSelectModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">选择要配置的参数 - 节点 ${nodeId}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <label class="form-label">可配置的参数：</label>
                            <select class="form-select" id="paramSelect">
                                ${inputOptions}
                            </select>
                            <div class="mt-3">
                                <small class="text-muted">注意：连接类型的参数（显示为[连接: xxx]）通常不需要配置，因为它们是节点间的连接。</small>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                            <button type="button" class="btn btn-primary" onclick="confirmAddParamConfig('${nodeId}')">确定</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // 移除已存在的模态框
        const existingModal = document.getElementById('paramSelectModal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // 添加新模态框
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('paramSelectModal'));
        modal.show();
        
    } catch (error) {
        console.error('解析工作流JSON失败:', error);
        alert('解析工作流JSON失败: ' + error.message);
    }
}

// 确认添加参数配置
function confirmAddParamConfig(nodeId) {
    const select = document.getElementById('paramSelect');
    const paramName = select.value;
    
    if (!paramName) {
        alert('请选择一个参数');
        return;
    }
    
    // 解析工作流JSON获取参数的默认值
    const workflowJson = document.getElementById('workflowJson').value;
    let defaultValue = '';
    
    try {
        const workflow = JSON.parse(workflowJson);
        const nodeData = workflow[nodeId];
        if (nodeData && nodeData.inputs && nodeData.inputs[paramName] !== undefined) {
            const inputValue = nodeData.inputs[paramName];
            if (!Array.isArray(inputValue)) { // 不是连接类型
                defaultValue = inputValue;
            }
        }
    } catch (error) {
        console.error('解析默认值失败:', error);
    }
    
    const container = document.getElementById('paramSelectModal').querySelector('.modal-body');
    const targetContainer = document.querySelector(`.param-item[data-node-id="${nodeId}"] .param-params-container`);
    
    const div = document.createElement('div');
    div.className = 'param-config-item mb-3 p-3 border rounded';
    div.setAttribute('data-param-name', paramName);
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <strong>${paramName}</strong>
            <div>
                <button type="button" class="btn btn-outline-secondary btn-sm me-2" onclick="toggleParamConfig(this)" data-bs-toggle="tooltip" title="展开/折叠">
                    <i class="bi bi-chevron-down"></i>
                </button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeParamConfig(this)">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
        
        <div class="param-config-content" style="display: none;">
            <div class="row">
                <div class="col-md-6 mb-2">
                    <label class="form-label">参数名</label>
                    <input type="text" class="form-control" name="param_name" 
                           value="${paramName}" readonly>
                </div>
                <div class="col-md-6 mb-2">
                    <label class="form-label">类型</label>
                    <select class="form-select" name="param_type">
                        <option value="text">文本</option>
                        <option value="number">数字</option>
                        <option value="select">选择</option>
                        <option value="boolean">布尔值</option>
                    </select>
                </div>
            </div>
            
            <div class="row">
                <div class="col-md-6 mb-2">
                    <label class="form-label">默认值</label>
                    <div class="param-default-container">
                        <input type="text" class="form-control param-default-text" name="param_default" 
                               value="${typeof defaultValue === 'string' ? defaultValue : ''}">
                        <input type="number" class="form-control param-default-number" name="param_default" 
                               value="${typeof defaultValue === 'number' ? defaultValue : ''}" 
                               style="display:none;" step="any">
                        <div class="form-check form-switch param-default-boolean" style="display:none;">
                            <input class="form-check-input" type="checkbox" name="param_default" onchange="updateBooleanLabel(this)">
                            <label class="form-check-label boolean-label">False</label>
                        </div>
                    </div>
                </div>
                <div class="col-md-6 mb-2">
                    <div class="form-check mt-4">
                        <input class="form-check-input" type="checkbox" name="param_required">
                        <label class="form-check-label">必需参数</label>
                    </div>
                </div>
            </div>
            
            <div class="mb-2">
                <label class="form-label">描述</label>
                <textarea class="form-control" name="param_description" rows="2"></textarea>
            </div>
            
            <div class="mb-2">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <label class="form-label mb-0">特殊功能</label>
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleSpecialFeatures(this)" data-bs-toggle="tooltip" title="展开/折叠">
                        <i class="bi bi-chevron-down"></i>
                    </button>
                </div>
                <div class="special-features-content" style="display: none;">
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_models">
                        <label class="form-check-label">注入可用模型列表</label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_loras">
                        <label class="form-check-label">注入可用LoRA列表</label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_samplers">
                        <label class="form-check-label">注入可用采样器列表</label>
                    </div>
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" name="param_inject_schedulers">
                        <label class="form-check-label">注入可用调度器列表</label>
                    </div>
                </div>
            </div>
            
            <div class="mb-2">
                <label class="form-label">别名 (用逗号分隔)</label>
                <input type="text" class="form-control" name="param_aliases">
            </div>
        </div>
    `;
    targetContainer.appendChild(div);
    
    // 关闭模态框
    const modal = bootstrap.Modal.getInstance(document.getElementById('paramSelectModal'));
    modal.hide();
}

function removeParamConfig(button) {
    button.closest('.param-config-item').remove();
}

function toggleParamConfig(button) {
    const paramItem = button.closest('.param-config-item');
    const content = paramItem.querySelector('.param-config-content');
    const icon = button.querySelector('i');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
    } else {
        content.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
    }
}

function toggleSpecialFeatures(button) {
    const paramItem = button.closest('.param-config-item');
    if (!paramItem) {
        console.error('无法找到 param-config-item 父元素');
        return;
    }
    
    let content = paramItem.querySelector('.special-features-content');
    if (!content) {
        console.error('无法找到 special-features-content 元素');
        return;
    }
    
    const icon = button.querySelector('i');
    if (!icon) {
        console.error('无法找到图标元素');
        return;
    }
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.classList.remove('bi-chevron-down');
        icon.classList.add('bi-chevron-up');
    } else {
        content.style.display = 'none';
        icon.classList.remove('bi-chevron-up');
        icon.classList.add('bi-chevron-down');
    }
}

function updateBooleanLabel(checkbox) {
    const label = checkbox.parentElement.querySelector('.boolean-label');
    if (label) {
        label.textContent = checkbox.checked ? 'True' : 'False';
    }
}
</script>
{% endblock %}
    """
    
    return html_template


@app.route('/workflow/create', methods=['POST'])
def workflow_create():
    """创建新工作流"""
    try:
        workflow_name = request.form.get('workflow_name', '').strip()
        
        if not workflow_name:
            flash('工作流名称不能为空！', 'error')
            return redirect(url_for('workflow_new'))
        
        # 检查工作流是否已存在
        workflow_path = WORKFLOW_DIR / workflow_name
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
        
        if config_manager.save_workflow(workflow_name, config, workflow_data):
            flash('工作流创建成功！', 'success')
            return redirect(url_for('workflow_detail', workflow_name=workflow_name))
        else:
            flash('工作流创建失败！', 'error')
            return redirect(url_for('workflow_new'))
            
    except Exception as e:
        logger.error(f"创建工作流失败: {e}")
        flash(f'创建失败: {str(e)}', 'error')
        return redirect(url_for('workflow_new'))


@app.route('/workflow/<workflow_name>/delete', methods=['POST'])
def workflow_delete(workflow_name):
    """删除工作流"""
    try:
        if config_manager.delete_workflow(workflow_name):
            flash('工作流删除成功！', 'success')
        else:
            flash('工作流删除失败！', 'error')
    except Exception as e:
        logger.error(f"删除工作流失败: {e}")
        flash(f'删除失败: {str(e)}', 'error')
    
    return redirect(url_for('index'))


@app.route('/api/workflow_templates')
def api_workflow_templates():
    """获取工作流模板"""
    templates = [
        {
            "name": "图像加密解密",
            "prefix": "encrypt",
            "description": "使用希尔伯特曲线对图像进行加密或解密处理",
            "version": "1.0.0",
            "author": "ComfyUI Plugin",
            "input_nodes": ["2"],
            "output_nodes": ["3"],
            "input_mappings": {
                "2": {
                    "parameter_name": "image",
                    "required": True,
                    "type": "image",
                    "description": "输入图片"
                }
            },
            "output_mappings": {
                "3": {
                    "parameter_name": "images",
                    "type": "image",
                    "description": "处理后的图片"
                }
            },
            "configurable_nodes": ["1", "3"],
            "node_configs": {
                "1": {
                    "mode": {
                        "type": "select",
                        "default": "encrypt",
                        "description": "处理模式：encrypt为加密模式，decrypt为解密模式",
                        "options": ["encrypt", "decrypt"],
                        "aliases": ["模式", "mode", "处理模式", "加密模式"]
                    },
                    "enable": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否启用加密/解密功能，false时直接输出原图",
                        "aliases": ["启用", "enable", "开启", "启用功能"]
                    }
                },
                "3": {
                    "filename_prefix": {
                        "type": "text",
                        "default": "ComfyUI",
                        "description": "保存图片的文件名前缀",
                        "aliases": ["文件前缀", "prefix", "文件名前缀", "保存前缀"]
                    }
                }
            }
        },
        {
            "name": "橘雪莉LoRA文生图",
            "prefix": "juxueli",
            "description": "使用橘雪莉LoRA进行文生图，支持图像加密功能",
            "version": "1.0.0",
            "author": "ComfyUI Plugin",
            "input_nodes": [],
            "output_nodes": ["9"],
            "input_mappings": {},
            "output_mappings": {
                "9": {
                    "parameter_name": "images",
                    "type": "image",
                    "description": "生成的图片"
                }
            },
            "configurable_nodes": ["6", "31", "33", "36", "30", "100", "44"],
            "node_configs": {
                "6": {
                    "text": {
                        "type": "text",
                        "required": True,
                        "default": "juxueli,blue_hair,embarrassed expression,outdoor,looking at viewer,hat,hair ornament,solo focus,gothic lolita dress,full body,front view",
                        "description": "正面提示词，描述想要生成的内容",
                        "aliases": ["提示词", "prompt", "positive_prompt", "正面提示"]
                    }
                },
                "31": {
                    "seed": {
                        "type": "number",
                        "default": -1,
                        "description": "随机种子，-1为随机种子，相同种子生成相同结果",
                        "min": -1,
                        "max": 4294967295,
                        "aliases": ["种子", "random_seed", "随机种子"]
                    },
                    "steps": {
                        "type": "number",
                        "default": 30,
                        "description": "采样步数，数值越大质量越高但速度越慢",
                        "min": 1,
                        "max": 150,
                        "aliases": ["步数", "inference_steps", "采样步数"]
                    },
                    "cfg": {
                        "type": "number",
                        "default": 6.5,
                        "description": "CFG系数，控制提示词对生成结果的影响强度",
                        "min": 1.0,
                        "max": 30.0,
                        "aliases": ["CFG", "cfg_scale", "CFG系数"]
                    },
                    "sampler_name": {
                        "type": "select",
                        "default": "euler",
                        "description": "采样器类型，影响生成风格和质量",
                        "options": ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde", "ddim", "uni_pc"],
                        "inject_samplers": True,
                        "aliases": ["采样器", "sampler", "采样方法"]
                    },
                    "scheduler": {
                        "type": "select",
                        "default": "simple",
                        "description": "调度器类型，影响采样过程",
                        "options": ["simple", "karras", "exponential", "normal", "sgm_uniform"],
                        "inject_schedulers": True,
                        "aliases": ["调度器", "scheduler_type", "调度方法"]
                    },
                    "denoise": {
                        "type": "number",
                        "default": 1.0,
                        "description": "噪声系数，文生图通常为1.0",
                        "min": 0.0,
                        "max": 1.0,
                        "aliases": ["噪声", "denoise_strength", "噪声系数"]
                    }
                },
                "33": {
                    "text": {
                        "type": "text",
                        "default": "bad quality,worst quality,worst detail, watermark, text, single background, incorrect cock position, unrealistic body structure, watermark, text, logo, composite roles, ((more than one cock)), cock through body, characteristics of multiple roles in one person, extra person, merged faces, blended features, crowd, chibi, doll, deformed anatomy, feature crossover",
                        "description": "负面提示词，描述不希望出现的内容",
                        "aliases": ["负面提示词", "negative_prompt", "负面提示"]
                    }
                },
                "36": {
                    "width": {
                        "type": "number",
                        "default": 1024,
                        "description": "生成图片的宽度像素",
                        "min": 64,
                        "max": 2048,
                        "aliases": ["宽度", "w", "image_width"]
                    },
                    "height": {
                        "type": "number",
                        "default": 1024,
                        "description": "生成图片的高度像素",
                        "min": 64,
                        "max": 2048,
                        "aliases": ["高度", "h", "image_height"]
                    },
                    "batch_size": {
                        "type": "number",
                        "default": 1,
                        "description": "批量生成数量，一次生成多张图片",
                        "min": 1,
                        "max": 6,
                        "aliases": ["批量", "batch", "批量数量"]
                    }
                },
                "30": {
                    "ckpt_name": {
                        "type": "select",
                        "default": "WAI_NSFW-illustrious-SDXL_v15.safetensors",
                        "description": "选择基础模型，决定生成风格",
                        "inject_models": True,
                        "aliases": ["模型", "model", "checkpoint", "基础模型"]
                    }
                },
                "100": {
                    "lora_name": {
                        "type": "select",
                        "default": "juxueli_v1.safetensors",
                        "description": "选择LoRA模型，用于特定角色或风格",
                        "options": ["juxueli_v1.safetensors"],
                        "aliases": ["LoRA", "lora", "LoRA模型"]
                    },
                    "strength_model": {
                        "type": "number",
                        "default": 1.0,
                        "description": "LoRA对模型的影响强度",
                        "min": 0.0,
                        "max": 2.0,
                        "aliases": ["LoRA模型强度", "lora_model_strength", "模型强度"]
                    },
                    "strength_clip": {
                        "type": "number",
                        "default": 1.0,
                        "description": "LoRA对CLIP文本编码的影响强度",
                        "min": 0.0,
                        "max": 2.0,
                        "aliases": ["LoRA文本强度", "lora_clip_strength", "文本强度"]
                    }
                },
                "44": {
                    "mode": {
                        "type": "select",
                        "default": "encrypt",
                        "description": "图像加密模式，encrypt为加密，decrypt为解密",
                        "options": ["encrypt", "decrypt"],
                        "aliases": ["模式", "encryption_mode", "加密模式"]
                    },
                    "enable": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否启用图像加密功能",
                        "aliases": ["启用", "enable", "开启加密"]
                    }
                }
            }
        }
    ]
    
    return jsonify(templates)


if __name__ == '__main__':
    print("🚀 启动 ComfyUI 配置管理界面...")
    print(f"📁 配置目录: {CONFIG_DIR}")
    print(f"🔧 工作流目录: {WORKFLOW_DIR}")
    print(f"🌐 访问地址: http://localhost:7777")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=7777, debug=True)
