#!/usr/bin/env python3
"""漫画翻译移植工具 - 启动器"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import subprocess
import threading
import sys
import os
import codecs
import queue
from pathlib import Path
import hashlib
import urllib.request
import importlib.util
import re
from typing import Optional, Callable, Tuple, List, Dict, Any

from core.config import ResourceManager

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


# ── 日志格式化器 ──────────────────────────────────────
class LogFormatter:
    """负责解析和格式化子进程的输出文本"""
    @staticmethod
    def classify_tag(text: str) -> Optional[str]:
        if "错误" in text or "Error" in text or "失败" in text:
            return "error"
        if "完成" in text or "成功" in text:
            return "success"
        if "INFO" in text:
            return "info"
        if text.startswith("命令:"):
            return "dim"
        return None

    @staticmethod
    def is_progress(text: str) -> bool:
        return bool(re.search(r'\d+%', text))

    @staticmethod
    def format_progress_line(text: str) -> str:
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return f"{now} | INFO | - {text}"
    
    @staticmethod
    def normalize_progress_bar(text: str) -> str:
        """去掉进度条和计数，只保留三位等宽百分比"""
        match = re.search(r'\s*(\d+%) ?\|[^|]+\|\s*\d+/\d+', text)
        if not match:
            return text
        percent_num = int(match.group(1).rstrip('%'))
        percent_str = f"{percent_num:>3d}%"
        return text[:match.start()] + percent_str


# ── 子进程执行器 ──────────────────────────────────────
class SubprocessRunner:
    """封装底层子进程的启动、流式读取与生命周期管理"""
    def __init__(self, on_output: Callable[[str], None], on_done: Callable[[int], None]):
        self.process: Optional[subprocess.Popen] = None
        self._on_output = on_output
        self._on_done = on_done
        self.formatter = LogFormatter()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def run(self, cmd: List[str], cwd: Path):
        threading.Thread(target=self._run_target, args=(cmd, cwd), daemon=True).start()

    def terminate(self):
        if self.is_running():
            self.process.terminate()

    def _run_target(self, cmd: List[str], cwd: Path):
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(cwd)
            )
            
            fd = self.process.stdout.fileno()
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            line_buf = ""

            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                
                text = decoder.decode(chunk)
                for ch in text:
                    if ch == '\r':
                        if line_buf: self._dispatch(line_buf)
                        line_buf = ""
                    elif ch == '\n':
                        if line_buf: self._dispatch(line_buf)
                        line_buf = ""
                    else:
                        line_buf += ch
                if line_buf.strip(): self._dispatch(line_buf)
                
            self.process.wait()
            self._on_done(self.process.returncode)
        except Exception as e:
            self._on_output(f"启动失败: {e}")
            self._on_done(-1)

    def _dispatch(self, line: str):
        stripped = line.strip()
        if not stripped: return
        if self.formatter.is_progress(stripped):
            stripped = self.formatter.normalize_progress_bar(stripped)  # 先归一化宽度
            stripped = self.formatter.format_progress_line(stripped)     # 再加时间戳
        self._on_output(stripped)



# ── 日志面板 ──────────────────────────────────────
class LogPanel(ttk.Frame):
    """日志展示组件，包含标题栏、清空按钮、文本域和异步队列"""
    def __init__(self, parent: ttk.Widget, text_height: int = 12, **kwargs):
        super().__init__(parent, **kwargs)
        self._queue: queue.Queue[Tuple[str, str, Optional[str]]] = queue.Queue()
        self._last_log_line: Optional[Tuple[str, Optional[str]]] = None

        # 布局：标题行
        log_header = ttk.Frame(self)
        log_header.pack(fill=tk.X, pady=(8, 3))
        ttk.Label(log_header, text="日志输出:", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_header, text="清空日志", width=8, command=self.clear).pack(side=tk.RIGHT)

        # 布局：文本框
        self.text_widget = scrolledtext.ScrolledText(
            self, height=text_height, state=tk.DISABLED, font=("Microsoft YaHei", 9),
            wrap=tk.WORD, bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
            selectbackground="#264f78", borderwidth=0, highlightthickness=1,
            highlightbackground="#444", padx=6, pady=4,
        )
        self.text_widget.pack(fill=tk.BOTH, expand=True)

        # 配置 Tag 颜色
        for tag, color in [("error", "#f44747"), ("success", "#6a9955"), 
                           ("info", "#569cd6"), ("dim", "#808080"), 
                           ("ok", "#6a9955"), ("missing", "#f44747")]:
            self.text_widget.tag_configure(tag, foreground=color)

    def start_polling(self, root: tk.Tk, interval: int = 50):
        self._poll(root, interval)

    def _poll(self, root: tk.Tk, interval: int):
        while not self._queue.empty():
            try:
                _, text, tag = self._queue.get_nowait()
            except queue.Empty:
                break
            self._insert_safe(text, tag)
        root.after(interval, self._poll, root, interval)

    def enqueue(self, text: str, tag: Optional[str] = None):
        self._queue.put(("log", text, tag))

    def append(self, text: str, tag: Optional[str] = None):
        """主线程直接追加"""
        self._insert_safe(text, tag)

    def clear(self):
        self.text_widget.configure(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)
        self.text_widget.configure(state=tk.DISABLED)
        self._last_log_line = None

    TIMESTAMP_PREFIX = " | INFO | - "

    def _insert_safe(self, text: str, tag: Optional[str]):
        dedup_key = text
        
        # 如果是带时间戳前缀的进度条，剥离时间戳用于去重对比
        if self.TIMESTAMP_PREFIX in text:
            core_text = text.split(self.TIMESTAMP_PREFIX, 1)[-1]
            if LogFormatter.is_progress(core_text):
                dedup_key = core_text

        # 核心去重判断
        if (dedup_key, tag) == self._last_log_line:
            return
        self._last_log_line = (dedup_key, tag)

        self.text_widget.configure(state=tk.NORMAL)
        self.text_widget.insert(tk.END, text + "\n", tag)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state=tk.DISABLED)


# ── 资源下载器 ──────────────────────────────────────
class ResourceDownloader:
    """资源文件下载器，含进度回调与哈希校验"""
    def __init__(self, progress_callback: Optional[Callable[[int, str], None]] = None):
        self.progress_callback = progress_callback

    @staticmethod
    def _format_size(bytes_count: int) -> str:
        return f"{bytes_count / (1024 * 1024):.2f} MB" if bytes_count >= 1024 * 1024 else f"{bytes_count / 1024:.2f} KB"

    @staticmethod
    def get_required_files() -> Dict[str, Tuple[str, str]]:
        import platform
        is_windows, is_linux = platform.system() == "Windows", platform.system() == "Linux"
        files = {}
        for rel_path, (remote_name, sha256) in ResourceManager.FILES.items():
            if ".dll" in rel_path and not is_windows: continue
            if ".so" in rel_path and not is_linux: continue
            files[rel_path] = (remote_name, sha256)
        return files

    @staticmethod
    def get_data_root() -> Path:
        return Path(__file__).resolve().parent / "data"

    def scan_missing(self) -> List[Tuple[Path, str, str]]:
        required = self.get_required_files()
        data_root = self.get_data_root()
        return [(data_root / rel_path, remote_name, sha256) 
                for rel_path, (remote_name, sha256) in required.items() 
                if not (data_root / rel_path).exists()]

    def download_file(self, remote_name: str, local_path: Path, sha256: str) -> bool:
        url = ResourceManager.BASE_URL + remote_name
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded, buffer = 0, bytearray()
            while True:
                chunk = response.read(8192)
                if not chunk: break
                buffer.extend(chunk)
                downloaded += len(chunk)
                if total_size and self.progress_callback:
                    self.progress_callback(int(downloaded / total_size * 100), 
                                           f"{remote_name} {self._format_size(downloaded)} / {self._format_size(total_size)}")
            if hashlib.sha256(buffer).hexdigest() != sha256:
                raise ValueError(f"哈希校验失败: {remote_name}")
            with open(local_path, 'wb') as f: f.write(buffer)
        return True

    def download_all(self, missing_files: List[Tuple[Path, str, str]]):
        total = len(missing_files)
        for idx, (local_path, remote_name, sha256) in enumerate(missing_files, 1):
            if self.progress_callback:
                self.progress_callback(int((idx - 1) / total * 100), f"准备下载 {remote_name} ({idx}/{total})")
            try:
                self.download_file(remote_name, local_path, sha256)
            except Exception as e:
                raise RuntimeError(f"下载失败: {remote_name} - {e}") from e
        if self.progress_callback:
            self.progress_callback(100, "下载完成")


# ── 拖拽输入框 ──────────────────────────────────────
class DropEntry(ttk.Entry):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind('<<Drop>>', self._on_drop)

    def _on_drop(self, event):
        path = event.data
        if path.startswith('{') and path.endswith('}'): path = path[1:-1]
        self.delete(0, tk.END)
        self.insert(0, path)


# ── 主控应用 ──────────────────────────────────────
class App(tk.Tk if not HAS_DND else TkinterDnD.Tk):
    PACKAGE_NAME_MAP = {"opencv_python": "cv2", "scikit-learn": "sklearn", "pillow": "PIL"}
    MIRRORS = {
        "无": "", "清华源": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "阿里云源": "https://mirrors.aliyun.com/pypi/simple/", 
        "中科大源": "https://pypi.mirrors.ustc.edu.cn/simple/",
        "华为云源": "https://mirrors.huaweicloud.com/repository/pypi/simple/",
    }

    def __init__(self):
        super().__init__()
        self.title("漫画翻译移植工具")
        self.geometry("600x480")
        self.minsize(600, 480)

        # 执行器
        self.cli_runner = SubprocessRunner(on_output=self._on_cli_output, on_done=self._on_cli_done)
        self.gui_runner = SubprocessRunner(on_output=self._on_gui_output, on_done=self._on_gui_done)
        self._cli_stopped_by_user = False
        self._gui_stopped_by_user = False

        self._build_ui()
        
        # 启动日志面板的异步轮询
        self.cli_log.start_polling(self)
        self.gui_log.start_polling(self)
        self.config_log.start_polling(self)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if not HAS_DND:
            self.after(100, lambda: self.cli_log.append("[提示] 未安装 tkinterdnd2，拖拽不可用\n[提示] pip install tkinterdnd2\n", "dim"))

    # ── 界面构建 ─────────────────────────
    def _build_ui(self):
        style = ttk.Style(self)
        style.configure("Header.TLabel", font=("", 10, "bold"))
        style.configure("TNotebook.Tab", width=10, anchor='center')

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 6))

        cli_frame = ttk.Frame(notebook, padding=8)
        notebook.add(cli_frame, text="命令行", sticky="nsew")
        self._build_cli_tab(cli_frame)

        gui_frame = ttk.Frame(notebook, padding=8)
        notebook.add(gui_frame, text="Web 服务", sticky="nsew")
        self._build_gui_tab(gui_frame)

        config_frame = ttk.Frame(notebook, padding=8)
        notebook.add(config_frame, text="配置", sticky="nsew")
        self._build_config_tab(config_frame)

    def _build_cli_tab(self, parent):
        # 生肉目录
        raw_frame = ttk.Frame(parent); raw_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(raw_frame, text="生肉目录:", style="Header.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.raw_entry = DropEntry(raw_frame); self.raw_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(raw_frame, text="浏览", width=6, command=lambda: self._browse(self.raw_entry)).pack(side=tk.RIGHT)

        # 熟肉目录
        text_frame = ttk.Frame(parent); text_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(text_frame, text="熟肉目录:", style="Header.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.text_entry = DropEntry(text_frame); self.text_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(text_frame, text="浏览", width=6, command=lambda: self._browse(self.text_entry)).pack(side=tk.RIGHT)

        # 操作行
        action_row = ttk.Frame(parent); action_row.pack(fill=tk.X, pady=(10, 4))
        self.start_btn = ttk.Button(action_row, text="\u25b6 运行", width=8, command=self._start_cli)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(action_row, text="\u25a0 停止", width=8, command=self._stop_cli, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        self.automatch_var = tk.BooleanVar(value=True)
        self.automatch_hint_label = ttk.Label(action_row, text="* 图片文件名须保持一致", foreground='red')
        
        def on_toggle(*args):
            self.automatch_hint_label.pack_forget() if self.automatch_var.get() else self.automatch_hint_label.pack(side=tk.LEFT, padx=(2, 0))
            
        self.automatch_var.trace_add('write', on_toggle)
        ttk.Checkbutton(action_row, text="自动匹配图片", variable=self.automatch_var).pack(side=tk.LEFT)
        self.automatch_hint_label.pack(side=tk.LEFT, padx=(2, 0))
        self.automatch_hint_label.pack_forget() # 默认隐藏

        # 日志面板组件
        self.cli_log = LogPanel(parent)
        self.cli_log.pack(fill=tk.BOTH, expand=True)

    def _build_gui_tab(self, parent):
        ttk.Label(parent, text="Web 服务器配置", style="Header.TLabel").pack(anchor=tk.W, pady=(0, 8))
        
        top_row = ttk.Frame(parent); top_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(top_row, text="主机:").pack(side=tk.LEFT, padx=(0, 2))
        self.gui_host_entry = ttk.Entry(top_row, width=16); self.gui_host_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.gui_host_entry.insert(0, "127.0.0.1")
        
        ttk.Label(top_row, text="端口:").pack(side=tk.LEFT, padx=(0, 2))
        self.gui_port_entry = ttk.Entry(top_row, width=8); self.gui_port_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.gui_port_entry.insert(0, "8080")
        
        self.gui_start_btn = ttk.Button(top_row, text="\u25b6 启动服务", width=10, command=self._start_gui)
        self.gui_start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.gui_stop_btn = ttk.Button(top_row, text="\u25a0 停止服务", width=10, command=self._stop_gui, state=tk.DISABLED)
        self.gui_stop_btn.pack(side=tk.LEFT)

        self.gui_log = LogPanel(parent)
        self.gui_log.pack(fill=tk.BOTH, expand=True)

    def _build_config_tab(self, parent):
        ttk.Label(parent, text="Python 配置", style="Header.TLabel").pack(anchor=tk.W, pady=(0, 8))
        
        interp_row = ttk.Frame(parent); interp_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(interp_row, text="解释器:").pack(side=tk.LEFT, padx=(0, 6))
        self.python_entry = ttk.Entry(interp_row); self.python_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.python_entry.insert(0, sys.executable)
        ttk.Button(interp_row, text="浏览", width=6, command=self._browse_python).pack(side=tk.RIGHT)

        dep_row = ttk.Frame(parent); dep_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(dep_row, text="依赖库:").pack(side=tk.LEFT, padx=(0, 6))
        self.check_dep_btn = ttk.Button(dep_row, text="安装依赖", command=self._check_and_install_deps)
        self.check_dep_btn.pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(dep_row, text="镜像源:").pack(side=tk.LEFT, padx=(0, 6))
        self.mirror_combo = ttk.Combobox(dep_row, values=list(self.MIRRORS.keys()), state="readonly", width=12)
        self.mirror_combo.set("无"); self.mirror_combo.pack(side=tk.LEFT)

        ttk.Separator(parent, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(parent, text="资源管理", style="Header.TLabel").pack(anchor=tk.W, pady=(0, 6))
        
        res_row = ttk.Frame(parent); res_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(res_row, text="检查资源", command=self._check_resources).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(res_row, text="下载资源", command=self._download_resources).pack(side=tk.LEFT)

        self.config_log = LogPanel(parent, text_height=8)
        self.config_log.pack(fill=tk.BOTH, expand=True)

    # ── 公共辅助方法 ─────────────────────────────────
    def _browse(self, entry: ttk.Entry):
        if path := filedialog.askdirectory(title="选择目录"):
            entry.delete(0, tk.END); entry.insert(0, path)

    def _browse_python(self):
        if path := filedialog.askopenfilename(title="选择 Python 解释器", filetypes=[("Python", "python.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")]):
            self.python_entry.delete(0, tk.END); self.python_entry.insert(0, path)

    # ── CLI 生命周期 ─────────────────────────────────
    def _on_cli_output(self, text: str):
        self.cli_log.enqueue(text, LogFormatter.classify_tag(text))

    def _on_cli_done(self, code: int):
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        
        # 如果是用户主动停止的，直接跳过结束日志的打印
        if not self._cli_stopped_by_user:
            self.cli_log.append("\u2550" * 56)
            self.cli_log.append(
                "处理完成" if code == 0 else f"异常退出 (code: {code})", 
                "success" if code == 0 else "error"
            )
        
        self._cli_stopped_by_user = False

    def _start_cli(self):
        raw_dir, text_dir = self.raw_entry.get().strip(), self.text_entry.get().strip()
        if not raw_dir or not Path(raw_dir).is_dir(): return self.cli_log.append("错误: 请选择有效的生肉目录", "error")
        if not text_dir or not Path(text_dir).is_dir(): return self.cli_log.append("错误: 请选择有效的熟肉目录", "error")
        
        cli_path = Path(__file__).resolve().parent / "cli.py"
        if not cli_path.exists(): return self.cli_log.append(f"错误: 未找到 cli.py ({cli_path})", "error")

        cmd = [sys.executable, str(cli_path), raw_dir, text_dir, "--automatch", str(self.automatch_var.get()).lower(), "--thumbnails"]
        self.cli_log.append(f"命令: {' '.join(cmd)}", "dim")
        self.cli_log.append("\u2500" * 56)
        
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.cli_runner.run(cmd, Path(__file__).resolve().parent)

    def _stop_cli(self):
        self._cli_stopped_by_user = True
        self.cli_runner.terminate()
        self.cli_log.append("已停止运行", "error")

    # ── GUI 服务生命周期 ─────────────────────────────────
    def _on_gui_output(self, text: str):
        self.gui_log.enqueue(text, LogFormatter.classify_tag(text))

    def _on_gui_done(self, code: int):
        self.gui_start_btn.configure(state=tk.NORMAL)
        self.gui_stop_btn.configure(state=tk.DISABLED)
        if not self._gui_stopped_by_user:
            self.gui_log.append("\u2550" * 56)
            self.gui_log.append("服务已停止" if code == 0 else f"异常退出 (code: {code})", "success" if code == 0 else "error")
        self._gui_stopped_by_user = False

    def _start_gui(self):
        host = self.gui_host_entry.get().strip() or "127.0.0.1"
        port = self.gui_port_entry.get().strip() or "8080"
        if not port.isdigit(): return self.gui_log.append("错误: 端口必须为整数", "error")
        
        gui_path = Path(__file__).resolve().parent / "web.py"
        if not gui_path.exists(): return self.gui_log.append(f"错误: 未找到 web.py ({gui_path})", "error")

        self.gui_log.append(f"启动服务: Host={host}, Port={port}", "dim")
        self.gui_start_btn.configure(state=tk.DISABLED)
        self.gui_stop_btn.configure(state=tk.NORMAL)
        
        # 修改环境变量并启动
        env_backup = os.environ.copy()
        os.environ["GUI_HOST"], os.environ["GUI_PORT"] = host, port
        self.gui_runner.run([sys.executable, str(gui_path)], Path(__file__).resolve().parent)
        os.environ.clear(); os.environ.update(env_backup) # 恢复环境变量隔离

    def _stop_gui(self):
        self._gui_stopped_by_user = True
        self.gui_runner.terminate()
        self.gui_log.append("服务已停止", "success")

    # ── 资源与依赖管理 ─────────────────────────────────
    def _check_resources(self):
        self.config_log.clear()
        self.config_log.append("正在检查资源文件...\n")
        dl = ResourceDownloader()
        data_root = ResourceDownloader.get_data_root()
        all_ok = True
        
        for rel_path, (remote_name, _) in dl.get_required_files().items():
            if (data_root / rel_path).exists():
                self.config_log.append(f"{remote_name} ✔\n", "ok")
            else:
                all_ok = False
                self.config_log.append(f"{remote_name} ❌\n", "missing")
                
        if all_ok:
            self.config_log.append("\n所有资源文件已就绪\n", "success")
        else:
            self.config_log.append("\n存在缺失的资源文件，请点击“下载资源”按钮进行下载\n", "error")


    def _download_resources(self):
        dl = ResourceDownloader()
        if not (missing := dl.scan_missing()): return self.config_log.append("所有资源文件已就绪\n", "success")
        self._show_download_dialog(missing, dl, exit_on_cancel=False, on_finish=self._check_resources)

    def _check_and_install_deps(self):
        req_path = Path(__file__).resolve().parent / "requirements.txt"
        python_path = self.python_entry.get().strip() or sys.executable
        self.config_log.clear()
        self.config_log.append(f"Python 解释器: {python_path}\n正在检查依赖...\n")
        if not req_path.exists(): return self.config_log.append("未找到 requirements.txt\n", "error")

        missing_specs = []
        for line in req_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            if match := re.match(r'^([a-zA-Z0-9_-]+)', line):
                pkg_name = match.group(1).lower()
                import_name = self.PACKAGE_NAME_MAP.get(pkg_name, pkg_name.replace("-", "_"))
                if importlib.util.find_spec(import_name) is None:
                    missing_specs.append(line)
                    self.config_log.append(f"缺失: {line}\n", "error")
        
        if not missing_specs: return self.config_log.append("所有依赖已安装\n", "success")
        
        mirror_url = self.MIRRORS.get(self.mirror_combo.get(), "")
        self.config_log.append(f"\n开始安装 {len(missing_specs)} 个依赖库...\n")
        if mirror_url: self.config_log.append(f"使用镜像源: {self.mirror_combo.get()}\n", "info")
        
        self.check_dep_btn.configure(state=tk.DISABLED)
        def run_install():
            cmd = [python_path, '-m', 'pip', 'install'] + (['-i', mirror_url] if mirror_url else []) + missing_specs
            env = os.environ.copy(); env['PYTHONUNBUFFERED'] = '1'
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, bufsize=1)
                for line in iter(p.stdout.readline, ''): 
                    if line.strip(): self.after(0, lambda l=line: self.config_log.append(l))
                p.wait()
                self.after(0, lambda: self.config_log.append("依赖安装完成\n" if p.returncode == 0 else f"安装失败，退出码: {p.returncode}\n", "success" if p.returncode == 0 else "error"))
            except Exception as e: self.after(0, lambda: self.config_log.append(f"错误: {e}\n", "error"))
            finally: self.after(0, lambda: self.check_dep_btn.configure(state=tk.NORMAL))
        threading.Thread(target=run_install, daemon=True).start()

    # ── 弹窗与下载控制 ─────────────────────────────────
    def _show_download_dialog(self, missing_files, downloader: ResourceDownloader, exit_on_cancel=True, on_finish=None):
        dlg = tk.Toplevel(self); dlg.title("下载资源文件"); dlg.resizable(False, False); dlg.transient(self)
        dlg_width, dlg_height = 450, 200
        # 居中计算逻辑
        x, y = (self.winfo_screenwidth() - dlg_width) // 2, (self.winfo_screenheight() - dlg_height) // 2
        dlg.geometry(f"{dlg_width}x{dlg_height}+{x}+{y}"); dlg.grab_set()

        progress_var, status_var = tk.DoubleVar(), tk.StringVar(value="准备下载...")
        ttk.Progressbar(dlg, variable=progress_var, maximum=100, length=400).pack(pady=(20, 10))
        ttk.Label(dlg, textvariable=status_var, wraplength=380).pack(pady=(0, 10))
        
        def on_close():
            dlg.destroy()
            if exit_on_cancel: self.destroy()
            elif on_finish: self.after(0, on_finish)
            
        ttk.Button(dlg, text="取消", command=on_close).pack(pady=(0, 10))
        dlg.protocol("WM_DELETE_WINDOW", on_close)

        def task():
            try:
                downloader.progress_callback = lambda p, m: self.after(0, lambda: (progress_var.set(p), status_var.set(m)))
                downloader.download_all(missing_files)
                self.after(0, dlg.destroy)
                self.after(0, lambda: tk.messagebox.showinfo("完成", "资源下载完成"))
            except Exception as exc: self.after(0, lambda: self._handle_dl_failure(exc, missing_files, downloader, exit_on_cancel, on_finish))
            finally:
                if on_finish: self.after(0, on_finish)
        threading.Thread(target=task, daemon=True).start()

    def _handle_dl_failure(self, error, missing_files, downloader, exit_on_cancel, on_finish):
        if tk.messagebox.askretrycancel("下载失败", f"下载过程出错: {error}\n\n是否重试？"):
            self._show_download_dialog(missing_files, downloader, exit_on_cancel, on_finish)
        elif exit_on_cancel:
            tk.messagebox.showwarning("资源缺失", "程序缺少必要资源，将退出。"); self.destroy()
        elif on_finish: self.after(0, on_finish)

    # ── 关闭事件 ─────────────────────────────────
    def _on_close(self):
        self.cli_runner.terminate()
        self.gui_runner.terminate()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
