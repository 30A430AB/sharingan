// ==================== 画布状态封装 ====================
const CanvasState = {
    canvas: null,
    comicImage: null,
    inpaintedImage: null,
    textImages: [],
    canvasContainer: null,
    minScale: 1,
    maxScale: 100,
    currentScale: 1,
    imageWidth: 1920,
    imageHeight: 1080,
    currentRawOpacity: 100,
    currentInpaintOpacity: 100,      // 修复层透明度
    currentTextLayerOpacity: 100,    // 文本层透明度
    workingReferenceImage: null,
    workingReferenceVisible: false,

    // 拖拽状态
    isDragging: false,
    lastPosX: 0,
    lastPosY: 0,
    isDraggingText: false,
    draggedTextIndex: -1,
    dragStartX: 0, dragStartY: 0,
    dragStartLeft: 0, dragStartTop: 0,
    hoveredTextImage: null,

    // 工具状态
    currentTool: 'drag',
    isDrawingRect: false,
    rectStartPoint: null,
    tempRect: null,
    brushSize: 15,
    isProcessing: false,
    processingRect: null
};

// 常量定义
const CONSTANTS = {
    MIN_RECT_SIZE: 5,
    DEFAULT_BRUSH_SIZE: 15,
    MAX_ZOOM: 100,
    WHEEL_ZOOM_SPEED: 500,
    EXPAND_FACTOR: 1.5,
    MIN_EXPAND: 50
};

// ==================== 辅助函数 ====================
function dataURItoBlob(dataURI) {
    const byteString = atob(dataURI.split(',')[1]);
    const mimeString = dataURI.split(',')[0].split(':')[1].split(';')[0];
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
    }
    return new Blob([ab], { type: mimeString });
}

function cropImage(source, x, y, w, h) {
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(source, x, y, w, h, 0, 0, w, h);
    return canvas.toDataURL('image/png');
}

function isMaskBlank(canvas) {
    const ctx = canvas.getContext('2d');
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
        if (data[i] > 0 || data[i+1] > 0 || data[i+2] > 0) {
            return false;
        }
    }
    return true;
}

// ==================== 画布初始化 ====================
function initCanvas() {
    CanvasState.canvasContainer = document.getElementById('canvas-container');
    
    CanvasState.canvas = new fabric.Canvas('comic-canvas', {
        selection: false,
        backgroundColor: '#FAFAFA',
        preserveObjectStacking: true,
        renderOnAddRemove: true
    });

    CanvasState.canvas.defaultCursor = 'default';
    CanvasState.canvas.hoverCursor = 'default';
    CanvasState.canvas.moveCursor = 'default';
    
    resizeCanvas();
    
    window.addEventListener('resize', () => {
        resizeCanvas();
        updateImagePosition();
    });
    
    CanvasState.canvas.on('mouse:wheel', onMouseWheel);
    
    bindCanvasEvents();
    switchTool('drag');
    window.currentAlgorithm = window.ALGO_PATCHMATCH;
}

function resizeCanvas() {
    if (!CanvasState.canvasContainer) return;
    
    const containerWidth = CanvasState.canvasContainer.clientWidth;
    const containerHeight = CanvasState.canvasContainer.clientHeight;
    
    CanvasState.canvas.setWidth(containerWidth);
    CanvasState.canvas.setHeight(containerHeight);
    CanvasState.canvas.calcViewportBoundaries();
    
    if (CanvasState.comicImage) {
        updateImagePosition();
    }
}

function updateImagePosition() {
    if (!CanvasState.comicImage) return;
    applyViewportBoundaries();
    CanvasState.canvas.requestRenderAll();
}

// ==================== 缩放与视口 ====================
function onMouseWheel(opt) {
    const delta = opt.e.deltaY;
    let zoom = CanvasState.canvas.getZoom();
    
    zoom = zoom * (1 - delta / CONSTANTS.WHEEL_ZOOM_SPEED);
    
    const containerWidth = CanvasState.canvasContainer.clientWidth;
    const containerHeight = CanvasState.canvasContainer.clientHeight;
    
    let targetWidth, targetHeight;
    if (CanvasState.workingReferenceImage && CanvasState.workingReferenceImage.visible) {
        targetWidth = CanvasState.imageWidth * 2;
        targetHeight = CanvasState.imageHeight;
    } else {
        targetWidth = CanvasState.imageWidth;
        targetHeight = CanvasState.imageHeight;
    }
    
    const minScaleX = containerWidth / targetWidth;
    const minScaleY = containerHeight / targetHeight;
    const newMinScale = Math.min(minScaleX, minScaleY);
    
    zoom = Math.max(zoom, newMinScale);
    zoom = Math.min(zoom, CanvasState.maxScale);
    
    CanvasState.canvas.setZoom(zoom);
    CanvasState.currentScale = zoom;
    CanvasState.minScale = newMinScale;
    
    updateImagePosition();
    
    opt.e.preventDefault();
    opt.e.stopPropagation();
}

function fitImageToCanvas() {
    if (!CanvasState.comicImage || !CanvasState.canvasContainer) return;
    
    const containerWidth = CanvasState.canvasContainer.clientWidth;
    const containerHeight = CanvasState.canvasContainer.clientHeight;
    
    let targetWidth, targetHeight;
    if (CanvasState.workingReferenceImage && CanvasState.workingReferenceImage.visible) {
        targetWidth = CanvasState.imageWidth * 2;
        targetHeight = CanvasState.imageHeight;
    } else {
        targetWidth = CanvasState.imageWidth;
        targetHeight = CanvasState.imageHeight;
    }
    
    const scaleX = containerWidth / targetWidth;
    const scaleY = containerHeight / targetHeight;
    const scale = Math.min(scaleX, scaleY);
    
    CanvasState.canvas.setZoom(scale);
    CanvasState.currentScale = scale;
    CanvasState.minScale = scale;
    
    centerImage();
    CanvasState.canvas.requestRenderAll();
}

function centerImage() {
    if (!CanvasState.comicImage) return;
    
    const containerWidth = CanvasState.canvasContainer.clientWidth;
    const containerHeight = CanvasState.canvasContainer.clientHeight;
    
    let targetWidth, targetHeight;
    if (CanvasState.workingReferenceImage && CanvasState.workingReferenceImage.visible) {
        targetWidth = CanvasState.imageWidth * 2;
        targetHeight = CanvasState.imageHeight;
    } else {
        targetWidth = CanvasState.imageWidth;
        targetHeight = CanvasState.imageHeight;
    }
    
    const scaledWidth = targetWidth * CanvasState.currentScale;
    const scaledHeight = targetHeight * CanvasState.currentScale;
    
    const left = (containerWidth - scaledWidth) / 2;
    const top = (containerHeight - scaledHeight) / 2;
    
    CanvasState.canvas.viewportTransform[4] = left;
    CanvasState.canvas.viewportTransform[5] = top;
    
    applyViewportBoundaries();
}

function applyViewportBoundaries() {
    if (!CanvasState.comicImage || !CanvasState.canvasContainer) return;
    const containerWidth = CanvasState.canvasContainer.clientWidth;
    const containerHeight = CanvasState.canvasContainer.clientHeight;
    const vpt = CanvasState.canvas.viewportTransform;
    
    let minX = 0, maxX = CanvasState.imageWidth;
    if (CanvasState.workingReferenceImage && CanvasState.workingReferenceImage.visible) {
        minX = -CanvasState.imageWidth;
        maxX = CanvasState.imageWidth;
    }
    const scaledMinX = minX * CanvasState.currentScale;
    const scaledMaxX = maxX * CanvasState.currentScale;
    const scaledWidth = scaledMaxX - scaledMinX;
    const scaledHeight = CanvasState.imageHeight * CanvasState.currentScale;
    
    if (scaledWidth <= containerWidth) {
        vpt[4] = (containerWidth - scaledWidth) / 2 - scaledMinX;
    } else {
        const maxBound = -scaledMinX;
        const minBound = containerWidth - scaledMaxX;
        vpt[4] = Math.min(maxBound, Math.max(minBound, vpt[4]));
    }
    
    if (scaledHeight <= containerHeight) {
        vpt[5] = (containerHeight - scaledHeight) / 2;
    } else {
        vpt[5] = Math.min(0, Math.max(containerHeight - scaledHeight, vpt[5]));
    }
}

// ==================== 工具切换 ====================
function switchTool(tool) {
    CanvasState.currentTool = tool;

    if (CanvasState.isDrawingRect && CanvasState.tempRect) {
        CanvasState.canvas.remove(CanvasState.tempRect);
        CanvasState.tempRect = null;
        CanvasState.isDrawingRect = false;
        CanvasState.rectStartPoint = null;
        CanvasState.canvas.renderAll();
    }

    CanvasState.isDrawingRect = false;
    CanvasState.canvas.isDrawingMode = false;

    document.querySelectorAll('.tool-button').forEach(btn => {
        btn.style.background = '';
        btn.style.color = '';
        btn.style.borderColor = '';
    });

    const activeBtn = document.querySelector(`.tool-button[data-tool="${tool}"]`);
    if (activeBtn) {
        activeBtn.style.background = 'linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%)';
        activeBtn.style.color = '#4A90E2';
        activeBtn.style.borderColor = '#4A90E2';
    }

    if ((tool === 'brush' || tool === 'restoreBrush') && !CanvasState.isProcessing) {
        CanvasState.canvas.isDrawingMode = true;
        if (!CanvasState.canvas.freeDrawingBrush) {
            CanvasState.canvas.freeDrawingBrush = new fabric.PencilBrush(CanvasState.canvas);
        }
        CanvasState.canvas.freeDrawingBrush.color = tool === 'restoreBrush' 
            ? 'rgba(144, 238, 144, 0.6)' 
            : 'rgba(102, 204, 255, 0.6)';
        CanvasState.canvas.freeDrawingBrush.width = CanvasState.brushSize;
    }
}

function setBrushSize(size) {
    CanvasState.brushSize = size;
    if (CanvasState.canvas.freeDrawingBrush) {
        CanvasState.canvas.freeDrawingBrush.width = size;
    }
}

// ==================== 事件绑定 ====================
function bindCanvasEvents() {
    CanvasState.canvas.off('mouse:down');
    CanvasState.canvas.off('mouse:move');
    CanvasState.canvas.off('mouse:up');
    CanvasState.canvas.off('path:created');
    CanvasState.canvas.on('mouse:down', onCanvasMouseDown);
    CanvasState.canvas.on('mouse:move', onCanvasMouseMove);
    CanvasState.canvas.on('mouse:up', onCanvasMouseUp);
    CanvasState.canvas.on('path:created', onPathCreated);
}

function onCanvasMouseDown(opt) {
    const evt = opt.e;
    const pointer = CanvasState.canvas.getPointer(opt.e);

    if (CanvasState.currentTool === 'rect' && !CanvasState.isProcessing) {
        CanvasState.isDrawingRect = true;
        CanvasState.rectStartPoint = { x: pointer.x, y: pointer.y };
        CanvasState.tempRect = new fabric.Rect({
            left: CanvasState.rectStartPoint.x,
            top: CanvasState.rectStartPoint.y,
            width: 0,
            height: 0,
            fill: 'rgba(102, 204, 255, 0.2)',
            stroke: '#66ccff',
            strokeWidth: 1.5,
            selectable: false,
            evented: false
        });
        CanvasState.canvas.add(CanvasState.tempRect);
        evt.preventDefault();
        return;
    }

    if (CanvasState.currentTool === 'drag') {
        const hitIndex = CanvasState.textImages.findIndex(img => {
            if (!img.visible) return false;
            const left = img.left;
            const top = img.top;
            const right = left + img.width * (img.scaleX || 1);
            const bottom = top + img.height * (img.scaleY || 1);
            return pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom;
        });
        if (hitIndex >= 0) {
            CanvasState.isDraggingText = true;
            CanvasState.draggedTextIndex = hitIndex;
            CanvasState.dragStartX = pointer.x;
            CanvasState.dragStartY = pointer.y;
            CanvasState.dragStartLeft = CanvasState.textImages[hitIndex].left;
            CanvasState.dragStartTop = CanvasState.textImages[hitIndex].top;
            evt.preventDefault();
            return;
        }
    }

    if (CanvasState.currentTool === 'drag' && CanvasState.currentScale > CanvasState.minScale) {
        CanvasState.isDragging = true;
        CanvasState.lastPosX = evt.clientX;
        CanvasState.lastPosY = evt.clientY;
        CanvasState.canvas.selection = false;
    }
}

function onCanvasMouseMove(opt) {
    const pointer = CanvasState.canvas.getPointer(opt.e);
    const evt = opt.e;

    if (CanvasState.isDrawingRect && CanvasState.tempRect) {
        const currentX = pointer.x;
        const currentY = pointer.y;
        const left = Math.min(CanvasState.rectStartPoint.x, currentX);
        const top = Math.min(CanvasState.rectStartPoint.y, currentY);
        const width = Math.abs(currentX - CanvasState.rectStartPoint.x);
        const height = Math.abs(currentY - CanvasState.rectStartPoint.y);
        CanvasState.tempRect.set({ left, top, width, height });
        CanvasState.canvas.renderAll();
        return;
    }

    if (CanvasState.isDraggingText) {
        const deltaX = pointer.x - CanvasState.dragStartX;
        const deltaY = pointer.y - CanvasState.dragStartY;
        const targetImg = CanvasState.textImages[CanvasState.draggedTextIndex];
        if (targetImg) {
            targetImg.set({
                left: CanvasState.dragStartLeft + deltaX,
                top: CanvasState.dragStartTop + deltaY
            });
            CanvasState.canvas.renderAll();
        }
        return;
    }

    if (CanvasState.currentTool === 'drag' && CanvasState.isDragging) {
        const deltaX = evt.clientX - CanvasState.lastPosX;
        const deltaY = evt.clientY - CanvasState.lastPosY;
        const vpt = CanvasState.canvas.viewportTransform;
        vpt[4] += deltaX;
        vpt[5] += deltaY;
        applyViewportBoundaries();
        CanvasState.canvas.requestRenderAll();
        CanvasState.lastPosX = evt.clientX;
        CanvasState.lastPosY = evt.clientY;
    }

    if (!CanvasState.isDraggingText && !CanvasState.isDragging && !CanvasState.isDrawingRect) {
        const hit = CanvasState.textImages.find(img => {
            if (!img.visible) return false;
            const left = img.left;
            const top = img.top;
            const right = left + img.width * (img.scaleX || 1);
            const bottom = top + img.height * (img.scaleY || 1);
            return pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom;
        });

        if (hit) {
            if (CanvasState.hoveredTextImage && CanvasState.hoveredTextImage !== hit) {
                CanvasState.hoveredTextImage.set('shadow', null);
            }
            if (!hit.shadow) {
                hit.set('shadow', '0 0 10px rgba(74, 144, 226, 0.8)');
                CanvasState.canvas.defaultCursor = 'pointer';
                CanvasState.hoveredTextImage = hit;
                CanvasState.canvas.renderAll();
            }
        } else {
            if (CanvasState.hoveredTextImage) {
                CanvasState.hoveredTextImage.set('shadow', null);
                CanvasState.hoveredTextImage = null;
                CanvasState.canvas.defaultCursor = 'default';
                CanvasState.canvas.renderAll();
            }
        }
    }
}

function onCanvasMouseUp(opt) {
    if (CanvasState.isDrawingRect && CanvasState.tempRect) {
        handleRectInpaint(opt);
        return;
    }

    if (CanvasState.currentTool === 'brush') {
        return;
    }
    
    if (CanvasState.isDraggingText) {
        CanvasState.isDraggingText = false;
        CanvasState.draggedTextIndex = -1;
        CanvasState.canvas.renderAll();
        return;
    }

    if (CanvasState.currentTool === 'drag') {
        CanvasState.isDragging = false;
        CanvasState.canvas.selection = false;
    }
}

async function handleRectInpaint(opt) {
    CanvasState.isDrawingRect = false;
    const width = CanvasState.tempRect.width;
    const height = CanvasState.tempRect.height;
    
    if (width < CONSTANTS.MIN_RECT_SIZE || height < CONSTANTS.MIN_RECT_SIZE) {
        CanvasState.canvas.remove(CanvasState.tempRect);
        CanvasState.tempRect = null;
        CanvasState.rectStartPoint = null;
        CanvasState.canvas.renderAll();
        return;
    }

    const left = CanvasState.tempRect.left;
    const top = CanvasState.tempRect.top;
    const right = left + width;
    const bottom = top + height;

    const intersectLeft = Math.max(left, 0);
    const intersectTop = Math.max(top, 0);
    const intersectRight = Math.min(right, CanvasState.imageWidth);
    const intersectBottom = Math.min(bottom, CanvasState.imageHeight);
    const intersectWidth = intersectRight - intersectLeft;
    const intersectHeight = intersectBottom - intersectTop;

    if (intersectWidth <= 0 || intersectHeight <= 0) {
        CanvasState.canvas.remove(CanvasState.tempRect);
        CanvasState.tempRect = null;
        CanvasState.rectStartPoint = null;
        CanvasState.canvas.renderAll();
        return;
    }

    CanvasState.isProcessing = true;
    CanvasState.canvas.isDrawingMode = false;
    CanvasState.processingRect = CanvasState.tempRect;
    CanvasState.tempRect = null;
    CanvasState.rectStartPoint = null;

    try {
        await processRectInpaint(intersectLeft, intersectTop, intersectWidth, intersectHeight);
    } catch (error) {
        console.error('矩形修复失败:', error);
    } finally {
        if (CanvasState.processingRect) {
            CanvasState.canvas.remove(CanvasState.processingRect);
            CanvasState.processingRect = null;
        }
        CanvasState.isProcessing = false;
        if (CanvasState.currentTool === 'brush' || CanvasState.currentTool === 'restoreBrush') {
            CanvasState.canvas.isDrawingMode = true;
            if (!CanvasState.canvas.freeDrawingBrush) {
                CanvasState.canvas.freeDrawingBrush = new fabric.PencilBrush(CanvasState.canvas);
            }
            CanvasState.canvas.freeDrawingBrush.color = CanvasState.currentTool === 'restoreBrush' 
                ? 'rgba(144, 238, 144, 0.6)' : 'rgba(102, 204, 255, 0.6)';
            CanvasState.canvas.freeDrawingBrush.width = CanvasState.brushSize;
        }
        CanvasState.canvas.renderAll();
    }
}

async function processRectInpaint(x, y, w, h) {
    const sourceImage = CanvasState.inpaintedImage || CanvasState.comicImage;
    if (!sourceImage) throw new Error('没有可用的图片');
    
    const imageDataURL = sourceImage.toDataURL({ format: 'png' });
    const formData = new FormData();
    formData.append('image', dataURItoBlob(imageDataURL));
    formData.append('x', Math.round(x));
    formData.append('y', Math.round(y));
    formData.append('w', Math.round(w));
    formData.append('h', Math.round(h));
    formData.append('algorithm', window.currentAlgorithm || window.ALGO_PATCHMATCH);

    const response = await fetch('/process_rect_inpaint', { method: 'POST', body: formData });
    const result = await response.json();
    if (!result.imageUrl) throw new Error('修复失败');
    
    return new Promise(resolve => {
        fabric.Image.fromURL(result.imageUrl, img => {
            if (CanvasState.inpaintedImage) CanvasState.canvas.remove(CanvasState.inpaintedImage);
            CanvasState.inpaintedImage = img;
            img.set({ selectable: false, hasControls: false, left: 0, top: 0 });
            if (Math.abs(img.width - CanvasState.imageWidth) > 1 || Math.abs(img.height - CanvasState.imageHeight) > 1) {
                img.scaleToWidth(CanvasState.imageWidth);
                img.scaleToHeight(CanvasState.imageHeight);
            }
            CanvasState.canvas.add(img);
            CanvasState.canvas.moveTo(img, CanvasState.canvas.getObjects().indexOf(CanvasState.comicImage) + 1);
            img.set('opacity', CanvasState.currentInpaintOpacity / 100);
            CanvasState.canvas.renderAll();
            resolve();
        });
    });
}

async function onPathCreated(e) {
    if (CanvasState.isProcessing) return;

    const path = e.path;
    if (!CanvasState.comicImage) {
        console.error('没有原图，无法处理');
        CanvasState.canvas.remove(path);
        return;
    }

    CanvasState.isProcessing = true;
    CanvasState.canvas.isDrawingMode = false;
    path.set('evented', false);
    CanvasState.canvas.selection = false;
    CanvasState.canvas.renderAll();

    try {
        const maskCanvas = await generateMaskFromPath(path);
        if (isMaskBlank(maskCanvas)) {
            console.log('绘制区域完全在图片外部，跳过修复');
            return;
        }

        if (CanvasState.currentTool === 'brush') {
            await processBrushInpaint(path, maskCanvas);
        } else if (CanvasState.currentTool === 'restoreBrush') {
            await restoreUsingMask(maskCanvas);
        }
    } catch (error) {
        console.error('处理路径时出错:', error);
    } finally {
        const paths = CanvasState.canvas.getObjects().filter(obj => obj.type === 'path');
        paths.forEach(p => CanvasState.canvas.remove(p));
        CanvasState.isProcessing = false;
        if (CanvasState.currentTool === 'brush' || CanvasState.currentTool === 'restoreBrush') {
            CanvasState.canvas.isDrawingMode = true;
            if (!CanvasState.canvas.freeDrawingBrush) {
                CanvasState.canvas.freeDrawingBrush = new fabric.PencilBrush(CanvasState.canvas);
            }
            CanvasState.canvas.freeDrawingBrush.color = CanvasState.currentTool === 'restoreBrush' 
                ? 'rgba(144, 238, 144, 0.6)' : 'rgba(102, 204, 255, 0.6)';
            CanvasState.canvas.freeDrawingBrush.width = CanvasState.brushSize;
        }
        CanvasState.canvas.renderAll();
    }
}

async function processBrushInpaint(path, maskCanvas) {
    const bbox = path.getBoundingRect();
    const vpt = CanvasState.canvas.viewportTransform;
    const scale = CanvasState.currentScale;

    let imgLeft = (bbox.left - vpt[4]) / scale;
    let imgTop = (bbox.top - vpt[5]) / scale;
    let imgRight = (bbox.left + bbox.width - vpt[4]) / scale;
    let imgBottom = (bbox.top + bbox.height - vpt[5]) / scale;

    const expandX = Math.max((bbox.width / scale) * CONSTANTS.EXPAND_FACTOR, CONSTANTS.MIN_EXPAND);
    const expandY = Math.max((bbox.height / scale) * CONSTANTS.EXPAND_FACTOR, CONSTANTS.MIN_EXPAND);

    const cropX = Math.max(0, Math.floor(imgLeft - expandX));
    const cropY = Math.max(0, Math.floor(imgTop - expandY));
    const cropW = Math.min(CanvasState.imageWidth - cropX, Math.ceil(imgRight - imgLeft + 2 * expandX));
    const cropH = Math.min(CanvasState.imageHeight - cropY, Math.ceil(imgBottom - imgTop + 2 * expandY));

    const sourceImage = CanvasState.inpaintedImage || CanvasState.comicImage;
    if (!sourceImage) throw new Error('没有可用的图片');

    const sourceElement = sourceImage.getElement();
    const subImageDataURL = cropImage(sourceElement, cropX, cropY, cropW, cropH);
    const subMaskDataURL = cropImage(maskCanvas, cropX, cropY, cropW, cropH);

    const formData = new FormData();
    formData.append('image', dataURItoBlob(subImageDataURL));
    formData.append('mask', dataURItoBlob(subMaskDataURL));
    formData.append('algorithm', window.currentAlgorithm || window.ALGO_PATCHMATCH);

    const response = await fetch('/process_inpaint', { method: 'POST', body: formData });
    const result = await response.json();
    if (result.imageUrl) {
        await mergeRepairedRegion(result.imageUrl, cropX, cropY, cropW, cropH);
    }
}

function mergeRepairedRegion(repairedImageUrl, x, y, w, h) {
    return new Promise(resolve => {
        fabric.Image.fromURL(repairedImageUrl, repairedImg => {
            const offscreenCanvas = document.createElement('canvas');
            offscreenCanvas.width = CanvasState.imageWidth;
            offscreenCanvas.height = CanvasState.imageHeight;
            const offscreenCtx = offscreenCanvas.getContext('2d');
            
            const sourceImage = CanvasState.inpaintedImage || CanvasState.comicImage;
            if (!sourceImage) {
                resolve();
                return;
            }
            const sourceElement = sourceImage.getElement();
            offscreenCtx.drawImage(sourceElement, 0, 0, CanvasState.imageWidth, CanvasState.imageHeight);
            offscreenCtx.drawImage(repairedImg.getElement(), x, y, w, h);
            
            const newDataURL = offscreenCanvas.toDataURL('image/png');
            
            fabric.Image.fromURL(newDataURL, newImg => {
                if (CanvasState.inpaintedImage) CanvasState.canvas.remove(CanvasState.inpaintedImage);
                CanvasState.inpaintedImage = newImg;
                newImg.set({ selectable: false, hasControls: false, left: 0, top: 0 });
                if (Math.abs(newImg.width - CanvasState.imageWidth) > 1 || Math.abs(newImg.height - CanvasState.imageHeight) > 1) {
                    newImg.scaleToWidth(CanvasState.imageWidth);
                    newImg.scaleToHeight(CanvasState.imageHeight);
                }
                CanvasState.canvas.add(newImg);
                CanvasState.canvas.moveTo(newImg, CanvasState.canvas.getObjects().indexOf(CanvasState.comicImage) + 1);
                newImg.set('opacity', CanvasState.currentInpaintOpacity / 100);
                CanvasState.canvas.renderAll();
                resolve();
            });
        });
    });
}

// 生成蒙版（保持原有逻辑，仅适配状态对象）
function generateMaskFromPath(path) {
    return new Promise((resolve) => {
        const containerWidth = CanvasState.canvasContainer.clientWidth;
        const containerHeight = CanvasState.canvasContainer.clientHeight;

        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = containerWidth;
        tempCanvas.height = containerHeight;
        const tempCtx = tempCanvas.getContext('2d');
        tempCtx.fillStyle = '#000000';
        tempCtx.fillRect(0, 0, containerWidth, containerHeight);

        const fabricTempCanvas = new fabric.StaticCanvas(tempCanvas, { enableRetinaScaling: false });
        fabricTempCanvas.setViewportTransform(CanvasState.canvas.viewportTransform);
        path.clone(function(clonedPath) {
            fabricTempCanvas.add(clonedPath);
            fabricTempCanvas.renderAll();

            const scaledWidth = CanvasState.imageWidth * CanvasState.currentScale;
            const scaledHeight = CanvasState.imageHeight * CanvasState.currentScale;
            const vpt = CanvasState.canvas.viewportTransform;
            const imageLeft = vpt[4];
            const imageTop = vpt[5];

            const maskCanvas = document.createElement('canvas');
            maskCanvas.width = CanvasState.imageWidth;
            maskCanvas.height = CanvasState.imageHeight;
            const maskCtx = maskCanvas.getContext('2d');
            maskCtx.fillStyle = '#000000';
            maskCtx.fillRect(0, 0, CanvasState.imageWidth, CanvasState.imageHeight);
            maskCtx.drawImage(
                tempCanvas,
                imageLeft, imageTop, scaledWidth, scaledHeight,
                0, 0, CanvasState.imageWidth, CanvasState.imageHeight
            );

            const imageData = maskCtx.getImageData(0, 0, CanvasState.imageWidth, CanvasState.imageHeight);
            const data = imageData.data;
            for (let i = 0; i < data.length; i += 4) {
                if (data[i] > 0 || data[i+1] > 0 || data[i+2] > 0) {
                    data[i] = 255;
                    data[i+1] = 255;
                    data[i+2] = 255;
                    data[i+3] = 255;
                } else {
                    data[i] = 0;
                    data[i+1] = 0;
                    data[i+2] = 0;
                    data[i+3] = 255;
                }
            }
            maskCtx.putImageData(imageData, 0, 0);

            resolve(maskCanvas);
        });
    });
}

// 恢复原图区域（保持原有逐像素逻辑）
function restoreUsingMask(maskCanvas) {
    return new Promise((resolve, reject) => {
        const width = CanvasState.imageWidth;
        const height = CanvasState.imageHeight;

        if (!CanvasState.comicImage) {
            reject('没有原图');
            return;
        }

        const originalCanvas = fabric.util.createCanvasElement();
        originalCanvas.width = width;
        originalCanvas.height = height;
        const originalCtx = originalCanvas.getContext('2d');
        const comicElement = CanvasState.comicImage.getElement();
        originalCtx.drawImage(comicElement, 0, 0, width, height);

        const targetCanvas = fabric.util.createCanvasElement();
        targetCanvas.width = width;
        targetCanvas.height = height;
        const targetCtx = targetCanvas.getContext('2d');
        if (CanvasState.inpaintedImage) {
            const inpaintedElement = CanvasState.inpaintedImage.getElement();
            targetCtx.drawImage(inpaintedElement, 0, 0, width, height);
        } else {
            targetCtx.drawImage(comicElement, 0, 0, width, height);
        }

        const maskCtx = maskCanvas.getContext('2d');
        const maskData = maskCtx.getImageData(0, 0, width, height).data;

        const originalImageData = originalCtx.getImageData(0, 0, width, height);
        const originalData = originalImageData.data;

        const targetImageData = targetCtx.getImageData(0, 0, width, height);
        const targetData = targetImageData.data;

        for (let i = 0; i < maskData.length; i += 4) {
            if (maskData[i] === 255) {
                targetData[i] = originalData[i];
                targetData[i+1] = originalData[i+1];
                targetData[i+2] = originalData[i+2];
                targetData[i+3] = originalData[i+3];
            }
        }

        targetCtx.putImageData(targetImageData, 0, 0);

        const resultDataURL = targetCanvas.toDataURL('image/png');

        fabric.Image.fromURL(resultDataURL, (img) => {
            if (CanvasState.inpaintedImage) {
                CanvasState.canvas.remove(CanvasState.inpaintedImage);
            }
            CanvasState.inpaintedImage = img;
            img.set({
                selectable: false,
                hasControls: false,
                hasBorders: false,
                left: 0,
                top: 0
            });
            if (Math.abs(img.width - width) > 1 || Math.abs(img.height - height) > 1) {
                img.scaleToWidth(width);
                img.scaleToHeight(height);
            }
            CanvasState.canvas.add(img);

            if (CanvasState.comicImage) {
                const comicIndex = CanvasState.canvas.getObjects().indexOf(CanvasState.comicImage);
                CanvasState.canvas.moveTo(img, comicIndex + 1);
            } else {
                CanvasState.canvas.sendToBack(img);
            }

            img.set('opacity', CanvasState.currentInpaintOpacity / 100);
            CanvasState.canvas.renderAll();
            resolve();
        });
    });
}

// ==================== 图层加载 ====================
function loadLayers(originalUrl, inpaintedUrl, textBlocks) {
    return new Promise((resolve, reject) => {
        if (!CanvasState.canvas) {
            reject('Canvas not initialized');
            return;
        }

        const prevRender = CanvasState.canvas.renderOnAddRemove;
        CanvasState.canvas.renderOnAddRemove = false;

        const loadImage = (url) => {
            return new Promise((res, rej) => {
                if (!url) return res(null);
                fabric.Image.fromURL(url, img => img ? res(img) : rej(`Failed: ${url}`));
            });
        };

        Promise.all([
            loadImage(originalUrl),
            loadImage(inpaintedUrl),
            Promise.all((textBlocks || []).map(b => loadImage(b.imageUrl)))
        ]).then(([orig, inpaint, textImgs]) => {
            if (CanvasState.comicImage) CanvasState.canvas.remove(CanvasState.comicImage);
            if (CanvasState.inpaintedImage) CanvasState.canvas.remove(CanvasState.inpaintedImage);
            CanvasState.textImages.forEach(img => CanvasState.canvas.remove(img));
            CanvasState.textImages = [];

            CanvasState.comicImage = orig;
            orig.set({ selectable: false, hasControls: false, left: 0, top: 0 });
            CanvasState.canvas.add(orig);
            CanvasState.canvas.sendToBack(orig);
            orig.set('opacity', CanvasState.currentRawOpacity / 100);
            CanvasState.imageWidth = orig.width;
            CanvasState.imageHeight = orig.height;

            if (inpaint) {
                CanvasState.inpaintedImage = inpaint;
                inpaint.set({ selectable: false, hasControls: false, left: 0, top: 0 });
                if (Math.abs(inpaint.width - CanvasState.imageWidth) > 1 || Math.abs(inpaint.height - CanvasState.imageHeight) > 1) {
                    inpaint.scaleToWidth(CanvasState.imageWidth);
                    inpaint.scaleToHeight(CanvasState.imageHeight);
                }
                CanvasState.canvas.add(inpaint);
                inpaint.set('opacity', CanvasState.currentInpaintOpacity / 100);
            }

            textImgs.forEach((img, i) => {
                if (img) {
                    const block = textBlocks[i];
                    img.set({
                        left: block.left, top: block.top,
                        selectable: false, hasControls: false,
                        visible: block.visible !== false,
                        evented: true, hoverCursor: 'pointer'
                    });
                    CanvasState.canvas.add(img);
                    CanvasState.textImages[i] = img;
                }
            });
            CanvasState.textImages.forEach(img => img.set('opacity', CanvasState.currentTextLayerOpacity / 100));

            if (CanvasState.workingReferenceImage) {
                CanvasState.workingReferenceImage.set({ left: -CanvasState.imageWidth, top: 0 });
                CanvasState.canvas.add(CanvasState.workingReferenceImage);
                CanvasState.canvas.sendToBack(CanvasState.workingReferenceImage);
            }

            CanvasState.canvas.renderOnAddRemove = prevRender;
            fitImageToCanvas();
            bindCanvasEvents();
            CanvasState.canvas.renderAll();
            resolve();
        }).catch(err => {
            CanvasState.canvas.renderOnAddRemove = prevRender;
            reject(err);
        });
    });
}

// ==================== 参考图操作 ====================
function loadWorkingReference() {
    if (!window.projectDirectory || !window.currentImg) {
        window.showToast && window.showToast('项目未加载', 'error');
        return Promise.reject('No project loaded');
    }
    if (!CanvasState.comicImage) {
        window.showToast && window.showToast('请先加载图片', 'error');
        return Promise.reject('No image loaded');
    }
    return fetch('/get_working_image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ directory: window.projectDirectory, key: window.currentImg })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            window.showToast && window.showToast('没有对应的参考图', 'error');
            return;
        }
        if (data.imageUrl) {
            return new Promise((resolve, reject) => {
                fabric.Image.fromURL(data.imageUrl, img => {
                    if (!img) {
                        reject('Failed to create image from URL');
                        return;
                    }
                    if (CanvasState.workingReferenceImage) {
                        CanvasState.canvas.remove(CanvasState.workingReferenceImage);
                    }
                    CanvasState.workingReferenceImage = img;
                    img.set({
                        left: -CanvasState.imageWidth,
                        top: 0,
                        selectable: false,
                        hasControls: false,
                        hasBorders: false,
                        evented: false,
                        hoverCursor: 'default'
                    });
                    CanvasState.canvas.add(img);
                    CanvasState.canvas.sendToBack(img);
                    CanvasState.workingReferenceVisible = true;
                    updateSplitButtonActive(true);
                    adjustViewToIncludeReference(true);
                    CanvasState.canvas.renderAll();
                    resolve();
                });
            });
        }
    })
    .catch(err => {
        console.error('Error loading working reference:', err);
        window.showToast && window.showToast('加载参考图失败', 'error');
    });
}

function toggleWorkingReference() {
    if (!window.projectDirectory || !window.currentImg) {
        window.showToast && window.showToast('项目未加载', 'error');
        return;
    }
    if (!CanvasState.comicImage) {
        window.showToast && window.showToast('请先加载图片', 'error');
        return;
    }
    if (CanvasState.workingReferenceImage) {
        CanvasState.workingReferenceVisible = !CanvasState.workingReferenceVisible;
        CanvasState.workingReferenceImage.set('visible', CanvasState.workingReferenceVisible);
        updateSplitButtonActive(CanvasState.workingReferenceVisible);
        if (CanvasState.workingReferenceVisible) {
            adjustViewToIncludeReference(true);
        } else {
            fitImageToCanvas();
        }
        CanvasState.canvas.renderAll();
    } else {
        loadWorkingReference();
    }
}

function adjustViewToIncludeReference(include) {
    if (!CanvasState.comicImage || !CanvasState.canvasContainer) return;
    if (include && CanvasState.workingReferenceImage && CanvasState.workingReferenceImage.visible) {
        const containerWidth = CanvasState.canvasContainer.clientWidth;
        const containerHeight = CanvasState.canvasContainer.clientHeight;
        const totalWidth = CanvasState.imageWidth * 2;
        const totalHeight = CanvasState.imageHeight;
        const scaleX = containerWidth / totalWidth;
        const scaleY = containerHeight / totalHeight;
        const scale = Math.min(scaleX, scaleY);
        CanvasState.canvas.setZoom(scale);
        CanvasState.currentScale = scale;
        CanvasState.minScale = scale;
        const scaledTotalWidth = totalWidth * scale;
        const scaledTotalHeight = totalHeight * scale;
        const left = (containerWidth - scaledTotalWidth) / 2;
        const top = (containerHeight - scaledTotalHeight) / 2;
        CanvasState.canvas.viewportTransform[4] = left;
        CanvasState.canvas.viewportTransform[5] = top;
        applyViewportBoundaries();
        CanvasState.canvas.requestRenderAll();
    } else {
        fitImageToCanvas();
    }
}

function updateSplitButtonActive(active) {
    const splitBtn = document.querySelector('.tool-button[data-tool="split"]');
    if (splitBtn) {
        if (active) {
            splitBtn.style.background = 'linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%)';
            splitBtn.style.color = '#4A90E2';
            splitBtn.style.borderColor = '#4A90E2';
        } else {
            splitBtn.style.background = '';
            splitBtn.style.color = '';
            splitBtn.style.borderColor = '';
        }
    }
}

// ==================== 导出 API ====================
window.canvasControls = {
    zoomIn: function() {
        if (!CanvasState.canvas || !CanvasState.comicImage) return;
        let zoom = CanvasState.canvas.getZoom() * 1.1;
        zoom = Math.min(zoom, CanvasState.maxScale);
        CanvasState.canvas.setZoom(zoom);
        CanvasState.currentScale = zoom;
        updateImagePosition();
    },
    
    zoomOut: function() {
        if (!CanvasState.canvas || !CanvasState.comicImage) return;
        let zoom = CanvasState.canvas.getZoom() * 0.9;
        zoom = Math.max(zoom, CanvasState.minScale);
        CanvasState.canvas.setZoom(zoom);
        CanvasState.currentScale = zoom;
        updateImagePosition();
    },
    
    resetZoom: function() {
        fitImageToCanvas();
    },
    
    loadLayers: loadLayers,

    switchTool: function(tool) {
        switchTool(tool);
    },
    
    setBrushSize: function(size) {
        setBrushSize(size);
    },

    setInpaintedOpacity: function(value) {
        CanvasState.currentInpaintOpacity = value;
        if (CanvasState.inpaintedImage) {
            CanvasState.inpaintedImage.set('opacity', value / 100);
            CanvasState.canvas.requestRenderAll();
        }
    },

    setTextLayerOpacity: function(value) {
        CanvasState.currentTextLayerOpacity = value;
        CanvasState.textImages.forEach(img => {
            if (img) img.set('opacity', value / 100);
        });
        CanvasState.canvas.requestRenderAll();
    },
    
    setTextBlockVisibility: function(index, visible) {
        if (CanvasState.textImages[index]) {
            CanvasState.textImages[index].set('visible', visible);
            CanvasState.canvas.requestRenderAll();
        }
    },

    updateCurrentPageData: function() {
        if (!window.projectPages || !window.currentImg || !CanvasState.textImages) return;
        const entries = window.projectPages[window.currentImg];
        if (!entries || entries.length !== CanvasState.textImages.length) {
            console.warn('页面条目数量不匹配，无法保存');
            return;
        }
        for (let i = 0; i < entries.length; i++) {
            const img = CanvasState.textImages[i];
            if (img) {
                entries[i].xyxy = [
                    Math.round(img.left),
                    Math.round(img.top),
                    Math.round(img.left + img.width * (img.scaleX || 1)),
                    Math.round(img.top + img.height * (img.scaleY || 1))
                ];
                entries[i].matched = img.visible ? 1 : 0;
            }
        }
    },

    highlightTextBlock: function(index, highlight) {
        const obj = CanvasState.textImages[index];
        if (!obj) return;
        obj.set('shadow', highlight ? '0 0 10px rgba(74, 144, 226, 0.8)' : null);
        CanvasState.canvas.requestRenderAll();
    },

    toggleWorkingReference: toggleWorkingReference,
    loadWorkingReference: loadWorkingReference,
    removeWorkingReference: function() {
        if (CanvasState.workingReferenceImage) {
            CanvasState.canvas.remove(CanvasState.workingReferenceImage);
            CanvasState.workingReferenceImage = null;
        }
        CanvasState.canvas.renderAll();
    },
    isWorkingReferenceVisible: function() { return CanvasState.workingReferenceVisible; },
    getInpaintedImage: function() {
        if (CanvasState.inpaintedImage) {
            return { image: CanvasState.inpaintedImage, width: CanvasState.imageWidth, height: CanvasState.imageHeight };
        }
        return null;
    },
    getComicImage: function() {
        if (CanvasState.comicImage) {
            return { image: CanvasState.comicImage, width: CanvasState.imageWidth, height: CanvasState.imageHeight };
        }
        return null;
    }
};

// 启动
document.addEventListener('DOMContentLoaded', () => setTimeout(initCanvas, 100));