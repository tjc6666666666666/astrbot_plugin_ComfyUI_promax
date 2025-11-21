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

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_file
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
    return render_template('index.html', workflows=workflows)


@app.route('/main_config')
def main_config():
    """主配置页面"""
    config = config_manager.load_main_config()
    return render_template('main_config.html', config=config)


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
    
    return render_template('workflow_detail.html', workflow=workflow)


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
    
    return render_template('workflow_edit.html', workflow=workflow)


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
    return render_template('workflow_new.html')


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