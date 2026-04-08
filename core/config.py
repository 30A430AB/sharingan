import sys
import pooch
from pathlib import Path

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
        "models/resnet18-f37072fd.pth": ("resnet18-f37072fd.pth", "f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec"),
        "models/anime-manga-big-lama.pt": ("anime-manga-big-lama.pt", "479d3afdcb7ed2fd944ed4ebcc39ca45b33491f0f2e43eb1000bd623cfb41823"),
        "libs/libpatchmatch.so": ("libpatchmatch.so", "dcd2fe308a31cfe2c5e762aadbac68dde516fdaafa598744087a14dcd20c5533"),
        "libs/patchmatch_inpaint.dll": ("patchmatch_inpaint.dll", "0ba60cfe664c97629daa7e4d05c0888ebfe3edcb3feaf1ed5a14544079c6d7af"),
        "libs/opencv_world455.dll": ("opencv_world455.dll", "3b7619caa29dc3352b939de4e9981217a9585a13a756e1101a50c90c100acd8d"),
    }

    @classmethod
    def get_file(cls, local_rel_path: str):
        """按需获取资源文件，首次下载时校验哈希，后续仅检查存在性"""
        if not cls._is_needed(local_rel_path):
            return None

        data_root = Path(__file__).parent.parent / "data"
        local_path = data_root / local_rel_path

        # 如果文件已存在，直接返回（不校验哈希）
        if local_path.exists():
            return local_path

        # 文件不存在，使用 pooch 下载（下载时会自动校验哈希）
        pup = pooch.create(
            path=data_root,
            base_url=cls.BASE_URL,
            version=None,
        )
        def get_url(key):
            return cls.BASE_URL + Path(key).name
        pup.get_url = get_url

        if local_rel_path not in pup.registry:
            remote_filename, file_hash = cls.FILES[local_rel_path]
            pup.registry[local_rel_path] = file_hash

        fetched_path = pup.fetch(local_rel_path)  # 下载并校验
        return Path(fetched_path)
    
    @staticmethod
    def _is_needed(local_rel_path):
        if ".so" in local_rel_path and not sys.platform.startswith('linux'):
            return False
        if ".dll" in local_rel_path and not sys.platform.startswith('win'):
            return False
        return True