# 从同目录下的hilbert_encrypt模块导入节点映射
from .hilbert_encrypt import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# 定义模块导出内容，确保ComfyUI能正确识别
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
