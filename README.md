# 🎨 Astrbot ComfyUI Plugin Pro Max

[![Version](https://img.shields.io/badge/version-3.3-blue.svg)](https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

一个功能强大的 Astrbot 插件，集成了 ComfyUI AI 绘画功能，支持文生图、图生图、多服务器轮询、模型选择、LoRA 支持、自定义 Workflow 等高级功能。

## ✨ 主要特性

### 🎯 AI 绘画核心功能
- **文生图 (Text-to-Image)**：通过文字描述生成高质量图片
- **图生图 (Image-to-Image)**：基于现有图片进行二次创作
- **批量生成**：支持一次生成多张图片，提高效率
- **多服务器轮询**：智能分配任务到多个 ComfyUI 服务器

### 🛠️ 高级特性
- **🎭 模型选择**：支持切换不同的 AI 模型，满足不同风格需求
- **✨ LoRA 支持**：可使用多个 LoRA 模型增强效果
- **🔐 图片加密**：希尔伯特曲线图像加密保护
- **💾 自动保存**：生成的图片自动保存到本地
- **📦 压缩包下载**：支持打包下载当日生成的图片
- **📚 智能帮助系统**：支持文本和图片形式的帮助信息
- **⚙️ 自定义 Workflow**：支持用户自定义工作流，扩展功能无限可能

### 🎨 Workflow 系统
- **图像加密解密**：希尔伯特曲线加密/解密工作流
- **可扩展架构**：轻松添加新的自定义工作流
- **统一帮助系统**：每个工作流都有详细的帮助文档和图片说明
- **参数别名支持**：支持中英文参数名，使用更便捷
- **配置注入功能**：自动同步主程序的模型、LoRA、采样器配置
- **智能参数验证**：自动验证参数类型、范围和必需性
- **缓存机制**：帮助图片自动缓存，提高响应速度

## 📦 安装要求

### Python 依赖
```bash
pip install -r requirements.txt
```

**requirements.txt 内容：**
```txt
aiohttp>=3.8.0
Pillow>=9.0.0
aiofiles>=0.8.0
aiosqlite>=0.17.0
asyncio-throttle>=1.0.0
```

### ComfyUI 环境要求
- **ComfyUI**: 确保已安装并运行 ComfyUI
- **Python**: 3.8 或更高版本
- **GPU**: 推荐 NVIDIA GPU（支持 CUDA）

### ComfyUI 自定义节点（可选）
如果需要使用图片加密功能，需要在 ComfyUI 中安装以下自定义节点：

1. **图像压缩器** (`image_compressor`)
   - 作用：压缩和优化输入图片
   - 安装位置：`ComfyUI/custom_nodes/image_compressor`

2. **希尔伯特图像加密** (`hilbert_image_encrypt`)
   - 作用：对生成的图片进行加密保护
   - 安装位置：`ComfyUI/custom_nodes/hilbert_image_encrypt`

> ⚠️ **重要提示**：如果开启了图片解密功能，用户需要自行搜索"小番茄图片混淆"工具进行解密。

## 🚀 快速开始

### 1. 插件安装
将插件文件放置到 Astrbot 的插件目录中。



### 2. 启动插件
重启 Astrbot，插件将自动加载并连接到 ComfyUI 服务器。

## 📝 使用指南

### 🎨 文生图指令
```
aimg <提示词> [宽X,高Y] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]]
```

**示例：**
```bash
# 基础用法
aimg 美少女

# 完整参数
aimg 可爱女孩 宽512,高768 批量2 model:写实风格 lora:儿童:0.8 lora:可爱!1.0

# 高分辨率
aimg 风景画 宽1024,高768 批量1 model:风景风格
```

### 🖼️ 图生图指令
```
img2img <提示词> [噪声:数值] [批量N] [model:描述] [lora:描述[:强度][!CLIP强度]] + 图片
```

**示例：**
```bash
# 基础图生图
img2img 猫咪 噪声:0.7

# 完整参数（需要附带图片）
img2img 动漫角色 噪声:0.5 批量2 model:动漫风格 lora:角色:1.2!0.9
```

### 🔧 自定义 Workflow 指令
```
<前缀> [参数名:值 ...] [+ 图片（如需要）]
```

**示例：**
```bash
# 图像加密
encrypt 模式:encrypt 启用:true + 图片

# 图像解密
encrypt 模式:decrypt + 加密图片

# 使用别名
encrypt mode:decrypt enable:false + 图片
```

### 📦 压缩包下载
```
comfyuioutput
```
获取今天生成的所有图片的压缩包。

### 📚 帮助信息
```bash
aimg          # 文生图帮助
img2img       # 图生图帮助
encrypt       # 工作流帮助（示例）
```

## ⚙️ 参数详解

### 🎭 模型选择
- **格式**：`model:描述`
- **示例**：`model:写实风格`
- **说明**：描述对应配置文件中的模型描述

### ✨ LoRA 使用
- **基础格式**：`lora:描述`（使用默认强度 1.0/1.0）
- **仅模型强度**：`lora:描述:0.8`（strength_model=0.8）
- **仅 CLIP 强度**：`lora:描述!1.0`（strength_clip=1.0）
- **双强度**：`lora:描述:0.8!1.3`（model=0.8, clip=1.3）
- **多 LoRA**：空格分隔多个 lora 参数

**示例：**
```bash
lora:儿童 lora:学生:0.9 lora:可爱!1.2
```

### 📏 分辨率设置
- **格式**：`宽X,高Y` 或 `宽,高`
- **示例**：`宽512,高768` 或 `512,768`
- **限制**：64~2000 像素范围

### 🎲 其他参数
- **批量数**：`批量N`（N 为数量，最大 6）
- **噪声系数**：`噪声:数值`（0.0~1.0，默认 0.7）
- **种子**：`种子:数值`（-1 为随机）

## 🔧 配置说明

### 🌐 服务器配置
```json
{
  "comfyui_url": ["http://127.0.0.1:8188", "http://192.168.1.100:8188"],
  "max_task_queue": 10,
  "max_failure_count": 3,
  "retry_delay": 300
}
```

### 🎨 模型配置
```json
{
  "model_config": [
    {
      "filename": "v1-5-pruned-emaonly.safetensors",
      "description": "写实风格"
    },
    {
      "filename": "anime-model.safetensors", 
      "description": "动漫风格"
    }
  ]
}
```

### ✨ LoRA 配置
```json
{
  "lora_config": [
    {
      "filename": "lora1.safetensors",
      "description": "儿童"
    },
    {
      "filename": "lora2.safetensors",
      "description": "可爱"
    }
  ]
}
```

### 🕐 时间限制
```json
{
  "open_time_ranges": "7:00-8:00,11:00-14:00,17:00-24:00"
}
```

## 🔌 Web 配置界面

插件提供了功能强大的 Web 配置管理界面，支持可视化配置和管理：

### 🌐 访问方式
- 默认端口：`8090`
- 访问地址：`http://localhost:8090`
- 支持远程访问配置

### 📋 功能特性
- **工作流管理**：查看、编辑、删除自定义工作流
- **服务器配置**：动态添加/删除 ComfyUI 服务器，设置权重和启用状态
- **主配置管理**：可视化配置各项参数
- **实时状态监控**：查看服务器连接状态和任务队列
- **响应式设计**：支持桌面和移动设备访问

### 🎨 界面预览
- **现代化 UI**：基于 Bootstrap 5 的美观界面
- **侧边栏导航**：清晰的功能分类
- **卡片式布局**：直观的信息展示
- **实时反馈**：操作结果即时显示

## 📊 高级功能

### 📈 任务统计系统
- **实时统计**：总任务数、成功率、失败率
- **用户统计**：按用户分组的任务统计
- **服务器统计**：各服务器的负载和性能统计
- **性能监控**：平均响应时间、队列长度监控
- **可视化报表**：支持图片形式的统计报告

### 🔄 智能负载均衡
- **权重轮询**：根据服务器权重智能分配任务
- **健康检查**：自动检测服务器状态，故障自动切换
- **失败重试**：支持失败重试和恢复机制
- **动态调整**：根据服务器性能动态调整分配策略

### 🛡️ 用户权限管理
- **白名单机制**：仅允许特定用户使用
- **黑名单机制**：禁止特定用户使用
- **权限分级**：不同用户可配置不同权限
- **使用统计**：记录用户使用频率和偏好

### 💾 自动备份系统
- **定时备份**：支持定时自动备份配置和数据
- **增量备份**：仅备份变更内容，节省空间
- **备份管理**：自动清理过期备份，保持存储空间
- **一键恢复**：支持快速恢复到指定备份点

## 🔌 可扩展 Workflow 系统

### 🌟 功能概述

插件支持完全可扩展的 Workflow 系统，允许用户通过简单的配置文件创建自定义的 ComfyUI 工作流，无需修改主代码即可无限扩展功能。

### 📁 目录结构
```
workflow/
├── encrypt/
│   ├── config.json      # 工作流配置文件
│   ├── workflow.json    # ComfyUI 工作流定义
│   └── help.png         # 自动生成的帮助图片
├── juxueli/
│   ├── config.json
│   ├── workflow.json
│   └── help.png
└── your_workflow/
    ├── config.json
    ├── workflow.json
    └── help.png
```

### 🎯 核心特性

#### 1. 模块化设计
- **独立模块**：每个 workflow 都是独立的功能模块
- **热加载**：重启插件后自动加载新模块
- **零代码扩展**：通过配置文件即可添加新功能

#### 2. 灵活的参数系统
- **多种参数类型**：text、number、boolean、select、image
- **智能验证**：自动验证参数类型、范围和必需性
- **别名支持**：支持中英文参数名和自定义别名
- **默认值**：为所有参数提供合理的默认值

#### 3. 配置注入功能
- **模型注入**：自动同步主程序的模型配置
- **LoRA 注入**：动态显示可用的 LoRA 列表
- **采样器注入**：自动使用主程序的采样器配置
- **调度器注入**：自动同步调度器设置

#### 4. 智能帮助系统
- **自动生成帮助**：每个 workflow 自动生成详细帮助文档
- **图片帮助**：支持精美的图片形式帮助（与主帮助样式一致）
- **参数说明**：详细的参数类型、范围和示例说明
- **缓存机制**：帮助图片自动缓存，提高响应速度

### 📋 配置文件完整规范

#### 基础信息字段
```json
{
  "name": "工作流显示名称",
  "prefix": "命令前缀",
  "description": "工作流描述",
  "version": "1.0.0",
  "author": "作者",
  "input_mappings": {
    "6": {
      "positive_prompt": {
        "type": "text",
        "required": true,
        "description": "正面提示词",
        "aliases": ["prompt", "提示词"]
      }
    }
  },
  "output_mappings": {
    "9": {
      "images": {
        "type": "image",
        "description": "生成的图片"
      }
    }
  },
  "configurable_nodes": [
    {
      "node_id": "6",
      "parameter": "positive_prompt"
    }
  ],
  "node_configs": {
    "6": {
      "width": {
        "type": "number",
        "required": false,
        "default": 512,
        "min": 64,
        "max": 2048,
        "description": "图片宽度",
        "aliases": ["w", "宽度"]
      },
      "height": {
        "type": "number",
        "required": false,
        "default": 512,
        "min": 64,
        "max": 2048,
        "description": "图片高度",
        "aliases": ["h", "高度"]
      },
      "steps": {
        "type": "number",
        "required": false,
        "default": 20,
        "min": 1,
        "max": 150,
        "description": "采样步数",
        "aliases": ["步数"]
      },
      "sampler_name": {
        "type": "select",
        "required": false,
        "default": "euler",
        "options": ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde"],
        "inject_samplers": true,
        "description": "采样器",
        "aliases": ["sampler", "采样器"]
      },
      "ckpt_name": {
        "type": "select",
        "required": false,
        "default": "v1-5-pruned-emaonly.safetensors",
        "inject_models": true,
        "description": "模型",
        "aliases": ["model", "模型"]
      }
    }
  }
}
```

### 🎨 参数类型详解

#### 1. 文本类型 (text)
```json
"parameter_name": {
  "type": "text",
  "required": true,
  "description": "参数描述",
  "aliases": ["别名1", "别名2"]
}
```

#### 2. 数字类型 (number)
```json
"parameter_name": {
  "type": "number",
  "required": false,
  "default": 512,
  "min": 0,
  "max": 1000,
  "description": "参数描述",
  "aliases": ["别名1", "别名2"]
}
```

#### 3. 布尔类型 (boolean)
```json
"parameter_name": {
  "type": "boolean",
  "required": false,
  "default": true,
  "description": "参数描述",
  "aliases": ["开启", "关闭"]
}
```

#### 4. 选择类型 (select)
```json
"parameter_name": {
  "type": "select",
  "required": false,
  "default": "option1",
  "options": ["option1", "option2", "option3"],
  "description": "参数描述",
  "aliases": ["别名1", "别名2"]
}
```

#### 5. 图片类型 (image)
```json
"parameter_name": {
  "type": "image",
  "required": true,
  "description": "参数描述",
  "aliases": ["图片", "image_input"]
}
```

### 🚀 高级功能

#### 配置注入功能
通过特殊标记，让 workflow 自动同步主程序配置：

- **`"inject_models": true`**：自动从主程序的 `model_config` 加载可用模型
- **`"inject_loras": true`**：自动显示可用的 LoRA 列表和描述
- **`"inject_samplers": true`**：自动使用主程序的默认采样器配置
- **`"inject_schedulers": true`**：自动使用主程序的默认调度器配置

#### 别名系统
每个参数都支持多个别名，用户可以使用任何别名：

```json
{
  "width": {
    "aliases": ["w", "宽度", "image_width"]
  }
}
```

用户调用方式：
- `width:800`
- `宽度:800`
- `w:800`
- `image_width:800`

### 📚 内置 Workflow 示例

#### 1. 图像加密解密 (encrypt)
```bash
# 加密图片
encrypt 模式:encrypt 启用:true + 图片

# 解密图片
encrypt mode:decrypt + 加密图片

# 使用别名
encrypt mode:decrypt enable:false + 图片
```

#### 2. 聚理效果 (juxueli)
```bash
# 基础聚理效果
juxueli 强度:0.8 + 图片

# 完整参数
juxueli intensity:0.7 mode:enhance + 图片
```

### 🛠️ 开发新 Workflow

#### 步骤 1：创建目录
```bash
mkdir workflow/your_workflow
cd workflow/your_workflow
```

#### 步骤 2：创建配置文件 (config.json)
参考上面的完整规范创建配置文件。

#### 步骤 3：获取工作流定义
1. 在 ComfyUI 中设计你的工作流
2. 点击 "Save (API Format)" 保存为 workflow.json
3. 将文件放入你的 workflow 目录

#### 步骤 4：重启插件
重启 Astrbot 插件，新 workflow 将自动加载。

#### 步骤 5：测试功能
```bash
your_workflow --help  # 查看帮助
your_workflow 参数:值 + 图片  # 测试功能
```

### 🎯 最佳实践

1. **参数设计**：
   - 为所有参数提供合理的默认值
   - 使用描述性的参数名和别名
   - 设置适当的参数范围限制

2. **错误处理**：
   - 明确标记必需参数
   - 提供详细的参数说明
   - 使用参数验证避免错误

3. **用户体验**：
   - 提供中文别名
   - 编写清晰的描述
   - 使用一致的命名规范

4. **性能优化**：
   - 合理使用配置注入
   - 避免不必要的参数
   - 优化工作流结构

### 💾 存储配置
```json
{
  "enable_auto_save": true,
  "auto_save_directory": "output",
  "enable_output_zip": true,
  "daily_download_limit": 3,
  "only_own_images": false
}
```

### 📚 帮助系统配置
```json
{
  "enable_help_image": true,
  "help_server_port": 8080
}
```

## 🌐 Web API 接口

插件提供了完整的 RESTful API 接口，支持第三方集成：

### 📋 配置管理 API
```http
GET    /api/config           # 获取配置信息
POST   /api/config           # 更新配置信息
GET    /api/servers          # 获取服务器列表
POST   /api/servers          # 添加服务器
DELETE /api/servers/{id}     # 删除服务器
```

### 🎨 工作流 API
```http
GET    /api/workflows        # 获取工作流列表
GET    /api/workflow/{id}    # 获取特定工作流
POST   /api/workflow         # 创建新工作流
PUT    /api/workflow/{id}    # 更新工作流
DELETE /api/workflow/{id}    # 删除工作流
```

### 📊 统计信息 API
```http
GET    /api/stats            # 获取统计信息
GET    /api/performance      # 获取性能数据
GET    /api/health           # 健康检查
```

### 💾 备份管理 API
```http
GET    /api/backups          # 获取备份列表
POST   /api/backup           # 创建备份
POST   /api/restore/{id}     # 恢复备份
DELETE /api/backup/{id}      # 删除备份
```

## 🔌 Workflow 开发

### 📁 目录结构
```
workflow/
├── encrypt/
│   ├── config.json      # 工作流配置
│   ├── workflow.json    # ComfyUI 工作流定义
│   └── help.png         # 自动生成的帮助图片
└── your_workflow/
    ├── config.json
    ├── workflow.json
    └── help.png
```

### 🎯 参数类型支持
- **text**: 文本输入
- **number**: 数值输入（支持 min/max）
- **select**: 下拉选择
- **boolean**: 布尔值
- **image**: 图片输入

### 🚀 高级开发特性

#### 1. 条件参数
支持根据其他参数值动态显示/隐藏参数：
```json
{
  "conditional_parameters": {
    "mode": {
      "encrypt": ["key", "strength"],
      "decrypt": ["key"]
    }
  }
}
```

#### 2. 参数联动
支持参数间的联动计算：
```json
{
  "parameter_dependencies": {
    "aspect_ratio": {
      "depends_on": ["width", "height"],
      "calculate": "width/height"
    }
  }
}
```

#### 3. 自定义验证
支持自定义参数验证规则：
```json
{
  "custom_validators": {
    "prompt": {
      "min_length": 10,
      "max_length": 1000,
      "forbidden_words": ["nsfw", "illegal"],
      "required_patterns": ["^[a-zA-Z0-9\\s\\u4e00-\\u9fa5]+$"]
    }
  }
}
```

#### 4. 批量处理
支持批量处理多个输入：
```json
{
  "batch_processing": {
    "enabled": true,
    "max_batch_size": 10,
    "parallel_processing": true
  }
}
```

## 🛠️ 故障排除

### 常见问题

**Q: 插件无法连接到 ComfyUI**
A: 检查 ComfyUI 是否正常运行，URL 配置是否正确，防火墙设置是否允许连接。

**Q: 生成的图片质量不佳**
A: 尝试调整提示词、更换模型、调整 CFG 值或使用 LoRA 增强。

**Q: 帮助图片无法显示**
A: 检查 `enable_help_image` 配置是否为 `true`，确保插件有写入权限。

**Q: Workflow 无法使用**
A: 检查 workflow 目录下的配置文件格式是否正确，ComfyUI 是否有对应的自定义节点。

### 日志查看
插件运行日志会输出到 Astrbot 的日志系统中，可以通过查看日志来诊断问题。

### 调试模式
启用调试模式获取更详细的日志信息：
```json
{
  "debug_mode": true,
  "log_level": "DEBUG",
  "enable_performance_logging": true
}
```

## 📊 性能优化

### 🚀 提升生成速度
1. **多服务器部署**：配置多个 ComfyUI 服务器实现负载均衡
2. **批量生成**：合理设置批量数，减少请求次数
3. **分辨率优化**：根据需要选择合适的分辨率
4. **智能调度**：启用智能路由和负载均衡
5. **缓存机制**：合理使用模型和 LoRA 缓存

### 💾 存储优化
1. **自动清理**：定期清理旧的生成图片
2. **压缩设置**：调整图片质量和压缩比例
3. **分布式存储**：将存储目录配置到高速磁盘
4. **增量备份**：使用增量备份减少存储占用
5. **云存储集成**：支持对象存储服务

### 🔧 系统优化
1. **内存管理**：合理配置任务队列大小
2. **并发控制**：根据硬件配置调整并发数
3. **网络优化**：使用 CDN 加速图片传输
4. **监控告警**：设置性能阈值和告警机制

## 🎯 最佳实践

### 📋 部署建议
1. **硬件配置**：推荐使用 NVIDIA GPU，显存至少 8GB
2. **服务器规划**：根据用户量配置合适数量的 ComfyUI 服务器
3. **网络架构**：使用内网通信，减少网络延迟
4. **安全设置**：配置防火墙和访问控制

### 🎨 使用技巧
1. **提示词优化**：使用结构化提示词获得更好效果
2. **模型选择**：根据需求选择合适的模型和 LoRA
3. **参数调优**：通过实验找到最佳参数组合
4. **批量处理**：合理利用批量生成提高效率

### 🛠️ 维护管理
1. **定期备份**：设置自动备份策略
2. **性能监控**：定期检查系统性能指标
3. **日志分析**：通过日志分析发现问题
4. **版本管理**：保持插件和依赖的版本更新

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 🐛 报告问题
- 使用 Issue 模板报告 bug
- 提供详细的复现步骤
- 附上相关的日志和配置信息
- 说明运行环境（系统、Python版本等）

### 💡 功能建议
- 在 Issue 中描述新功能需求
- 说明使用场景和预期效果
- 提供可能的实现思路
- 讨论技术可行性

### 🔧 代码贡献
1. Fork 项目到你的 GitHub
2. 创建功能分支：`git checkout -b feature/new-feature`
3. 提交更改：`git commit -m 'Add new feature'`
4. 推送分支：`git push origin feature/new-feature`
5. 创建 Pull Request

### 📝 文档改进
- 修正文档中的错误
- 补充缺失的使用说明
- 翻译文档到其他语言
- 添加更多示例和最佳实践

## 📄 许可证

本项目采用 MIT 许可证，详情请查看 [LICENSE](LICENSE) 文件。

## 🙏 致谢

感谢以下开源项目和贡献者：

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) - 强大的 AI 绘图工具
- [AstrBot](https://github.com/yourusername/astrbot) - 优秀的聊天机器人框架
- 所有贡献者和用户的支持与反馈

## 📞 联系方式

- **项目主页**: [GitHub Repository](https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax)
- **问题反馈**: [GitHub Issues](https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax/issues)
- **讨论交流**: [GitHub Discussions](https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax/discussions)

## 🗺️ 路线图

### v3.4 计划功能
- [ ] 更多内置 Workflow 模板
- [ ] 支持视频生成功能
- [ ] 集成更多 AI 模型
- [ ] 移动端 App 支持

### v3.5 计划功能
- [ ] 分布式部署支持
- [ ] 高级调度算法
- [ ] 机器学习优化
- [ ] 云原生部署


---

<div align="center">



[![Star](https://img.shields.io/github/stars/tjc6666666666666/astrbot_plugin_ComfyUI_promax.svg?style=social&label=Star)](https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax)
[![Fork](https://img.shields.io/github/forks/tjc6666666666666/astrbot_plugin_ComfyUI_promax.svg?style=social&label=Fork)](https://github.com/tjc6666666666666/astrbot_plugin_ComfyUI_promax/fork)

</div>
