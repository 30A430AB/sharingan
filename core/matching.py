import json
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from PIL import Image
import os
from pathlib import Path
from natsort import natsorted
from loguru import logger
import cv2
import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as transforms
from sklearn.metrics.pairwise import cosine_similarity


# ==================== 文本框匹配模块 ====================
class TextBoxMatcher:
    def __init__(self, jp_json: str, cn_json: str):
        self.jp_json = jp_json
        self.cn_json = cn_json
        self._box_cache = {}

    def load_boxes(self, json_path: str, image_name: str) -> list:
        cache_key = f"{json_path}-{image_name}"
        if cache_key in self._box_cache:
            return self._box_cache[cache_key]

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        image_entry = data.get(image_name, {})
        annotations = image_entry.get("annotations", [])
        result = [ann['xyxy'] for ann in annotations if isinstance(ann, dict) and 'xyxy' in ann]

        self._box_cache[cache_key] = result
        return result

    def convert_coordinates(self, box: tuple, src_size: tuple, dst_size: tuple) -> tuple:
        src_w, src_h = src_size
        dst_w, dst_h = dst_size
        return (
            int(box[0] * (dst_w / src_w)),
            int(box[1] * (dst_h / src_h)),
            int(box[2] * (dst_w / src_w)),
            int(box[3] * (dst_h / src_h))
        )

    def match_boxes(self, jp_key: str, cn_key: str, jp_size: tuple, cn_size: tuple) -> dict:
        jp_boxes = self.load_boxes(self.jp_json, jp_key)
        cn_boxes = [self.convert_coordinates(b, cn_size, jp_size) for b in self.load_boxes(self.cn_json, cn_key)]

        # 坐标转换
        cn_boxes_converted = [
            (
                int(box[0]),
                int(box[1]),
                int(box[2]),
                int(box[3])
            )
            for box in cn_boxes
        ]

        # 计算动态阈值
        avg_height = np.mean([box[3]-box[1] for box in jp_boxes]) if jp_boxes else 0
        self.y_threshold = avg_height * 0.5

        # 中心点计算
        jp_centers = [((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in jp_boxes]
        cn_centers = [((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in cn_boxes_converted]

        # 匈牙利匹配（替换原KD树贪心匹配）
        matches = self._hungarian_match(jp_boxes, cn_boxes_converted)

        # 坐标转换逻辑（基于中心点对齐）
        adjusted_positions = []
        for (jp_idx, cn_idx) in matches:
            # 使用日漫中心点坐标
            target_cx, target_cy = jp_centers[jp_idx]

            # 获取汉化文本框原始尺寸
            cn_box = cn_boxes[cn_idx]
            w = int(cn_box[2] - cn_box[0])
            h = int(cn_box[3] - cn_box[1])

            # 以日漫中心点为基准计算新坐标
            x1 = int(target_cx - w/2)
            y1 = int(target_cy - h/2)
            x2 = int(x1 + w)
            y2 = int(y1 + h)

            adjusted_positions.append((x1, y1, x2, y2))

        return {
            'matches': matches,
            'adjusted_positions': adjusted_positions
        }

    def _hungarian_match(self, jp_boxes: list, cn_boxes: list) -> list:
        """匈牙利算法匹配（全局最优匹配）"""
        if not jp_boxes or not cn_boxes:
            return []

        # 使用已有的 y_threshold
        y_thresh = self.y_threshold

        n_jp = len(jp_boxes)
        n_cn = len(cn_boxes)

        # 预先计算中心点
        jp_centers = [((b[0]+b[2])/2, (b[1]+b[3])/2) for b in jp_boxes]
        cn_centers = [((b[0]+b[2])/2, (b[1]+b[3])/2) for b in cn_boxes]

        # 构建KD树用于快速Y轴候选查找
        jp_y = np.array([c[1] for c in jp_centers]).reshape(-1, 1)
        tree = cKDTree(jp_y)

        INF = 1e9
        cost_matrix = np.full((n_jp, n_cn), INF, dtype=np.float32)

        # 为每个中文框找到Y轴阈值内的日文框候选
        for cn_idx, cn_center in enumerate(cn_centers):
            cy = cn_center[1]
            candidates = tree.query_ball_point([[cy]], y_thresh)[0]
            for jp_idx in candidates:
                # 计算水平距离作为代价
                jp_cx = jp_centers[jp_idx][0]
                cn_cx = cn_center[0]
                h_dist = abs(jp_cx - cn_cx)
                cost_matrix[jp_idx, cn_idx] = h_dist

        # 使用匈牙利算法求解最小权匹配
        jp_indices, cn_indices = linear_sum_assignment(cost_matrix)

        # 收集有效匹配（代价小于阈值）
        matches = []
        for jp_idx, cn_idx in zip(jp_indices, cn_indices):
            if cost_matrix[jp_idx, cn_idx] < INF / 2:  # 有效匹配
                matches.append((jp_idx, cn_idx))

        return matches


def create_new_masks(match_result, raw_mask_dir, output_dir):
    """
    根据匹配结果创建新的日漫mask，并根据图片尺寸自适应膨胀
    """
    logger.info("开始创建新的日漫mask...")
    os.makedirs(output_dir, exist_ok=True)

    for page_name, page_entries in match_result["pages"].items():
        mask_path = Path(raw_mask_dir) / f"mask-{page_name}.png"
        if not mask_path.exists():
            logger.warning(f"警告: 未找到mask文件: {mask_path}")
            continue

        try:
            with Image.open(mask_path) as orig_mask_img:
                orig_width, orig_height = orig_mask_img.size

            # 创建全黑图像
            new_mask = Image.new('L', (orig_width, orig_height), 0)
            original_mask = Image.open(mask_path).convert('L')

            # 粘贴原始mask区域
            for entry in page_entries:
                if entry["matched"]:
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

            # 根据图片短边计算膨胀核大小（可调节分母）
            short_side = min(orig_width, orig_height)
            kernel_size = max(1, short_side // 100)  # 例如900x1600 -> 9
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))

            # 膨胀
            mask_np = np.array(new_mask)
            dilated_np = cv2.dilate(mask_np, kernel)
            dilated_mask = Image.fromarray(dilated_np)

            # 保存
            output_path = Path(output_dir) / f"mask-{page_name}.png"
            dilated_mask.save(output_path)
            logger.info(f"已创建膨胀后的mask: {output_path.name} (膨胀核大小={kernel_size})")

        except Exception as e:
            logger.error(f"创建页面 {page_name} 的新mask时出错: {str(e)}")

    logger.info("新mask创建完成！")



def match_and_create_masks(raw_annotations_path, text_annotations_path, output_path,
                          raw_mask_dir, new_mask_dir, status_callback=None):
    """
    匹配文本框并创建新mask的完整流程

    参数:
        raw_annotations_path: 生肉标注文件路径
        text_annotations_path: 熟肉标注文件路径
        output_path: 匹配结果JSON输出路径
        raw_mask_dir: 原始mask目录
        new_mask_dir: 新mask输出目录
        status_callback: 进度回调函数
    """
    logger.info("开始文本框匹配并创建新mask...")

    try:
        with open(raw_annotations_path, 'r', encoding='utf-8') as f:
            raw_json = json.load(f)

        with open(text_annotations_path, 'r', encoding='utf-8') as f:
            text_json = json.load(f)
    except Exception as e:
        logger.error(f"读取标注文件失败: {e}")
        return False

    output_root = Path(raw_annotations_path).parent.parent.parent
    raw_dir = output_root
    text_dir = Path(text_annotations_path).parent.parent

    matcher = TextBoxMatcher(raw_annotations_path, text_annotations_path)

    result = {
        "directory": str(output_root.resolve()),
        "pages": {},
        "current_img": None
    }

    raw_pages = list(raw_json.keys())
    text_pages = list(text_json.keys())

    if not raw_pages:
        logger.error("错误: 生肉标注文件中没有找到任何页面")
        return False

    raw_pages = natsorted(raw_pages)
    result["current_img"] = raw_pages[0]

    total_pages = len(raw_pages)

    for page_idx, page_name in enumerate(raw_pages):
        if status_callback:
            status_callback(page_idx + 1, total_pages)

        if page_name not in text_json:
            logger.warning(f"警告: 熟肉中未找到页面 {page_name}，跳过")
            result["pages"][page_name] = []
            continue

        try:
            raw_image_path = None
            for ext in ['.jpg', '.jpeg', '.png']:
                potential_path = raw_dir / f"{page_name}{ext}"
                if potential_path.exists():
                    raw_image_path = potential_path
                    break

            text_image_path = None
            for ext in ['.jpg', '.jpeg', '.png']:
                potential_path = text_dir / f"{page_name}{ext}"
                if potential_path.exists():
                    text_image_path = potential_path
                    break

            if not raw_image_path or not text_image_path:
                logger.warning(f"警告: 未找到图片文件 {page_name}")
                result["pages"][page_name] = []
                continue

            with Image.open(raw_image_path) as raw_img:
                raw_size = raw_img.size
            with Image.open(text_image_path) as text_img:
                text_size = text_img.size

        except Exception as e:
            logger.error(f"获取图片尺寸失败 {page_name}: {e}")
            result["pages"][page_name] = []
            continue

        logger.info(f"页面 {page_name}: 生肉尺寸 {raw_size}, 熟肉尺寸 {text_size}")

        match_result = matcher.match_boxes(page_name, page_name, raw_size, text_size)

        raw_boxes = []
        if "annotations" in raw_json[page_name]:
            for annotation in raw_json[page_name]["annotations"]:
                if "xyxy" in annotation:
                    raw_boxes.append(annotation["xyxy"])

        text_boxes = []
        if "annotations" in text_json[page_name]:
            for annotation in text_json[page_name]["annotations"]:
                if "xyxy" in annotation:
                    text_boxes.append(annotation["xyxy"])

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

        result["pages"][page_name] = page_results
        matched_count = len([r for r in page_results if r['matched']])
        logger.info(f"页面 {page_name}: 匹配 {matched_count}/{len(page_results)} 个文本框")

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
        logger.info(f"匹配结果已保存到: {output_path}")
    except Exception as e:
        logger.error(f"保存匹配结果失败: {e}")
        return False

    # 创建新mask
    create_new_masks(result, raw_mask_dir, new_mask_dir)

    return True

# ==================== 图片匹配模块 ====================
def load_model(weights_path, device):
    """
    加载预训练的 ResNet18 模型，并移除最后的全连接层以得到特征向量。
    """
    model = models.resnet18(weights=None)
    num_ftrs = model.fc.in_features
    model.fc = nn.Identity()
    model.load_state_dict(torch.load(weights_path, map_location=device), strict=False)
    model = model.to(device)
    model.eval()
    return model

def get_transform():
    """
    定义图像预处理流程：调整大小、转换为张量、归一化（使用 ImageNet 统计值）
    """
    return transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

def extract_features(image_paths, model, transform, device, batch_size=32):
    """
    从一组图像路径中提取特征向量。
    返回：特征矩阵 (n_samples, feature_dim) 和对应的文件路径列表。
    """
    features = []
    valid_paths = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[i:i+batch_size]
            batch_tensors = []
            for path in batch_paths:
                try:
                    img = Image.open(path).convert('RGB')
                    img_tensor = transform(img).unsqueeze(0)
                    batch_tensors.append(img_tensor)
                    valid_paths.append(path)
                except Exception as e:
                    continue
            if not batch_tensors:
                continue
            batch_input = torch.cat(batch_tensors, dim=0).to(device)
            batch_features = model(batch_input).cpu().numpy()
            features.append(batch_features)
    if not features:
        return np.array([]), []
    features = np.vstack(features)
    return features, valid_paths

def match_images(raw_dir, text_dir, model_weights_path, batch_size=32, device=None):
    """
    匹配 raw 图片和 text 图片，返回匹配结果列表。

    参数：
        raw_dir (str): 原始图片（生肉）文件夹路径
        text_dir (str): 翻译后图片（熟肉）文件夹路径
        model_weights_path (str): ResNet18 预训练权重文件路径
        batch_size (int): 特征提取时的批大小，默认为 32
        device (str, optional): 计算设备 ('cuda' 或 'cpu')，默认自动选择

    返回：
        list: 匹配结果列表，每个元素为字典，包含以下字段：
            - 'raw_path': raw 图片的原始路径
            - 'text_path': 匹配到的 text 图片的路径
            - 'similarity': 余弦相似度
    """
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

    raw_images = [os.path.join(raw_dir, f) for f in os.listdir(raw_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff'))]
    text_images = [os.path.join(text_dir, f) for f in os.listdir(text_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff'))]

    if not raw_images or not text_images:
        raise ValueError("No images found in one of the directories.")

    model = load_model(model_weights_path, device)
    transform = get_transform()

    raw_features, raw_valid = extract_features(raw_images, model, transform, device, batch_size)
    if raw_features.size == 0:
        raise RuntimeError("No valid raw images after loading.")

    text_features, text_valid = extract_features(text_images, model, transform, device, batch_size)
    if text_features.size == 0:
        raise RuntimeError("No valid text images after loading.")

    sim_matrix = cosine_similarity(raw_features, text_features)

    matches = []
    for i, raw_path in enumerate(raw_valid):
        best_idx = np.argmax(sim_matrix[i])
        best_sim = sim_matrix[i, best_idx]
        text_path = text_valid[best_idx]
        matches.append({
            'raw_path': raw_path,
            'text_path': text_path,
            'similarity': best_sim
        })

    return matches