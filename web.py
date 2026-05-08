import json
import base64
import asyncio
import concurrent.futures
import multiprocessing
import atexit
import hashlib
import io
import os
from pathlib import Path
from typing import Optional, Dict, Any, List

from nicegui import ui, app, run, events
from fastapi import Request
from PIL import Image
from natsort import natsorted
from loguru import logger

from core.matching import match_images
from core.inpainting import Inpainter
from core.config import SUPPORTED_EXTENSIONS, DirPaths, DataPaths, InpaintAlgorithm, SVG_ICONS
from cli import MangaTransFerPipeline

# ==================== 全局设置 ====================
multiprocessing.set_start_method('spawn', force=True)

_process_pool: Optional[concurrent.futures.ProcessPoolExecutor] = None

def get_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    """延迟创建进程池，确保在需要时才初始化"""
    global _process_pool
    if _process_pool is None:
        _process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
        atexit.register(_process_pool.shutdown)
    return _process_pool

final_matches = None

async def run_inpainter_in_process(img_bytes: bytes, mask_bytes: bytes, algorithm: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        get_process_pool(),
        run_inpainter_sync,
        img_bytes, mask_bytes, algorithm
    )

async def run_rect_inpainter_in_process(img_bytes: bytes, x: int, y: int, w: int, h: int, algorithm: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        get_process_pool(),
        sync_rect_inpaint_worker,
        img_bytes, x, y, w, h, algorithm
    )

# ==================== 辅助函数 ====================
def run_inpainter_sync(img_bytes: bytes, mask_bytes: bytes, algorithm: str) -> bytes:
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as temp_dir:
        img_path = os.path.join(temp_dir, 'image.png')
        mask_path = os.path.join(temp_dir, 'mask.png')
        out_path = os.path.join(temp_dir, 'result.png')
        with open(img_path, 'wb') as f:
            f.write(img_bytes)
        with open(mask_path, 'wb') as f:
            f.write(mask_bytes)
        Inpainter(
            img_path=img_path,
            mask_path=mask_path,
            output_path=out_path,
            algorithm=algorithm,
            debug=False,
            do_dilate=True
        )
        with open(out_path, 'rb') as f:
            return f.read()

def sync_rect_inpaint_worker(img_bytes: bytes, x: int, y: int, w: int, h: int, backend_algo: str) -> bytes:
    import io
    import os
    import tempfile
    import numpy as np
    import cv2
    from PIL import Image
    from core.inpainting import Inpainter
    from core.textblock_mask import connected_canny_flood

    pil_img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    open_cv_image = np.array(pil_img)[:, :, ::-1].copy()
    h_img, w_img = open_cv_image.shape[:2]
    x = max(0, min(x, w_img - 1))
    y = max(0, min(y, h_img - 1))
    w = min(w, w_img - x)
    h = min(h, h_img - y)
    if w <= 5 or h <= 5:
        return img_bytes
    roi = open_cv_image[y:y+h, x:x+w]
    try:
        text_mask, _, _ = connected_canny_flood(roi, show_process=False, inpaint_sdthresh=10)
    except Exception as e:
        logger.error(f"connected_canny_flood 出错: {e}")
        return img_bytes
    if text_mask is None or np.sum(text_mask) == 0:
        return img_bytes
    mask_pil = Image.fromarray(text_mask).convert('L')
    with tempfile.TemporaryDirectory() as temp_dir:
        roi_path = os.path.join(temp_dir, 'roi.png')
        mask_path = os.path.join(temp_dir, 'mask.png')
        out_path = os.path.join(temp_dir, 'result.png')
        roi_pil = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
        roi_pil.save(roi_path)
        mask_pil.save(mask_path)
        try:
            Inpainter(
                img_path=roi_path,
                mask_path=mask_path,
                output_path=out_path,
                algorithm=backend_algo,
                debug=False,
                do_dilate=True
            )
        except Exception as e:
            logger.error(f"Inpainter 出错: {e}")
            return img_bytes
        repaired_roi = cv2.imread(out_path)
        if repaired_roi is None:
            return img_bytes
    open_cv_image[y:y+h, x:x+w] = repaired_roi
    result_pil = Image.fromarray(cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2RGB))
    result_bytes_io = io.BytesIO()
    result_pil.save(result_bytes_io, format='PNG')
    return result_bytes_io.getvalue()

def generate_text_blocks(directory: str, page_key: str, entries: List[Dict]) -> List[Dict]:
    """生成文本块数据，返回包含 base64 图片的列表（保持原样）"""
    text_dir = Path(directory) / 'temp' / 'text'
    if not text_dir.exists():
        return []
    text_img_path = None
    for ext in IMAGE_EXTS:
        potential = text_dir / f"{page_key}{ext}"
        if potential.exists():
            text_img_path = potential
            break
    if not text_img_path:
        return []
    text_blocks = []
    try:
        with Image.open(text_img_path) as text_img:
            for idx, entry in enumerate(entries):
                try:
                    matched = entry.get('matched', 0)
                    orig_xyxy = entry.get('orig_xyxy')
                    xyxy = entry.get('xyxy')

                    if not orig_xyxy or len(orig_xyxy) != 4:
                        continue

                    crop_box = (orig_xyxy[0], orig_xyxy[1], orig_xyxy[2], orig_xyxy[3])
                    if (crop_box[0] < 0 or crop_box[1] < 0 or
                        crop_box[2] > text_img.width or crop_box[3] > text_img.height):
                        continue

                    cropped = text_img.crop(crop_box)
                    buffer = io.BytesIO()
                    cropped.save(buffer, format='PNG')
                    img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                    img_url = f'data:image/png;base64,{img_base64}'

                    if not xyxy or len(xyxy) != 4:
                        left, top, right, bottom = orig_xyxy
                        width = right - left
                        height = bottom - top
                        visible = False
                    else:
                        left, top, right, bottom = xyxy
                        width = right - left
                        height = bottom - top
                        visible = (matched == 1)

                    text_blocks.append({
                        'imageUrl': img_url,
                        'left': left,
                        'top': top,
                        'width': width,
                        'height': height,
                        'visible': visible
                    })
                except Exception as e:
                    logger.warning(f"处理 entry {idx} 时出错: {e}")
                    continue
    except Exception as e:
        logger.error(f"打开 text 图片失败: {e}")
        return []
    return text_blocks

def mount_static_directory(directory: Path, prefix: str) -> str:
    """挂载目录到静态路由，返回访问 URL 前缀"""
    if not hasattr(app, '_mounted_dirs'):
        app._mounted_dirs = {}
    dir_hash = hashlib.md5(str(directory.resolve()).encode()).hexdigest()[:8]
    mount_path = f"/{prefix}_{dir_hash}"
    if mount_path not in app._mounted_dirs:
        app.add_static_files(mount_path, str(directory))
        app._mounted_dirs[mount_path] = directory
    return mount_path

def get_project_data(directory: str, pages: Dict[str, Any], current_img: str) -> Dict[str, Any]:
    """获取项目数据，供前端加载"""
    directory_path = Path(directory)
    current_img_stem = Path(current_img).stem # 强制去除可能存在的扩展名
    
    # 查找原始图片
    img_filename = None
    for ext in IMAGE_EXTS:
        potential = directory_path / f"{current_img_stem}{ext}"
        if potential.exists():
            img_filename = potential.name
            break
            
    if not img_filename:
        raise Exception(f'Image not found for {current_img_stem} in {directory}')
        
    # 挂载原始图片目录
    raw_mount = mount_static_directory(directory_path, 'raw')
    image_url = f"{raw_mount}/{img_filename}"
    
    # 挂载 inpainted 目录
    inpainted_dir = directory_path / 'inpainted'
    inpainted_dir.mkdir(parents=True, exist_ok=True)
    inpainted_mount = mount_static_directory(inpainted_dir, 'inpainted')
    inpainted_url = None
    for ext in IMAGE_EXTS:
        potential = inpainted_dir / f"{current_img_stem}{ext}"
        if potential.exists():
            mtime = int(potential.stat().st_mtime)
            inpainted_url = f"{inpainted_mount}/{current_img_stem}{ext}?t={mtime}"
            break
            
    # 处理缩略图（异步生成缺失的缩略图）
    thumb_dir = directory_path / DirPaths.THUMBS
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_mount = mount_static_directory(thumb_dir, 'thumbs')
    thumbnails = []
    for key in pages.keys():
        # 查找原始图片路径
        raw_path = None
        for ext in IMAGE_EXTS:
            p = directory_path / f"{key}{ext}"
            if p.exists():
                raw_path = p
                break
        if not raw_path:
            continue
            
        thumb_filename = f"thumb_raw_{key}.jpg"
        thumb_path = thumb_dir / thumb_filename
        thumb_url = f"{thumb_mount}/{thumb_filename}"
        
        if not thumb_path.exists():
            # 异步生成缩略图，避免阻塞
            async def generate_thumb(path: Path, thumb: Path, url: str):
                try:
                    def _gen():
                        with Image.open(path) as img:
                            w, h = img.size
                            new_h = 150
                            new_w = int(w * new_h / h)
                            img.thumbnail((new_w, new_h), Image.Resampling.LANCZOS)
                            if img.mode in ('RGBA', 'LA', 'P'):
                                background = Image.new('RGB', img.size, (255, 255, 255))
                                if img.mode == 'P':
                                    img = img.convert('RGBA')
                                if img.mode == 'RGBA':
                                    background.paste(img, mask=img.split()[-1])
                                else:
                                    background.paste(img)
                                img = background
                            elif img.mode != 'RGB':
                                img = img.convert('RGB')
                            img.save(thumb, 'JPEG', quality=85)
                    await run.io_bound(_gen)
                except Exception as e:
                    logger.error(f"生成缩略图失败 {key}: {e}")
            asyncio.create_task(generate_thumb(raw_path, thumb_path, thumb_url))
            
        thumbnails.append({'key': key, 'thumb_url': thumb_url})

    # ========== 修改点：兼容新格式的 entries 提取 ==========
    page_data = pages.get(current_img_stem, [])
    if isinstance(page_data, dict):
        current_entries = page_data.get('entries', [])
    else:
        current_entries = page_data
    # ========== 修改点结束 ==========

    text_blocks = generate_text_blocks(directory, current_img_stem, current_entries)
    
    return {
        'directory': str(directory_path),
        'imageUrl': image_url,
        'inpaintedImageUrl': inpainted_url,
        'pages': pages,
        'regions': current_entries,
        'thumbnails': thumbnails,
        'textBlocks': text_blocks,
        'current_img': current_img_stem
    }

# ==================== UI 页面定义 ====================
ui.page_title('漫画翻译移植')
app.add_static_files('/static', 'static')

current_algorithm = InpaintAlgorithm.PATCHMATCH

ui.add_head_html('<link rel="stylesheet" href="/static/styles.css">')
ui.add_head_html('<script src="/static/fabric.min.js"></script>')
ui.add_head_html('<script src="/static/view.js"></script>')
ui.add_head_html('<script src="/static/canvas.js"></script>')
ui.add_head_html(f'''
<script>
    window.ALGO_PATCHMATCH = "{InpaintAlgorithm.PATCHMATCH}";
    window.ALGO_LAMA = "{InpaintAlgorithm.LAMA}";
</script>
''')

IMAGE_EXTS = list(SUPPORTED_EXTENSIONS) + [ext.upper() for ext in SUPPORTED_EXTENSIONS]

def show_new_project_dialog():
    with ui.dialog().props('persistent') as dialog, ui.card().style('min-width: 400px;'):
        ui.label('新建项目').classes('text-h6 w-full text-center')
        with ui.column().classes('w-full gap-4 p-4'):
            with ui.row().classes('w-full items-center gap-2'):
                ui.label('原图:').classes('w-10 text-right')
                raw_input = ui.input(placeholder='输入原始图片目录路径')\
                    .classes('flex-grow')\
                    .props('id="raw-dir-input"')
                async def pick_raw():
                    picker = local_dir_picker(raw_input.value or '.')
                    path = await picker
                    if path:
                        raw_input.value = path
                ui.button('浏览', on_click=pick_raw).props('flat dense').classes('h-9 px-3')
            with ui.row().classes('w-full items-center gap-2'):
                ui.label('文本:').classes('w-10 text-right')
                text_input = ui.input(placeholder='输入文本图片目录路径')\
                    .classes('flex-grow')\
                    .props('id="text-dir-input"')
                async def pick_text():
                    picker = local_dir_picker(text_input.value or '.')
                    path = await picker
                    if path:
                        text_input.value = path
                ui.button('浏览', on_click=pick_text).props('flat dense').classes('h-9 px-3')
            with ui.row().classes('w-full justify-center gap-4 mt-4'):
                async def on_start():
                    # 原始输入去掉首尾空格
                    raw_val = raw_input.value.strip()
                    text_val = text_input.value.strip()
                    if not raw_val or not text_val:
                        ui.notify('请填写原图和文本图片目录路径', type='warning', position='top')
                        return

                    # ★ 强制转换为正斜杠路径 ★
                    raw_dir = Path(raw_val).as_posix()
                    text_dir = Path(text_val).as_posix()

                    # 模型路径同样处理
                    resnet_path = DataPaths.RESNET18
                    if resnet_path is None:
                        ui.notify('ResNet 模型文件缺失', type='negative', position='top')
                        return
                    match_model_path = Path(resnet_path).as_posix()
                    if not Path(match_model_path).exists():
                        ui.notify(f'匹配模型不存在: {match_model_path}', type='negative', position='top')
                        return

                    dialog.close()
                    loading_dialog.open()

                    try:
                        matches = await run.io_bound(
                            match_images,
                            raw_dir=raw_dir,
                            text_dir=text_dir,
                            model_weights_path=match_model_path,
                            generate_thumbnails=True
                        )
                        if not matches:
                            ui.notify('图片匹配失败，未获得任何匹配结果', type='negative', position='top')
                            loading_dialog.close()
                            return

                        sorted_matches = natsorted(matches, key=lambda x: Path(x['raw_path']).name)
                        loading_dialog.close()
                        await show_match_result_panel(sorted_matches, raw_dir, text_dir)
                    except Exception as e:
                        loading_dialog.close()
                        ui.notify(f'匹配过程出错: {str(e)}', type='negative', position='top')

                ui.button('取消', on_click=dialog.close).props('outline').classes('w-24')
                ui.button('开始', on_click=on_start).props('outline').classes('w-24')
    dialog.open()

async def show_match_result_panel(matches, raw_dir, text_dir):
    global final_matches
    final_matches = matches
    app.storage.general['final_matches'] = matches

    # 挂载缩略图静态目录
    thumb_dir = Path(raw_dir) / DirPaths.THUMBS
    if not hasattr(app, '_thumb_dirs'):
        app._thumb_dirs = set()
    if str(thumb_dir) not in app._thumb_dirs:
        app.add_static_files('/thumbs', str(thumb_dir))
        app._thumb_dirs.add(str(thumb_dir))

    with ui.dialog(value=True).props('persistent') as result_dialog, ui.card().style('width: 85vw; max-width: 1200px; max-height: 85vh;').props('id="match-result-dialog"'):
        with ui.scroll_area().classes('match-scroll-area').style('height: calc(85vh - 160px); width: 100%;'):
            with ui.row().classes('w-full flex-nowrap items-center p-2 bg-gray-100 sticky top-0 z-10'):
                ui.label('原始图片').classes('w-1/3 text-center font-bold')
                ui.label('文本图片').classes('w-1/3 text-center font-bold')
                ui.label('相似度').classes('w-1/3 text-center font-bold')
            matches_container = ui.column().classes('w-full')
        with ui.row().classes('w-full justify-center gap-4 mt-4'):
            ui.button('取消', on_click=result_dialog.close).props('outline').classes('w-28')
            continue_btn = ui.button('继续', color='primary').classes('w-28')

    BATCH_SIZE = 15
    for i in range(0, len(matches), BATCH_SIZE):
        batch = matches[i:i+BATCH_SIZE]
        for match in batch:
            raw_thumb_name = Path(match.get('raw_thumbnail_path', '')).name
            text_thumb_name = Path(match.get('text_thumbnail_path', '')).name
            
            raw_thumb_url = None
            if raw_thumb_name:
                raw_thumb_path = thumb_dir / raw_thumb_name
                if raw_thumb_path.exists():
                    t = int(raw_thumb_path.stat().st_mtime)
                    raw_thumb_url = f'/thumbs/{raw_thumb_name}?t={t}'

            text_thumb_url = None
            if text_thumb_name:
                text_thumb_path = thumb_dir / text_thumb_name
                if text_thumb_path.exists():
                    t = int(text_thumb_path.stat().st_mtime)
                    text_thumb_url = f'/thumbs/{text_thumb_name}?t={t}'

            raw_path = match['raw_path']
            original_text_path = match['text_path']

            with matches_container:
                with ui.row().classes('w-full flex-nowrap items-stretch p-3 border-b hover:bg-gray-50').props(f'data-raw-path="{raw_path}" data-original-text-path="{original_text_path}"'):
                    with ui.column().classes('w-1/3 items-center gap-1'):
                        if raw_thumb_url:
                            ui.html(f'<img src="{raw_thumb_url}" style="width: auto; height: 150px;" />')
                        else:
                            ui.label('缩略图缺失').style('height: 150px;')
                        ui.label(Path(match['raw_path']).name).classes('text-xs text-gray-600 text-center w-full break-all')
                    with ui.column().classes('w-1/3 items-center gap-1 text-col'):
                        if text_thumb_url:
                            wrapper = ui.html(f'''
                                <div class="thumb-wrapper" style="position: relative; display: inline-block;">
                                    <img src="{text_thumb_url}" style="width: auto; height: 150px; display: block;" />
                                    <div class="del-btn" style="position: absolute; top: 0; right: 0; width: 36px; height: 36px; background-color: #f08080; clip-path: polygon(100% 0, 0 0, 100% 100%); display: none; align-items: center; justify-content: center; cursor: pointer;">
                                        <i class="material-icons" style="color: white; font-size: 18px; position: relative; left: 8px; top: -8px;">close</i>
                                    </div>
                                </div>
                            ''')
                        else:
                            ui.label('缩略图缺失').style('height: 150px;')
                        ui.label(Path(match['text_path']).name).classes('text-xs text-gray-600 text-center w-full break-all')
                    with ui.column().classes('w-1/3 items-center justify-center'):
                        sim = match['similarity']
                        color = 'text-green-600' if sim >= 0.8 else 'text-orange-600' if sim >= 0.6 else 'text-red-600'
                        ui.label(f'{sim:.4f}').classes(f'text-lg font-bold {color}')
        await asyncio.sleep(0)
    try:
        thumb_dir = Path(raw_dir) / DirPaths.THUMBS
        await ui.run_javascript(f'window.initMatchResultPanel("{text_dir}", "{thumb_dir.as_posix()}");')
    except TimeoutError:
        # 前端可能未及时响应，但脚本通常已成功执行
        pass

    # 显式复制变量避免闭包引用问题
    _raw_dir = raw_dir
    _text_dir = text_dir

    async def on_continue():
        result_dialog.close()
        try:
            current_matches = app.storage.general.get('final_matches', final_matches)
            if not current_matches:
                ui.notify('匹配数据丢失，请重新匹配', type='negative', position='top')
                return
            
            backend_algorithm = InpaintAlgorithm.PATCHMATCH
            pipeline = MangaTransFerPipeline(
                raw_dir=_raw_dir,
                text_dir=_text_dir,
                model_path=str(DataPaths.COMIC_TEXT_DETECTOR),
                inpaint_algorithm=backend_algorithm,
                output_dir=None,
                automatch=False,
                generate_thumbnails=False,
                precomputed_matches=current_matches
            )
            
            loading_dialog.open()
            try:
                await run.io_bound(pipeline.run)
                json_path = Path(_raw_dir) / 'match_results.json'
                if json_path.exists():
                    with open(json_path, 'r', encoding='utf-8') as f:
                        project_data = json.load(f)
                    ui.run_javascript(f'window.loadProjectFromData({json.dumps(project_data)});')
                    ui.notify('处理完成，项目已加载', type='positive', position='top')
                else:
                    ui.notify('处理完成，但未找到 match_results.json', type='warning', position='top')
            except Exception as e:
                ui.notify(f'处理失败: {str(e)}', type='negative', position='top')
            finally:
                loading_dialog.close()
        except Exception as e:
            ui.notify(f'启动流水线失败: {str(e)}', type='negative', position='top')

    continue_btn.on('click', on_continue)

    # 为 text_dir 挂载静态路由（使用哈希避免冲突）
    text_mount = mount_static_directory(Path(_text_dir), 'text_original')
    # 将挂载路径传递给前端
    await ui.run_javascript(f'window.textOriginalMount = "{text_mount}";')

loading_dialog = ui.dialog().props('persistent').classes('bg-transparent')
with loading_dialog, ui.card().classes('items-center justify-center').style('width: 300px; height: 200px;'):
    ui.spinner(size='lg')
    ui.label('处理中...请稍候').classes('text-lg mt-4')

with ui.element('div').classes('fixed top-0 left-0 w-full h-full').style('margin:0; padding:0; overflow:hidden;'):
    with ui.element('div').style('height:100%; margin:0; padding:0; display:flex; width:100%;'):
        with ui.element('div').style('width:10%; min-width:200px; flex-shrink:0; margin:0; padding:0; display:flex; flex-direction:column;').props('name="left-sidebar"'):
            ui.label('MangaTransFer').classes('bg-white').style(
                'height:40px; flex-shrink:0; margin:0; padding:0 12px; '
                'display:flex; align-items:center; justify-content:center; font-weight:bold; font-size:20px; '
                'border-bottom:1px solid #E0E0E0; box-sizing:border-box;'
            ).props('name="title-bar"')
            thumbnail_scroll = ui.element('div').classes('modern-scrollbar').style('height:calc(100% - 40px); margin:0; padding:0; overflow-y:auto; background-color:#F0F2F5;').props('id="thumbnail-list" name="thumbnail-list"')
        with ui.element('div').style('width:80%; min-width:100px; margin:0; padding:0; background-color:#ffffff; display:flex; flex-direction:column; border-left:1px solid #E0E0E0; border-right:1px solid #E0E0E0;').props('name="main-container"'):
            with ui.element('div').style('height:calc(100% - 40px); width:100%; margin:0; padding:0; display:flex;').props('name="canvas-toolbar-container"'):
                with ui.element('div').classes('bg-white').style('width:60px; flex-shrink:0; margin:0; padding:0; display:flex; flex-direction:column; align-items:center; border-right:1px solid #E0E0E0;').props('name="toolbar"'):
                    with ui.element('div').style('width:100%; display:flex; flex-direction:column; align-items:center; padding-top:20px; gap:5px;'):
                        drag_button = ui.button(icon='open_with', color='transparent') \
                            .classes('tool-button') \
                            .props('flat data-tool="drag"') \
                            .on('click', lambda: ui.run_javascript('window.canvasControls.switchTool("drag")')) \
                            .on('mouseover', lambda e: e.sender.classes(add='hover')) \
                            .on('mouseout', lambda e: e.sender.classes(remove='hover'))
                        brush_button = ui.button(icon='brush', color='transparent') \
                            .classes('tool-button') \
                            .props('flat data-tool="brush"') \
                            .on('click', lambda: ui.run_javascript('window.canvasControls.switchTool("brush")')) \
                            .on('mouseover', lambda e: e.sender.classes(add='hover')) \
                            .on('mouseout', lambda e: e.sender.classes(remove='hover'))
                        restore_button = ui.button(icon='healing', color='transparent') \
                            .classes('tool-button') \
                            .props('flat data-tool="restoreBrush"') \
                            .on('click', lambda: ui.run_javascript('window.canvasControls.switchTool("restoreBrush")')) \
                            .on('mouseover', lambda e: e.sender.classes(add='hover')) \
                            .on('mouseout', lambda e: e.sender.classes(remove='hover'))
                        rect_button = ui.button(icon='check_box_outline_blank', color='transparent') \
                            .classes('tool-button') \
                            .props('flat data-tool="rect"') \
                            .on('click', lambda: ui.run_javascript('window.canvasControls.switchTool("rect")')) \
                            .on('mouseover', lambda e: e.sender.classes(add='hover')) \
                            .on('mouseout', lambda e: e.sender.classes(remove='hover'))
                with ui.element('div').style('flex:1; min-width:0; margin:0; padding:0; display:flex; flex-direction:column;').props('name="canvas-area"'):
                    with ui.element('div').classes('bg-white').style('height:40px; flex-shrink:0; margin:0; padding:0 12px; border-bottom:1px solid #E0E0E0; display:flex; align-items:center; box-sizing:border-box;').props('name="controls-bar"'):
                        with ui.element('div').style('display: flex; align-items: center; gap: 10px;'):
                            zoom_in_btn = ui.button(icon='zoom_in', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.canvasControls.zoomIn()'))
                            zoom_out_btn = ui.button(icon='zoom_out', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.canvasControls.zoomOut()'))
                            reset_btn = ui.button(icon='crop_free', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.canvasControls.resetZoom()'))
                            split_btn = ui.button(color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.canvasControls.toggleWorkingReference()'))           
                            with split_btn:
                                ui.html(f'<img src="{SVG_ICONS["view_column_2"]}" style="width:24px;height:24px;">')
                            new_btn = ui.button(icon='o_create_new_folder', color='transparent') \
                                .props('flat dense') \
                                .on('click', show_new_project_dialog)
                            load_btn = ui.button(icon='folder_open', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.selectProjectFile()'))
                            save_btn = ui.button(icon='o_save', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.saveProject()'))
                            prev_btn = ui.button(icon='navigate_before', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.goToPrevPage()'))
                            next_btn = ui.button(icon='navigate_next', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.goToNextPage()'))
                        with ui.element('div').style('margin-left: auto; display: flex; align-items: center;'):
                            algorithm_label = ui.label(InpaintAlgorithm.PATCHMATCH) \
                                .style('padding:4px 12px; min-width: 100px;text-align: center; background:#f0f0f0; border-radius:4px; cursor:pointer; font-size:14px;') \
                                .props('id="algorithm-selector"')
                            def toggle_algorithm():
                                global current_algorithm
                                if current_algorithm == InpaintAlgorithm.PATCHMATCH:
                                    current_algorithm = InpaintAlgorithm.LAMA
                                else:
                                    current_algorithm = InpaintAlgorithm.PATCHMATCH
                                ui.run_javascript(f'window.updateAlgorithmLabel("{current_algorithm}");')
                            algorithm_label.on('click', toggle_algorithm)
                    with ui.element('div').style('height:calc(100% - 40px); margin:0; padding:0; display:flex;').props('name="canvas"'):
                        with ui.element('div').props('id="canvas-container"').style('flex:1; min-width:0; height:100%; position:relative; overflow:hidden;'):
                            ui.element('canvas').props('id="comic-canvas"').style('width:100%; height:100%; display:block;')

                        with ui.element('div').style('width:40px; height:100%; background-color:#ffffff; border-left:1px solid #E0E0E0; display:flex; flex-direction:column; align-items:center; padding-top:10px; gap:10px;').props('id="right-button-bar"'):
                            # 插入文本
                            with ui.button(color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.canvasControls.insertTextBlock()')):
                                ui.html(f'<img src="{SVG_ICONS["insert_text"]}" style="width:24px;height:24px;">')
                            # 选择字体
                            with ui.button(color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.selectFontFamily()')):
                                ui.html(f'<img src="{SVG_ICONS["brand_family"]}" style="width:24px;height:24px;">')
                            # 字号增大
                            ui.button(icon='text_increase', color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.increaseFontSize()'))
                            # 字号减小
                            ui.button(icon='text_decrease', color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.decreaseFontSize()'))
                            # 加粗
                            ui.button(icon='format_bold', color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.toggleFontBold()'))
                            # 斜体
                            ui.button(icon='format_italic', color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.toggleItalic()'))
                            # 字体颜色
                            with ui.button(color='transparent').props('flat dense id="font-color-btn"').on('click', lambda: ui.run_javascript('window.chooseTextColor()')):
                                ui.html(f'<img src="{SVG_ICONS["format_color_text"]}" style="width:24px;height:24px;">')
                            # 行距
                            ui.button(icon='format_line_spacing', color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.setLineSpacing()'))
                            # 字间距
                            with ui.button(color='transparent').props('flat dense').on('click', lambda: ui.run_javascript('window.setLetterSpacing()')):
                                ui.html(f'<img src="{SVG_ICONS["format_letter_spacing_2"]}" style="width:24px;height:24px;">')
                            # 竖排
                            with ui.button(color='transparent').props('flat dense id="vertical-text-btn"').on('click', lambda: ui.run_javascript('window.setTextDirectionVertical()')):
                                ui.html(f'<img src="{SVG_ICONS["format_textdirection_on_vertical"]}" style="width:24px;height:24px;">')
                                  
            with ui.element('div').classes('bg-white').style('height:40px; flex-shrink:0; margin:0; padding:0; width:100%; display:flex; align-items:center; border-top:1px solid #E0E0E0; box-sizing:border-box;').props('name="zoom-bar"'):
                with ui.element('div').style('width:33%; height:100%; display:flex; align-items:center; justify-content:center; padding:0 10px;'):
                    with ui.element('div').style('display:flex; align-items:center; gap:10px; width:100%;'):
                        ui.label('笔刷').style('min-width: 40px; color:#202124;')
                        brush_slider = ui.slider(min=1, max=200, value=20).style('flex-grow:1; min-width:100px;')
                        brush_value = ui.label('20px').style('min-width: 40px; color:#202124;')
                        def update_brush_size(e):
                            size = int(e.sender.value)
                            brush_value.set_text(f'{size}px')
                            ui.run_javascript(f'window.canvasControls.setBrushSize({size})')
                        brush_slider.on('update:model-value', update_brush_size)
                with ui.element('div').style('width:33%; height:100%; display:flex; align-items:center; justify-content:center; padding:0 10px;'):
                    with ui.element('div').style('display:flex; align-items:center; gap:10px; width:100%;'):
                        ui.label('修复层').style('min-width: 60px; color:#202124;')
                        inpainted_slider = ui.slider(min=0, max=100, value=100).style('flex-grow:1; min-width:100px;')
                        inpainted_value = ui.label('100%').style('min-width: 40px; color:#202124;')
                        def update_inpainted_value(e):
                            value = int(e.sender.value)
                            inpainted_value.set_text(f'{value}%')
                            ui.run_javascript(f'window.canvasControls.setInpaintedOpacity({value})')
                        inpainted_slider.on('update:model-value', update_inpainted_value)
                with ui.element('div').style('width:33%; height:100%; display:flex; align-items:center; justify-content:center; padding:0 10px;'):
                    with ui.element('div').style('display:flex; align-items:center; gap:10px; width:100%;'):
                        ui.label('文本层').style('min-width: 60px; color:#202124;')
                        text_slider = ui.slider(min=0, max=100, value=100).style('flex-grow:1; min-width:100px;')
                        text_value = ui.label('100%').style('min-width: 40px; color:#202124;')
                        def update_text_layer_value(e):
                            value = int(e.sender.value)
                            text_value.set_text(f'{value}%')
                            ui.run_javascript(f'window.canvasControls.setTextLayerOpacity({value})')
                        text_slider.on('update:model-value', update_text_layer_value)
        with ui.element('div').style('width:10%; min-width:100px; flex-shrink:0; margin:0; padding:0; display:flex; flex-direction:column;').props('name="right-sidebar"'):
            with ui.element('div').style('height:100%; margin:0; padding:0; display:flex; flex-direction:column;').props('name="text-list"'):
                with ui.element('div').style('height:40px; flex-shrink:0; margin:0; padding:0 12px; display:flex; align-items:center; background-color:#ffffff; border-bottom:1px solid #E0E0E0; box-sizing:border-box;').props('id="text-block-header"'):
                    ui.label('文本块 (0)').props('id="text-block-count"').style('color:#202124; font-weight:bold; font-size:14px;')
                with ui.element('div').classes('modern-scrollbar').style('height:calc(100% - 40px); margin:0; padding:0; background-color:#F5F7FA; overflow-y:auto;'):
                    with ui.element('div').props('id="text-block-list"').style('padding:8px;'):
                        pass

class local_dir_picker(ui.dialog):
    """本地目录选择器，允许用户导航并选择一个目录"""

    def __init__(self, directory: str, upper_limit: str | None = None):
        super().__init__()
        self.path = Path(directory).expanduser().resolve()
        self.upper_limit = Path(upper_limit).expanduser().resolve() if upper_limit else None
        with self, ui.card().style('min-width: 400px;'):
            self.grid = ui.aggrid({
                'columnDefs': [
                    {'field': 'name', 'headerName': '目录', 'resizable': False}
                ],
                'rowSelection': {
                    'mode': 'singleRow',
                    'enableClickSelection': True,
                    'checkboxes': False,
                },
                'defaultColDef': {
                    'resizable': False,
                    'sortable': False,
                    'filter': False
                },
            }, html_columns=[0]).classes('w-full').style('height: 400px;').on('cellDoubleClicked', self._handle_double_click)
            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=self.close).props('outline')
                ui.button('选择', on_click=self._handle_ok)
        self._update_grid()

    def _update_grid(self):
        row_data = []
        can_go_up = False
        if self.upper_limit is None:
            can_go_up = self.path != self.path.parent
        else:
            can_go_up = self.path != self.upper_limit
        if can_go_up:
            row_data.append({
                'name': '<i class="material-icons" style="font-size:16px;">folder</i> 返回上一级',
                'path': str(self.path.parent)
            })

        try:
            items = [p for p in self.path.glob('*') if p.is_dir()]
        except PermissionError:
            items = []
        items.sort(key=lambda p: p.name.lower())
        for p in items:
            if p == self.path.parent:
                continue
            row_data.append({
                'name': f'<i class="material-icons" style="font-size:16px;">folder</i> {p.name}',
                'path': str(p)
            })

        self.grid.options['rowData'] = row_data
        self.grid.update()

    def _handle_double_click(self, e: events.GenericEventArguments):
        try:
            new_path = Path(e.args['data']['path'])
            if self.upper_limit is not None and new_path == self.upper_limit.parent:
                pass
            self.path = new_path
            self.grid.run_grid_method('deselectAll')
            self._update_grid()
        except Exception as ex:
            logger.error(f"目录跳转失败: {ex}")

    async def _handle_ok(self):
        selected_rows = await self.grid.get_selected_rows()
        if selected_rows:
            selected_path = selected_rows[0]['path']
            self.submit(selected_path)
        else:
            self.submit(str(self.path))

# ==================== 路由处理函数 ====================
@app.post('/get_text_images')
async def get_text_images(request: Request):
    data = await request.json()
    text_dir = data.get('text_dir')
    if not text_dir or not Path(text_dir).exists():
        return {'error': 'text_dir 不存在'}
    IMG_EXTS = set(SUPPORTED_EXTENSIONS) | {ext.upper() for ext in SUPPORTED_EXTENSIONS}
    files = [f for f in Path(text_dir).iterdir() if f.suffix in IMG_EXTS]
    files = natsorted([f.name for f in files])
    return {'files': files}

@app.post('/update_match_text')
async def update_match_text(request: Request):
    try:
        data = await request.json()
        raw_path = data.get('raw_path')
        new_text_path = data.get('new_text_path')
        text_dir = data.get('text_dir')
        
        final_matches = app.storage.general.get('final_matches')
        if final_matches is None:
            return {'error': '没有匹配数据，请重新进行图片匹配'}
        
        # 如果 new_text_path 为空，表示清空匹配
        if not new_text_path:
            for match in final_matches:
                if match['raw_path'] == raw_path:
                    match['text_path'] = ''
                    match['text_thumbnail_path'] = None
                    app.storage.general['final_matches'] = final_matches
                    return {'success': True}
            return {'error': '未找到匹配项'}
        
        # 非空路径才进行安全校验
        if text_dir:
            text_dir_path = Path(text_dir).resolve()
            new_path = Path(new_text_path).resolve()
            if text_dir_path not in new_path.parents and new_path != text_dir_path:
                return {'error': '无效的文件路径'}
        
        for match in final_matches:
            if match['raw_path'] == raw_path:
                match['text_path'] = new_text_path
                match['text_thumbnail_path'] = None
                app.storage.general['final_matches'] = final_matches
                return {'success': True}
        return {'error': '未找到匹配项'}
    except Exception as e:
        logger.exception(f"更新匹配文本失败: {e}")
        return {'error': str(e)}

@app.post('/get_text_thumbnails')
async def get_text_thumbnails(request: Request):
    try:
        data = await request.json()
        thumb_dir = data.get('thumb_dir')
        if not thumb_dir or not Path(thumb_dir).exists():
            return {'error': 'thumb目录不存在'}
        files = [f.name for f in Path(thumb_dir).glob('thumb_text_*')]
        files = natsorted(files)
        return {'files': files}
    except Exception as e:
        logger.error(f"获取缩略图列表失败: {e}")
        return {'error': str(e)}

@app.post('/load_project')
async def load_project(request: Request):
    data = await request.json()
    directory = data.get('directory')
    pages = data.get('pages', {})
    current_img = data.get('current_img')
    if not directory or not current_img or current_img not in pages:
        return {'error': 'Invalid project file'}
    try:
        result = get_project_data(directory, pages, current_img)
        return result
    except Exception as e:
        logger.exception(f"加载项目失败: {e}")
        return {'error': str(e)}

@app.post('/update_current_page')
async def update_current_page(request: Request):
    data = await request.json()
    directory = data.get('directory')
    current_img = data.get('current_img')
    if not directory or not current_img:
        return {'error': '缺少 directory 或 current_img'}
    json_path = Path(directory) / 'match_results.json'
    if not json_path.exists():
        return {'error': '项目文件不存在'}
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            project = json.load(f)
        project['current_img'] = Path(current_img).stem
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(project, f, ensure_ascii=False, separators=(',', ':'))
        return {'success': True}
    except Exception as e:
        logger.exception(f"更新当前页面失败: {e}")
        return {'error': str(e)}

@app.post('/get_image')
async def get_image(request: Request):
    data = await request.json()
    directory = data.get('directory')
    key = data.get('key')
    entries = data.get('entries', [])
    if not directory or not key:
        return {'error': 'Missing directory or key'}

    directory_path = Path(directory)
    key_stem = Path(key).stem

    # 查找原始图片
    img_filename = None
    for ext in IMAGE_EXTS:
        potential = directory_path / f"{key_stem}{ext}"
        if potential.exists():
            img_filename = potential.name
            break
    if not img_filename:
        return {'error': f'Original image not found for {key_stem}'}

    raw_mount = mount_static_directory(directory_path, 'raw')
    original_url = f"{raw_mount}/{img_filename}"

    inpainted_dir = directory_path / 'inpainted'
    inpainted_url = None
    if inpainted_dir.exists():
        inpainted_mount = mount_static_directory(inpainted_dir, 'inpainted')
        for ext in IMAGE_EXTS:
            potential = inpainted_dir / f"{key_stem}{ext}"
            if potential.exists():
                mtime = int(potential.stat().st_mtime)
                inpainted_url = f"{inpainted_mount}/{key_stem}{ext}?t={mtime}"
                break

    text_blocks = generate_text_blocks(directory, key_stem, entries)
    return {
        'originalImageUrl': original_url,
        'inpaintedImageUrl': inpainted_url,
        'textBlocks': text_blocks
    }

@app.post('/process_inpaint')
async def process_inpaint(request: Request):
    form = await request.form()
    image_file = form.get('image')
    mask_file = form.get('mask')
    algorithm = form.get('algorithm', InpaintAlgorithm.PATCHMATCH)
    if not image_file or not mask_file:
        return {'error': '缺少图片或掩码'}
    img_bytes = await image_file.read()
    mask_bytes = await mask_file.read()
    result_bytes = await run_inpainter_in_process(img_bytes, mask_bytes, algorithm)
    encoded = base64.b64encode(result_bytes).decode('utf-8')
    return {'imageUrl': f'data:image/png;base64,{encoded}'}

@app.post('/process_rect_inpaint')
async def process_rect_inpaint(request: Request):
    form = await request.form()
    image_file = form.get('image')
    x = int(form.get('x'))
    y = int(form.get('y'))
    w = int(form.get('w'))
    h = int(form.get('h'))
    algorithm = form.get('algorithm', InpaintAlgorithm.PATCHMATCH)
    if not image_file or x is None or y is None or w is None or h is None:
        return {'error': '缺少图片或矩形坐标'}
    img_bytes = await image_file.read()
    try:
        result_bytes = await run_rect_inpainter_in_process(img_bytes, x, y, w, h, algorithm)
    except Exception as e:
        logger.error(f"处理矩形修复时发生异常: {e}")
        return {'error': f'处理失败: {str(e)}'}
    encoded = base64.b64encode(result_bytes).decode('utf-8')
    return {'imageUrl': f'data:image/png;base64,{encoded}'}

@app.post('/save_project')
async def save_project(request: Request):
    try:
        data = await request.json()
        directory = data.get('directory')
        pages = data.get('pages')
        current_img = data.get('current_img')
        if not directory or not pages or not current_img:
            return {'error': '缺少必要字段'}
        save_path = Path(directory) / 'match_results.json'
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump({
                'directory': directory,
                'pages': pages,
                'current_img': Path(current_img).stem
            }, f, ensure_ascii=False, separators=(',', ':'))
        return {'success': True, 'path': str(save_path)}
    except Exception as e:
        logger.exception(f"保存失败: {e}")
        return {'error': str(e)}

@app.post('/save_inpainted')
async def save_inpainted(request: Request):
    try:
        form = await request.form()
        directory = form.get('directory')
        key = form.get('key')
        image_file = form.get('image')
        if not directory or not key or not image_file:
            return {'error': '缺少参数'}
        img_bytes = await image_file.read()
        inpainted_dir = Path(directory) / 'inpainted'
        inpainted_dir.mkdir(parents=True, exist_ok=True)
        save_path = inpainted_dir / f"{Path(key).stem}.png"
        with open(save_path, 'wb') as f:
            f.write(img_bytes)
        return {'success': True, 'path': str(save_path)}
    except Exception as e:
        logger.exception(f"保存 inpainted 失败: {e}")
        return {'error': str(e)}

@app.post('/save_result')
async def save_result(request: Request):
    try:
        form = await request.form()
        directory = form.get('directory')
        key = form.get('key')
        entries_str = form.get('entries')
        text_layer_file = form.get('text_layer')

        entries = json.loads(entries_str) if entries_str else []

        if directory is None or key is None or entries is None:
            return {'error': '缺少参数'}

        directory_path = Path(directory)
        key_stem = Path(key).stem

        # 查找底图（优先 inpainted，其次原始）
        base_path = None
        inpainted_dir = directory_path / 'inpainted'
        for ext in IMAGE_EXTS:
            p = inpainted_dir / f"{key_stem}{ext}"
            if p.exists():
                base_path = p
                break
        if not base_path:
            for ext in IMAGE_EXTS:
                p = directory_path / f"{key_stem}{ext}"
                if p.exists():
                    base_path = p
                    break
        if not base_path:
            return {'error': '找不到底图'}

        base_img = Image.open(base_path).convert('RGBA')

        # 合成匹配的文本块
        text_dir = directory_path / 'temp' / 'text'
        text_img_path = None
        for ext in IMAGE_EXTS:
            p = text_dir / f"{key_stem}{ext}"
            if p.exists():
                text_img_path = p
                break
        if text_img_path:
            text_img = Image.open(text_img_path).convert('RGBA')
            for entry in entries:
                if entry.get('matched') != 1:
                    continue
                orig_xyxy = entry.get('orig_xyxy')
                if not orig_xyxy or len(orig_xyxy) != 4:
                    continue
                crop_box = tuple(orig_xyxy)
                block = text_img.crop(crop_box)
                xyxy = entry.get('xyxy')
                if not xyxy or len(xyxy) != 4:
                    continue
                left, top = xyxy[0], xyxy[1]
                target_w = xyxy[2] - xyxy[0]
                target_h = xyxy[3] - xyxy[1]
                if block.size != (target_w, target_h):
                    block = block.resize((target_w, target_h), Image.Resampling.LANCZOS)
                base_img.paste(block, (left, top), block)

        # ========== 合成用户文本图层 ==========
        if text_layer_file:
            text_layer_bytes = await text_layer_file.read()
            text_layer_img = Image.open(io.BytesIO(text_layer_bytes)).convert('RGBA')
            # 确保尺寸匹配
            if text_layer_img.size != base_img.size:
                text_layer_img = text_layer_img.resize(base_img.size, Image.Resampling.LANCZOS)
            base_img = Image.alpha_composite(base_img, text_layer_img)

        result_dir = directory_path / 'result'
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / f"{key_stem}.png"
        base_img.save(result_path, 'PNG')

        return {'success': True, 'path': str(result_path)}
    except Exception as e:
        logger.exception(f"保存结果失败: {e}")
        return {'error': str(e)}

@app.post('/get_working_image')
async def get_working_image(request: Request):
    data = await request.json()
    directory = data.get('directory')
    key = data.get('key')
    if not directory or not key:
        return {'error': 'Missing directory or key'}
    directory_path = Path(directory)
    working_dir = directory_path / 'temp'
    if not working_dir.exists():
        return {'error': 'temp directory not found'}

    key_stem = Path(key).stem
    img_filename = None
    for ext in IMAGE_EXTS:
        potential = working_dir / f"{key_stem}{ext}"
        if potential.exists():
            img_filename = potential.name
            break
    if not img_filename:
        return {'error': f'Working image not found for {key_stem}'}

    working_mount = mount_static_directory(working_dir, 'working')
    image_url = f"{working_mount}/{img_filename}"
    return {'imageUrl': image_url}

# ==================== 启动应用 ====================
ui.run(
    title='MangaTransFer',
    host=os.environ.get('GUI_HOST', '127.0.0.1'),
    port=int(os.environ.get('GUI_PORT', '8080')),
    dark=False,
    reload=False,
    language='zh-CN',
)