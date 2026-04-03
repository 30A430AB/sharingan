import json
import os
from .basemodel import TextDetBase, TextDetBaseDNN
import os.path as osp
import numpy as np
import cv2
import torch
from pathlib import Path
import torch
from .utils.yolov5_utils import non_max_suppression
from .utils.db_utils import SegDetectorRepresenter
from .utils.io_utils import imread, imwrite, find_all_imgs, NumpyEncoder
from .utils.imgproc_utils import letterbox, xyxy2yolo
from .utils.textblock import group_output
from .utils.textmask import refine_mask, refine_undetected_mask, REFINEMASK_INPAINT, REFINEMASK_ANNOTATION
from pathlib import Path
from typing import Optional, Union


def safe_get_images(directory):
    """遍历目录获取所有图片文件）"""
    image_extensions = {'.jpg', '.jpeg', '.png'}
    files = []
    try:
        for entry in Path(directory).iterdir():
            if entry.is_file() and entry.suffix.lower() in image_extensions:
                files.append(str(entry.resolve()))  # 返回绝对路径字符串
    except FileNotFoundError:
        pass
    return files

def model2annotations(model_path, img_dir_list, save_dir, save_json=False, progress_callback: Optional[callable] = None):
    if isinstance(img_dir_list, str):
        img_dir_list = [img_dir_list]
    cuda = torch.cuda.is_available()
    device = 'cuda' if cuda else 'cpu'
    model = TextDetector(model_path=model_path, input_size=1024, device=device, act='leaky')
    
    # 获取输入文件夹名称用于生成唯一JSON文件名
    first_img_dir = img_dir_list[0] if isinstance(img_dir_list, list) else img_dir_list
    folder_name = Path(first_img_dir).name
    output_json_name = "annotations.json"
    output_json_path = osp.join(save_dir, output_json_name)
    
    all_annotations = {}  # 用字典存储所有结果，键为图片名
    
    imglist = []
    for img_dir in img_dir_list:
        imglist += safe_get_images(img_dir)
    
    total = len(imglist)
    for idx, img_path in enumerate(imglist):
        imgname = osp.basename(img_path)
        img = imread(img_path)
        im_h, im_w = img.shape[:2]
        imname = imgname.replace(Path(imgname).suffix, '')
        maskname = 'mask-'+imname+'.png'

        if progress_callback:
            # 传递当前进度（从1开始）和总数
            progress_callback(idx+1, total)

        _, mask_refined, blk_list = model(img, refine_mode=REFINEMASK_ANNOTATION, keep_undetected_mask=True)
        
        # 构建图片对应的标注数据
        img_annotations = {
            "image_path": img_path,
            "mask_path": osp.join(save_dir, maskname),
            "annotations": [blk.to_dict() for blk in blk_list]
        }
        all_annotations[imname] = img_annotations
        
        imwrite(osp.join(save_dir, maskname), mask_refined)

    if save_json:
        with open(output_json_path, 'w', encoding='utf8') as f:
            json.dump(all_annotations, f, ensure_ascii=False, cls=NumpyEncoder, indent=2)

def preprocess_img(img, input_size=(1024, 1024), device='cpu', bgr2rgb=True, half=False, to_tensor=True):
    if bgr2rgb:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img_in, ratio, (dw, dh) = letterbox(img, new_shape=input_size, auto=False, stride=64)
    if to_tensor:
        img_in = img_in.transpose((2, 0, 1))[::-1]  # HWC to CHW, BGR to RGB
        img_in = np.array([np.ascontiguousarray(img_in)]).astype(np.float32) / 255
        if to_tensor:
            img_in = torch.from_numpy(img_in).to(device)
            if half:
                img_in = img_in.half()
    return img_in, ratio, int(dw), int(dh)

def postprocess_mask(img: Union[torch.Tensor, np.ndarray], thresh=None):
    if isinstance(img, torch.Tensor):
        img = img.squeeze_()
        if img.device != 'cpu':
            img = img.detach_().cpu()
        img = img.numpy()
    else:
        img = img.squeeze()
    if thresh is not None:
        img = img > thresh
    img = img * 255

    return img.astype(np.uint8)

def postprocess_yolo(det, conf_thresh, nms_thresh, resize_ratio, sort_func=None):
    det = non_max_suppression(det, conf_thresh, nms_thresh)[0]
    if det.device != 'cpu':
        det = det.detach_().cpu().numpy()
    det[..., [0, 2]] = det[..., [0, 2]] * resize_ratio[0]
    det[..., [1, 3]] = det[..., [1, 3]] * resize_ratio[1]
    if sort_func is not None:
        det = sort_func(det)

    blines = det[..., 0:4].astype(np.int32)
    confs = np.round(det[..., 4], 3)
    cls = det[..., 5].astype(np.int32)
    return blines, cls, confs

class TextDetector:
    lang_list = ['eng', 'ja', 'unknown']
    langcls2idx = {'eng': 0, 'ja': 1, 'unknown': 2}

    def __init__(self, model_path, input_size=1024, device='cpu', half=False, nms_thresh=0.35, conf_thresh=0.4, mask_thresh=0.3, act='leaky'):
        super(TextDetector, self).__init__()
        cuda = device == 'cuda'

        if Path(model_path).suffix == '.onnx':
            self.model = cv2.dnn.readNetFromONNX(model_path)
            self.net = TextDetBaseDNN(input_size, model_path)
            self.backend = 'opencv'
        else:
            self.net = TextDetBase(model_path, device=device, act=act)
            self.backend = 'torch'
        
        if isinstance(input_size, int):
            input_size = (input_size, input_size)
        self.input_size = input_size
        self.device = device
        self.half = half
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh
        self.seg_rep = SegDetectorRepresenter(thresh=0.3)

    @torch.no_grad()
    def __call__(self, img, refine_mode=REFINEMASK_INPAINT, keep_undetected_mask=False):
        img_in, ratio, dw, dh = preprocess_img(img, input_size=self.input_size, device=self.device, half=self.half, to_tensor=self.backend=='torch')
        im_h, im_w = img.shape[:2]

        blks, mask, lines_map = self.net(img_in)

        resize_ratio = (im_w / (self.input_size[0] - dw), im_h / (self.input_size[1] - dh))
        blks = postprocess_yolo(blks, self.conf_thresh, self.nms_thresh, resize_ratio)

        if self.backend == 'opencv':
            if mask.shape[1] == 2:     # some version of opencv spit out reversed result
                tmp = mask
                mask = lines_map
                lines_map = tmp
        mask = postprocess_mask(mask)

        lines, scores = self.seg_rep(self.input_size, lines_map)
        box_thresh = 0.6
        idx = np.where(scores[0] > box_thresh)
        lines, scores = lines[0][idx], scores[0][idx]
        
        # map output to input img
        mask = mask[: mask.shape[0]-dh, : mask.shape[1]-dw]
        mask = cv2.resize(mask, (im_w, im_h), interpolation=cv2.INTER_LINEAR)
        if lines.size == 0 :
            lines = []
        else :
            lines = lines.astype(np.float64)
            lines[..., 0] *= resize_ratio[0]
            lines[..., 1] *= resize_ratio[1]
            lines = lines.astype(np.int32)
        blk_list = group_output(blks, lines, im_w, im_h, mask)
        mask_refined = refine_mask(img, mask, blk_list, refine_mode=refine_mode)
        if keep_undetected_mask:
            mask_refined = refine_undetected_mask(img, mask, mask_refined, blk_list, refine_mode=refine_mode)
    
        return mask, mask_refined, blk_list

if __name__ == '__main__':
    device = 'cpu'
    model_path = 'data/comictextdetector.pt'
    model_path = 'data/comictextdetector.pt.onnx'
    img_dir = r'data/examples'
    save_dir = r'data/backup'
    model2annotations(model_path, img_dir, save_dir, save_json=True)