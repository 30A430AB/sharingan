import logging
import os
from typing import Callable, Optional


class ComicTextDetector:
    def __init__(
        self,
        img_dir: str,
        save_dir: str,
        model_path: str,
        save_json: bool = True,
        device: str = 'cuda',
        logger: Optional[logging.Logger] = None,
        status_callback: Optional[Callable] = None
    ):
        # 初始化参数
        self.img_dir = img_dir
        self.save_dir = save_dir
        self.model_path = model_path
        self.save_json = save_json
        self.device = device
        self.logger = logger or logging.getLogger('TextDetector')
        self.status_callback = status_callback

        # 验证路径和准备输出目录
        self._validate_paths()
        self._prepare_output()

    def _validate_paths(self):
        """路径验证"""
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"模型文件 {self.model_path} 不存在！")
        if not os.path.exists(self.img_dir):
            raise FileNotFoundError(f"输入目录 {self.img_dir} 不存在！")

    def _prepare_output(self):
        """准备输出目录"""
        os.makedirs(self.save_dir, exist_ok=True)

    def detect(self):
        """执行检测"""
        from .ctd_utils.inference import model2annotations
        
        model2annotations(
        self.model_path,
        self.img_dir,
        self.save_dir,
        save_json=self.save_json,
        progress_callback=self.status_callback
    )

