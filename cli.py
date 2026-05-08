import argparse
import logging
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import torch
from loguru import logger
from natsort import natsorted
from tqdm import tqdm

from core.compositing import (
    resize_text_images_to_match_raw,
    extract_text_from_masks,
    inpaint_raw_images,
    apply_text_to_inpainted_step,
)
from core.config import SUPPORTED_EXTENSIONS, DirPaths, DataPaths, InpaintAlgorithm
from core.detection import ComicTextDetector
from core.box_refiner import CoordinateAdjuster
from core.matching import match_and_create_masks, match_images

# ==========================================
# 常量配置
# ==========================================
TQDM_BAR_FORMAT = '{l_bar}{bar}| {n_fmt}/{total_fmt}'
DEFAULT_DILATION_ITERATIONS = 2


# ==========================================
# 日志配置 (保持不变)
# ==========================================
def configure_logging() -> None:
    """配置 loguru 日志输出并拦截标准 logging 模块"""
    original_stdout = sys.stdout
    logger.remove()
    
    logger.add(
        original_stdout,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
        level="INFO",
        colorize=True,
    )

    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)


# ==========================================
# 进度条抽象工具
# ==========================================
@contextmanager
def tqdm_progress(total: int, desc: str, unit: str = "张"):
    """tqdm 上下文管理器，吸收不同签名的回调差异"""
    with tqdm(
        total=total, desc=desc, unit=unit,
        bar_format=TQDM_BAR_FORMAT, mininterval=0, miniters=1
    ) as pbar:
        def update_callback(*args: Any, **kwargs: Any) -> None:
            pbar.update(1)
        
        yield update_callback


# ==========================================
# 流水线
# ==========================================
class MangaTransFerPipeline:
    """漫画翻译移植流程"""

    def __init__(
        self,
        raw_dir: str | Path,
        text_dir: str | Path,
        model_path: str | Path,
        inpaint_algorithm: InpaintAlgorithm = InpaintAlgorithm.PATCHMATCH,
        output_dir: Optional[str | Path] = None,
        automatch: bool = True,
        generate_thumbnails: bool = False,
        precomputed_matches: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.text_dir = Path(text_dir)
        self.model_path = Path(model_path)
        self.inpaint_algorithm = inpaint_algorithm
        self.output_dir = Path(output_dir) if output_dir else self.raw_dir
        
        self.precomputed_matches = precomputed_matches 
        self.automatch = automatch
        self.generate_thumbnails = generate_thumbnails

        self.logger = logger.bind(name='MangaPipeline')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 缓存设备信息，避免重复调用
        self._device = 'cuda' if torch.cuda.is_available() else "cpu"

    def _get_sorted_images(self, directory: Path) -> List[Path]:
        """获取目录下支持格式的图片并自然排序"""
        if not directory.exists():
            return []
        return natsorted(
            [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS],
            key=lambda x: x.name
        )

    def prepare_directories(self) -> Dict[str, Path]:
        """准备输出目录结构"""
        dirs = {
            'temp': self.output_dir / DirPaths.TEMP,
            'text': self.output_dir / DirPaths.TEXT,
            'raw_mask': self.output_dir / DirPaths.RAW_MASK,
            'text_mask': self.output_dir / DirPaths.TEXT_MASK,
            'new_mask': self.output_dir / DirPaths.NEW_MASK,
            'inpainted': self.output_dir / DirPaths.INPAINTED,
            'result': self.output_dir / DirPaths.RESULT,
        }
        for dir_path in dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
        return dirs

    def _resolve_match_list(self) -> List[Dict[str, Path]]:
        """
        统一返回 [{'raw_path': Path, 'text_path': Path}, ...]
        """
        if self.precomputed_matches is not None:
            self.logger.info("使用 GUI 传入的匹配结果...")
            if not self.precomputed_matches:
                raise Exception("匹配结果为空")
            
            valid_matches = []
            for match in self.precomputed_matches:
                text_path_str = match.get('text_path', '')
                if text_path_str and Path(text_path_str).exists():
                    valid_matches.append({
                        'raw_path': Path(match['raw_path']),
                        'text_path': Path(text_path_str)
                    })
            return valid_matches

        if self.automatch:
            self.logger.info("启用自动匹配模式，正在计算图片相似度...")
            matches = match_images(
                raw_dir=str(self.raw_dir),
                text_dir=str(self.text_dir),
                model_weights_path=str(DataPaths.RESNET18),
                generate_thumbnails=self.generate_thumbnails,
            )
            if not matches:
                raise Exception("图片匹配失败，未获得任何匹配结果")
            return [
                {'raw_path': Path(m['raw_path']), 'text_path': Path(m['text_path'])}
                for m in matches
            ]

        self.logger.info("未启用自动匹配，复制所有文本图片...")
        text_images = self._get_sorted_images(self.text_dir)
        if not text_images:
            raise Exception(f"文本图片目录中没有图片文件: {self.text_dir}")
        
        # 无匹配模式下，raw_path 指向自身以保证后续 raw_stem + text_suffix 拼接逻辑的正确性
        return [{'raw_path': img, 'text_path': img} for img in text_images]

    def resize_images(self, directories: Dict[str, Path]) -> bool:
        """复制熟肉图片到工作目录，并根据图片匹配结果重命名及调整尺寸"""
        dst_dir = directories['temp']
        dst_dir.mkdir(parents=True, exist_ok=True)

        # 1. 获取统一格式的匹配列表
        match_list = self._resolve_match_list()

        # 2. 执行统一的复制逻辑
        for item in match_list:
            raw_path, text_path = item['raw_path'], item['text_path']
            target_name = f"{raw_path.stem}{text_path.suffix}"
            shutil.copy2(text_path, dst_dir / target_name)

        self.text_dir = dst_dir
        effective_match_count = len(match_list)

        # 3. 调整尺寸
        with tqdm_progress(effective_match_count, "尺寸调整") as callback:
            resize_text_images_to_match_raw(
                raw_dir=str(self.raw_dir),
                text_dir=str(self.text_dir),
                status_callback=callback,
            )
        return True

    def detect_text(self, directories: Dict[str, Path]) -> bool:
        """文本检测"""
        total_images = len(self._get_sorted_images(self.raw_dir)) + len(self._get_sorted_images(self.text_dir))

        with tqdm_progress(total_images, "文本检测") as callback:
            for img_dir, mask_dir in [
                (self.raw_dir, directories['raw_mask']),
                (self.text_dir, directories['text_mask'])
            ]:
                detector = ComicTextDetector(
                    img_dir=str(img_dir),
                    save_dir=str(mask_dir),
                    model_path=str(self.model_path),
                    save_json=True,
                    device=self._device,  # 使用缓存属性
                    logger=self.logger,
                    status_callback=callback,
                )
                detector.detect()
        return True

    def match_boxes(self, directories: Dict[str, Path]) -> Path:
        """文本框匹配"""
        match_output = self.output_dir / "match_results.json"
        total_raw = len(self._get_sorted_images(self.raw_dir))

        with tqdm_progress(total_raw, "文本匹配") as callback:
            success = match_and_create_masks(
                raw_annotations_path=str(directories['raw_mask'] / "annotations.json"),
                text_annotations_path=str(directories['text_mask'] / "annotations.json"),
                output_path=str(match_output),
                raw_mask_dir=str(directories['raw_mask']),
                new_mask_dir=str(directories['new_mask']),
                text_image_dir=str(self.text_dir),
                status_callback=callback,
            )
            if not success:
                raise Exception("文本框匹配失败")
        
        return match_output

    def adjust_coordinates(self, directories: Dict[str, Path]) -> bool:
        """文本框坐标调整"""
        match_results_path = self.output_dir / "match_results.json"
        
        with open(match_results_path, 'r', encoding='utf-8') as f:
            total_images = len(json.load(f).get('pages', {}))

        with tqdm_progress(total_images, "坐标调整") as callback:
            adjuster = CoordinateAdjuster(
                match_results_path=str(match_results_path),
                text_dir=str(directories['text']),
                status_callback=callback,
            )
            adjuster.adjust_annotations()
        return True

    def extract_text(self, directories: Dict[str, Path]) -> bool:
        """文本提取"""
        total = len(self._get_sorted_images(self.text_dir))

        with tqdm_progress(total, "文本提取") as callback:
            extract_text_from_masks(
                input_dir=str(self.text_dir),
                mask_dir=str(directories['text_mask']),
                output_dir=str(directories['text']),
                dilation_iterations=DEFAULT_DILATION_ITERATIONS,  # 消除魔法值
                status_callback=callback,
            )
        return True

    def inpaint_raw(self, directories: Dict[str, Path]) -> bool:
        """图片修复"""
        total = len(self._get_sorted_images(self.raw_dir))

        with tqdm_progress(total, "图像修复") as callback:
            inpainted_count = inpaint_raw_images(
                raw_img_dir=str(self.raw_dir),
                new_mask_dir=str(directories['new_mask']),
                output_dir=str(directories['inpainted']),
                algorithm=self.inpaint_algorithm,
                status_callback=callback,
            )
            if inpainted_count == 0:
                raise Exception("修复失败")
        return True

    def apply_text(self, match_output_path: Path) -> bool:
        """文本移植"""
        with open(match_output_path, 'r', encoding='utf-8') as f:
            total_pages = len(json.load(f).get("pages", {}))

        with tqdm_progress(total_pages, "文本移植") as callback:
            success = apply_text_to_inpainted_step(
                json_path=str(match_output_path),
                raw_dir=str(self.raw_dir),
                status_callback=callback,
            )
            if not success:
                raise Exception("文本移植失败")
        return True

    def run(self) -> None:
        """运行处理流程"""
        self.logger.info("开始漫画文本移植")
        self.logger.info(f"生肉目录: {self.raw_dir}")
        self.logger.info(f"熟肉目录: {self.text_dir}")
        self.logger.info(f"输出目录: {self.output_dir}")

        if not self._get_sorted_images(self.raw_dir):
            raise Exception(f"生肉目录中没有支持的图片文件: {self.raw_dir}")
        if not self._get_sorted_images(self.text_dir):
            raise Exception(f"熟肉目录中没有支持的图片文件: {self.text_dir}")

        try:
            directories = self.prepare_directories()
            
            # 流水线步骤
            self.resize_images(directories)
            self.detect_text(directories)
            match_output = self.match_boxes(directories)
            self.extract_text(directories)
            self.adjust_coordinates(directories)
            self.inpaint_raw(directories)
            self.apply_text(match_output)

            self.logger.info("处理结束。")
            self.logger.info(f"最终结果保存在: {directories['result']}")
        except Exception as e:
            self.logger.error(f"处理失败: {e}")
            raise


# ==========================================
# 命令行入口
# ==========================================
def main() -> int:
    configure_logging()
    
    parser = argparse.ArgumentParser(description='漫画翻译移植工具')
    parser.add_argument('raw_dir', help='原始图片目录路径')
    parser.add_argument('text_dir', help='文本图片目录路径')
    parser.add_argument('--automatch', default='true', choices=['true', 'false'], help='是否自动匹配图片')
    parser.add_argument('--thumbnails', action='store_true')
    args = parser.parse_args()

    if not Path(args.raw_dir).exists():
        print(f"错误: 生肉目录不存在 - {args.raw_dir}")
        return 1
    if not Path(args.text_dir).exists():
        print(f"错误: 熟肉目录不存在 - {args.text_dir}")
        return 1

    model_path = DataPaths.COMIC_TEXT_DETECTOR

    try:
        pipeline = MangaTransFerPipeline(
            raw_dir=args.raw_dir,
            text_dir=args.text_dir,
            model_path=model_path,
            output_dir=None,
            automatch=(args.automatch.lower() == 'true'),
            generate_thumbnails=args.thumbnails,
        )
        pipeline.run()
    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
