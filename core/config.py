import sys
from pathlib import Path

import torch

# 项目根目录
_PROJECT_ROOT = Path(__file__).parent.parent

# 支持的图片扩展名
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.avif'}

class classproperty:
    """使类方法可以像属性一样访问，不需要实例"""
    def __init__(self, fget):
        self.fget = fget
    def __get__(self, instance, owner):
        return self.fget(owner)

class DirPaths:
    TEMP = "temp"
    TEXT = "temp/text"
    RAW_MASK = "temp/raw_mask"
    TEXT_MASK = "temp/text_mask"
    THUMBS = "temp/thumbs"
    NEW_MASK = "mask"
    INPAINTED = "inpainted"
    RESULT = "result"

class DataPaths:
    DATA_ROOT = _PROJECT_ROOT / "data"
    MODELS_DIR = DATA_ROOT / "models"
    LIBS_DIR = DATA_ROOT / "libs"

    @classproperty
    def COMIC_TEXT_DETECTOR(cls):
        # 有 CUDA 时直接用 PyTorch 模型
        if torch.cuda.is_available():
            return ResourceManager.get_file("models/comictextdetector.pt")
        # 纯 CPU 优先尝试 ONNX，缺失则回退到 PyTorch
        try:
            return ResourceManager.get_file("models/comictextdetector.pt.onnx")
        except FileNotFoundError:
            return ResourceManager.get_file("models/comictextdetector.pt")

    @classproperty
    def RESNET18(cls):
        return ResourceManager.get_file("models/resnet18-f37072fd.pth")

    @classproperty
    def LAMA(cls):
        return ResourceManager.get_file("models/anime-manga-big-lama.pt")

    @classproperty
    def PATCHMATCH_SO(cls):
        return ResourceManager.get_file("libs/libpatchmatch.so")

    @classproperty
    def OPENCV_DLL(cls):
        return ResourceManager.get_file("libs/opencv_world455.dll")

    @classproperty
    def PATCHMATCH_INPAINT_DLL(cls):
        return ResourceManager.get_file("libs/patchmatch_inpaint.dll")

class InpaintAlgorithm:
    PATCHMATCH = "PatchMatch"
    LAMA = "LaMa"

class ResourceManager:
    BASE_URL = "https://github.com/30A430AB/MangaTransFer/releases/download/v0.1.0/"

    # 本地相对路径 (相对于 data/) -> (远程文件名, SHA-256)
    FILES = {
        "models/comictextdetector.pt": ("comictextdetector.pt", "1f90fa60aeeb1eb82e2ac1167a66bf139a8a61b8780acd351ead55268540cccb"),
        "models/comictextdetector.pt.onnx": ("comictextdetector.pt.onnx","1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f"),
        "models/resnet18-f37072fd.pth": ("resnet18-f37072fd.pth", "f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec"),
        "models/anime-manga-big-lama.pt": ("anime-manga-big-lama.pt", "479d3afdcb7ed2fd944ed4ebcc39ca45b33491f0f2e43eb1000bd623cfb41823"),
        "libs/libpatchmatch.so": ("libpatchmatch.so", "dcd2fe308a31cfe2c5e762aadbac68dde516fdaafa598744087a14dcd20c5533"),
        "libs/patchmatch_inpaint.dll": ("patchmatch_inpaint.dll", "0ba60cfe664c97629daa7e4d05c0888ebfe3edcb3feaf1ed5a14544079c6d7af"),
        "libs/opencv_world455.dll": ("opencv_world455.dll", "3b7619caa29dc3352b939de4e9981217a9585a13a756e1101a50c90c100acd8d"),
    }

    @classmethod
    def get_file(cls, local_rel_path: str):
        """获取资源文件的本地路径，文件不存在则抛出 FileNotFoundError"""
        if not cls._is_needed(local_rel_path):
            return None

        data_root = Path(__file__).parent.parent / "data"
        local_path = data_root / local_rel_path

        if not local_path.exists():
            raise FileNotFoundError(f"资源文件缺失: {local_rel_path}")

        return local_path

    @staticmethod
    def _is_needed(local_rel_path):
        if ".so" in local_rel_path and not sys.platform.startswith('linux'):
            return False
        if ".dll" in local_rel_path and not sys.platform.startswith('win'):
            return False
        return True