import torch
from PIL import Image
import numpy as np
import comfy.utils

class ImageCompressor:
    """
    图像压缩/缩放节点，支持同时设置最大分辨率和最小分辨率
    - 若图像尺寸超过最大分辨率：按比例缩小至最大分辨率内
    - 若图像尺寸小于最小分辨率：按比例放大至最小分辨率以上
    - 若图像尺寸在最小-最大之间：保持原图不变
    """
    def __init__(self):
        pass
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                # 新增：最小宽度设置
                "min_width": ("INT", {
                    "default": 16,      # 默认最小宽度（与原代码最小限制一致）
                    "min": 16,          # 绝对最小限制（避免过小图像）
                    "max": 4096,        # 与最大宽度范围对齐
                    "step": 16,         # 步长16，与分辨率调节习惯一致
                    "display": "slider" # 滑块控件，便于调节
                }),
                # 新增：最小高度设置
                "min_height": ("INT", {
                    "default": 16,      # 默认最小高度
                    "min": 16,          # 绝对最小限制
                    "max": 4096,        # 与最大高度范围对齐
                    "step": 16,         # 步长16
                    "display": "slider" # 滑块控件
                }),
                # 原有：最大宽度设置（保持不变）
                "max_width": ("INT", {
                    "default": 1024,
                    "min": 16,
                    "max": 4096,
                    "step": 16,
                    "display": "slider"
                }),
                # 原有：最大高度设置（保持不变）
                "max_height": ("INT", {
                    "default": 1024,
                    "min": 16,
                    "max": 4096,
                    "step": 16,
                    "display": "slider"
                }),
            }
        }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "compress_image"
    CATEGORY = "图像处理/压缩"  # 节点分类保持不变
    
    def compress_image(self, image, min_width, min_height, max_width, max_height):
        """
        核心逻辑：同时满足最小分辨率和最大分辨率的图像缩放
        处理流程：
        1. 原图尺寸 → 2. 若小于最小分辨率：放大至最小分辨率以上
        3. 若大于最大分辨率：缩小至最大分辨率以内 → 4. 最终确保尺寸在 [min, max] 区间
        """
        # 1. Tensor转PIL图像（保持原逻辑）
        pil_images = []
        for img in image:
            img_np = img.cpu().numpy()
            img_np = np.clip(img_np * 255.0, 0, 255).astype(np.uint8)
            pil_img = Image.fromarray(img_np)
            pil_images.append(pil_img)
        
        processed_images = []
        for pil_img in pil_images:
            original_width, original_height = pil_img.size
            
            # 2. 先处理「小于最小分辨率」的情况：按比例放大
            if original_width < min_width or original_height < min_height:
                # 计算「达到最小分辨率」所需的缩放比例（取较大值，确保宽高都达标）
                width_ratio_min = min_width / original_width if original_width != 0 else 1.0
                height_ratio_min = min_height / original_height if original_height != 0 else 1.0
                scale_ratio = max(width_ratio_min, height_ratio_min)
                
                # 计算放大后的尺寸（确保不小于最小分辨率）
                new_width = max(int(original_width * scale_ratio), min_width)
                new_height = max(int(original_height * scale_ratio), min_height)
                
                # 高质量放大（LANCZOS算法支持放大和缩小）
                resized_img = pil_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # 3. 再处理「超过最大分辨率」的情况：按比例缩小
            elif original_width > max_width or original_height > max_height:
                # 计算「不超过最大分辨率」的缩放比例（取较小值，确保宽高都不超标）
                width_ratio_max = max_width / original_width
                height_ratio_max = max_height / original_height
                scale_ratio = min(width_ratio_max, height_ratio_max)
                
                # 计算缩小后的尺寸，并二次确认不小于最小分辨率
                new_width = max(int(original_width * scale_ratio), min_width)
                new_height = max(int(original_height * scale_ratio), min_height)
                
                # 高质量缩小
                resized_img = pil_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # 4. 尺寸在 [min, max] 区间内：保持原图不变
            else:
                resized_img = pil_img
            
            # 5. PIL转Tensor（保持原逻辑）
            img_np = np.array(resized_img).astype(np.float32) / 255.0
            processed_images.append(torch.from_numpy(img_np))
        
        # 堆叠图像并返回（保持原逻辑）
        return (torch.stack(processed_images),)

# 节点注册（保持原逻辑）
NODE_CLASS_MAPPINGS = {
    "ImageCompressor": ImageCompressor
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImageCompressor": "图像压缩器（支持最小分辨率）"
}