// 全局变量
let workflows = {};
let mainConfig = {};

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 初始化工具提示
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // 根据页面类型初始化不同功能
    if (document.getElementById('mainConfigForm')) {
        initMainConfig();
    } else if (document.getElementById('workflowForm')) {
        initWorkflowEdit();
    }
});

// 主配置页面初始化
function initMainConfig() {
    loadMainConfig();
    loadWorkflows();
}

// 加载主配置
function loadMainConfig() {
    fetch('/api/config')
        .then(response => response.json())
        .then(data => {
            mainConfig = data;
            populateMainConfigForm(data);
        })
        .catch(error => {
            console.error('加载配置失败:', error);
            showAlert('加载配置失败', 'danger');
        });
}

// 填充主配置表单
function populateMainConfigForm(config) {
    // 填充服务器配置
    const serversList = document.getElementById('serversList');
    if (serversList && config.servers) {
        serversList.innerHTML = '';
        config.servers.forEach((server, index) => {
            addServerField(server, index);
        });
    }
    
    // 填充其他配置
    if (config.auto_save !== undefined) {
        document.getElementById('auto_save').checked = config.auto_save;
    }
    if (config.auto_zip !== undefined) {
        document.getElementById('auto_zip').checked = config.auto_zip;
    }
    if (config.default_workflow) {
        document.getElementById('default_workflow').value = config.default_workflow;
    }
}

// 添加服务器字段
function addServerField(server = null, index = null) {
    const serversList = document.getElementById('serversList');
    const serverDiv = document.createElement('div');
    serverDiv.className = 'card mb-3';
    serverDiv.innerHTML = `
        <div class="card-body">
            <div class="row">
                <div class="col-md-5">
                    <label class="form-label">服务器地址</label>
                    <input type="text" class="form-control" name="server_address" value="${server ? server.address : ''}" placeholder="http://localhost:8188">
                </div>
                <div class="col-md-3">
                    <label class="form-label">权重</label>
                    <input type="number" class="form-control" name="server_weight" value="${server ? server.weight : 1}" min="1" max="100">
                </div>
                <div class="col-md-2">
                    <label class="form-label">启用</label>
                    <div class="form-check mt-2">
                        <input class="form-check-input" type="checkbox" name="server_enabled" ${server ? (server.enabled ? 'checked' : '') : 'checked'}>
                    </div>
                </div>
                <div class="col-md-2">
                    <label class="form-label">操作</label>
                    <button type="button" class="btn btn-danger btn-sm" onclick="removeServerField(this)">删除</button>
                </div>
            </div>
        </div>
    `;
    serversList.appendChild(serverDiv);
}

// 删除服务器字段
function removeServerField(button) {
    button.closest('.card').remove();
}

// 保存主配置
function saveMainConfig() {
    const servers = [];
    const serverCards = document.querySelectorAll('#serversList .card');
    
    serverCards.forEach(card => {
        const address = card.querySelector('input[name="server_address"]').value;
        const weight = parseInt(card.querySelector('input[name="server_weight"]').value);
        const enabled = card.querySelector('input[name="server_enabled"]').checked;
        
        if (address) {
            servers.push({ address, weight, enabled });
        }
    });
    
    const config = {
        servers: servers,
        auto_save: document.getElementById('auto_save').checked,
        auto_zip: document.getElementById('auto_zip').checked,
        default_workflow: document.getElementById('default_workflow').value
    };
    
    fetch('/api/config', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(config)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showAlert('配置保存成功', 'success');
        } else {
            showAlert('配置保存失败: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('保存配置失败:', error);
        showAlert('保存配置失败', 'danger');
    });
}

// 加载工作流列表
function loadWorkflows() {
    fetch('/api/workflows')
        .then(response => response.json())
        .then(data => {
            workflows = data;
            populateWorkflowsList(data);
        })
        .catch(error => {
            console.error('加载工作流失败:', error);
            showAlert('加载工作流失败', 'danger');
        });
}

// 填充工作流列表
function populateWorkflowsList(workflowsData) {
    const container = document.getElementById('workflowsContainer');
    if (!container) return;
    
    container.innerHTML = '';
    
    Object.entries(workflowsData).forEach(([prefix, workflow]) => {
        const card = document.createElement('div');
        card.className = 'col-md-6 col-lg-4 mb-4';
        card.innerHTML = `
            <div class="card workflow-card h-100">
                <div class="card-body">
                    <h5 class="card-title">${workflow.name}</h5>
                    <p class="card-text text-muted small">${workflow.description}</p>
                    <div class="mb-2">
                        <span class="badge bg-primary">${prefix}</span>
                        <span class="badge bg-secondary">v${workflow.version}</span>
                    </div>
                    <div class="btn-group w-100" role="group">
                        <a href="/workflow/${prefix}" class="btn btn-outline-primary btn-sm">查看</a>
                        <a href="/workflow/${prefix}/edit" class="btn btn-outline-secondary btn-sm">编辑</a>
                        <button class="btn btn-outline-danger btn-sm" onclick="deleteWorkflow('${prefix}')">删除</button>
                    </div>
                </div>
            </div>
        `;
        container.appendChild(card);
    });
}

// 删除工作流
function deleteWorkflow(prefix) {
    if (!confirm(`确定要删除工作流 "${prefix}" 吗？此操作不可恢复。`)) {
        return;
    }
    
    fetch(`/api/workflow/${prefix}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showAlert('工作流删除成功', 'success');
            loadWorkflows(); // 重新加载列表
        } else {
            showAlert('删除失败: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('删除工作流失败:', error);
        showAlert('删除失败', 'danger');
    });
}

// 工作流编辑页面初始化
function initWorkflowEdit() {
    const workflowPrefix = document.body.dataset.workflowPrefix;
    if (workflowPrefix) {
        loadWorkflowForEdit(workflowPrefix);
    }
}

// 加载工作流进行编辑
function loadWorkflowForEdit(prefix) {
    fetch(`/api/workflow/${prefix}`)
        .then(response => response.json())
        .then(data => {
            populateWorkflowEditForm(data);
        })
        .catch(error => {
            console.error('加载工作流失败:', error);
            showAlert('加载工作流失败', 'danger');
        });
}

// 填充工作流编辑表单
function populateWorkflowEditForm(workflow) {
    document.getElementById('workflow_name').value = workflow.name || '';
    document.getElementById('workflow_prefix').value = workflow.prefix || '';
    document.getElementById('workflow_description').value = workflow.description || '';
    document.getElementById('workflow_version').value = workflow.version || '';
    document.getElementById('workflow_author').value = workflow.author || '';
    
    // 填充JSON配置
    document.getElementById('workflow_json').value = JSON.stringify(workflow, null, 2);
}

// 验证JSON格式
function validateJson() {
    const jsonInput = document.getElementById('workflow_json').value;
    try {
        JSON.parse(jsonInput);
        document.getElementById('jsonValidation').innerHTML = '<span class="text-success">✓ JSON格式正确</span>';
        return true;
    } catch (error) {
        document.getElementById('jsonValidation').innerHTML = `<span class="text-danger">✗ JSON格式错误: ${error.message}</span>`;
        return false;
    }
}

// 格式化JSON
function formatJson() {
    const jsonInput = document.getElementById('workflow_json').value;
    try {
        const parsed = JSON.parse(jsonInput);
        document.getElementById('workflow_json').value = JSON.stringify(parsed, null, 2);
        validateJson();
    } catch (error) {
        showAlert('JSON格式错误，无法格式化', 'danger');
    }
}

// 保存工作流
function saveWorkflow() {
    if (!validateJson()) {
        showAlert('请先修正JSON格式错误', 'danger');
        return;
    }
    
    const workflowData = JSON.parse(document.getElementById('workflow_json').value);
    
    fetch('/api/workflow', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(workflowData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showAlert('工作流保存成功', 'success');
            setTimeout(() => {
                window.location.href = '/workflow/' + workflowData.prefix;
            }, 1500);
        } else {
            showAlert('保存失败: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('保存工作流失败:', error);
        showAlert('保存失败', 'danger');
    });
}

// 显示提示消息
function showAlert(message, type = 'info') {
    const alertContainer = document.getElementById('alertContainer');
    if (!alertContainer) return;
    
    const alert = document.createElement('div');
    alert.className = `alert alert-${type} alert-dismissible fade show`;
    alert.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    alertContainer.appendChild(alert);
    
    // 3秒后自动消失
    setTimeout(() => {
        if (alert.parentNode) {
            alert.remove();
        }
    }, 3000);
}

// 检查服务器状态
function checkServerStatus() {
    const statusContainer = document.getElementById('serverStatusContainer');
    if (!statusContainer) return;
    
    statusContainer.innerHTML = '<div class="loading-spinner"></div> 检查中...';
    
    fetch('/api/server_status')
        .then(response => response.json())
        .then(data => {
            statusContainer.innerHTML = '';
            
            data.servers.forEach(server => {
                const statusDiv = document.createElement('div');
                statusDiv.className = 'd-flex align-items-center mb-2';
                statusDiv.innerHTML = `
                    <span class="server-status ${server.online ? 'online' : 'offline'}"></span>
                    <span>${server.address} - ${server.online ? '在线' : '离线'}</span>
                `;
                statusContainer.appendChild(statusDiv);
            });
        })
        .catch(error => {
            console.error('检查服务器状态失败:', error);
            statusContainer.innerHTML = '<span class="text-danger">检查失败</span>';
        });
}