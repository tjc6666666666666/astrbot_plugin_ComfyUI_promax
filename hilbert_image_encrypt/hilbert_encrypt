import numpy as np
import torch
from PIL import Image
import math
import cv2


class HilbertImageEncrypt:
    """基于希尔伯特空间填充曲线的图片加密节点（带启用控制）"""
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "mode": (["encrypt", "decrypt"],),
                "enable": ("BOOLEAN", {"default": True}),  # 新增：启用控制选项
            }
        }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "process_image"
    CATEGORY = "图像操作/加密"

    def gilbert2d(self, width, height):
        """生成希尔伯特空间填充曲线的坐标序列"""
        coordinates = []
        
        if width >= height:
            self.generate2d(0, 0, width, 0, 0, height, coordinates)
        else:
            self.generate2d(0, 0, 0, height, width, 0, coordinates)
            
        return coordinates
    
    def generate2d(self, x, y, ax, ay, bx, by, coordinates):
        """递归生成希尔伯特曲线坐标"""
        w = abs(ax + ay)
        h = abs(bx + by)
        
        dax = int(math.copysign(1, ax)) if ax != 0 else 0
        day = int(math.copysign(1, ay)) if ay != 0 else 0
        dbx = int(math.copysign(1, bx)) if bx != 0 else 0
        dby = int(math.copysign(1, by)) if by != 0 else 0
        
        if h == 1:
            # 填充一行
            for _ in range(w):
                coordinates.append((x, y))
                x += dax
                y += day
            return
        
        if w == 1:
            # 填充一列
            for _ in range(h):
                coordinates.append((x, y))
                x += dbx
                y += dby
            return
        
        ax2 = ax // 2
        ay2 = ay // 2
        bx2 = bx // 2
        by2 = by // 2
        
        w2 = abs(ax2 + ay2)
        h2 = abs(bx2 + by2)
        
        if 2 * w > 3 * h:
            if (w2 % 2) and (w > 2):
                ax2 += dax
                ay2 += day
            
            # 长形情况：分为两部分
            self.generate2d(x, y, ax2, ay2, bx, by, coordinates)
            self.generate2d(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by, coordinates)
        else:
            if (h2 % 2) and (h > 2):
                bx2 += dbx
                by2 += dby
            
            # 标准情况：上一步，长水平，下一步
            self.generate2d(x, y, bx2, by2, ax2, ay2, coordinates)
            self.generate2d(x + bx2, y + by2, ax, ay, bx - bx2, by - by2, coordinates)
            self.generate2d(
                x + (ax - dax) + (bx2 - dbx), 
                y + (ay - day) + (by2 - dby),
                -bx2, -by2, -(ax - ax2), -(ay - ay2), 
                coordinates
            )
    
    def process_image(self, image, mode, enable):
        """处理图像：根据enable参数决定是否执行加密/解密"""
        # 如果不启用，直接返回原始图像
        if not enable:
            return (image,)
            
        # 启用时执行原有处理逻辑
        img_np = image.cpu().numpy()
        batch_size, height, width, channels = img_np.shape
        
        curve = self.gilbert2d(width, height)
        total_pixels = width * height
        offset = round((math.sqrt(5) - 1) / 2 * total_pixels)
        
        result = []
        
        for b in range(batch_size):
            img = img_np[b].copy()
            new_img = np.zeros_like(img)
            
            if mode == "encrypt":
                # 加密模式
                for i in range(total_pixels):
                    old_x, old_y = curve[i]
                    new_idx = (i + offset) % total_pixels
                    new_x, new_y = curve[new_idx]
                    new_img[new_y, new_x] = img[old_y, old_x]
            else:
                # 解密模式
                for i in range(total_pixels):
                    old_x, old_y = curve[i]
                    new_idx = (i + offset) % total_pixels
                    new_x, new_y = curve[new_idx]
                    new_img[old_y, old_x] = img[new_y, new_x]
            
            result.append(new_img)
        
        result_tensor = torch.from_numpy(np.stack(result)).float()
        return (result_tensor,)

# 节点映射
NODE_CLASS_MAPPINGS = {
    "HilbertImageEncrypt": HilbertImageEncrypt
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HilbertImageEncrypt": "希尔伯特曲线图像加密"
}
