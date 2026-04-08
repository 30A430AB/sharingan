import argparse
from pathlib import Path
import sys
from natsort import natsorted
from loguru import logger
from tqdm import tqdm
import torch
import json

from core.detection import ComicTextDetector
from core.box_refiner import CoordinateAdjuster
from core.matching import match_and_create_masks, match_images
from core.compositing import (
    resize_text_images_to_match_raw,
    extract_text_from_masks,
    inpaint_raw_images,
    apply_text_to_inpainted_step
)
from core.config import (
    SUPPORTED_EXTENSIONS,
    DirPaths,
    DataPaths,
    InpaintAlgorithm,
)


def configure_logging():
    """配置 loguru 日志输出和重定向"""
    # 保存原始的 stdout/stderr
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    # 移除 loguru 默认的 stderr 输出
    logger.remove()

    # 控制台输出到原始 stdout，避免循环
    logger.add(
        original_stdout,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
        level="INFO",
        colorize=True
    )

    # 重定向 print → loguru
    class StreamToLogger:
        def __init__(self, logger, level="INFO", original_stream=None):
            self.logger = logger
            self.level = level
            self.original_stream = original_stream  # 可选，用于 flush

        def write(self, message):
            if message.strip():
                self.logger.log(self.level, message.rstrip())

        def flush(self):
            if self.original_stream:
                self.original_stream.flush()

        def isatty(self):
            # 防止依赖 isatty 的库（如 uvicorn）报错
            return False

    sys.stdout = StreamToLogger(logger, "INFO", original_stdout)
    sys.stderr = StreamToLogger(logger, "ERROR", original_stderr)

    # 拦截标准 logging 模块的日志，使其也通过 loguru 输出
    import logging
    class InterceptHandler(logging.Handler):
        def emit(self, record):
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

class MangaTransFerPipeline:
    """漫画翻译移植流程"""
    
    def __init__(self, raw_dir, text_dir, model_path, inpaint_algorithm=InpaintAlgorithm.PATCHMATCH, 
                output_dir=None, automatch=True, generate_thumbnails=False, precomputed_matches=None):
        """
        初始化
        
        Args:
            raw_dir: 生肉图片目录
            text_dir: 熟肉图片目录  
            model_path: 模型路径
            output_dir: 输出目录，默认为生肉目录
            automatch: 是否自动匹配图片（若 precomputed_matches 提供，则忽略此参数）
            generate_thumbnails: 是否生成缩略图（仅在自动匹配时有效）
            precomputed_matches: 预计算的匹配结果列表（GUI 传入），格式与 match_images 返回值相同
        """
        self.raw_dir = Path(raw_dir)
        self.text_dir = Path(text_dir)
        self.model_path = Path(model_path)
        self.inpaint_algorithm = inpaint_algorithm
        self.output_dir = Path(output_dir) if output_dir else self.raw_dir
        self.automatch = automatch
        self.generate_thumbnails = generate_thumbnails
        self.precomputed_matches = precomputed_matches
        
        # 设置日志
        self.logger = logger.bind(name='MangaPipeline')
        
        # 创建输出目录
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_sorted_images(self, directory):
        image_files = []
        try:
            for entry in Path(directory).iterdir():
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                    image_files.append(entry)
        except FileNotFoundError:
            pass
        return natsorted(image_files, key=lambda x: x.name)
    
    def prepare_directories(self):
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
    
    def resize_images(self, directories):
        """复制熟肉图片到工作目录，并根据图片匹配结果重命名"""

        src_dir = self.text_dir
        dst_dir = directories['temp']
        dst_dir.mkdir(parents=True, exist_ok=True)

        # ----- 复制阶段 -----
        if self.precomputed_matches is not None:
            self.logger.info("使用 GUI 传入的匹配结果...")
            matches = self.precomputed_matches
            if not matches:
                raise Exception("匹配结果为空")

            for match in matches:
                text_path_str = match.get('text_path', '')
                if not text_path_str or not Path(text_path_str).exists():
                    raw_path = Path(match['raw_path'])
                    self.logger.warning(f"跳过匹配项 {raw_path.name}: 未选择文本图片")
                    continue

                raw_path = Path(match['raw_path'])
                text_path = Path(text_path_str)
                raw_stem = raw_path.stem
                text_suffix = text_path.suffix
                target_name = raw_stem + text_suffix
                dst_path = dst_dir / target_name
                import shutil
                shutil.copy2(text_path, dst_path)

        elif self.automatch:
            self.logger.info("启用自动匹配模式，正在计算图片相似度...")
            match_model_path = DataPaths.RESNET18
            # if not match_model_path.exists():
            #     raise Exception(f"匹配模型不存在: {match_model_path}")

            matches = match_images(
                raw_dir=str(self.raw_dir),
                text_dir=str(src_dir),
                model_weights_path=str(match_model_path),
                generate_thumbnails=self.generate_thumbnails
            )
            if not matches:
                raise Exception("图片匹配失败，未获得任何匹配结果")

            for match in matches:
                raw_path = Path(match['raw_path'])
                text_path = Path(match['text_path'])
                raw_stem = raw_path.stem
                text_suffix = text_path.suffix
                target_name = raw_stem + text_suffix
                dst_path = dst_dir / target_name
                import shutil
                shutil.copy2(text_path, dst_path)
        else:
            self.logger.info("未启用自动匹配，复制所有文本图片...")
            text_images = self._get_sorted_images(src_dir)
            if not text_images:
                raise Exception(f"文本图片目录中没有图片文件: {src_dir}")

            for img_path in text_images:
                dst_path = dst_dir / img_path.name
                import shutil
                shutil.copy2(img_path, dst_path)

        self.text_dir = dst_dir

        # ----- 调整尺寸阶段 -----
        raw_images = self._get_sorted_images(self.raw_dir)
        total_raw = len(raw_images)
        pbar = tqdm(total=total_raw, desc="尺寸调整", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def resize_callback(idx, total):
            pbar.update(1)

        resize_text_images_to_match_raw(
            raw_dir=str(self.raw_dir),
            text_dir=str(self.text_dir),
            status_callback=resize_callback
        )

        pbar.close()
        return True
    
    def detect_text(self, directories):
        """文本检测"""

        # 获取图片数量
        raw_images = self._get_sorted_images(self.raw_dir)
        text_images = self._get_sorted_images(self.text_dir)
        total_images = len(raw_images) + len(text_images)

        # 创建统一的进度条
        pbar = tqdm(total=total_images, desc="文本检测", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def progress_callback(idx, total):
            pbar.update(1)

        # 检测生肉文本
        raw_detector = ComicTextDetector(
            img_dir=str(self.raw_dir),
            save_dir=str(directories['raw_mask']),
            model_path=str(self.model_path),
            save_json=True,
            device='cuda' if torch.cuda.is_available() else "cpu",
            logger=self.logger,
            status_callback=progress_callback
        )
        raw_detector.detect()

        # 检测熟肉文本
        text_detector = ComicTextDetector(
            img_dir=str(self.text_dir),
            save_dir=str(directories['text_mask']),
            model_path=str(self.model_path),
            save_json=True,
            device='cuda' if torch.cuda.is_available() else "cpu",
            logger=self.logger,
            status_callback=progress_callback
        )
        text_detector.detect()

        pbar.close()
        return True
    
    def match_boxes(self, directories):
        """文本框匹配"""

        text_annotations = directories['text_mask'] / "annotations.json"
        raw_annotations = directories['raw_mask'] / "annotations.json"
        match_output = self.output_dir / "match_results.json"

        # 获取 raw 图片总数用于进度条
        raw_images = self._get_sorted_images(self.raw_dir)
        total_raw = len(raw_images)
        pbar = tqdm(total=total_raw, desc="文本匹配", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def progress_callback(idx, total):
            pbar.update(1)

        success = match_and_create_masks(
            raw_annotations_path=str(raw_annotations),
            text_annotations_path=str(text_annotations),
            output_path=str(match_output),
            raw_mask_dir=str(directories['raw_mask']),
            new_mask_dir=str(directories['new_mask']),
            status_callback=progress_callback
        )

        pbar.close()

        if not success:
            raise Exception("文本框匹配失败")

        return match_output
    
    def adjust_coordinates(self, directories):
        """文本框坐标调整"""

        match_results_path = self.output_dir / "match_results.json"
        text_dir = directories['text']

        # 读取 annotations.json 获取图片总数
        with open(match_results_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        total_images = len(data.get('pages', {}))

        pbar = tqdm(total=total_images, desc="坐标调整", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def progress_callback(processed, total):
            pbar.update(1)

        adjuster = CoordinateAdjuster(       
            match_results_path=str(match_results_path),
            text_dir=str(text_dir),
            status_callback=progress_callback,
        )

        adjuster.adjust_annotations()

        pbar.close()
        return True
    
    def extract_text(self, directories):
        """文本提取"""

        input_images = self._get_sorted_images(self.text_dir)
        total = len(input_images)

        pbar = tqdm(total=total, desc="文本提取", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def progress_callback(processed, total):
            pbar.update(1)

        extract_text_from_masks(
            input_dir=str(self.text_dir),
            mask_dir=str(directories['text_mask']),
            output_dir=str(directories['text']),
            dilation_iterations=2,
            status_callback=progress_callback
        )

        pbar.close()
        return True
    
    def inpaint_raw(self, directories):
        """图片修复"""

        # 获取需要修复的图片数量（基于 raw_dir 中的图片）
        raw_images = self._get_sorted_images(self.raw_dir)
        total = len(raw_images)

        pbar = tqdm(total=total, desc="图像修复", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def progress_callback(idx, total):
            pbar.update(1)

        inpainted_count = inpaint_raw_images(
            raw_img_dir=str(self.raw_dir),
            new_mask_dir=str(directories['new_mask']),
            output_dir=str(directories['inpainted']),
            algorithm=self.inpaint_algorithm,
            status_callback=progress_callback
        )

        pbar.close()

        if inpainted_count == 0:
            raise Exception("修复失败")

        return True
    
    def apply_text(self, match_output_path):
        """文本移植"""

        # 读取 JSON 获取页面总数
        import json
        with open(match_output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        total_pages = len(data.get("pages", {}))

        pbar = tqdm(total=total_pages, desc="文本移植", unit="张",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

        def progress_callback():
            pbar.update(1)

        success = apply_text_to_inpainted_step(
            json_path=str(match_output_path),
            status_callback=progress_callback
        )

        pbar.close()

        if not success:
            raise Exception("文本移植失败")

        return True
    
    def run(self):
        """运行完整处理流程"""
        self.logger.info("开始漫画重嵌处理")
        self.logger.info(f"生肉目录: {self.raw_dir}")
        self.logger.info(f"熟肉目录: {self.text_dir}")
        self.logger.info(f"输出目录: {self.output_dir}")
        
        try:
            # 准备目录结构
            directories = self.prepare_directories()
            
            # 执行处理步骤
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


def main():
    """命令行主函数"""
    parser = argparse.ArgumentParser(description='漫画翻译移植工具')
    parser.add_argument('raw_dir', help='原始图片目录路径')
    parser.add_argument('text_dir', help='文本图片目录路径')
    # parser.add_argument('--inpaint-algorithm', '-i', default='patchmatch', choices=['patchmatch', 'lama_large_512px'],
    #                     help='修复算法 (patchmatch 或 lama_large_512px)')
    parser.add_argument('--automatch', default='true', choices=['true', 'false'],
                    help='是否自动匹配图片')
    parser.add_argument('--thumbnails', action='store_true')
    args = parser.parse_args()
    
    # 检查输入目录
    if not Path(args.raw_dir).exists():
        print(f"错误: 生肉目录不存在 - {args.raw_dir}")
        return
        
    if not Path(args.text_dir).exists():
        print(f"错误: 熟肉目录不存在 - {args.text_dir}")
        return
    
    # 固定模型路径（默认位置）
    model_path = DataPaths.COMIC_TEXT_DETECTOR
    # if not model_path.exists():
    #     print(f"错误: 模型文件不存在 - {model_path}")
    #     print("请确保 comictextdetector.pt 模型文件存在于 data/models/ 目录下")
    #     return
    
    try:
        # 创建并运行流程（输出目录默认为生肉目录）
        pipeline = MangaTransFerPipeline(
            raw_dir=args.raw_dir,
            text_dir=args.text_dir,
            model_path=model_path,
            # inpaint_algorithm=args.inpaint_algorithm,
            output_dir=None,  # 使用默认（生肉目录）
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
