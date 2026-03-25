import json
from nicegui import ui, app, run
from fastapi import Request
from PIL import Image
import os
import io
import base64
import tempfile
from core.inpainting import Inpainter
from cli import MangaTranslationPipeline
import asyncio
import concurrent.futures
import multiprocessing


# ==================== 全局设置 ====================
# 设置多进程启动方式
multiprocessing.set_start_method('spawn', force=True)

# 创建进程池（全局单例）
process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)

async def run_inpainter_in_process(img_bytes, mask_bytes, algorithm):
    """在独立进程中执行修复"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(process_pool, run_inpainter_sync, img_bytes, mask_bytes, algorithm)

async def run_rect_inpainter_in_process(img_bytes, x, y, w, h, algorithm):
    """在独立进程中执行矩形修复"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        process_pool,
        sync_rect_inpaint_worker,
        img_bytes, x, y, w, h, algorithm
    )

# ==================== 辅助函数 ====================
def run_inpainter_sync(img_bytes: bytes, mask_bytes: bytes, algorithm: str) -> bytes:
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

def run_pipeline(raw_dir, text_dir, algorithm):
    try:
        pipeline = MangaTranslationPipeline(
            raw_dir=raw_dir,
            text_dir=text_dir,
            model_path='data/models/comictextdetector.pt',
            inpaint_algorithm=algorithm,
            output_dir=None
        )
        pipeline.run()
        return True, raw_dir
    except Exception as e:
        error_msg = str(e).encode('ascii', errors='replace').decode('ascii')
        return False, f"处理失败: {error_msg}"

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
        print(f"connected_canny_flood 出错: {e}")
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
            print(f"Inpainter 出错: {e}")
            return img_bytes
        repaired_roi = cv2.imread(out_path)
        if repaired_roi is None:
            return img_bytes
    open_cv_image[y:y+h, x:x+w] = repaired_roi
    result_pil = Image.fromarray(cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2RGB))
    result_bytes_io = io.BytesIO()
    result_pil.save(result_bytes_io, format='PNG')
    return result_bytes_io.getvalue()

def generate_text_blocks(directory, page_key, entries):
    text_dir = os.path.join(directory, 'temp', 'text')
    if not os.path.exists(text_dir):
        return []
    text_img_path = None
    for ext in IMAGE_EXTS:
        potential_path = os.path.join(text_dir, page_key + ext)
        if os.path.exists(potential_path):
            text_img_path = potential_path
            break
    if not text_img_path:
        # print(f"text 图片不存在 for {page_key} in {text_dir}")
        return []
    text_blocks = []
    try:
        with Image.open(text_img_path) as text_img:
            for idx, entry in enumerate(entries):
                try:
                    matched = entry.get('matched', 0)
                    orig_xyxy = entry.get('orig_xyxy')
                    xyxy = entry.get('xyxy')

                    # 必须要有有效的 orig_xyxy 才能裁剪文本图像
                    if not orig_xyxy or len(orig_xyxy) != 4:
                        print(f"Entry {idx} 的 orig_xyxy 无效: {orig_xyxy}，跳过")
                        continue

                    crop_box = (orig_xyxy[0], orig_xyxy[1], orig_xyxy[2], orig_xyxy[3])
                    # 检查裁剪区域是否超出图片边界
                    if (crop_box[0] < 0 or crop_box[1] < 0 or
                        crop_box[2] > text_img.width or crop_box[3] > text_img.height):
                        print(f"Entry {idx} 裁剪区域 {crop_box} 超出图片范围，跳过")
                        continue

                    # 裁剪原始文本图像
                    cropped = text_img.crop(crop_box)
                    buffer = io.BytesIO()
                    cropped.save(buffer, format='PNG')
                    img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                    img_url = f'data:image/png;base64,{img_base64}'

                    # 处理位置信息：若 xyxy 无效，则使用 orig_xyxy 作为后备位置，并默认不可见
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
                    print(f"处理 entry {idx} 时出错: {e}")
                    continue
    except Exception as e:
        print(f"打开 text 图片失败: {e}")
        return []
    return text_blocks

def get_project_data(directory, pages, current_img):
    img_filename = None
    for ext in IMAGE_EXTS:
        potential_path = os.path.join(directory, current_img + ext)
        if os.path.exists(potential_path):
            img_filename = current_img + ext
            break
    if not img_filename:
        raise Exception(f'Image not found for {current_img} in {directory}')
    with open(os.path.join(directory, img_filename), 'rb') as f:
        img_data = f.read()
    img_base64 = base64.b64encode(img_data).decode('utf-8')
    ext = img_filename.split('.')[-1].lower()
    if ext == 'jpg':
        ext = 'jpeg'
    mime_type = f'image/{ext}'
    image_url = f'data:{mime_type};base64,{img_base64}'
    inpainted_url = None
    inpainted_dir = os.path.join(directory, 'inpainted')
    if os.path.exists(inpainted_dir):
        for ext in IMAGE_EXTS:
            inpainted_path = os.path.join(inpainted_dir, current_img + ext)
            if os.path.exists(inpainted_path):
                with open(inpainted_path, 'rb') as f:
                    img_data = f.read()
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                ext = inpainted_path.split('.')[-1].lower()
                if ext == 'jpg':
                    ext = 'jpeg'
                mime_type = f'image/{ext}'
                inpainted_url = f'data:{mime_type};base64,{img_base64}'
                break
    thumbnails = []
    for key in pages.keys():
        img_filename = None
        for ext in IMAGE_EXTS:
            potential_path = os.path.join(directory, key + ext)
            if os.path.exists(potential_path):
                img_filename = key + ext
                break
        if img_filename:
            with Image.open(os.path.join(directory, img_filename)) as img:
                img.thumbnail((200, 200))
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1])
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                thumb_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                thumb_url = f'data:image/jpeg;base64,{thumb_base64}'
                thumbnails.append({'key': key, 'thumb_url': thumb_url})
    text_blocks = generate_text_blocks(directory, current_img, pages[current_img])
    return {
        'directory': directory,
        'imageUrl': image_url,
        'inpaintedImageUrl': inpainted_url,
        'pages': pages,
        'regions': pages[current_img],
        'thumbnails': thumbnails,
        'textBlocks': text_blocks,
        'current_img': current_img
    }

# ==================== UI 页面定义 ====================
ui.page_title('MangaTransFer')
app.add_static_files('/static', 'static')

current_algorithm = 'patch_match'

ui.add_head_html('<link rel="stylesheet" href="/static/styles.css">')
ui.add_head_html('<script src="/static/fabric.min.js"></script>')
ui.add_head_html('<script src="/static/canvas.js"></script>')
ui.add_head_html('<script src="/static/load_project.js"></script>')

IMAGE_EXTS = ['.jpg', '.JPG', '.jpeg', '.JPEG', '.png', '.PNG']

def show_new_project_dialog():
    with ui.dialog() as dialog, ui.card().style('min-width: 400px;'):
        ui.label('新建项目').classes('text-h6 w-full text-center')
        with ui.column().classes('w-full gap-4 p-4'):
            with ui.row().classes('w-full items-center gap-2'):
                ui.label('原图:').classes('w-10 text-right')
                raw_input = ui.input(placeholder='输入原始图片目录路径')\
                    .classes('flex-grow')\
                    .props('id="raw-dir-input"')
            with ui.row().classes('w-full items-center gap-2'):
                ui.label('文本:').classes('w-10 text-right')
                text_input = ui.input(placeholder='输入文本图片目录路径')\
                    .classes('flex-grow')\
                    .props('id="text-dir-input"')
            with ui.row().classes('w-full justify-center gap-4 mt-4'):
                async def on_start():
                    raw_dir = raw_input.value.strip()
                    text_dir = text_input.value.strip()
                    if not raw_dir or not text_dir:
                        ui.notify('请填写原图和文本目录', type='warning')
                        return
                    algo = current_algorithm
                    if algo == 'patch_match':
                        algo = 'patchmatch'
                    dialog.close()
                    loading_dialog.open()
                    success, result = await run.io_bound(run_pipeline, raw_dir, text_dir, algo)
                    loading_dialog.close()
                    if success:
                        raw_dir = result
                        json_path = os.path.join(raw_dir, 'match_results.json')
                        if not os.path.exists(json_path):
                            ui.notify('处理完成，但未找到 match_results.json', type='warning')
                            return
                        with open(json_path, 'r', encoding='utf-8') as f:
                            project_data = json.load(f)
                        ui.run_javascript(f'window.loadProjectFromData({json.dumps(project_data)});')
                        ui.notify('项目加载成功', type='positive')
                    else:
                        ui.notify(result, type='negative')
                ui.button('开始', on_click=on_start).props('outline').classes('w-24')
                ui.button('取消', on_click=dialog.close).props('outline').classes('w-24')
    dialog.open()

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
                            # split_btn = ui.button(icon='compare', color='transparent') \
                            #     .props('flat dense') \
                            #     .on('click', lambda: ui.run_javascript('window.canvasControls.toggleWorkingReference()'))
                            split_btn = ui.button(color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.canvasControls.toggleWorkingReference()'))
                            
                            with split_btn:
                                ui.image('static/icons/view_column_2.svg').style('width: 24px; height: 24px')
                            
                            new_btn = ui.button(color='transparent') \
                                .props('flat dense') \
                                .on('click', show_new_project_dialog)
                            with new_btn:
                                ui.image('static/icons/create_new_folder.svg').style('width: 24px; height: 24px')

                            load_btn = ui.button(icon='folder_open', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.selectProjectFile()'))
                            
                            save_btn = ui.button(color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.saveProject()'))
                            with save_btn:
                                ui.image('static/icons/save.svg').style('width: 24px; height: 24px')

                            prev_btn = ui.button(icon='navigate_before', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.goToPrevPage()'))
                            next_btn = ui.button(icon='navigate_next', color='transparent') \
                                .props('flat dense') \
                                .on('click', lambda: ui.run_javascript('window.goToNextPage()'))
                        with ui.element('div').style('margin-left: auto; display: flex; align-items: center;'):
                            algorithm_label = ui.label('patch_match') \
                                .style('padding:4px 12px; background:#f0f0f0; border-radius:4px; cursor:pointer; font-size:14px;') \
                                .props('id="algorithm-selector"')
                            def toggle_algorithm():
                                global current_algorithm
                                if current_algorithm == 'patch_match':
                                    current_algorithm = 'lama_large_512px'
                                else:
                                    current_algorithm = 'patch_match'
                                ui.run_javascript(f'''
                                    let el = document.getElementById('algorithm-selector');
                                    el.innerText = '{current_algorithm}';
                                    window.currentAlgorithm = '{current_algorithm}';
                                ''')
                            algorithm_label.on('click', toggle_algorithm)
                    with ui.element('div').style('height:calc(100% - 40px); margin:0; padding:0;').props('name="canvas"'):
                        with ui.element('div').props('id="canvas-container"').style('width:100%; height:100%; position:relative; overflow:hidden;'):
                            ui.element('canvas').props('id="comic-canvas"').style('width:100%; height:100%; display:block;')
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

# ==================== 路由处理函数 ====================
@app.post('/load_project')
async def load_project(request: Request):
    data = await request.json()
    directory = data.get('directory')
    pages = data.get('pages', {})
    current_img = data.get('current_img')
    if not directory or not current_img or current_img not in pages:
        return {'error': 'Invalid project file: missing directory, current_img, or current_img not in pages'}
    try:
        result = get_project_data(directory, pages, current_img)
        return result
    except Exception as e:
        return {'error': str(e)}

@app.post('/get_image')
async def get_image(request: Request):
    data = await request.json()
    directory = data.get('directory')
    key = data.get('key')
    entries = data.get('entries', [])
    # print(f"/get_image called: key={key}, entries count={len(entries)}")
    if not directory or not key:
        return {'error': 'Missing directory or key'}
    original_url = None
    img_filename = None
    for ext in IMAGE_EXTS:
        potential_path = os.path.join(directory, key + ext)
        if os.path.exists(potential_path):
            img_filename = key + ext
            break
    if img_filename:
        try:
            with open(os.path.join(directory, img_filename), 'rb') as f:
                img_data = f.read()
            img_base64 = base64.b64encode(img_data).decode('utf-8')
            ext = img_filename.split('.')[-1].lower()
            if ext == 'jpg':
                ext = 'jpeg'
            mime_type = f'image/{ext}'
            original_url = f'data:{mime_type};base64,{img_base64}'
        except Exception as e:
            return {'error': f'Failed to read original image: {str(e)}'}
    else:
        return {'error': f'Original image not found for {key}'}
    inpainted_url = None
    inpainted_dir = os.path.join(directory, 'inpainted')
    if os.path.exists(inpainted_dir):
        for ext in IMAGE_EXTS:
            potential_path = os.path.join(inpainted_dir, key + ext)
            if os.path.exists(potential_path):
                try:
                    with open(potential_path, 'rb') as f:
                        img_data = f.read()
                    img_base64 = base64.b64encode(img_data).decode('utf-8')
                    ext = potential_path.split('.')[-1].lower()
                    if ext == 'jpg':
                        ext = 'jpeg'
                    mime_type = f'image/{ext}'
                    inpainted_url = f'data:{mime_type};base64,{img_base64}'
                except Exception as e:
                    print(f"读取 inpainted 图片失败 {key}: {e}")
                break
    text_blocks = generate_text_blocks(directory, key, entries)
    # print(f"Generated {len(text_blocks)} text blocks")
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
    algorithm = form.get('algorithm', 'patch_match')
    if not image_file or not mask_file:
        return {'error': '缺少图片或掩码'}
    algo_map = {
        'patch_match': 'patchmatch',
        'lama_large_512px': 'lama_large_512px'
    }
    backend_algo = algo_map.get(algorithm, 'patchmatch')
    img_bytes = await image_file.read()
    mask_bytes = await mask_file.read()
    # 使用进程池执行
    result_bytes = await run_inpainter_in_process(img_bytes, mask_bytes, backend_algo)
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
    algorithm = form.get('algorithm', 'patch_match')
    if not image_file or x is None or y is None or w is None or h is None:
        return {'error': '缺少图片或矩形坐标'}
    algo_map = {
        'patch_match': 'patchmatch',
        'lama_large_512px': 'lama_large_512px'
    }
    backend_algo = algo_map.get(algorithm, 'patchmatch')
    img_bytes = await image_file.read()
    try:
        result_bytes = await run_rect_inpainter_in_process(img_bytes, x, y, w, h, backend_algo)
    except Exception as e:
        print(f"处理矩形修复时发生异常: {e}")
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
        save_path = os.path.join(directory, 'match_results.json')
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump({
                'directory': directory,
                'pages': pages,
                'current_img': current_img
            }, f, ensure_ascii=False, separators=(',', ':'))
        return {'success': True, 'path': save_path}
    except Exception as e:
        print(f"保存失败: {e}")
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
        inpainted_dir = os.path.join(directory, 'inpainted')
        os.makedirs(inpainted_dir, exist_ok=True)
        save_path = os.path.join(inpainted_dir, f"{key}.png")
        with open(save_path, 'wb') as f:
            f.write(img_bytes)
        return {'success': True, 'path': save_path}
    except Exception as e:
        print(f"保存 inpainted 失败: {e}")
        return {'error': str(e)}

@app.post('/save_result')
async def save_result(request: Request):
    try:
        data = await request.json()
        directory = data.get('directory')
        key = data.get('key')
        entries = data.get('entries')
        if directory is None or key is None or entries is None:
            return {'error': '缺少参数'}
        base_path = None
        inpainted_dir = os.path.join(directory, 'inpainted')
        for ext in ['.png', '.jpg', '.jpeg', '.PNG', '.JPG', '.JPEG']:
            test_path = os.path.join(inpainted_dir, key + ext)
            if os.path.exists(test_path):
                base_path = test_path
                break
        if not base_path:
            for ext in ['.png', '.jpg', '.jpeg', '.PNG', '.JPG', '.JPEG']:
                test_path = os.path.join(directory, key + ext)
                if os.path.exists(test_path):
                    base_path = test_path
                    break
        if not base_path:
            return {'error': '找不到底图'}
        base_img = Image.open(base_path).convert('RGBA')
        text_dir = os.path.join(directory, 'temp', 'text')
        text_img_path = None
        for ext in ['.png', '.jpg', '.jpeg', '.PNG', '.JPG', '.JPEG']:
            test_path = os.path.join(text_dir, key + ext)
            if os.path.exists(test_path):
                text_img_path = test_path
                break
        if not text_img_path:
            result_dir = os.path.join(directory, 'result')
            os.makedirs(result_dir, exist_ok=True)
            result_path = os.path.join(result_dir, f"{key}.png")
            base_img.save(result_path, 'PNG')
            return {'success': True, 'path': result_path}
        text_img = Image.open(text_img_path).convert('RGBA')
        for entry in entries:
            if entry.get('matched') != 1:
                continue
            orig_xyxy = entry.get('orig_xyxy')
            if not orig_xyxy or len(orig_xyxy) != 4:
                continue
            crop_box = (orig_xyxy[0], orig_xyxy[1], orig_xyxy[2], orig_xyxy[3])
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
        result_dir = os.path.join(directory, 'result')
        os.makedirs(result_dir, exist_ok=True)
        result_path = os.path.join(result_dir, f"{key}.png")
        base_img.save(result_path, 'PNG')
        return {'success': True, 'path': result_path}
    except Exception as e:
        print(f"保存结果失败: {e}")
        return {'error': str(e)}

@app.post('/get_working_image')
async def get_working_image(request: Request):
    data = await request.json()
    directory = data.get('directory')
    key = data.get('key')
    if not directory or not key:
        return {'error': 'Missing directory or key'}
    working_dir = os.path.join(directory, 'temp')
    if not os.path.exists(working_dir):
        return {'error': 'temp directory not found'}
    for ext in IMAGE_EXTS:
        potential_path = os.path.join(working_dir, key + ext)
        if os.path.exists(potential_path):
            try:
                with open(potential_path, 'rb') as f:
                    img_data = f.read()
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                ext = potential_path.split('.')[-1].lower()
                if ext == 'jpg':
                    ext = 'jpeg'
                mime_type = f'image/{ext}'
                return {'imageUrl': f'data:{mime_type};base64,{img_base64}'}
            except Exception as e:
                return {'error': f'Failed to read working image: {str(e)}'}
    return {'error': f'Working image not found for {key}'}

# ==================== 启动应用 ====================
ui.run(
    title='MangaTransFer',
    host='127.0.0.1',
    port=8080,
    dark=False,
    reload=False,
    language='zh-CN',
)