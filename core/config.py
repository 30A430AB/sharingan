import sys
from pathlib import Path

import torch

# 项目根目录
_PROJECT_ROOT = Path(__file__).parent.parent

# 支持的图片扩展名
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.avif'}

SVG_ICONS = {
    "brand_family": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIGhlaWdodD0iMjRweCIgdmlld0JveD0iMCAtOTYwIDk2MCA5NjAiIHdpZHRoPSIyNHB4IiBmaWxsPSIjMWYxZjFmIj48cGF0aCBkPSJNMTg2LTgwcS01NCAwLTgwLTIydC0yNi02NnEwLTU4IDQ5LTc0dDExNi0xNmgyMXYtNTZxMC0zNC0xLTU1LjV0LTYtMzUuNXEtNS0xNC0xMS41LTE5LjVUMjMwLTQzMHEtOSAwLTE2LjUgM3QtMTIuNSA4cS00IDUtNSAxMC41dDEgMTEuNXE2IDExIDE0IDIxLjV0OCAyNC41cTAgMjUtMTcuNSA0Mi41VDE1OS0yOTFxLTI1IDAtNDIuNS0xNy41VDk5LTM1MXEwLTI3IDEyLTQ0dDMyLjUtMjdxMjAuNS0xMCA0Ny41LTE0dDU4LTRxODUgMCAxMTggMzAuNVQ0MDAtMzAydjE0N3EwIDE5IDQuNSAyOHQxNS41IDlxMTIgMCAxOS41LTE4dDkuNS01NmgxMXEtMyA2Mi0yMy41IDg3VDM2OC04MHEtNDMgMC02Ny41LTEzLjVUMjY5LTEzNHEtMTAgMjktMjkuNSA0MS41VDE4Ni04MFptMzczIDBxLTIwIDAtMzIuNS0xNi41VDUyMi0xMzJsMTAyLTI2OXE3LTE3IDIyLTI4dDM0LTExcTE5IDAgMzQgMTF0MjIgMjhsMTAyIDI2OXE4IDE5LTQuNSAzNS41VDgwMS04MHEtMTIgMC0yMi03dC0xNS0xOWwtMjAtNThINjE2bC0yMCA1OHEtNCAxMS0xNCAxOC41VDU1OS04MFptLTMyNC0yOXExMyAwIDIyLTIwLjV0OS00OS41di02N3EtMjYgMC0zOCAxNS41VDIxNi0xODB2MTFxMCAzNiA0IDQ4dDE1IDEyWm00MDctMTI1aDc3bC0zOS0xMTQtMzggMTE0Wm0tMzctMjg1cS00OCAwLTc2LjUtMzMuNVQ1MDAtNjQzcTAtMTA0IDY2LTE3MC41VDczNS04ODBxNDIgMCA2OCA5LjV0MjYgMjQuNXEwIDYtMiAxMnQtNyAxMXEtNSA3LTEyLjUgMTB0LTE1LjUgMXEtMTQtNC0zMi03dC0zMy0zcS03MSAwLTExNCA0OHQtNDMgMTI3cTAgMjIgOCA0NnQzNiAyNHExMSAwIDIxLjUtNXQxOC41LTE0cTE3LTE4IDMxLjUtNjBUNzEyLTc1OHEyLTEzIDEwLjUtMTguNVQ3NDYtNzgycTE4IDAgMjcuNSA5LjVUNzc5LTc0OXEtMTIgNDMtMTcuNSA3NXQtNS41IDU4cTAgMjAgNS41IDI5dDE2LjUgOXExMSAwIDIxLjUtOHQyOS41LTMwcTItMyAxNS03IDggMCAxMiA2dDQgMTdxMCAyOC0zMiA1NHQtNjcgMjZxLTI2IDAtNDQuNS0xNFQ2OTEtNTc0cS0xNSAyNi0zNyA0MC41VDYwNS01MTlabS00ODUtMXYtMjIwcTAtNTggNDEtOTl0OTktNDFxNTggMCA5OSA0MXQ0MSA5OXYyMjBoLTgwdi04MEgyMDB2ODBoLTgwWm04MC0xNjBoMTIwdi02MHEwLTI1LTE3LjUtNDIuNVQyNjAtODAwcS0yNSAwLTQyLjUgMTcuNVQyMDAtNzQwdjYwWiIvPjwvc3ZnPg==",
    "format_color_text": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIGhlaWdodD0iMjRweCIgdmlld0JveD0iMCAtOTYwIDk2MCA5NjAiIHdpZHRoPSIyNHB4IiBmaWxsPSIjMWYxZjFmIj48cGF0aCBkPSJNODAgMHYtMTYwaDgwMFYwSDgwWm0xNDAtMjgwIDIxMC01NjBoMTAwbDIxMCA1NjBoLTk2bC01MC0xNDRIMzY4bC01MiAxNDRoLTk2Wm0xNzYtMjI0aDE2OGwtODItMjMyaC00bC04MiAyMzJaIi8+PC9zdmc+",
    "format_letter_spacing_2": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIGhlaWdodD0iMjRweCIgdmlld0JveD0iMCAtOTYwIDk2MCA5NjAiIHdpZHRoPSIyNHB4IiBmaWxsPSIjMWYxZjFmIj48cGF0aCBkPSJNMjQwLTgwIDgwLTI0MGwxNjAtMTYwIDU3IDU2LTY0IDY0aDQ5NGwtNjMtNjQgNTYtNTYgMTYwIDE2MEw3MjAtODBsLTU3LTU2IDY0LTY0SDIzM2w2MyA2NC01NiA1NlptMzYtMzYwIDE2NC00NDBoODBsMTY0IDQ0MGgtNzZsLTM4LTExMkgzOTJsLTQwIDExMmgtNzZabTEzOC0xNzZoMTMybC02NC0xODJoLTRsLTY0IDE4MloiLz48L3N2Zz4=",
    "format_textdirection_on_vertical": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIGhlaWdodD0iMjRweCIgdmlld0JveD0iMCAtOTYwIDk2MCA5NjAiIHdpZHRoPSIyNHB4IiBmaWxsPSIjMWYxZjFmIj48cGF0aCBkPSJNMjQwLTI0MHYtMjAwcS02NiAwLTExMy00N1Q4MC02MDBxMC02NiA0Ny0xMTN0MTEzLTQ3aDMyMHY4MGgtODB2NDQwaC04MHYtNDQwaC04MHY0NDBoLTgwWm00ODAgODBMNTYwLTMyMGw1Ni01NiA2NCA2M3YtNDQ3aDgwdjQ0N2w2NC02NCA1NiA1Ny0xNjAgMTYwWk0yNDAtNTIwdi0xNjBxLTMzIDAtNTYuNSAyMy41VDE2MC02MDBxMCAzMyAyMy41IDU2LjVUMjQwLTUyMFptMC04MFoiLz48L3N2Zz4=",
    "insert_text": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIGhlaWdodD0iMjRweCIgdmlld0JveD0iMCAtOTYwIDk2MCA5NjAiIHdpZHRoPSIyNHB4IiBmaWxsPSIjMWYxZjFmIj48cGF0aCBkPSJNNDQwLTMyMHYtMjQwSDMyMHYtODBoMzIwdjgwSDUyMHYyNDBoLTgwWk00MC00MHYtMjQwaDgwdi00MDBINDB2LTI0MGgyNDB2ODBoNDAwdi04MGgyNDB2MjQwaC04MHY0MDBoODB2MjQwSDY4MHYtODBIMjgwdjgwSDQwWm0yNDAtMTYwaDQwMHYtODBoODB2LTQwMGgtODB2LTgwSDI4MHY4MGgtODB2NDAwaDgwdjgwWk0xMjAtNzYwaDgwdi04MGgtODB2ODBabTY0MCAwaDgwdi04MGgtODB2ODBabTAgNjQwaDgwdi04MGgtODB2ODBabS02NDAgMGg4MHYtODBoLTgwdjgwWm04MC02NDBabTU2MCAwWm0wIDU2MFptLTU2MCAwWiIvPjwvc3ZnPg==",
    "view_column_2": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIGhlaWdodD0iMjRweCIgdmlld0JveD0iMCAtOTYwIDk2MCA5NjAiIHdpZHRoPSIyNHB4IiBmaWxsPSIjMWYxZjFmIj48cGF0aCBkPSJNNjAwLTEyMHEtMzMgMC01Ni41LTIzLjVUNTIwLTIwMHYtNTYwcTAtMzMgMjMuNS01Ni41VDYwMC04NDBoMTYwcTMzIDAgNTYuNSAyMy41VDg0MC03NjB2NTYwcTAgMzMtMjMuNSA1Ni41VDc2MC0xMjBINjAwWm0wLTY0MHY1NjBoMTYwdi01NjBINjAwWk0yMDAtMTIwcS0zMyAwLTU2LjUtMjMuNVQxMjAtMjAwdi01NjBxMC0zMyAyMy41LTU2LjVUMjAwLTg0MGgxNjBxMzMgMCA1Ni41IDIzLjVUNDQwLTc2MHY1NjBxMCAzMy0yMy41IDU2LjVUMzYwLTEyMEgyMDBabTAtNjQwdjU2MGgxNjB2LTU2MEgyMDBabTU2MCAwSDYwMGgxNjBabS00MDAgMEgyMDBoMTYwWiIvPjwvc3ZnPg==",
}

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