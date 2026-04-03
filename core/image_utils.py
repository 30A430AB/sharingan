import json
from pathlib import Path
import shutil
from PIL import Image
import os
from loguru import logger
from natsort import natsorted

from core.extraction import MaskProcessor
from core.inpainting import Inpainter


def get_image_files(directory: Path) -> list:
    """获取目录下所有图片文件（自然排序）"""
    image_extensions = {'.jpg', '.jpeg', '.png'}
    files = []
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.suffix.lower() in image_extensions:
                files.append(entry)
    except FileNotFoundError:
        pass
    return natsorted(files, key=lambda x: x.name)


def apply_text_to_inpainted(json_data: dict, status_callback=None):
    """将文本块贴到inpainted图片上"""
    # 设置目录路径
    base_dir = Path(json_data["directory"])
    inpainted_dir = base_dir / "inpainted"
    text_dir = base_dir / "temp" / "text"
    result_dir = base_dir / "result"

    # 创建结果目录
    os.makedirs(result_dir, exist_ok=True)

    # 逐页处理
    for page_name, page_entries in json_data["pages"].items():
        # 查找inpainted图片文件
        inpainted_path = find_image_file(inpainted_dir, page_name)
        if not inpainted_path.exists():
            logger.warning(f"警告: 未找到inpainted图片: {inpainted_path}")
            if status_callback:
                status_callback()
            continue

        # 查找文本图片文件
        text_path = find_image_file(text_dir, page_name)
        if not text_path.exists():
            # 没有文本图片时，复制原始图片到结果目录
            logger.warning(f"警告: 未找到文本图片: {text_path}，将复制原始图片")
            raw_path = find_image_file(base_dir, page_name)  # 原图路径
            if not raw_path.exists():
                logger.error(f"错误: 未找到原始图片: {raw_path}，跳过")
                if status_callback:
                    status_callback()
                continue
            try:
                # 保持原始扩展名复制
                result_path = result_dir / raw_path.name
                shutil.copy2(raw_path, result_path)
                # logger.info(f"已复制原始图片: {raw_path.name} -> {result_path.name}")
            except Exception as e:
                logger.error(f"复制原始图片 {page_name} 时出错: {str(e)}")
            if status_callback:
                status_callback()
            continue  # 跳过后续贴图逻辑

        try:
            # 加载inpainted图片
            base_img = Image.open(inpainted_path).convert("RGBA")

            # 加载文本图片
            text_img = Image.open(text_path).convert("RGBA")

            # 处理每个文本块
            for entry in page_entries:
                if entry["matched"]:
                    # 提取坐标
                    orig_xyxy = entry["orig_xyxy"]  # 在text图片中的原始坐标
                    xyxy = entry["xyxy"]            # 在inpainted图片中的目标坐标

                    # 从文本图片中裁剪文本块
                    text_block = text_img.crop((
                        orig_xyxy[0], orig_xyxy[1],
                        orig_xyxy[2], orig_xyxy[3]
                    ))

                    # 将文本块贴到基础图片上
                    base_img.paste(text_block, (xyxy[0], xyxy[1]), text_block)

            # 保存结果图片为PNG格式
            result_path = result_dir / f"{page_name}.png"
            base_img.save(result_path, "PNG")
            # logger.info(f"已合成: {page_name}.png")

        except Exception as e:
            logger.error(f"处理页面 {page_name} 时出错: {str(e)}")

        if status_callback:
            status_callback()


def find_image_file(directory: Path, base_name: str) -> Path:
    """在目录中查找与基本名称匹配的图片文件（支持多种格式）"""
    # 支持的图片格式扩展名
    image_extensions = {'.jpg', '.jpeg', '.png'}

    # 尝试查找匹配的文件
    for ext in image_extensions:
        file_path = directory / f"{base_name}{ext}"
        if file_path.exists():
            return file_path

    # 如果未找到任何匹配文件，尝试使用目录中的第一个匹配项
    for file in directory.iterdir():
        if file.stem == base_name and file.suffix.lower() in image_extensions:
            return file

    # 如果仍未找到，返回默认路径（尽管可能不存在）
    return directory / f"{base_name}.png"


def copy_input_images_to_temp(original_input_dir, temp_input_dir):
    """
    将原始输入图片复制到temp目录

    Args:
        original_input_dir: 原始输入目录
        temp_input_dir: temp目录下的输入目录
    """

    os.makedirs(temp_input_dir, exist_ok=True)

    # 使用安全的遍历函数获取图片文件
    image_files = get_image_files(Path(original_input_dir))

    copied_count = 0
    for img_path in image_files:
        dest_path = Path(temp_input_dir) / img_path.name
        shutil.copy2(img_path, dest_path)
        copied_count += 1
        # logger.info(f"复制: {img_path.name}")

    # logger.info(f"已复制 {copied_count} 张图片到 {temp_input_dir}")
    return copied_count


def resize_text_images_to_match_raw(raw_dir, text_dir, status_callback=None):
    """
    调整熟肉图片大小，使其高度与生肉图片相同

    Args:
        raw_dir: 生肉图片目录
        text_dir: 熟肉图片目录
        status_callback: 进度回调函数
    """

    raw_images = get_image_files(Path(raw_dir))

    if not raw_images:
        logger.error(f"错误: 生肉目录 {raw_dir} 中没有找到图片文件")
        return 0

    processed_count = 0
    total_count = len(raw_images)

    for idx, raw_img_path in enumerate(raw_images):
        if status_callback:
            status_callback(idx + 1, total_count)

        img_stem = raw_img_path.stem

        # 在熟肉目录中查找同名图片（扩展名不限）
        text_img_path = None
        for ext in {'.jpg', '.jpeg', '.png'}:
            candidate = Path(text_dir) / f"{img_stem}{ext}"
            if candidate.exists():
                text_img_path = candidate
                break

        if text_img_path is None:
            logger.warning(f"警告: 未找到熟肉图片 {img_stem}，跳过")
            continue

        try:
            with Image.open(raw_img_path) as raw_img:
                raw_height = raw_img.height

            with Image.open(text_img_path) as text_img:
                ratio = raw_height / text_img.height
                new_width = int(text_img.width * ratio)
                resized_img = text_img.resize((new_width, raw_height), resample=Image.LANCZOS)
                resized_img.save(text_img_path)
                processed_count += 1
                # logger.info(f"已调整: {text_img_path.name} -> {new_width}x{raw_height}")

        except Exception as e:
            logger.error(f"调整失败 {text_img_path.name}: {e}")

    # logger.info(f"图片尺寸调整完成！共处理 {processed_count} 张图片")
    return processed_count


def extract_text_from_masks(input_dir, mask_dir, output_dir, dilation_iterations=2, status_callback=None):
    """
    从掩码图像中提取文字

    Args:
        input_dir: 原始图片目录
        mask_dir: 掩码图片目录
        output_dir: 提取结果输出目录
        dilation_iterations: 膨胀迭代次数，用于扩大文字区域
        status_callback: 进度回调函数，每处理一张图片调用一次，参数为(processed, total)
    """
    # logger.info("开始文字提取...")

    os.makedirs(output_dir, exist_ok=True)

    input_images = get_image_files(Path(input_dir))
    total = len(input_images)
    processed_count = 0

    for idx, input_img_path in enumerate(input_images):
        if status_callback:
            status_callback(idx + 1, total)

        img_name = input_img_path.stem
        mask_filename = f"mask-{img_name}.png"
        mask_path = Path(mask_dir) / mask_filename

        if not mask_path.exists():
            logger.warning(f"警告: 未找到掩码文件 {mask_filename}，跳过 {input_img_path.name}")
            continue

        output_filename = f"{img_name}.png"
        output_path = Path(output_dir) / output_filename

        try:
            processor = MaskProcessor(
                input_path=str(input_img_path),
                mask_path=str(mask_path),
                output_path=str(output_path),
                dilation_iterations=dilation_iterations
            )
            processed_count += 1
            # logger.info(f"已提取: {input_img_path.name} -> {output_filename}")

        except Exception as e:
            logger.error(f"提取失败 {input_img_path.name}: {e}")

    # logger.info(f"文字提取完成！共处理 {processed_count} 张图片")
    return processed_count


def inpaint_raw_images(raw_img_dir, new_mask_dir, output_dir, algorithm="patchmatch", status_callback=None):
    """
    修复生肉图片

    Args:
        raw_img_dir: 生肉图片目录
        new_mask_dir: 新掩膜目录
        output_dir: 修复结果输出目录
        algorithm: 修复算法，默认使用patchmatch
        status_callback: 进度回调函数
    """
    # logger.info("开始修复生肉图片...")

    os.makedirs(output_dir, exist_ok=True)

    raw_images = get_image_files(Path(raw_img_dir))

    if not raw_images:
        logger.error(f"错误: 生肉目录 {raw_img_dir} 中没有找到图片文件")
        return 0

    processed_count = 0
    total_count = len(raw_images)

    for idx, raw_img_path in enumerate(raw_images):
        if status_callback:
            status_callback(idx + 1, total_count)

        img_name = raw_img_path.stem
        mask_filename = f"mask-{img_name}.png"
        mask_path = Path(new_mask_dir) / mask_filename

        if not mask_path.exists():
            logger.warning(f"警告: 未找到掩膜文件 {mask_filename}，跳过 {raw_img_path.name}")
            continue

        output_filename = f"{img_name}.png"
        output_path = Path(output_dir) / output_filename

        try:
            inpainter = Inpainter(
                img_path=str(raw_img_path),
                mask_path=str(mask_path),
                output_path=str(output_path),
                algorithm=algorithm,
                debug=False
            )
            # 假设 Inpainter 在初始化时执行修复并保存
            processed_count += 1
            # logger.info(f"已修复: {raw_img_path.name} -> {output_filename}")

        except Exception as e:
            logger.error(f"修复失败 {raw_img_path.name}: {e}")

    # logger.info(f"生肉图片修复完成！共处理 {processed_count} 张图片")
    return processed_count


def apply_text_to_inpainted_step(json_path, status_callback=None):
    """
    第七步：将文字贴到修复后的图片上

    Args:
        json_path: 匹配结果JSON文件路径
        status_callback: 进度回调函数，每处理一张图片调用一次，无参数
    """

    # logger.info("开始将文字贴到修复后的图片上...")

    if not os.path.exists(json_path):
        logger.error(f"错误: JSON文件不存在: {json_path}")
        return False

    try:
        # 读取JSON数据
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)

        # 调用贴图函数，传入回调
        apply_text_to_inpainted(json_data, status_callback=status_callback)

        return True

    except Exception as e:
        logger.error(f"文字贴图失败: {e}")
        return False
