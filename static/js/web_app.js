// 全局变量
let currentUser = null;
let authToken = null;
let serverStatus = null;
let availableModels = [];
let availableLoRAs = [];
let availableWorkflows = [];
let generationHistory = [];
let currentGenerationTask = null;

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 确保加载遮罩层是隐藏的 - 使用Bootstrap类和内联样式双重保险
    const loadingOverlay = document.getElementById('loadingOverlay');
    if (loadingOverlay) {
        loadingOverlay.classList.remove('d-flex');
        loadingOverlay.classList.add('d-none');
        loadingOverlay.style.display = 'none !important';
    }
    
    // 检查是否已登录
    const savedToken = localStorage.getItem('authToken');
    const savedUser = localStorage.getItem('currentUser');
    
    if (savedToken && savedUser) {
        authToken = savedToken;
        currentUser = JSON.parse(savedUser);
        showApp();
    } else {
        showLogin();
    }
    
    // 绑定导航事件
    bindNavigationEvents();
    
    // 绑定表单事件
    bindFormEvents();
    
    // 初始化工具提示
    initTooltips();
    
    // 安全检查：确保遮罩层在页面加载完成后是隐藏的
    setTimeout(() => {
        const loadingOverlay = document.getElementById('loadingOverlay');
        if (loadingOverlay) {
            loadingOverlay.classList.remove('d-flex');
            loadingOverlay.classList.add('d-none');
            loadingOverlay.style.display = 'none !important';
            console.log('Force hiding loading overlay with Bootstrap classes');
        }
    }, 100);
    
    // 全局错误处理：确保在任何错误情况下遮罩层都不会卡住
    window.addEventListener('error', function() {
        const loadingOverlay = document.getElementById('loadingOverlay');
        if (loadingOverlay) {
            loadingOverlay.classList.remove('d-flex');
            loadingOverlay.classList.add('d-none');
            loadingOverlay.style.display = 'none !important';
        }
    });
    
    window.addEventListener('unhandledrejection', function() {
        const loadingOverlay = document.getElementById('loadingOverlay');
        if (loadingOverlay) {
            loadingOverlay.classList.remove('d-flex');
            loadingOverlay.classList.add('d-none');
            loadingOverlay.style.display = 'none !important';
        }
    });
});

// 显示登录页面
function showLogin() {
    document.getElementById('loginPage').style.display = 'flex';
    document.getElementById('registerPage').style.display = 'none';
    document.getElementById('appPages').style.display = 'none';
}

// 显示注册页面
function showRegister() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('registerPage').style.display = 'flex';
    document.getElementById('appPages').style.display = 'none';
}

// 显示应用主界面
function showApp() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('registerPage').style.display = 'none';
    document.getElementById('appPages').style.display = 'block';
    
    // 更新用户信息
    document.getElementById('username').textContent = currentUser.username;
    document.getElementById('settingsUsername').value = currentUser.username;
    
    // 加载服务器状态和可用资源
    loadServerStatus();
    loadAvailableResources();
    loadGenerationHistory();
    
    // 显示默认页面
    showPage('txt2img');
}

// 绑定导航事件
function bindNavigationEvents() {
    // 导航链接点击事件
    document.querySelectorAll('[data-page]').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const page = this.getAttribute('data-page');
            showPage(page);
        });
    });
}

// 显示指定页面
function showPage(pageName) {
    // 隐藏所有页面
    document.querySelectorAll('.page').forEach(page => {
        page.style.display = 'none';
    });
    
    // 显示目标页面
    const targetPage = document.getElementById(pageName + 'Page');
    if (targetPage) {
        targetPage.style.display = 'block';
        targetPage.classList.add('fade-in');
    }
    
    // 更新导航状态
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
    });
    document.querySelector(`[data-page="${pageName}"]`).classList.add('active');
    
    // 页面特定的初始化
    switch(pageName) {
        case 'workflows':
            loadWorkflows();
            break;
        case 'history':
            displayGenerationHistory();
            break;
        case 'settings':
            loadSettings();
            break;
    }
}

// 绑定表单事件
function bindFormEvents() {
    // 登录表单
    document.getElementById('loginForm').addEventListener('submit', function(e) {
        e.preventDefault();
        handleLogin();
    });
    
    // 注册表单
    document.getElementById('registerForm').addEventListener('submit', function(e) {
        e.preventDefault();
        handleRegister();
    });
    
    // 文生图表单
    document.getElementById('txt2imgForm').addEventListener('submit', function(e) {
        e.preventDefault();
        handleTxt2Img();
    });
    
    // 图生图表单
    document.getElementById('img2imgForm').addEventListener('submit', function(e) {
        e.preventDefault();
        handleImg2Img();
    });
    
    // 图片上传预览
    document.getElementById('inputImage').addEventListener('change', function(e) {
        previewImage(e.target.files[0], 'imagePreview');
    });
    
    // 噪声强度滑块
    document.getElementById('denoise').addEventListener('input', function(e) {
        document.getElementById('denoiseValue').textContent = e.target.value;
    });
    
    // 随机种子复选框
    document.getElementById('randomSeed').addEventListener('change', function(e) {
        const seedInput = document.getElementById('seed');
        seedInput.disabled = e.target.checked;
        if (e.target.checked) {
            seedInput.value = '';
        }
    });
    
    document.getElementById('img2imgRandomSeed').addEventListener('change', function(e) {
        const seedInput = document.getElementById('img2imgSeed');
        seedInput.disabled = e.target.checked;
        if (e.target.checked) {
            seedInput.value = '';
        }
    });
}

// 处理登录
async function handleLogin() {
    const username = document.getElementById('loginUsername').value;
    const password = document.getElementById('loginPassword').value;
    
    try {
        const response = await fetch('/api/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            authToken = data.token;
            currentUser = { username: data.username };
            
            // 保存到本地存储
            localStorage.setItem('authToken', authToken);
            localStorage.setItem('currentUser', JSON.stringify(currentUser));
            
            showToast('登录成功', 'success');
            showApp();
        } else {
            showToast(data.error || '登录失败', 'danger');
        }
    } catch (error) {
        showToast('网络错误，请重试', 'danger');
        console.error('Login error:', error);
    }
}

// 处理注册
async function handleRegister() {
    const username = document.getElementById('registerUsername').value;
    const password = document.getElementById('registerPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    
    if (password !== confirmPassword) {
        showToast('两次输入的密码不一致', 'danger');
        return;
    }
    
    try {
        const response = await fetch('/api/register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            authToken = data.token;
            currentUser = { username: data.username };
            
            // 保存到本地存储
            localStorage.setItem('authToken', authToken);
            localStorage.setItem('currentUser', JSON.stringify(currentUser));
            
            showToast('注册成功', 'success');
            showApp();
        } else {
            showToast(data.error || '注册失败', 'danger');
        }
    } catch (error) {
        showToast('网络错误，请重试', 'danger');
        console.error('Register error:', error);
    }
}

// 退出登录
function logout() {
    localStorage.removeItem('authToken');
    localStorage.removeItem('currentUser');
    authToken = null;
    currentUser = null;
    showLogin();
    showToast('已退出登录', 'info');
}

// 加载服务器状态
async function loadServerStatus() {
    try {
        const response = await apiRequest('/api/status', 'GET');
        
        if (response) {
            serverStatus = response;
            displayServerStatus(response);
            
            // 加载采样器和调度器选项
            loadSamplerOptions(response.samplers || []);
            loadSchedulerOptions(response.schedulers || []);
            
            // 加载模型和LoRA选项
            availableModels = response.models || [];
            availableLoRAs = response.loras || [];
            loadModelOptions();
            loadLoRAOptions();
        }
    } catch (error) {
        console.error('Failed to load server status:', error);
        displayServerStatusError();
    }
}

// 显示服务器状态
function displayServerStatus(status) {
    const container = document.getElementById('serverStatus');
    
    let html = '<div class="server-list">';
    
    status.servers.forEach(server => {
        const statusClass = server.healthy ? (server.busy ? 'busy' : 'online') : 'offline';
        const statusText = server.healthy ? (server.busy ? '忙碌' : '在线') : '离线';
        
        html += `
            <div class="d-flex align-items-center mb-2 p-2 border rounded">
                <span class="server-status ${statusClass}"></span>
                <div class="flex-grow-1">
                    <div class="fw-bold">${server.name}</div>
                    <small class="text-muted">${statusText}</small>
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    container.innerHTML = html;
}

// 显示服务器状态错误
function displayServerStatusError() {
    const container = document.getElementById('serverStatus');
    container.innerHTML = `
        <div class="text-center text-danger">
            <i class="bi bi-exclamation-triangle"></i>
            <p>无法获取服务器状态</p>
        </div>
    `;
}

// 加载可用资源
async function loadAvailableResources() {
    try {
        const response = await apiRequest('/api/status', 'GET');
        
        if (response) {
            availableModels = response.models || [];
            availableLoRAs = response.loras || [];
            availableWorkflows = response.workflows || [];
            
            // 更新UI
            updateModelSelects();
            updateLoRASelects();
        }
    } catch (error) {
        console.error('Failed to load available resources:', error);
    }
}

// 更新模型选择框
function updateModelSelects() {
    const selects = ['model', 'img2imgModel'];
    
    selects.forEach(selectId => {
        const select = document.getElementById(selectId);
        if (select) {
            // 保留默认选项
            const defaultOption = select.querySelector('option[value=""]');
            select.innerHTML = '';
            if (defaultOption) {
                select.appendChild(defaultOption);
            }
            
            // 添加可用模型
            availableModels.forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                select.appendChild(option);
            });
        }
    });
}

// 更新LoRA选择框
function updateLoRASelects() {
    const selects = document.querySelectorAll('.lora-select');
    
    selects.forEach(select => {
        // 保留默认选项
        const defaultOption = select.querySelector('option[value=""]');
        select.innerHTML = '';
        if (defaultOption) {
            select.appendChild(defaultOption);
        }
        
        // 添加可用LoRA
        availableLoRAs.forEach(lora => {
            const option = document.createElement('option');
            option.value = lora;
            option.textContent = lora;
            select.appendChild(option);
        });
    });
}

// 添加LoRA
function addLora() {
    const container = document.getElementById('loraContainer');
    const loraItem = document.createElement('div');
    loraItem.className = 'lora-item mb-2';
    
    loraItem.innerHTML = `
        <select class="form-select lora-select">
            <option value="">选择LoRA</option>
        </select>
        <input type="number" class="form-control lora-strength" placeholder="强度" min="0" max="2" step="0.1" value="1.0">
        <button type="button" class="btn btn-sm btn-danger" onclick="removeLora(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    
    container.appendChild(loraItem);
    updateLoRASelects(); // 更新新添加的选择框
}

// 移除LoRA
function removeLora(button) {
    const loraItem = button.closest('.lora-item');
    loraItem.remove();
}

// 添加图生图LoRA
function addImg2ImgLora() {
    const container = document.getElementById('img2imgLoraContainer');
    const loraItem = document.createElement('div');
    loraItem.className = 'lora-item mb-2';
    
    loraItem.innerHTML = `
        <select class="form-select img2img-lora-select">
            <option value="">选择LoRA</option>
        </select>
        <input type="number" class="form-control lora-strength" placeholder="强度" min="0" max="2" step="0.1" value="1.0">
        <button type="button" class="btn btn-sm btn-danger" onclick="removeImg2ImgLora(this)">
            <i class="bi bi-trash"></i>
        </button>
    `;
    
    container.appendChild(loraItem);
    loadLoRAOptions(); // 更新新添加的选择框
}

// 移除图生图LoRA
function removeImg2ImgLora(button) {
    const loraItem = button.closest('.lora-item');
    loraItem.remove();
}

// 处理文生图
async function handleTxt2Img() {
    const formData = collectTxt2ImgData();
    
    if (!formData.prompt.trim()) {
        showToast('请输入提示词', 'warning');
        return;
    }
    
    try {
        showLoading(true);
        const response = await apiRequest('/api/aimg', 'POST', formData);
        
        if (response && response.success) {
            showGenerationResult(response);
            addToHistory('txt2img', formData, response);
            showToast('图片生成成功', 'success');
        } else {
            showToast(response?.error || '生成失败', 'danger');
        }
    } catch (error) {
        showToast('生成失败，请重试', 'danger');
        console.error('Txt2Img error:', error);
    } finally {
        showLoading(false);
    }
}

// 收集文生图数据
function collectTxt2ImgData() {
    const loras = [];
    document.querySelectorAll('.lora-item').forEach(item => {
        const loraSelect = item.querySelector('.lora-select:not(.img2img-lora-select)');
        const strengthInput = item.querySelector('.lora-strength');
        
        if (loraSelect && loraSelect.value && strengthInput.value) {
            loras.push({
                name: loraSelect.value,
                strength: parseFloat(strengthInput.value)
            });
        }
    });
    
    return {
        prompt: document.getElementById('prompt').value,
        negative_prompt: document.getElementById('negativePrompt').value,
        width: parseInt(document.getElementById('width').value),
        height: parseInt(document.getElementById('height').value),
        batch_size: parseInt(document.getElementById('batchSize').value),
        seed: document.getElementById('randomSeed').checked ? -1 : parseInt(document.getElementById('seed').value) || -1,
        model: document.getElementById('model').value || null,
        sampler: document.getElementById('sampler').value,
        scheduler: document.getElementById('scheduler').value,
        lora: loras
    };
}

// 处理图生图
async function handleImg2Img() {
    const fileInput = document.getElementById('inputImage');
    const formData = collectImg2ImgData();
    
    if (!fileInput.files[0]) {
        showToast('请选择输入图片', 'warning');
        return;
    }
    
    if (!formData.prompt.trim()) {
        showToast('请输入提示词', 'warning');
        return;
    }
    
    try {
        showLoading(true);
        const formDataObj = new FormData();
        formDataObj.append('image', fileInput.files[0]);
        formDataObj.append('prompt', formData.prompt);
        formDataObj.append('denoise', formData.denoise);
        formDataObj.append('batch_size', formData.batch_size);
        formDataObj.append('seed', formData.seed);
        if (formData.model) {
            formDataObj.append('model', formData.model);
        }
        
        // 添加LoRA数据到FormData
        const loras = [];
        document.querySelectorAll('#img2imgLoraContainer .lora-item').forEach(item => {
            const loraSelect = item.querySelector('.img2img-lora-select');
            const strengthInput = item.querySelector('.lora-strength');
            
            if (loraSelect && loraSelect.value && strengthInput.value) {
                loras.push({
                    name: loraSelect.value,
                    strength: parseFloat(strengthInput.value)
                });
            }
        });
        
        formDataObj.append('lora', JSON.stringify(loras));
        
        const response = await apiRequest('/api/img2img', 'POST', formDataObj);
        
        if (response && response.success) {
            showGenerationResult(response);
            addToHistory('img2img', formData, response);
            showToast('图片生成成功', 'success');
        } else {
            showToast(response?.error || '生成失败', 'danger');
        }
    } catch (error) {
        showToast('生成失败，请重试', 'danger');
        console.error('Img2Img error:', error);
    } finally {
        showLoading(false);
    }
}

// 收集图生图数据
function collectImg2ImgData() {
    const loras = [];
    document.querySelectorAll('#img2imgLoraContainer .lora-item').forEach(item => {
        const loraSelect = item.querySelector('.img2img-lora-select');
        const strengthInput = item.querySelector('.lora-strength');
        
        if (loraSelect && loraSelect.value && strengthInput.value) {
            loras.push({
                name: loraSelect.value,
                strength: parseFloat(strengthInput.value)
            });
        }
    });
    
    return {
        prompt: document.getElementById('img2imgPrompt').value,
        denoise: parseFloat(document.getElementById('denoise').value),
        batch_size: parseInt(document.getElementById('img2imgBatchSize').value),
        seed: document.getElementById('img2imgRandomSeed').checked ? -1 : parseInt(document.getElementById('img2imgSeed').value) || -1,
        model: document.getElementById('img2imgModel').value || null,
        sampler: document.getElementById('img2imgSampler').value,
        scheduler: document.getElementById('img2imgScheduler').value,
        lora: loras
    };
}

// 显示生成结果
function showGenerationResult(result) {
    const container = document.getElementById('resultContainer');
    
    let html = '<div class="row">';
    
    if (result.images && result.images.length > 0) {
        result.images.forEach((imageUrl, index) => {
            html += `
                <div class="col-md-6 mb-3">
                    <img src="${imageUrl}" class="result-image" alt="Generated image ${index + 1}" 
                         onclick="window.open('${imageUrl}', '_blank')">
                </div>
            `;
        });
    }
    
    html += '</div>';
    
    // 添加生成信息
    html += `
        <div class="result-info">
            <h6>生成信息</h6>
            <div class="row">
                <div class="col-md-6">
                    <p><strong>提示词:</strong> ${result.prompt || 'N/A'}</p>
                    <p><strong>种子:</strong> ${result.seed || 'N/A'}</p>
                    <p><strong>模型:</strong> ${result.model || '默认'}</p>
                </div>
                <div class="col-md-6">
                    <p><strong>宽度:</strong> ${result.width || 'N/A'}</p>
                    <p><strong>高度:</strong> ${result.height || 'N/A'}</p>
                    <p><strong>LoRA数量:</strong> ${result.lora_count || 0}</p>
                </div>
            </div>
        </div>
    `;
    
    container.innerHTML = html;
    
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('resultModal'));
    modal.show();
    
    // 保存图片URL用于下载
    window.currentResultImages = result.images || [];
}

// 添加到历史记录
function addToHistory(type, params, result) {
    const historyItem = {
        id: Date.now(),
        type: type,
        timestamp: new Date().toISOString(),
        params: params,
        result: result
    };
    
    generationHistory.unshift(historyItem);
    
    // 限制历史记录数量
    if (generationHistory.length > 50) {
        generationHistory = generationHistory.slice(0, 50);
    }
    
    // 保存到本地存储
    localStorage.setItem('generationHistory', JSON.stringify(generationHistory));
    
    // 更新显示
    displayGenerationHistory();
}

// 显示生成历史
function displayGenerationHistory() {
    const container = document.getElementById('historyContainer');
    
    if (generationHistory.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5">
                <i class="bi bi-clock-history display-4 text-muted"></i>
                <h5 class="mt-3 text-muted">暂无生成历史</h5>
                <p class="text-muted">开始生成图片后，历史记录将显示在这里</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    
    generationHistory.forEach(item => {
        const date = new Date(item.timestamp);
        const typeText = item.type === 'txt2img' ? '文生图' : '图生图';
        
        html += `
            <div class="history-item">
                <div class="row align-items-center">
                    <div class="col-auto">
                        ${item.result.images && item.result.images.length > 0 ? 
                            `<img src="${item.result.images[0]}" class="history-image" 
                                 onclick="window.open('${item.result.images[0]}', '_blank')">` : 
                            '<div class="history-placeholder"><i class="bi bi-image"></i></div>'
                        }
                    </div>
                    <div class="col">
                        <h6>${typeText}</h6>
                        <p class="mb-1"><strong>提示词:</strong> ${item.params.prompt}</p>
                        <small class="text-muted">${date.toLocaleString()}</small>
                    </div>
                    <div class="col-auto">
                        <button class="btn btn-sm btn-outline-primary" onclick="regenerateFromHistory(${item.id})">
                            <i class="bi bi-arrow-clockwise"></i> 重新生成
                        </button>
                    </div>
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// 加载生成历史
function loadGenerationHistory() {
    const saved = localStorage.getItem('generationHistory');
    if (saved) {
        try {
            generationHistory = JSON.parse(saved);
        } catch (error) {
            console.error('Failed to load generation history:', error);
            generationHistory = [];
        }
    }
}

// 从历史记录重新生成
function regenerateFromHistory(historyId) {
    const historyItem = generationHistory.find(item => item.id === historyId);
    if (!historyItem) return;
    
    // 切换到对应页面
    showPage(historyItem.type);
    
    // 填充表单
    if (historyItem.type === 'txt2img') {
        fillTxt2ImgForm(historyItem.params);
    } else if (historyItem.type === 'img2img') {
        fillImg2ImgForm(historyItem.params);
    }
}

// 填充文生图表单
function fillTxt2ImgForm(params) {
    document.getElementById('prompt').value = params.prompt || '';
    document.getElementById('negativePrompt').value = params.negative_prompt || '';
    document.getElementById('width').value = params.width || 512;
    document.getElementById('height').value = params.height || 512;
    document.getElementById('batchSize').value = params.batch_size || 1;
    document.getElementById('sampler').value = params.sampler || 'euler';
    document.getElementById('scheduler').value = params.scheduler || '';
    
    if (params.seed && params.seed !== -1) {
        document.getElementById('seed').value = params.seed;
        document.getElementById('randomSeed').checked = false;
        document.getElementById('seed').disabled = false;
    }
    
    if (params.model) {
        document.getElementById('model').value = params.model;
    }
    
    // 清空并重新添加LoRA
    const container = document.getElementById('loraContainer');
    container.innerHTML = '';
    
    if (params.lora && params.lora.length > 0) {
        params.lora.forEach(lora => {
            addLora();
            const lastLoraItem = container.lastElementChild;
            lastLoraItem.querySelector('.lora-select').value = lora.name || lora;
            lastLoraItem.querySelector('.lora-strength').value = lora.strength || 1.0;
        });
    }
}

// 填充图生图表单
function fillImg2ImgForm(params) {
    document.getElementById('img2imgPrompt').value = params.prompt || '';
    document.getElementById('denoise').value = params.denoise || 0.7;
    document.getElementById('denoiseValue').textContent = params.denoise || 0.7;
    document.getElementById('img2imgBatchSize').value = params.batch_size || 1;
    
    if (params.seed && params.seed !== -1) {
        document.getElementById('img2imgSeed').value = params.seed;
        document.getElementById('img2imgRandomSeed').checked = false;
        document.getElementById('img2imgSeed').disabled = false;
    }
    
    if (params.model) {
        document.getElementById('img2imgModel').value = params.model;
    }
    
    if (params.sampler) {
        document.getElementById('img2imgSampler').value = params.sampler;
    }
    
    if (params.scheduler) {
        document.getElementById('img2imgScheduler').value = params.scheduler;
    }
    
    // 清空并重新添加LoRA
    const container = document.getElementById('img2imgLoraContainer');
    container.innerHTML = '';
    
    if (params.lora && params.lora.length > 0) {
        params.lora.forEach(lora => {
            addImg2ImgLora();
            const lastLoraItem = container.lastElementChild;
            lastLoraItem.querySelector('.img2img-lora-select').value = lora.name || lora;
            lastLoraItem.querySelector('.lora-strength').value = lora.strength || 1.0;
        });
    }
}

// 加载工作流
async function loadWorkflows() {
    const container = document.getElementById('workflowsContainer');
    
    if (availableWorkflows.length === 0) {
        container.innerHTML = `
            <div class="col-12">
                <div class="text-center py-5">
                    <i class="bi bi-diagram-3 display-4 text-muted"></i>
                    <h5 class="mt-3 text-muted">暂无可用工作流</h5>
                    <p class="text-muted">请联系管理员添加工作流配置</p>
                </div>
            </div>
        `;
        return;
    }
    
    let html = '';
    
    availableWorkflows.forEach(workflow => {
        html += `
            <div class="col-md-6 col-lg-4 mb-4">
                <div class="card workflow-card h-100" onclick="executeWorkflow('${workflow}')">
                    <div class="card-body text-center">
                        <i class="bi bi-diagram-3-fill display-4 text-primary mb-3"></i>
                        <h5 class="card-title">${workflow}</h5>
                        <p class="card-text text-muted">点击执行此工作流</p>
                        <button class="btn btn-primary">
                            <i class="bi bi-play"></i> 执行
                        </button>
                    </div>
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// 执行工作流
async function executeWorkflow(workflowName) {
    // 这里可以实现工作流执行逻辑
    showToast(`工作流执行功能待实现: ${workflowName}`, 'info');
}

// 加载设置
function loadSettings() {
    // 从本地存储加载设置
    const settings = JSON.parse(localStorage.getItem('userSettings') || '{}');
    
    document.getElementById('defaultWidth').value = settings.defaultWidth || '512';
    document.getElementById('defaultHeight').value = settings.defaultHeight || '512';
}

// 保存设置
function saveSettings() {
    const settings = {
        defaultWidth: document.getElementById('defaultWidth').value,
        defaultHeight: document.getElementById('defaultHeight').value
    };
    
    localStorage.setItem('userSettings', JSON.stringify(settings));
    showToast('设置保存成功', 'success');
}

// 重置文生图表单
function resetTxt2ImgForm() {
    document.getElementById('txt2imgForm').reset();
    document.getElementById('randomSeed').checked = true;
    document.getElementById('seed').disabled = true;
    
    // 清空LoRA容器
    const container = document.getElementById('loraContainer');
    container.innerHTML = `
        <div class="lora-item mb-2">
            <select class="form-select lora-select">
                <option value="">选择LoRA</option>
            </select>
            <input type="number" class="form-control lora-strength" placeholder="强度" min="0" max="2" step="0.1">
            <button type="button" class="btn btn-sm btn-danger" onclick="removeLora(this)">
                <i class="bi bi-trash"></i>
            </button>
        </div>
    `;
    updateLoRASelects();
}

// 重置图生图表单
function resetImg2ImgForm() {
    document.getElementById('img2imgForm').reset();
    document.getElementById('img2imgRandomSeed').checked = true;
    document.getElementById('img2imgSeed').disabled = true;
    document.getElementById('imagePreview').innerHTML = '';
    
    // 清空LoRA容器并重新添加默认LoRA项
    const container = document.getElementById('img2imgLoraContainer');
    container.innerHTML = `
        <div class="lora-item mb-2">
            <select class="form-select img2img-lora-select">
                <option value="">选择LoRA</option>
            </select>
            <input type="number" class="form-control lora-strength" placeholder="强度" min="0" max="2" step="0.1">
            <button type="button" class="btn btn-sm btn-danger" onclick="removeImg2ImgLora(this)">
                <i class="bi bi-trash"></i>
            </button>
        </div>
    `;
    loadLoRAOptions(); // 重新加载LoRA选项
}

// 加载示例提示词
function loadExamplePrompts() {
    const examples = [
        "a beautiful landscape, anime style, high quality",
        "a cute cat, sitting on a windowsill, detailed fur",
        "a futuristic city, cyberpunk style, neon lights",
        "a serene mountain lake, sunset, reflection",
        "a portrait of a young woman, renaissance style"
    ];
    
    const randomExample = examples[Math.floor(Math.random() * examples.length)];
    document.getElementById('prompt').value = randomExample;
    
    showToast('已加载示例提示词', 'success');
}

// 保存配置到历史
function saveToHistory() {
    const formData = collectTxt2ImgData();
    const configName = prompt('请为此配置命名:');
    
    if (configName) {
        const savedConfigs = JSON.parse(localStorage.getItem('savedConfigs') || '[]');
        savedConfigs.push({
            name: configName,
            timestamp: new Date().toISOString(),
            config: formData
        });
        localStorage.setItem('savedConfigs', JSON.stringify(savedConfigs));
        showToast('配置已保存', 'success');
    }
}

// 预览图片
function previewImage(file, containerId) {
    const container = document.getElementById(containerId);
    
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            container.innerHTML = `<img src="${e.target.result}" class="image-preview">`;
        };
        reader.readAsDataURL(file);
    } else {
        container.innerHTML = '';
    }
}

// 显示/隐藏加载遮罩
function showLoading(show) {
    const overlay = document.getElementById('loadingOverlay');
    if (!overlay) return;
    
    if (show) {
        overlay.classList.remove('d-none');
        overlay.classList.add('d-flex');
        overlay.style.display = 'flex';
        
        // 模拟进度条
        let progress = 0;
        const progressBar = document.getElementById('progressBar');
        if (progressBar) {
            const interval = setInterval(() => {
                progress += Math.random() * 10;
                if (progress > 90) progress = 90;
                progressBar.style.width = progress + '%';
            }, 500);
            
            window.currentProgressInterval = interval;
        }
    } else {
        overlay.classList.remove('d-flex');
        overlay.classList.add('d-none');
        overlay.style.display = 'none !important';
        
        // 完成进度条
        if (window.currentProgressInterval) {
            clearInterval(window.currentProgressInterval);
            window.currentProgressInterval = null;
        }
        const progressBar = document.getElementById('progressBar');
        if (progressBar) {
            progressBar.style.width = '0%';
        }
    }
}

// 下载所有图片
function downloadAllImages() {
    if (window.currentResultImages && window.currentResultImages.length > 0) {
        window.currentResultImages.forEach((imageUrl, index) => {
            const link = document.createElement('a');
            link.href = imageUrl;
            link.download = `generated_image_${index + 1}.png`;
            link.target = '_blank';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        });
        showToast('开始下载图片', 'success');
    }
}

// API请求封装
async function apiRequest(endpoint, method = 'GET', data = null) {
    const options = {
        method: method,
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
    };
    
    if (data) {
        if (data instanceof FormData) {
            // FormData 不需要设置 Content-Type
            options.body = data;
        } else {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(data);
        }
    }
    
    try {
        const response = await fetch(endpoint, options);
        
        if (response.status === 401) {
            // Token过期，重新登录
            logout();
            showToast('登录已过期，请重新登录', 'warning');
            return null;
        }
        
        return await response.json();
    } catch (error) {
        console.error('API request error:', error);
        throw error;
    }
}

// 显示提示消息
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toastId = 'toast-' + Date.now();
    
    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-white bg-${type} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', toastHtml);
    
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, {
        autohide: true,
        delay: 3000
    });
    
    toast.show();
    
    // 自动移除
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}

// 初始化工具提示
function initTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

// 加载采样器选项
function loadSamplerOptions(samplers) {
    // 为文生图页面添加采样器选项
    const txt2imgSamplerSelect = document.getElementById('sampler');
    if (txt2imgSamplerSelect) {
        txt2imgSamplerSelect.innerHTML = '<option value="">默认采样器</option>';
        
        samplers.forEach(sampler => {
            const option = document.createElement('option');
            option.value = sampler;
            option.textContent = sampler;
            txt2imgSamplerSelect.appendChild(option);
        });
        
        // 设置默认值
        if (serverStatus && serverStatus.default_sampler) {
            txt2imgSamplerSelect.value = serverStatus.default_sampler;
        }
    }
    
    // 为图生图页面添加采样器选项
    const img2imgSamplerSelect = document.getElementById('img2imgSampler');
    if (img2imgSamplerSelect) {
        img2imgSamplerSelect.innerHTML = '<option value="">默认采样器</option>';
        
        samplers.forEach(sampler => {
            const option = document.createElement('option');
            option.value = sampler;
            option.textContent = sampler;
            img2imgSamplerSelect.appendChild(option);
        });
        
        // 设置默认值
        if (serverStatus && serverStatus.default_sampler) {
            img2imgSamplerSelect.value = serverStatus.default_sampler;
        }
    }
}

// 加载调度器选项
function loadSchedulerOptions(schedulers) {
    // 为文生图页面添加调度器选项
    const txt2imgSchedulerSelect = document.getElementById('scheduler');
    if (txt2imgSchedulerSelect) {
        txt2imgSchedulerSelect.innerHTML = '<option value="">默认调度器</option>';
        
        schedulers.forEach(scheduler => {
            const option = document.createElement('option');
            option.value = scheduler;
            option.textContent = scheduler;
            txt2imgSchedulerSelect.appendChild(option);
        });
        
        // 设置默认值
        if (serverStatus && serverStatus.default_scheduler) {
            txt2imgSchedulerSelect.value = serverStatus.default_scheduler;
        }
    }
    
    // 为图生图页面添加调度器选项
    const img2imgSchedulerSelect = document.getElementById('img2imgScheduler');
    if (img2imgSchedulerSelect) {
        img2imgSchedulerSelect.innerHTML = '<option value="">默认调度器</option>';
        
        schedulers.forEach(scheduler => {
            const option = document.createElement('option');
            option.value = scheduler;
            option.textContent = scheduler;
            img2imgSchedulerSelect.appendChild(option);
        });
        
        // 设置默认值
        if (serverStatus && serverStatus.default_scheduler) {
            img2imgSchedulerSelect.value = serverStatus.default_scheduler;
        }
    }
}

// 加载模型选项
function loadModelOptions() {
    const selects = ['model', 'img2imgModel'];
    
    selects.forEach(selectId => {
        const select = document.getElementById(selectId);
        if (select) {
            // 保留默认选项
            const defaultOption = select.querySelector('option[value=""]');
            select.innerHTML = '';
            if (defaultOption) {
                select.appendChild(defaultOption);
            }
            
            // 添加可用模型
            availableModels.forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                select.appendChild(option);
            });
        }
    });
}

// 加载LoRA选项
function loadLoRAOptions() {
    // 为文生图页面添加LoRA选项
    const txt2imgSelects = document.querySelectorAll('.lora-select:not(.img2img-lora-select)');
    
    txt2imgSelects.forEach(select => {
        // 保留默认选项
        const defaultOption = select.querySelector('option[value=""]');
        select.innerHTML = '';
        if (defaultOption) {
            select.appendChild(defaultOption);
        }
        
        // 添加可用LoRA
        availableLoRAs.forEach(lora => {
            const option = document.createElement('option');
            option.value = lora;
            option.textContent = lora;
            select.appendChild(option);
        });
    });
    
    // 为图生图页面添加LoRA选项
    const img2imgSelects = document.querySelectorAll('.img2img-lora-select');
    
    img2imgSelects.forEach(select => {
        // 保留默认选项
        const defaultOption = select.querySelector('option[value=""]');
        select.innerHTML = '';
        if (defaultOption) {
            select.appendChild(defaultOption);
        }
        
        // 添加可用LoRA
        availableLoRAs.forEach(lora => {
            const option = document.createElement('option');
            option.value = lora;
            option.textContent = lora;
            select.appendChild(option);
        });
    });
}