import json
import os
import logging
import math
from PIL import Image

class CoordinateAdjuster:
    """坐标调整器（按边独立扩展）"""
    
    def __init__(self, text_dir: str, annotations_path: str, status_callback: callable = None):
        self.annotations_path = annotations_path
        self.mask_dir = text_dir
        self.status_callback = status_callback
        self.logger = logging.getLogger('CoordinateAdjuster')

    def _get_image_size(self, base_name: str) -> tuple:
        """获取图片尺寸 - 使用mask文件格式"""
        mask_path = os.path.join(self.mask_dir, f"mask-{base_name}.png")
        try:
            with Image.open(mask_path) as img:
                return img.size
        except Exception as e:
            self.logger.error(f"获取尺寸失败: {base_name} - {str(e)}")
            return (0, 0)

    def _check_edge_outer_white(self, img: Image.Image, coord: list, edge: str) -> bool:
        """
        检测指定边外一像素是否全白
        coord: [x1,y1,x2,y2] 当前文本框坐标
        edge: left/right/top/bottom
        返回: True(全白) / False(存在非白像素)
        """
        x1, y1, x2, y2 = coord
        img_w, img_h = img.size

        # 定义检测区域
        if edge == 'left':
            if x1 == 0: return True  # 已达左边界
            area = (x1-1, y1, x1, y2)
        elif edge == 'right':
            if x2 >= img_w: return True
            area = (x2, y1, x2+1, y2)
        elif edge == 'top':
            if y1 == 0: return True
            area = (x1, y1-1, x2, y1)
        elif edge == 'bottom':
            if y2 >= img_h: return True
            area = (x1, y2, x2, y2+1)
        else:
            return True

        # 检测白色像素 (RGB 255,255,255)
        strip = img.crop(area)
        # 转换为RGB模式（如果是二值图，白色像素值为255）
        if strip.mode != 'RGB':
            strip = strip.convert('RGB')
        
        # 检查所有像素是否为白色
        for pixel in strip.getdata():
            if pixel != (255, 255, 255):
                return False
        return True

    def _calculate_max_expansion(self, img_size: tuple) -> dict:
        """计算各边最大扩展量"""
        max_w = math.ceil(img_size[0] * 0.2)  # 宽度20%
        max_h = math.ceil(img_size[1] * 0.2)  # 高度20%
        return {'left': max_w, 'right': max_w, 'top': max_h, 'bottom': max_h}

    def _expand_box(self, base_name: str, coord: list) -> list:
        """智能扩展逻辑"""
        img_size = self._get_image_size(base_name)
        if img_size == (0, 0):
            return coord

        x1, y1, x2, y2 = coord
        original_coord = coord.copy()
        max_expansion = self._calculate_max_expansion(img_size)
        expansion_counter = {'left':0, 'right':0, 'top':0, 'bottom':0}

        try:
            # 使用mask文件格式打开图片
            mask_path = os.path.join(self.mask_dir, f"mask-{base_name}.png")
            with Image.open(mask_path) as img:
                while True:
                    modified = False
                    
                    # 左边界扩展检测
                    if (not self._check_edge_outer_white(img, [x1,y1,x2,y2], 'left') 
                        and expansion_counter['left'] < max_expansion['left']):
                        x1 = max(0, x1 - 1)
                        expansion_counter['left'] += 1
                        modified = True

                    # 右边界扩展检测
                    if (not self._check_edge_outer_white(img, [x1,y1,x2,y2], 'right') 
                        and expansion_counter['right'] < max_expansion['right']):
                        x2 = min(img_size[0], x2 + 1)
                        expansion_counter['right'] += 1
                        modified = True

                    # 上边界扩展检测
                    if (not self._check_edge_outer_white(img, [x1,y1,x2,y2], 'top') 
                        and expansion_counter['top'] < max_expansion['top']):
                        y1 = max(0, y1 - 1)
                        expansion_counter['top'] += 1
                        modified = True

                    # 下边界扩展检测
                    if (not self._check_edge_outer_white(img, [x1,y1,x2,y2], 'bottom') 
                        and expansion_counter['bottom'] < max_expansion['bottom']):
                        y2 = min(img_size[1], y2 + 1)
                        expansion_counter['bottom'] += 1
                        modified = True

                    if not modified:
                        break

        except Exception as e:
            self.logger.error(f"扩展失败 {base_name}: {str(e)}")
            return original_coord  # 出错时返回原坐标

        return [int(x1), int(y1), int(x2), int(y2)]

    def adjust_annotations(self):
        """主调整方法"""
        try:
            with open(self.annotations_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            total = len(data)
            processed = 0
            
            for idx, base_name in enumerate(list(data.keys())):
                entry = data[base_name]
                img_size = self._get_image_size(base_name)
                if img_size == (0, 0): 
                    continue

                for anno in entry['annotations']:
                    original = anno['xyxy']
                    anno['xyxy'] = self._expand_box(base_name, original)

                # 每处理完一个文件更新进度
                processed += 1
                if self.status_callback:
                    self.status_callback(processed, total)

            # 写入
            temp_path = self.annotations_path + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.annotations_path)

        except Exception as e:
            self.logger.error(f"全局调整失败: {str(e)}")
            raise