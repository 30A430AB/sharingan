import json
import os
from typing import Optional, Callable, List, Tuple, Union

import cv2
import numpy as np
from loguru import logger


class CoordinateAdjuster:
    """基于 mask 图像精确调整 match_results.json 中的 orig_xyxy 坐标"""

    # 阈值常量
    GRAY_THRESHOLD = 30
    ALPHA_THRESHOLD = 50

    def __init__(
        self,
        match_results_path: str,
        text_dir: str,
        status_callback: Optional[Callable[[int, int], None]] = None
    ):
        """
        Args:
            match_results_path: match_results.json 文件路径
            text_dir: 存放 mask 图像的目录（即 temp/text，文件名为 {page_name}.png）
            status_callback: 进度回调函数，接收当前处理索引和总数
        """
        self.match_results_path = match_results_path
        self.mask_dir = text_dir
        self.status_callback = status_callback

    def _get_text_pixels(self, mask: np.ndarray, roi_bbox: List[int]) -> np.ndarray:
        """从 ROI 区域内提取文字像素坐标（相对 ROI 左上角）"""
        x1, y1, x2, y2 = map(int, roi_bbox)
        roi = mask[y1:y2, x1:x2]

        if roi.size == 0:
            return np.empty((0, 2), dtype=int)

        if roi.ndim == 2:
            # 单通道灰度/二值 mask
            ys, xs = np.where(roi > self.GRAY_THRESHOLD)
            return np.column_stack((ys, xs))

        # 三通道或四通道图像
        if roi.shape[2] == 4:
            # RGBA: 综合 alpha 通道和灰度值判断
            alpha = roi[:, :, 3]
            gray = cv2.cvtColor(roi[:, :, :3], cv2.COLOR_BGR2GRAY)
            mask_region = (gray > self.GRAY_THRESHOLD) & (alpha > self.ALPHA_THRESHOLD)
        else:
            # RGB: 仅基于灰度判断
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            mask_region = gray > self.GRAY_THRESHOLD

        ys, xs = np.where(mask_region)
        return np.column_stack((ys, xs))

    def _get_min_rect_from_mask(
        self, mask: np.ndarray, roi_bbox: List[int]
    ) -> Tuple[Union[List[int], None], int]:
        """根据 mask 中的实际文字像素计算最小包围框"""
        pts = self._get_text_pixels(mask, roi_bbox)
        if len(pts) == 0:
            return None, 0

        x1, y1, _, _ = map(int, roi_bbox)
        ys, xs = pts[:, 0], pts[:, 1]

        new_x1 = x1 + np.min(xs)
        new_y1 = y1 + np.min(ys)
        new_x2 = x1 + np.max(xs) + 1
        new_y2 = y1 + np.max(ys) + 1

        return [int(new_x1), int(new_y1), int(new_x2), int(new_y2)], len(pts)

    def adjust_annotations(self) -> None:
        """执行坐标调整并写回 JSON 文件"""
        try:
            with open(self.match_results_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            pages = data.get('pages', {})
            total = len(pages)
            processed = 0

            for page_name, entries in pages.items():
                mask_path = os.path.join(self.mask_dir, f"{page_name}.png")
                if not os.path.exists(mask_path):
                    processed += 1
                    if self.status_callback:
                        self.status_callback(processed, total)
                    continue

                try:
                    # 以二进制方式读取文件，再用 cv2.imdecode 解码
                    with open(mask_path, 'rb') as f:
                        img_bytes  = np.frombuffer(f.read(), np.uint8)
                    mask = cv2.imdecode(img_bytes , cv2.IMREAD_UNCHANGED)
                except Exception as e:
                    mask = None
                    
                if mask is None:
                    logger.error(f"无法读取 mask: {mask_path}")
                    processed += 1
                    if self.status_callback:
                        self.status_callback(processed, total)
                    continue

                h, w = mask.shape[:2]
                # 动态计算扩展像素：短边的 2% 或至少 5 像素
                expand_px = max(5, min(h, w) // 50)

                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if 'orig_xyxy' not in entry or not entry['orig_xyxy'] or len(entry['orig_xyxy']) != 4:
                        continue

                    x1, y1, x2, y2 = entry['orig_xyxy']
                    # 扩展搜索区域
                    x1 = max(0, x1 - expand_px)
                    y1 = max(0, y1 - expand_px)
                    x2 = min(w, x2 + expand_px)
                    y2 = min(h, y2 + expand_px)
                    search_roi = [int(x1), int(y1), int(x2), int(y2)]

                    min_rect, _ = self._get_min_rect_from_mask(mask, search_roi)
                    if min_rect is not None:
                        entry['orig_xyxy'] = min_rect

                processed += 1
                if self.status_callback:
                    self.status_callback(processed, total)

            with open(self.match_results_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception(f"坐标调整失败: {str(e)}")
            raise