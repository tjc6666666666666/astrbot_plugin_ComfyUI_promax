# comfyui-mirage-tank/init.py
from PIL import Image
import numpy as np
import torch
from torchvision.transforms.functional import to_pil_image, to_tensor

class MirageTankGenerator:
    """幻影坦克图片生成节点"""
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "front_image": ("IMAGE",),  # 表图 (ComfyUI默认格式: [B, H, W, C] 张量)
                "back_image": ("IMAGE",),   # 里图
                "mode": (["gray", "color"],),  # 模式选择
                "param_a": ("FLOAT", {"default": 5.0, "min": 1.0, "max": 20.0, "step": 0.1}),  # 算法参数a
                "param_b": ("FLOAT", {"default": 5.0, "min": 1.0, "max": 20.0, "step": 0.1}),  # 算法参数b
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "generate"
    CATEGORY = "Mirage Tank"

    def generate(self, front_image, back_image, mode, param_a, param_b):
        # 转换ComfyUI张量格式到PIL图像
        front_pil = self._tensor_to_pil(front_image)
        back_pil = self._tensor_to_pil(back_image)

        # 根据模式生成幻影坦克
        if mode == "gray":
            result_pil = self._generate_gray_tank(front_pil, back_pil, param_a, param_b)
        else:  # color
            result_pil = self._generate_color_tank(front_pil, back_pil, param_a, param_b)

        # 转换回ComfyUI张量格式
        result_tensor = self._pil_to_tensor(result_pil)
        return (result_tensor,)

    def _tensor_to_pil(self, tensor):
        """将ComfyUI的IMAGE张量([B, H, W, C])转换为PIL图像"""
        # 取第一个批次并转换范围为0-255
        img_np = (tensor[0].cpu().numpy() * 255).astype(np.uint8)
        return Image.fromarray(img_np)

    def _pil_to_tensor(self, pil_img):
        """将PIL图像转换为ComfyUI的IMAGE张量格式"""
        img_np = np.array(pil_img).astype(np.float32) / 255.0
        return torch.from_numpy(img_np).unsqueeze(0)

    def _generate_gray_tank(self, front_img, back_img, a, b):
        """生成黑白幻影坦克（移植自原算法）"""
        # 转为灰度图
        image_f = front_img.convert("L")
        image_b = back_img.convert("L")

        # 尺寸对齐
        w, h = min(image_f.width, image_b.width), min(image_f.height, image_b.height)
        image_f = image_f.resize((w, h), Image.Resampling.LANCZOS)
        image_b = image_b.resize((w, h), Image.Resampling.LANCZOS)

        # 转换为numpy数组
        array_f = np.array(image_f, dtype=np.float64)
        array_b = np.array(image_b, dtype=np.float64)
        new_pixels = np.zeros((h, w, 4), dtype=np.uint8)

        # 灰度模式核心算法
        wf = array_f * a / 10 + 128
        wb = array_b * b / 10
        alpha = 1.0 - wf / 255.0 + wb / 255.0
        R_new = np.where(np.abs(alpha) > 1e-6, wb / alpha, 255.0)

        # 构建RGBA通道（灰度图三通道值相同）
        new_pixels[:, :, 0] = np.clip(R_new, 0, 255).astype(np.uint8)
        new_pixels[:, :, 1] = new_pixels[:, :, 0]
        new_pixels[:, :, 2] = new_pixels[:, :, 0]
        new_pixels[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)

        return Image.fromarray(new_pixels, mode="RGBA")

    def _generate_color_tank(self, front_img, back_img, a, b):
        """生成彩色幻影坦克（移植自原算法）"""
        # 转为RGB
        image_f = front_img.convert("RGB")
        image_b = back_img.convert("RGB")

        # 尺寸对齐
        w, h = min(image_f.width, image_b.width), min(image_f.height, image_b.height)
        image_f = image_f.resize((w, h), Image.Resampling.LANCZOS)
        image_b = image_b.resize((w, h), Image.Resampling.LANCZOS)

        # 转换为numpy数组
        array_f = np.array(image_f, dtype=np.float64)
        array_b = np.array(image_b, dtype=np.float64)
        new_pixels = np.zeros((h, w, 4), dtype=np.uint8)

        # 彩色模式核心算法（LAB近似）
        Rf, Gf, Bf = array_f[:, :, 0] * a / 10, array_f[:, :, 1] * a / 10, array_f[:, :, 2] * a / 10
        Rb, Gb, Bb = array_b[:, :, 0] * b / 10, array_b[:, :, 1] * b / 10, array_b[:, :, 2] * b / 10

        delta_r = Rb - Rf
        delta_g = Gb - Gf
        delta_b = Bb - Bf
        coe_a = 8 + 255 / 256 + (delta_r - delta_b) / 256
        coe_b = 4 * delta_r + 8 * delta_g + 6 * delta_b + ((delta_r - delta_b) * (Rb + Rf)) / 256 + (delta_r **2 - delta_b** 2) / 512
        A_new = 255 + coe_b / (2 * coe_a)

        A_new = np.clip(A_new, 0, 255)
        A_safe = np.where(A_new < 1, 1, A_new)  # 防止除零

        # 计算RGB通道值
        R_new = np.clip((255 * Rb * b / 10) / A_safe, 0, 255)
        G_new = np.clip((255 * Gb * b / 10) / A_safe, 0, 255)
        B_new = np.clip((255 * Bb * b / 10) / A_safe, 0, 255)

        # 构建RGBA通道
        new_pixels[:, :, 0] = R_new.astype(np.uint8)
        new_pixels[:, :, 1] = G_new.astype(np.uint8)
        new_pixels[:, :, 2] = B_new.astype(np.uint8)
        new_pixels[:, :, 3] = A_new.astype(np.uint8)

        return Image.fromarray(new_pixels, mode="RGBA")


# 注册节点
NODE_CLASS_MAPPINGS = {
    "MirageTankGenerator": MirageTankGenerator
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MirageTankGenerator": "幻影坦克生成器"
}
