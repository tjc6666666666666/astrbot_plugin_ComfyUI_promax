# ComfyUI Workflow Config.json 开发文档

## 概述

本文档详细介绍如何为 ComfyUI 插件开发自定义 Workflow 的 `config.json` 配置文件。通过配置文件，您可以创建独立的 Workflow 模块，无需修改主代码即可扩展功能。

## 目录结构

每个 Workflow 模块需要以下目录结构：

```
workflow/
└── your_workflow/
    ├── config.json      # 配置文件（本文档重点）
    └── workflow.json    # ComfyUI 工作流定义
```

## Config.json 完整配置规范

### 基础信息字段

```json
{
  "name": "工作流名称",
  "prefix": "调用前缀",
  "description": "工作流描述",
  "version": "1.0.0",
  "author": "作者名称"
}
```

**字段说明：**
- `name`: Workflow 的显示名称，用于帮助信息展示
- `prefix`: 调用该 Workflow 的命令前缀，用户通过此前缀触发功能
- `description`: 详细描述，说明该 Workflow 的用途和功能
- `version`: 版本号（可选）
- `author`: 作者信息（可选）

### 节点映射配置

```json
{
  "input_nodes": ["51", "52"],
  "output_nodes": ["9", "save_image_websocket_node"],
  "input_mappings": {
    "51": {
      "parameter_name": "image",
      "required": true,
      "type": "image",
      "description": "输入图片"
    }
  },
  "output_mappings": {
    "9": {
      "parameter_name": "images",
      "type": "image",
      "description": "输出图片"
    }
  }
}
```

**字段说明：**
- `input_nodes`: 输入节点 ID 列表，对应 workflow.json 中的节点 ID
- `output_nodes`: 输出节点 ID 列表
- `input_mappings`: 输入节点参数映射
  - `parameter_name`: 节点中的参数名
  - `required`: 是否必需（true/false）
  - `type`: 参数类型（"image", "text", "number", "boolean", "select"）
  - `description`: 参数描述
- `output_mappings`: 输出节点参数映射

### 可配置节点参数

```json
{
  "configurable_nodes": ["31", "6", "33"],
  "node_configs": {
    "31": {
      "seed": {
        "type": "number",
        "default": -1,
        "description": "随机种子，-1为随机",
        "min": -1,
        "max": 4294967295,
        "aliases": ["种子", "random_seed"]
      },
      "steps": {
        "type": "number",
        "default": 30,
        "description": "采样步数",
        "min": 1,
        "max": 150,
        "aliases": ["步数", "inference_steps"]
      },
      "cfg": {
        "type": "number",
        "default": 7.0,
        "description": "CFG系数",
        "min": 1.0,
        "max": 30.0,
        "aliases": ["CFG", "cfg_scale"]
      },
      "sampler_name": {
        "type": "select",
        "default": "euler",
        "description": "采样器",
        "options": ["euler", "dpmpp_2m", "ddim", "dpmpp_sde"],
        "inject_samplers": true,
        "aliases": ["采样器", "sampler"]
      },
      "scheduler": {
        "type": "select",
        "default": "simple",
        "description": "调度器",
        "options": ["simple", "karras", "exponential", "normal"],
        "inject_schedulers": true,
        "aliases": ["调度器", "scheduler_type"]
      }
    },
    "6": {
      "text": {
        "type": "text",
        "required": true,
        "description": "正面提示词",
        "aliases": ["提示词", "prompt", "positive_prompt"]
      }
    },
    "33": {
      "text": {
        "type": "text",
        "default": "bad quality, worst quality",
        "description": "负面提示词",
        "aliases": ["负面提示词", "negative_prompt"]
      }
    }
  }
}
```

**字段说明：**
- `configurable_nodes`: 可配置的节点 ID 列表
- `node_configs`: 各节点的可配置参数详情

### 参数类型详解

#### 1. 文本类型 (text)
```json
{
  "parameter_name": {
    "type": "text",
    "required": false,
    "default": "默认值",
    "description": "参数描述",
    "aliases": ["别名1", "别名2"]
  }
}
```

#### 2. 数字类型 (number)
```json
{
  "parameter_name": {
    "type": "number",
    "required": false,
    "default": 100,
    "description": "参数描述",
    "min": 0,
    "max": 1000,
    "aliases": ["别名1", "别名2"]
  }
}
```

#### 3. 布尔类型 (boolean)
```json
{
  "parameter_name": {
    "type": "boolean",
    "required": false,
    "default": true,
    "description": "参数描述",
    "aliases": ["启用", "enable", "开启"]
  }
}
```

#### 4. 选择类型 (select)
```json
{
  "parameter_name": {
    "type": "select",
    "required": false,
    "default": "option1",
    "description": "参数描述",
    "options": ["option1", "option2", "option3"],
    "aliases": ["别名1", "别名2"]
  }
}
```

#### 5. 图片类型 (image)
```json
{
  "parameter_name": {
    "type": "image",
    "required": true,
    "description": "输入图片",
    "aliases": ["图片", "image_input"]
  }
}
```

## 高级功能

### 1. 配置注入功能

通过特殊标记，可以让 Workflow 自动同步主程序的配置：

#### 模型注入
```json
{
  "ckpt_name": {
    "type": "select",
    "description": "选择模型",
    "inject_models": true,
    "aliases": ["模型", "model"]
  }
}
```
- 自动从主程序的 `model_config` 加载可用模型
- 用户可以使用模型描述来选择模型

#### LoRA 注入
```json
{
  "lora_name": {
    "type": "text",
    "description": "LoRA描述",
    "inject_loras": true,
    "aliases": ["LoRA", "lora"]
  }
}
```
- 自动在描述中显示可用的 LoRA 列表
- 用户可以使用 LoRA 描述来引用

#### 采样器注入
```json
{
  "sampler_name": {
    "type": "select",
    "description": "采样器",
    "inject_samplers": true,
    "aliases": ["采样器", "sampler"]
  }
}
```
- 自动使用主程序的默认采样器配置

#### 调度器注入
```json
{
  "scheduler": {
    "type": "select",
    "description": "调度器",
    "inject_schedulers": true,
    "aliases": ["调度器", "scheduler_type"]
  }
}
```
- 自动使用主程序的默认调度器配置

### 2. 别名系统

每个参数都支持多个别名，用户可以使用任何别名来指定参数值：

```json
{
  "width": {
    "type": "number",
    "default": 512,
    "description": "图片宽度",
    "aliases": ["宽度", "w", "image_width"]
  }
}
```

用户调用方式：
- `width:800`
- `宽度:800`
- `w:800`
- `image_width:800`

### 3. 参数验证

系统会自动进行参数验证：
- **必需参数检查**: 缺少必需参数时会提示错误
- **类型验证**: 数字参数必须是数字，布尔参数必须是 true/false
- **范围验证**: 数字参数会在 min/max 范围内
- **选项验证**: select 类型参数必须是预定义的选项之一

## 完整示例

### 示例1: 简单图片缩放

```json
{
  "name": "图片缩放",
  "prefix": "resize",
  "description": "将图片缩放到指定尺寸",
  "input_nodes": ["51"],
  "output_nodes": ["9"],
  "input_mappings": {
    "51": {
      "parameter_name": "image",
      "required": true,
      "type": "image",
      "description": "要缩放的图片"
    }
  },
  "output_mappings": {
    "9": {
      "parameter_name": "images",
      "type": "image",
      "description": "缩放后的图片"
    }
  },
  "configurable_nodes": ["52"],
  "node_configs": {
    "52": {
      "width": {
        "type": "number",
        "default": 512,
        "description": "目标宽度",
        "min": 64,
        "max": 2048,
        "aliases": ["宽度", "w"]
      },
      "height": {
        "type": "number",
        "default": 512,
        "description": "目标高度",
        "min": 64,
        "max": 2048,
        "aliases": ["高度", "h"]
      },
      "interpolation": {
        "type": "select",
        "default": "bicubic",
        "description": "插值方法",
        "options": ["nearest", "bilinear", "bicubic", "lanczos"],
        "aliases": ["插值", "interpolation_method"]
      }
    }
  }
}
```

### 示例2: 文生图 with LoRA

```json
{
  "name": "文生图LoRA",
  "prefix": "t2l",
  "description": "支持自定义LoRA的文生图",
  "input_nodes": [],
  "output_nodes": ["9"],
  "output_mappings": {
    "9": {
      "parameter_name": "images",
      "type": "image",
      "description": "生成的图片"
    }
  },
  "configurable_nodes": ["31", "6", "33", "30"],
  "node_configs": {
    "31": {
      "seed": {
        "type": "number",
        "default": -1,
        "description": "随机种子，-1为随机",
        "min": -1,
        "max": 4294967295,
        "aliases": ["种子", "random_seed"]
      },
      "steps": {
        "type": "number",
        "default": 30,
        "description": "采样步数",
        "min": 1,
        "max": 150,
        "aliases": ["步数", "inference_steps"]
      },
      "cfg": {
        "type": "number",
        "default": 7.0,
        "description": "CFG系数",
        "min": 1.0,
        "max": 30.0,
        "aliases": ["CFG", "cfg_scale"]
      },
      "sampler_name": {
        "type": "select",
        "default": "euler",
        "description": "采样器",
        "inject_samplers": true,
        "aliases": ["采样器", "sampler"]
      },
      "scheduler": {
        "type": "select",
        "default": "simple",
        "description": "调度器",
        "inject_schedulers": true,
        "aliases": ["调度器", "scheduler_type"]
      }
    },
    "6": {
      "text": {
        "type": "text",
        "required": true,
        "description": "正面提示词",
        "aliases": ["提示词", "prompt", "positive_prompt"]
      }
    },
    "33": {
      "text": {
        "type": "text",
        "default": "bad quality, worst quality",
        "description": "负面提示词",
        "aliases": ["负面提示词", "negative_prompt"]
      }
    },
    "30": {
      "ckpt_name": {
        "type": "select",
        "description": "选择模型",
        "inject_models": true,
        "aliases": ["模型", "model"]
      }
    }
  }
}
```

## 使用方法

### 基本语法
```
<前缀> [参数名:值 ...] [+ 图片（如需要）]
```

### 示例调用
```
# 图片缩放
resize width:800 height:600 + [图片]

# 文生图
t2l 提示词:可爱女孩 步数:30 CFG:7.0 模型:写实风格

# 使用别名
t2l prompt:可爱女孩 steps:30 cfg:7.0 model:写实风格
```

## 开发最佳实践

### 1. 命名规范
- 使用有意义的 `name` 和 `prefix`
- 参数名使用英文，别名支持中文
- 保持别名简洁易记

### 2. 参数设计
- 为所有参数提供合理的默认值
- 设置适当的数值范围限制
- 为必需参数添加 `required: true`

### 3. 描述信息
- 提供清晰的 `description`
- 说明参数的用途和取值范围
- 在注入配置时自动显示可用选项

### 4. 错误处理
- 系统会自动验证参数格式和范围
- 提供友好的错误提示信息
- 支持参数缺失时的默认值回退

### 5. 性能考虑
- 避免配置过于复杂的 Workflow
- 合理设置节点数量和参数数量
- 使用配置注入减少重复配置

## 故障排除

### 常见问题

1. **Workflow 未加载**
   - 检查 `config.json` 语法是否正确
   - 确认必需字段都已填写
   - 查看插件日志中的错误信息

2. **参数无效**
   - 检查参数类型是否正确
   - 确认数值在有效范围内
   - 验证 select 类型的选项是否存在

3. **前缀冲突**
   - 确保前缀唯一，不与其他 Workflow 重复
   - 检查主程序命令是否占用此前缀

4. **配置注入失败**
   - 确认主程序配置正确
   - 检查注入标记语法
   - 查看日志中的注入信息

### 调试方法

1. **查看日志**
   ```bash
   # 查看插件启动日志
   tail -f /path/to/astrbot.log | grep "workflow"
   ```

2. **验证配置**
   ```bash
   # 验证 JSON 语法
   python -m json.tool config.json
   ```

3. **测试 Workflow**
   - 使用简单参数测试基本功能
   - 逐步增加复杂度
   - 检查 ComfyUI 服务器状态

## 版本兼容性

- **向后兼容**: 新版本会保持对旧配置格式的支持
- **新功能**: 通过可选字段添加新功能，不影响现有配置
- **弃用字段**: 会在多个版本中保持支持，并提供迁移指导

## 扩展功能

### 未来可能的功能
1. **条件参数**: 根据其他参数值动态显示/隐藏参数
2. **参数依赖**: 定义参数间的依赖关系
3. **模板系统**: 支持参数模板和继承
4. **验证规则**: 自定义参数验证规则
5. **国际化**: 支持多语言描述和别名

### API 扩展
- 支持动态参数类型
- 自定义参数验证器
- 插件间配置共享
- 远程配置加载

---

通过本文档，您应该能够创建功能完整的 Workflow 配置文件。如有问题，请参考示例配置或查看插件日志获取更多信息。
