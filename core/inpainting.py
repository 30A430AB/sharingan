import os
import numpy as np
import cv2
from pathlib import Path
from PIL import Image
from simple_lama_inpainting import SimpleLama
from .patch_match import inpaint as patch_match_inpaint


class Inpainter:
    def __init__(self, 
                 img_path: str,
                 mask_path: str,
                 output_path: str = "result.png",
                 dilate_iterations: int = 3,
                 kernel_size: int = 3,
                 algorithm: str = "patchmatch",
                 debug: bool = False):
        
        self.img_path = img_path
        self.mask_path = mask_path
        self.output_path = output_path
        self.dilate_iterations = dilate_iterations
        self.kernel_size = kernel_size
        self.original_size = None
        self.debug = debug
        self.algorithm = algorithm
        
        # 初始化算法组件
        if self.algorithm == "lama_large_512px":
            self.model_path = Path("data/models/anime-manga-big-lama.pt")
            os.environ["LAMA_MODEL"] = str(self.model_path)
            self.simple_lama = SimpleLama()
        elif self.algorithm == "patchmatch":
            self._init_patchmatch()
        
        self.process()

    def _init_patchmatch(self):
        """初始化PatchMatch依赖"""
        self.patch_size = 3

    def _load_images(self):
        """加载并处理图像和掩膜"""
        # 加载原图
        self.img_pil = Image.open(self.img_path).convert("RGB")
        self.original_size = self.img_pil.size

        # 加载并处理掩膜
        mask_pil = Image.open(self.mask_path).convert("L")
        mask_np = np.array(mask_pil)
        _, binary_mask = cv2.threshold(mask_np, 127, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.kernel_size, self.kernel_size))
        dilated_mask = cv2.dilate(binary_mask, kernel, iterations=self.dilate_iterations)
        self.mask_pil = Image.fromarray(dilated_mask).convert("L")

        if self.debug:
            self.mask_pil.save("debug_dilated_mask.png")

    def _validate_images(self):
        if self.img_pil is None:
            raise ValueError(f"无法加载图像文件: {self.img_path}")
        if self.mask_pil is None:
            raise ValueError(f"无法加载掩膜文件: {self.mask_path}")

    def _execute_inpaint(self):
        """执行修复的核心方法"""
        if self.algorithm == "lama_large_512px":
            return self._run_lama()
        elif self.algorithm == "patchmatch":
            return self._run_patchmatch()

    def _run_lama(self):
        """LaMa算法实现"""
        result_pil = self.simple_lama(self.img_pil, self.mask_pil)
        if result_pil.size != self.original_size:
            result_pil = result_pil.crop((0, 0, *self.original_size))
        return result_pil

    def _run_patchmatch(self):
        """PatchMatch算法实现"""
        # 转换图像格式
        img_np = cv2.cvtColor(np.array(self.img_pil), cv2.COLOR_RGB2BGR)
        mask_np = np.array(self.mask_pil)
        
        # 确保掩膜是单通道且形状正确
        if mask_np.ndim == 2:
            mask_np = mask_np[:, :, np.newaxis]  # 添加通道维度
        
        # 执行修复 - 直接使用导入的函数
        result_np = patch_match_inpaint(
            img_np.astype(np.uint8),
            mask_np.astype(np.uint8),
            patch_size=self.patch_size
        )
        
        # 转换回PIL格式
        return Image.fromarray(cv2.cvtColor(result_np, cv2.COLOR_BGR2RGB))

    def process(self):
        """处理流程控制器"""
        self._load_images()
        self._validate_images()
        result = self._execute_inpaint()
        
        # 确保图片为 RGB 模式，去除透明度
        if result.mode == 'RGBA':
            # 创建白色背景并合成，保留 RGB 通道，丢弃 alpha
            background = Image.new('RGB', result.size, (255, 255, 255))
            # 如果 alpha 通道存在，将其作为蒙版合成到白色背景上
            background.paste(result, mask=result.split()[3])
            result = background
        elif result.mode != 'RGB':
            result = result.convert('RGB')
        
        result.save(self.output_path, format="PNG", optimize=True, compress_level=9)