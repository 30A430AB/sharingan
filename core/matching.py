import json
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from PIL import Image
import os
from pathlib import Path
from natsort import natsorted
from loguru import logger


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

    参数:
        raw_boxes: 原始文本框列表，格式 [[x1, y1, x2, y2], ...]
        text_box: 待匹配的文本文本框，格式 [x1, y1, x2, y2]
        iou_threshold: IOU 验证阈值，默认0.1

    返回:
        list: 匹配到的原始框坐标 [x1, y1, x2, y2]，如果没有匹配则返回 None
    """
    if not raw_boxes:
        return None

    # 计算原始文本框的平均高度作为Y轴阈值基准
    heights = [box[3] - box[1] for box in raw_boxes]
    avg_height = np.mean(heights) if heights else 0
    y_threshold = avg_height * 0.5

    # 计算所有原始文本框的中心点Y坐标
    raw_centers_y = np.array([(box[1] + box[3]) / 2 for box in raw_boxes]).reshape(-1, 1)

    # 构建KD树（仅Y轴坐标）
    tree = cKDTree(raw_centers_y)

    # 计算待匹配文本框的中心点
    text_center_y = (text_box[1] + text_box[3]) / 2
    text_center_x = (text_box[0] + text_box[2]) / 2

    # 在Y轴阈值范围内查找候选原始框
    candidate_indices = tree.query_ball_point(np.array([[text_center_y]]), y_threshold)

    # 如果没有候选框，返回None
    if not candidate_indices or not candidate_indices[0]:
        return None

    # 获取第一个查询点的候选索引
    candidate_indices = candidate_indices[0]

    # 在候选框中查找X轴最接近且IOU满足阈值的原始框
    min_x_diff = float('inf')
    matched_box = None

    for idx in candidate_indices:
        raw_box = raw_boxes[idx]
        raw_center_x = (raw_box[0] + raw_box[2]) / 2
        x_diff = abs(raw_center_x - text_center_x)

        # 计算 IOU
        iou = calculate_iou(raw_box, text_box)

        # 如果找到更接近的X轴匹配且IOU满足阈值则更新结果
        if x_diff < min_x_diff and iou >= iou_threshold:
            min_x_diff = x_diff
            matched_box = raw_box

    return matched_box


def calculate_iou(box1, box2):
    """
    计算两个框的交并比(IOU)

    参数:
        box1: (x1, y1, x2, y2)
        box2: (x1, y1, x2, y2)

    返回:
        float: 交并比 (0.0 - 1.0)
    """
    # 计算交集区域
    x_left = max(box1[0], box2[0])
    y_top = max(box1[1], box2[1])
    x_right = min(box1[2], box2[2])
    y_bottom = min(box1[3], box2[3])

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    # 计算交集面积
    intersection_area = (x_right - x_left) * (y_bottom - y_top)

    # 计算各自面积
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])

    # 计算并集面积
    union_area = box1_area + box2_area - intersection_area

    # 避免除以零
    if union_area == 0:
        return 0.0

    return intersection_area / union_area


def create_new_masks(match_result, raw_mask_dir, output_dir):
    """
    根据匹配结果创建新的日漫mask

    参数:
        match_result: 匹配结果字典，包含pages信息
        raw_mask_dir: 原始mask目录路径
        output_dir: 新mask输出目录路径
    """
    logger.info("开始创建新的日漫mask...")

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    # 获取生肉图片目录
    raw_dir = Path(match_result["directory"])

    # 遍历所有页面
    for page_name, page_entries in match_result["pages"].items():
        # 查找对应的生肉图片文件
        image_extensions = {'.jpg', '.jpeg', '.png'}
        raw_image_path = None
        for ext in image_extensions:
            potential_path = raw_dir / f"{page_name}{ext}"
            if potential_path.exists():
                raw_image_path = potential_path
                break

        if not raw_image_path:
            logger.warning(f"警告: 未找到生肉图片: {page_name}")
            continue

        # 查找对应的mask文件
        mask_path = Path(raw_mask_dir) / f"mask-{page_name}.png"
        if not mask_path.exists():
            logger.warning(f"警告: 未找到mask文件: {mask_path}")
            continue

        try:
            # 打开生肉图片获取尺寸
            with Image.open(raw_image_path) as raw_img:
                raw_width, raw_height = raw_img.size

            # 创建全黑图像（与生肉图片同样大小）
            new_mask = Image.new('L', (raw_width, raw_height), 0)  # 'L' 模式，0表示黑色

            # 打开原始mask
            original_mask = Image.open(mask_path).convert('L')

            # 处理每个匹配的文本框
            for entry in page_entries:
                if entry["matched"]:
                    # 获取生肉文本框坐标
                    raw_box = entry["raw_xyxy"]

                    # 从原始mask中截取对应区域
                    x1, y1, x2, y2 = raw_box
                    # 确保坐标在图片范围内
                    x1 = max(0, x1)
                    y1 = max(0, y1)
                    x2 = min(raw_width, x2)
                    y2 = min(raw_height, y2)

                    if x1 >= x2 or y1 >= y2:
                        continue

                    # 从原始mask中截取区域
                    mask_region = original_mask.crop((x1, y1, x2, y2))

                    # 将截取的区域粘贴到新mask的同样位置
                    new_mask.paste(mask_region, (x1, y1))

            # 保存新mask
            output_path = Path(output_dir) / f"mask-{page_name}.png"
            new_mask.save(output_path)
            logger.info(f"✓ 已创建新mask: {output_path.name}")

        except Exception as e:
            logger.error(f"✗ 创建页面 {page_name} 的新mask时出错: {str(e)}")

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

    # 读取生肉和熟肉的标注文件
    try:
        with open(raw_annotations_path, 'r', encoding='utf-8') as f:
            raw_json = json.load(f)

        with open(text_annotations_path, 'r', encoding='utf-8') as f:
            text_json = json.load(f)
    except Exception as e:
        logger.error(f"读取标注文件失败: {e}")
        return False

    # 获取生肉图片目录
    raw_dir = Path(raw_annotations_path).parent.parent
    text_dir = Path(text_annotations_path).parent.parent

    # 创建TextBoxMatcher实例
    matcher = TextBoxMatcher(raw_annotations_path, text_annotations_path)

    # 创建结果字典
    result = {
        "directory": str(raw_dir.resolve()),
        "pages": {},
        "current_img": None
    }

    # 获取所有页面名称
    raw_pages = list(raw_json.keys())
    text_pages = list(text_json.keys())

    if not raw_pages:
        logger.error("错误: 生肉标注文件中没有找到任何页面")
        return False

    raw_pages = natsorted(raw_pages)

    # 设置当前图片为第一页
    result["current_img"] = raw_pages[0]

    total_pages = len(raw_pages)

    # 遍历所有页面进行匹配
    for page_idx, page_name in enumerate(raw_pages):
        if status_callback:
            status_callback(page_idx + 1, total_pages)

        # 检查熟肉中是否有对应的页面
        if page_name not in text_json:
            logger.warning(f"警告: 熟肉中未找到页面 {page_name}，跳过")
            result["pages"][page_name] = []
            continue

        # 获取图片尺寸
        try:
            # 获取生肉图片尺寸
            raw_image_path = None
            for ext in ['.jpg', '.jpeg', '.png']:
                potential_path = raw_dir / f"{page_name}{ext}"
                if potential_path.exists():
                    raw_image_path = potential_path
                    break

            # 获取熟肉图片尺寸
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
                raw_size = raw_img.size  # (width, height)

            with Image.open(text_image_path) as text_img:
                text_size = text_img.size  # (width, height)

        except Exception as e:
            logger.error(f"获取图片尺寸失败 {page_name}: {e}")
            result["pages"][page_name] = []
            continue

        logger.info(f"页面 {page_name}: 生肉尺寸 {raw_size}, 熟肉尺寸 {text_size}")

        # 使用TextBoxMatcher进行匹配
        match_result = matcher.match_boxes(page_name, page_name, raw_size, text_size)

        # 获取原始文本框和熟肉文本框
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

        # logger.info(f"页面 {page_name}: 生肉 {len(raw_boxes)} 个框, 熟肉 {len(text_boxes)} 个框")

        # 创建当前页的结果列表
        page_results = []

        # 初始化所有文本框为未匹配
        for i in range(len(text_boxes)):
            page_results.append({
                "xyxy": [],
                "orig_xyxy": text_boxes[i],
                "matched": 0
            })

        # 处理匹配结果
        matches = match_result.get('matches', [])
        adjusted_positions = match_result.get('adjusted_positions', [])

        # 更新匹配成功的文本框
        for match_idx, (jp_idx, cn_idx) in enumerate(matches):
            if cn_idx < len(page_results):
                page_results[cn_idx] = {
                    "xyxy": adjusted_positions[match_idx],
                    "orig_xyxy": text_boxes[cn_idx],
                    "raw_xyxy": raw_boxes[jp_idx],
                    "matched": 1
                }

        # 将当前页结果添加到总结果
        result["pages"][page_name] = page_results
        matched_count = len([r for r in page_results if r['matched']])
        logger.info(f"页面 {page_name}: 匹配 {matched_count}/{len(page_results)} 个文本框")

    # 保存匹配结果到文件
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            # json.dump(result, f, indent=2, ensure_ascii=False)
            json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
        logger.info(f"匹配结果已保存到: {output_path}")
    except Exception as e:
        logger.error(f"保存匹配结果失败: {e}")
        return False

    # 创建新mask
    create_new_masks(result, raw_mask_dir, new_mask_dir)

    return True