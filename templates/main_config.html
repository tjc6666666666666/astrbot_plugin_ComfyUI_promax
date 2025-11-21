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