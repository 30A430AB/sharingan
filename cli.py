import argparse
from pathlib import Path
import sys
from natsort import natsorted
from loguru import logger

from core.detection import ComicTextDetector
from core.adjustment import CoordinateAdjuster
from core.matching import match_and_create_masks
from core.image_utils import (
    resize_text_images_to_match_raw,
    extract_text_from_masks,
    inpaint_raw_images,
    apply_text_to_inpainted_step
)


def configure_logging():
    """配置 loguru 日志输出和重定向（仅在命令行模式下调用）"""
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

class MangaTranslationPipeline:
    """漫画重嵌处理流水线"""
    
    def __init__(self, raw_dir, text_dir, model_path, inpaint_algorithm='patchmatch', output_dir=None):
        """
        初始化流水线
        
        Args:
            raw_dir: 生肉图片目录
            text_dir: 熟肉图片目录  
            model_path: 模型路径
            output_dir: 输出目录，默认为生肉目录
        """
        self.raw_dir = Path(raw_dir)
        self.text_dir = Path(text_dir)
        self.model_path = Path(model_path)
        self.inpaint_algorithm = inpaint_algorithm
        self.output_dir = Path(output_dir) if output_dir else self.raw_dir
        
        # 设置日志
        self.logger = logger.bind(name='MangaPipeline')
        
        # 创建输出目录
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    # def _setup_logging(self):
    #     """设置日志格式"""
    #     logging.basicConfig(
    #         level=logging.INFO,
    #         format='%(asctime)s - %(levelname)s - %(message)s',
    #         datefmt='%H:%M:%S'
    #     )
    #     return logging.getLogger('MangaPipeline')
    
    def _get_sorted_images(self, directory):
        """获取自然排序的图片文件列表（安全遍历）"""
        from natsort import natsorted
        image_extensions = {'.jpg', '.jpeg', '.png'}
        image_files = []
        try:
            for entry in Path(directory).iterdir():
                if entry.is_file() and entry.suffix.lower() in image_extensions:
                    image_files.append(entry)
        except FileNotFoundError:
            pass
        return natsorted(image_files, key=lambda x: x.name)
    
    def prepare_directories(self):
        """准备输出目录结构"""
        dirs = {
            'text': self.output_dir / "text",           # 文字提取输出
            'raw_mask': self.output_dir / "raw_mask",   # 生肉检测输出
            'text_mask': self.output_dir / "text_mask", # 熟肉检测输出
            'new_mask': self.output_dir / "mask",       # 匹配后mask
            'inpainted': self.output_dir / "inpainted", # 修复结果
            'result': self.output_dir / "result"        # 最终结果
        }
        
        for dir_path in dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
            
        return dirs
    
    def step1_resize_images(self):
        """步骤1: 调整熟肉图片尺寸与生肉匹配"""
        self.logger.info("步骤1: 调整熟肉图片尺寸")
        
        resize_count = resize_text_images_to_match_raw(
            raw_dir=str(self.raw_dir),
            text_dir=str(self.text_dir),
            status_callback=lambda idx, total: self.logger.info(f"调整图片 {idx}/{total}")
        )
        
        if resize_count == 0:
            raise Exception("没有成功调整任何熟肉图片尺寸")
            
        self.logger.info(f"成功调整 {resize_count} 张图片")
        return True
    
    def step2_detect_text(self, directories):
        """步骤2: 文字检测（熟肉和生肉）"""
        self.logger.info("步骤2: 文字检测")
        
        # 检测熟肉文字
        self.logger.info("检测熟肉文字...")
        text_detector = ComicTextDetector(
            img_dir=str(self.text_dir),
            save_dir=str(directories['text_mask']),
            model_path=str(self.model_path),
            save_json=True,
            device='cuda',
            logger=self.logger
        )
        text_detector.detect()
        
        # 检测生肉文字
        self.logger.info("检测生肉文字...")
        raw_detector = ComicTextDetector(
            img_dir=str(self.raw_dir),
            save_dir=str(directories['raw_mask']),
            model_path=str(self.model_path),
            save_json=True,
            device='cuda',
            logger=self.logger
        )
        raw_detector.detect()
        
        self.logger.info("文字检测完成")
        return True
    
    def step3_match_boxes(self, directories):
        """步骤3: 文本框匹配"""
        self.logger.info("步骤3: 文本框匹配")
        
        text_annotations = directories['text_mask'] / "annotations.json"
        raw_annotations = directories['raw_mask'] / "annotations.json"
        match_output = self.output_dir / "match_results.json"
        
        success = match_and_create_masks(
            raw_annotations_path=str(raw_annotations),
            text_annotations_path=str(text_annotations),
            output_path=str(match_output),
            raw_mask_dir=str(directories['raw_mask']),
            new_mask_dir=str(directories['new_mask']),
            status_callback=lambda idx, total: self.logger.info(f"匹配文本框 {idx}/{total}")
        )
        
        if not success:
            raise Exception("文本框匹配失败")
            
        self.logger.info("文本框匹配完成")
        return match_output
    
    def step4_adjust_coordinates(self, directories):
        """步骤4: 熟肉坐标调整"""
        self.logger.info("步骤4: 熟肉坐标调整")
        
        annotations_path = directories['text_mask'] / "annotations.json"
        
        if not annotations_path.exists():
            raise Exception(f"标注文件不存在: {annotations_path}")
        
        adjuster = CoordinateAdjuster(
            text_dir=str(directories['text_mask']),
            annotations_path=str(annotations_path),
            status_callback=lambda idx, total: self.logger.info(f"调整坐标 {idx}/{total}")
        )
        
        adjuster.adjust_annotations()
        self.logger.info("坐标调整完成")
        return True
    
    def step5_extract_text(self, directories):
        """步骤5: 熟肉文字提取"""
        self.logger.info("步骤5: 熟肉文字提取")
        
        processed_count = extract_text_from_masks(
            input_dir=str(self.text_dir),
            mask_dir=str(directories['text_mask']),
            output_dir=str(directories['text']),
            dilation_iterations=2
        )
        
        if processed_count == 0:
            raise Exception("没有成功提取任何文字")
            
        self.logger.info(f"成功提取 {processed_count} 张图片的文字")
        return True
    
    def step6_inpaint_raw(self, directories):
        """步骤6: 生肉图片修复"""
        self.logger.info("步骤6: 生肉图片修复")
        
        inpainted_count = inpaint_raw_images(
            raw_img_dir=str(self.raw_dir),
            new_mask_dir=str(directories['new_mask']),
            output_dir=str(directories['inpainted']),
            algorithm=self.inpaint_algorithm,
            status_callback=lambda idx, total: self.logger.info(f"修复图片 {idx}/{total}")
        )
        
        if inpainted_count == 0:
            raise Exception("没有成功修复任何生肉图片")
            
        self.logger.info(f"成功修复 {inpainted_count} 张图片")
        return True
    
    def step7_apply_text(self, match_output_path):
        """步骤7: 文字贴图"""
        self.logger.info("步骤7: 文字贴图")
        
        success = apply_text_to_inpainted_step(
            json_path=str(match_output_path),
            status_callback=lambda idx, total: self.logger.info(f"贴图处理 {idx}/{total}")
        )
        
        if not success:
            raise Exception("文字贴图失败")
            
        self.logger.info("文字贴图完成")
        return True
    
    def run(self):
        """运行完整处理流水线"""
        self.logger.info("开始漫画重嵌处理")
        self.logger.info(f"生肉目录: {self.raw_dir}")
        self.logger.info(f"熟肉目录: {self.text_dir}")
        self.logger.info(f"输出目录: {self.output_dir}")
        
        try:
            # 准备目录结构
            directories = self.prepare_directories()
            
            # 执行处理步骤
            self.step1_resize_images()
            self.step2_detect_text(directories)
            match_output = self.step3_match_boxes(directories)
            self.step4_adjust_coordinates(directories)
            self.step5_extract_text(directories)
            self.step6_inpaint_raw(directories)
            self.step7_apply_text(match_output)
            
            self.logger.info("处理结束。")
            self.logger.info(f"最终结果保存在: {directories['result']}")
            
        except Exception as e:
            self.logger.error(f"处理失败: {e}")
            raise


def main():
    """命令行主函数"""
    parser = argparse.ArgumentParser(description='漫画翻译自动嵌字工具')
    parser.add_argument('raw_dir', help='生肉图片目录路径')
    parser.add_argument('text_dir', help='熟肉图片目录路径')
    parser.add_argument('--inpaint-algorithm', '-i', default='patchmatch', choices=['patchmatch', 'lama_large_512px'],
                        help='修复算法 (patchmatch 或 lama_large_512px)')
    
    args = parser.parse_args()
    
    # 检查输入目录
    if not Path(args.raw_dir).exists():
        print(f"错误: 生肉目录不存在 - {args.raw_dir}")
        return
        
    if not Path(args.text_dir).exists():
        print(f"错误: 熟肉目录不存在 - {args.text_dir}")
        return
    
    # 固定模型路径（默认位置）
    model_path = Path("data/models/comictextdetector.pt")
    if not model_path.exists():
        print(f"错误: 模型文件不存在 - {model_path}")
        print("请确保 comictextdetector.pt 模型文件存在于 data/models/ 目录下")
        return
    
    try:
        # 创建并运行流水线（输出目录默认为生肉目录）
        pipeline = MangaTranslationPipeline(
            raw_dir=args.raw_dir,
            text_dir=args.text_dir,
            model_path=model_path,
            inpaint_algorithm=args.inpaint_algorithm,
            output_dir=None  # 使用默认（生肉目录）
        )
        pipeline.run()
        
    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())