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