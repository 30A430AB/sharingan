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

# ==================== 掩膜处理相关函数 ====================
def detect_text_orientation(mask):
    """
    判断文本方向
    输入：二值图（文字白色）
    返回：'horizontal' 或 'vertical'
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 'horizontal'
    
    all_points = np.vstack([cnt for cnt in contours])
    x, y, w, h = cv2.boundingRect(all_points)
    
    if w > h * 1.2:
        return 'horizontal'
    elif h > w * 1.2:
        return 'vertical'
    else:
        return 'horizontal'

def get_median_line_size(mask, orientation):
    """
    获取横排时的文字行高中位数，或竖排时的文字列宽中位数
    """
    if orientation == 'horizontal':
        row_sums = np.sum(mask // 255, axis=1)
        thresh = 0.01 * mask.shape[1]
        text_rows = np.where(row_sums > thresh)[0]
        if len(text_rows) == 0:
            return max(1, mask.shape[0] // 10)
        
        heights = []
        start = text_rows[0]
        for i in range(1, len(text_rows)):
            if text_rows[i] != text_rows[i-1] + 1:
                heights.append(text_rows[i-1] - start + 1)
                start = text_rows[i]
        heights.append(text_rows[-1] - start + 1)
        
        if not heights:
            return max(1, mask.shape[0] // 10)
        return int(np.median(heights))
    
    else:  # vertical
        col_sums = np.sum(mask // 255, axis=0)
        thresh = 0.01 * mask.shape[0]
        text_cols = np.where(col_sums > thresh)[0]
        if len(text_cols) == 0:
            return max(1, mask.shape[1] // 10)
        
        widths = []
        start = text_cols[0]
        for i in range(1, len(text_cols)):
            if text_cols[i] != text_cols[i-1] + 1:
                widths.append(text_cols[i-1] - start + 1)
                start = text_cols[i]
        widths.append(text_cols[-1] - start + 1)
        
        if not widths:
            return max(1, mask.shape[1] // 10)
        return int(np.median(widths))

def adaptive_expand_mask(mask, expand_ratio, close_kernel_size=None):
    """
    沿着文字最外围边缘扩展掩膜，保留原始形状的凹凸特征
    
    输入：
        mask: 二值图，文字白色（255），背景黑色（0）
        expand_ratio: 膨胀距离相对于文字典型尺寸的比例（例如0.5表示半个字）
        close_kernel_size: 闭运算核大小，若为None则自动设为 expand_distance 的一半（至少1）
    输出：
        fill_mask: 白色填充的膨胀区域掩膜（二值图，背景黑，待修复区域白）
        result: 绘制了红色轮廓的彩色图（用于可视化）
        expanded_contour: 膨胀后区域的外轮廓点集
    """
    if len(mask.shape) == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)

    # 1. 方向检测与尺寸计算
    orientation = detect_text_orientation(mask)
    median_size = get_median_line_size(mask, orientation)
    expand_distance = max(1, int(median_size * expand_ratio))
    
    # 2. 闭运算：填补文字间的缝隙和断裂，使整个文字区域连通
    if close_kernel_size is None:
        close_kernel_size = max(1, expand_distance // 2)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_kernel_size, close_kernel_size))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)

    # 3. 膨胀：向外扩展指定距离（保持原始形状的凹凸性）
    kernel_size = 2 * expand_distance + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    dilated = cv2.dilate(closed, kernel, iterations=1)

    # 4. 提取膨胀后区域的外轮廓（只取最外层）
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        fill_mask = np.zeros_like(mask)
        result = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        return fill_mask, result, None

    # 取面积最大的轮廓（整个文字区域）
    largest_contour = max(contours, key=cv2.contourArea)
    expanded_contour = largest_contour

    # 5. 生成白色填充掩膜
    fill_mask = np.zeros_like(mask)
    cv2.drawContours(fill_mask, [expanded_contour], -1, 255, thickness=cv2.FILLED)

    # 6. 可视化：在原图上绘制红色轮廓
    result = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(result, [expanded_contour], -1, (0, 0, 255), 2)

    return fill_mask, result, expanded_contour


# ==================== 原有匹配相关类与函数 ====================
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

def match_text_box(raw_boxes, text_box, iou_threshold=0.1):
    """
    匹配单个文本文本框到原始文本框，使用位置匹配 + IOU 验证
    """
    if not raw_boxes:
        return None

    heights = [box[3] - box[1] for box in raw_boxes]
    avg_height = np.mean(heights) if heights else 0
    y_threshold = avg_height * 0.5

    raw_centers_y = np.array([(box[1] + box[3]) / 2 for box in raw_boxes]).reshape(-1, 1)
    tree = cKDTree(raw_centers_y)

    text_center_y = (text_box[1] + text_box[3]) / 2
    text_center_x = (text_box[0] + text_box[2]) / 2

    candidate_indices = tree.query_ball_point(np.array([[text_center_y]]), y_threshold)
    if not candidate_indices or not candidate_indices[0]:
        return None

    candidate_indices = candidate_indices[0]
    min_x_diff = float('inf')
    matched_box = None

    for idx in candidate_indices:
        raw_box = raw_boxes[idx]
        raw_center_x = (raw_box[0] + raw_box[2]) / 2
        x_diff = abs(raw_center_x - text_center_x)
        iou = calculate_iou(raw_box, text_box)
        if x_diff < min_x_diff and iou >= iou_threshold:
            min_x_diff = x_diff
            matched_box = raw_box

    return matched_box

def calculate_iou(box1, box2):
    """
    计算两个框的交并比(IOU)
    """
    x_left = max(box1[0], box2[0])
    y_top = max(box1[1], box2[1])
    x_right = min(box1[2], box2[2])
    y_bottom = min(box1[3], box2[3])

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    intersection_area = (x_right - x_left) * (y_bottom - y_top)
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union_area = box1_area + box2_area - intersection_area

    if union_area == 0:
        return 0.0
    return intersection_area / union_area

def create_new_masks(match_result, raw_mask_dir, output_dir, expand_ratio=0.5):
    """
    根据匹配结果创建新的日漫mask，并对每个匹配的文本块进行自适应膨胀处理

    参数:
        match_result: 匹配结果字典，包含pages信息
        raw_mask_dir: 原始mask目录路径
        output_dir: 新mask输出目录路径
        expand_ratio: 外扩距离相对于文字典型尺寸的比例，默认0.5（半个字高/宽）
    """
    logger.info("开始创建新的日漫mask...")
    os.makedirs(output_dir, exist_ok=True)

    raw_dir = Path(match_result["directory"])

    for page_name, page_entries in match_result["pages"].items():
        mask_path = Path(raw_mask_dir) / f"mask-{page_name}.png"
        if not mask_path.exists():
            logger.warning(f"警告: 未找到mask文件: {mask_path}")
            continue

        try:
            # 打开原始mask获取尺寸
            with Image.open(mask_path) as orig_mask_img:
                orig_width, orig_height = orig_mask_img.size

            # 创建全黑图像（与原始mask同样大小）
            new_mask = Image.new('L', (orig_width, orig_height), 0)

            # 打开原始mask（用于截取区域）
            original_mask = Image.open(mask_path).convert('L')

            # 处理每个匹配的文本框
            for entry in page_entries:
                if entry["matched"]:
                    raw_box = entry["raw_xyxy"]   # 生肉文本框坐标（在生肉图片上的位置）
                    x1, y1, x2, y2 = raw_box
                    # 确保坐标在图片范围内
                    x1 = max(0, x1)
                    y1 = max(0, y1)
                    x2 = min(orig_width, x2)
                    y2 = min(orig_height, y2)

                    if x1 >= x2 or y1 >= y2:
                        continue

                    # 从原始mask中截取对应区域（PIL图像）
                    mask_region = original_mask.crop((x1, y1, x2, y2))

                    # 将截取区域转换为numpy数组（灰度），进行自适应膨胀处理
                    region_np = np.array(mask_region, dtype=np.uint8)

                    # 调用自适应膨胀函数，保留原始形状的凹凸性
                    try:
                        fill_mask_np, _, _ = adaptive_expand_mask(region_np, expand_ratio)
                    except Exception as e:
                        logger.error(f"处理文本块时出错: {e}, 使用原始区域")
                        fill_mask_np = region_np

                    # 将处理后的numpy数组转回PIL图像
                    processed_region = Image.fromarray(fill_mask_np, mode='L')

                    # 将处理后的区域粘贴到新mask的相同位置
                    new_mask.paste(processed_region, (x1, y1))

            # 保存新mask
            output_path = Path(output_dir) / f"mask-{page_name}.png"
            new_mask.save(output_path)
            logger.info(f"已创建新mask: {output_path.name}")

        except Exception as e:
            logger.error(f"创建页面 {page_name} 的新mask时出错: {str(e)}")

    logger.info("新mask创建完成！")


def match_and_create_masks(raw_annotations_path, text_annotations_path, output_path,
                          raw_mask_dir, new_mask_dir, status_callback=None, expand_ratio=0.5):
    """
    匹配文本框并创建新mask的完整流程

    参数:
        raw_annotations_path: 生肉标注文件路径
        text_annotations_path: 熟肉标注文件路径
        output_path: 匹配结果JSON输出路径
        raw_mask_dir: 原始mask目录
        new_mask_dir: 新mask输出目录
        status_callback: 进度回调函数
        expand_ratio: 外扩距离相对于文字典型尺寸的比例，默认0.5
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

    # 创建新mask，并传入expand_ratio参数
    create_new_masks(result, raw_mask_dir, new_mask_dir, expand_ratio=expand_ratio)

    return True
