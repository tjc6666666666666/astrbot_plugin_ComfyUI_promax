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