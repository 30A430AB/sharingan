import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import cv2
import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
from natsort import natsorted
from loguru import logger
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from sklearn.metrics.pairwise import cosine_similarity

from core.compositing import get_image_files, find_image_file
from core.config import DirPaths

MASK_PREFIX = "mask-"


# ==================== 文本框匹配模块 ====================
class TextBoxMatcher:
    def __init__(self, jp_json: str, cn_json: str):
        self.jp_json = jp_json
        self.cn_json = cn_json
        self._box_cache = {}

    def load_boxes(self, json_path: str, image_name: str) -> List[Tuple[int, int, int, int]]:
        cache_key = f"{json_path}-{image_name}"
        if cache_key in self._box_cache:
            return self._box_cache[cache_key]

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        image_entry = data.get(image_name, {})
        annotations = image_entry.get("annotations", [])
        result = [tuple(ann['xyxy']) for ann in annotations if isinstance(ann, dict) and 'xyxy' in ann]

        self._box_cache[cache_key] = result
        return result

    @staticmethod
    def convert_coordinates(box: Tuple[int, int, int, int],
                            src_size: Tuple[int, int],
                            dst_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
        src_w, src_h = src_size
        dst_w, dst_h = dst_size
        return (
            int(box[0] * (dst_w / src_w)),
            int(box[1] * (dst_h / src_h)),
            int(box[2] * (dst_w / src_w)),
            int(box[3] * (dst_h / src_h))
        )

    def match_boxes(self, jp_key: str, cn_key: str,
                    jp_size: Tuple[int, int], cn_size: Tuple[int, int]) -> Dict[str, Any]:
        jp_boxes = self.load_boxes(self.jp_json, jp_key)
        cn_boxes_raw = self.load_boxes(self.cn_json, cn_key)
        cn_boxes = [self.convert_coordinates(b, cn_size, jp_size) for b in cn_boxes_raw]

        if not jp_boxes or not cn_boxes:
            return {'matches': [], 'adjusted_positions': []}

        # 计算动态 Y 阈值
        avg_height = np.mean([b[3] - b[1] for b in jp_boxes])
        y_threshold = avg_height * 0.5

        # 中心点
        jp_centers = [((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in jp_boxes]
        cn_centers = [((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in cn_boxes]

        # 匈牙利匹配
        matches = self._hungarian_match(jp_boxes, cn_boxes, y_threshold)

        adjusted_positions = []
        for jp_idx, cn_idx in matches:
            target_cx, target_cy = jp_centers[jp_idx]
            cn_box = cn_boxes_raw[cn_idx]  # 使用原始尺寸计算宽度高度
            w = cn_box[2] - cn_box[0]
            h = cn_box[3] - cn_box[1]
            x1 = int(target_cx - w / 2)
            y1 = int(target_cy - h / 2)
            x2 = int(x1 + w)
            y2 = int(y1 + h)
            adjusted_positions.append((x1, y1, x2, y2))

        return {
            'matches': matches,
            'adjusted_positions': adjusted_positions
        }

    def _hungarian_match(self, jp_boxes: List[Tuple[int, int, int, int]],
                         cn_boxes: List[Tuple[int, int, int, int]],
                         y_threshold: float) -> List[Tuple[int, int]]:
        if not jp_boxes or not cn_boxes:
            return []

        n_jp = len(jp_boxes)
        n_cn = len(cn_boxes)

        jp_centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in jp_boxes]
        cn_centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in cn_boxes]

        jp_y = np.array([c[1] for c in jp_centers]).reshape(-1, 1)
        tree = cKDTree(jp_y)

        INF = 1e9
        cost_matrix = np.full((n_jp, n_cn), INF, dtype=np.float32)

        for cn_idx, cn_center in enumerate(cn_centers):
            cy = cn_center[1]
            candidates = tree.query_ball_point([[cy]], y_threshold)[0]
            for jp_idx in candidates:
                h_dist = abs(jp_centers[jp_idx][0] - cn_center[0])
                cost_matrix[jp_idx, cn_idx] = h_dist

        jp_indices, cn_indices = linear_sum_assignment(cost_matrix)
        matches = [(int(jp), int(cn)) for jp, cn in zip(jp_indices, cn_indices)
                   if cost_matrix[jp, cn] < INF / 2]
        return matches


def create_new_masks(match_result: Dict[str, Any], raw_mask_dir: str, output_dir: str) -> None:
    """根据匹配结果创建新的日漫 mask，并自适应膨胀"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for page_name, page_entries in match_result["pages"].items():
        mask_path = Path(raw_mask_dir) / f"{MASK_PREFIX}{page_name}.png"
        if not mask_path.exists():
            logger.warning(f"未找到 mask 文件: {mask_path}")
            continue

        try:
            with Image.open(mask_path) as orig_mask_img:
                orig_width, orig_height = orig_mask_img.size
                original_mask = orig_mask_img.convert('L')  # 复用已打开图像

                # 创建全黑新 mask
                new_mask = Image.new('L', (orig_width, orig_height), 0)

                for entry in page_entries:
                    if entry.get("matched"):
                        raw_box = entry["raw_xyxy"]
                        x1, y1, x2, y2 = raw_box
                        x1 = max(0, x1)
                        y1 = max(0, y1)
                        x2 = min(orig_width, x2)
                        y2 = min(orig_height, y2)
                        if x1 >= x2 or y1 >= y2:
                            continue
                        mask_region = original_mask.crop((x1, y1, x2, y2))
                        new_mask.paste(mask_region, (x1, y1))

                # 膨胀
                short_side = min(orig_width, orig_height)
                kernel_size = max(1, short_side // 100)
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
                mask_np = np.array(new_mask)
                dilated_np = cv2.dilate(mask_np, kernel)
                dilated_mask = Image.fromarray(dilated_np)

                save_path = output_path / f"{MASK_PREFIX}{page_name}.png"
                dilated_mask.save(save_path)

        except Exception as e:
            logger.exception(f"创建页面 {page_name} 的新 mask 时出错: {e}")


def deduplicate_overlapping_boxes(boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对重叠的文本框去重，保留面积最大的"""
    n = len(boxes)
    matched_indices = [i for i, b in enumerate(boxes) if b.get('matched') == 1 and b.get('xyxy')]
    if len(matched_indices) <= 1:
        return boxes

    areas = {}
    for i in matched_indices:
        x1, y1, x2, y2 = boxes[i]['xyxy']
        areas[i] = (x2 - x1) * (y2 - y1)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in matched_indices:
        for j in matched_indices:
            if i >= j:
                continue
            xi1, yi1, xi2, yi2 = boxes[i]['xyxy']
            xj1, yj1, xj2, yj2 = boxes[j]['xyxy']
            if xi1 < xj2 and xi2 > xj1 and yi1 < yj2 and yi2 > yj1:
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in matched_indices:
        root = find(i)
        groups.setdefault(root, []).append(i)

    for group in groups.values():
        if len(group) <= 1:
            continue
        max_idx = max(group, key=lambda idx: areas[idx])
        for idx in group:
            if idx != max_idx:
                boxes[idx]['matched'] = 0
    return boxes


def match_and_create_masks(raw_annotations_path: str,
                           text_annotations_path: str,
                           output_path: str,
                           raw_mask_dir: str,
                           new_mask_dir: str,
                           text_image_dir: Optional[str] = None,
                           status_callback: Optional[callable] = None) -> bool:
    """
    匹配文本框并创建新 mask 的完整流程

    Args:
        raw_annotations_path: 生肉标注文件路径
        text_annotations_path: 熟肉标注文件路径
        output_path: 匹配结果 JSON 输出路径
        raw_mask_dir: 原始 mask 目录
        new_mask_dir: 新 mask 输出目录
        text_image_dir: 熟肉图片所在目录（若为 None，则尝试自动推导）
        status_callback: 进度回调函数
    """
    try:
        with open(raw_annotations_path, 'r', encoding='utf-8') as f:
            raw_json = json.load(f)
        with open(text_annotations_path, 'r', encoding='utf-8') as f:
            text_json = json.load(f)
    except Exception as e:
        logger.exception(f"读取标注文件失败: {e}")
        return False

    output_root = Path(raw_annotations_path).parent.parent.parent
    raw_dir = output_root

    # 确定熟肉图片目录
    if text_image_dir is None:
        # 默认：output_root / "temp" / "text" （cli.py 会将熟肉图片复制至此）
        text_dir = output_root / DirPaths.TEMP / "text"
    else:
        text_dir = Path(text_image_dir)

    matcher = TextBoxMatcher(raw_annotations_path, text_annotations_path)

    result: Dict[str, Any] = {
        "directory": str(output_root.resolve()),
        "pages": {},
        "current_img": None
    }

    raw_pages = natsorted(raw_json.keys())
    if not raw_pages:
        logger.error("生肉标注文件中没有找到任何页面")
        return False

    result["current_img"] = raw_pages[0]
    total_pages = len(raw_pages)

    for page_idx, page_name in enumerate(raw_pages):
        if status_callback:
            status_callback(page_idx + 1, total_pages)

        if page_name not in text_json:
            result["pages"][page_name] = []
            continue

        try:
            raw_image_path = find_image_file(raw_dir, page_name)
            text_image_path = find_image_file(text_dir, page_name)

            if raw_image_path is None or text_image_path is None:
                logger.warning(f"未找到图片文件: {page_name}")
                result["pages"][page_name] = []
                continue

            with Image.open(raw_image_path) as raw_img:
                raw_size = raw_img.size
            with Image.open(text_image_path) as text_img:
                text_size = text_img.size

        except Exception as e:
            logger.exception(f"获取图片尺寸失败 {page_name}: {e}")
            result["pages"][page_name] = []
            continue

        match_result = matcher.match_boxes(page_name, page_name, raw_size, text_size)

        # 提取原始框列表
        raw_boxes = []
        if "annotations" in raw_json[page_name]:
            for ann in raw_json[page_name]["annotations"]:
                if "xyxy" in ann:
                    raw_boxes.append(ann["xyxy"])

        text_boxes = []
        if "annotations" in text_json[page_name]:
            for ann in text_json[page_name]["annotations"]:
                if "xyxy" in ann:
                    text_boxes.append(ann["xyxy"])

        page_results = []
        for i in range(len(text_boxes)):
            page_results.append({
                "xyxy": [],
                "orig_xyxy": text_boxes[i],
                "matched": 0
            })

        matches = match_result.get('matches', [])
        adjusted_positions = match_result.get('adjusted_positions', [])

        for match_idx, (jp_idx, cn_idx) in enumerate(matches):
            if cn_idx < len(page_results):
                page_results[cn_idx] = {
                    "xyxy": adjusted_positions[match_idx],
                    "orig_xyxy": text_boxes[cn_idx],
                    "raw_xyxy": raw_boxes[jp_idx],
                    "matched": 1
                }

        page_results = deduplicate_overlapping_boxes(page_results)
        result["pages"][page_name] = page_results

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
    except Exception as e:
        logger.exception(f"保存匹配结果失败: {e}")
        return False

    create_new_masks(result, raw_mask_dir, new_mask_dir)
    return True


# ==================== 图片匹配模块 ====================
def load_model(weights_path: Union[str, Path], device: torch.device) -> nn.Module:
    """加载预训练的 ResNet18 特征提取模型"""
    model = models.resnet18(weights=None)
    num_ftrs = model.fc.in_features
    model.fc = nn.Identity()
    state_dict = torch.load(weights_path, map_location=device)
    model.load_state_dict(state_dict, strict=False)
    model = model.to(device)
    model.eval()
    return model


def get_transform() -> transforms.Compose:
    """图像预处理"""
    return transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])


def extract_features(image_paths: List[str],
                     model: nn.Module,
                     transform: transforms.Compose,
                     device: torch.device,
                     batch_size: int = 32) -> Tuple[np.ndarray, List[str]]:
    """提取图像特征向量"""
    features = []
    valid_paths = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[i:i + batch_size]
            batch_tensors = []
            for path in batch_paths:
                try:
                    img = Image.open(path).convert('RGB')
                    img_tensor = transform(img).unsqueeze(0)
                    batch_tensors.append(img_tensor)
                    valid_paths.append(path)
                except Exception as e:
                    logger.warning(f"加载图片失败 {path}: {e}")
                    continue
            if not batch_tensors:
                continue
            batch_input = torch.cat(batch_tensors, dim=0).to(device)
            batch_features = model(batch_input).cpu().numpy()
            features.append(batch_features)
    if not features:
        return np.array([]), []
    return np.vstack(features), valid_paths


def match_images(raw_dir: str,
                 text_dir: str,
                 model_weights_path: str,
                 batch_size: int = 32,
                 device: Optional[str] = None,
                 generate_thumbnails: bool = False,
                 thumb_output_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    匹配 raw 图片和 text 图片，返回匹配结果列表。

    Args:
        raw_dir: 生肉图片目录
        text_dir: 熟肉图片目录
        model_weights_path: ResNet18 预训练权重文件路径
        batch_size: 批大小
        device: 计算设备 ('cuda' 或 'cpu')，默认自动选择
        generate_thumbnails: 是否生成缩略图
        thumb_output_dir: 缩略图输出目录，若为 None 则使用 text_dir 下的 thumbs

    Returns:
        匹配结果列表，每个元素为字典
    """
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    device_obj = torch.device(device)

    raw_images = [str(p) for p in get_image_files(Path(raw_dir))]
    text_images = [str(p) for p in get_image_files(Path(text_dir))]

    if not raw_images or not text_images:
        raise ValueError("未找到任何图片文件。")

    # 缩略图生成
    raw_thumb_map = {}
    text_thumb_map = {}
    if generate_thumbnails:
        thumb_dir = Path(raw_dir) / DirPaths.THUMBS
        thumb_dir.mkdir(parents=True, exist_ok=True)

        def generate_thumb(path: Path, prefix: str) -> Optional[Tuple[str, str]]:
            """生成单张缩略图，返回 (原路径, 缩略图路径) 或 None"""
            name = path.stem
            thumb_path = thumb_dir / f"{prefix}_{name}.jpg"
            try:
                img = Image.open(path)
                img.thumbnail((150, 150))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(thumb_path, 'JPEG', quality=85)
                return str(path), str(thumb_path)
            except Exception as e:
                logger.warning(f"生成缩略图失败 {path}: {e}")
                return None

        with ThreadPoolExecutor() as executor:
            # 提交所有 raw 和 text 缩略图生成任务
            raw_futures = {executor.submit(generate_thumb, Path(p), "thumb_raw"): p for p in raw_images}
            text_futures = {executor.submit(generate_thumb, Path(p), "thumb_text"): p for p in text_images}

            # 收集 raw 缩略图结果
            for future in as_completed(raw_futures):
                result = future.result()
                if result:
                    raw_thumb_map[result[0]] = result[1]

            # 收集 text 缩略图结果
            for future in as_completed(text_futures):
                result = future.result()
                if result:
                    text_thumb_map[result[0]] = result[1]

    model = load_model(model_weights_path, device_obj)
    transform = get_transform()

    raw_features, raw_valid = extract_features(raw_images, model, transform, device_obj, batch_size)
    if raw_features.size == 0:
        raise RuntimeError("无法从生肉图片中提取有效特征。")

    text_features, text_valid = extract_features(text_images, model, transform, device_obj, batch_size)
    if text_features.size == 0:
        raise RuntimeError("无法从熟肉图片中提取有效特征。")

    sim_matrix = cosine_similarity(raw_features, text_features)

    matches = []
    for i, raw_path in enumerate(raw_valid):
        best_idx = np.argmax(sim_matrix[i])
        best_sim = sim_matrix[i, best_idx]
        text_path = text_valid[best_idx]

        match_item = {
            'raw_path': raw_path,
            'text_path': text_path,
            'similarity': float(best_sim)
        }
        if generate_thumbnails:
            match_item['raw_thumbnail_path'] = raw_thumb_map.get(raw_path)
            match_item['text_thumbnail_path'] = text_thumb_map.get(text_path)

        matches.append(match_item)

    return matches