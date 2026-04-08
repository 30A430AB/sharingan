let canvas;
let comicImage = null;
let inpaintedImage = null;
let currentOriginalUrl = null;
let currentInpaintedUrl = null;
let textImages = [];
let canvasContainer;
let minScale = 1;
let maxScale = 100;
let isDragging = false;
let lastPosX = 0;
let lastPosY = 0;
let currentScale = 1;
let imageWidth = 1920;
let imageHeight = 1080;
let currentRawOpacity = 100;
let currentTextOpacity = 100;          // 修复层透明度
let currentTextLayerOpacity = 100;      // 新增：文本层透明度，默认100
let workingReferenceImage = null;
let workingReferenceVisible = false;

let isDraggingText = false;
let draggedTextIndex = -1;
let dragStartX = 0, dragStartY = 0;
let dragStartLeft = 0, dragStartTop = 0;
let hoveredTextImage = null;

let currentTool = 'drag';
let isDrawingRect = false;
let rectStartPoint = null;
let tempRect = null;
let brushSize = 15;
let isProcessing = false;
let processingRect = null;

function initCanvas() {
    canvasContainer = document.getElementById('canvas-container');
    
    canvas = new fabric.Canvas('comic-canvas', {
        selection: false,
        backgroundColor: '#FAFAFA',
        preserveObjectStacking: true,
        renderOnAddRemove: true
    });

    canvas.defaultCursor = 'default';
    canvas.hoverCursor = 'default';
    canvas.moveCursor = 'default';
    
    resizeCanvas();
    
    window.addEventListener('resize', function() {
        resizeCanvas();
        updateImagePosition();
    });
    
    canvas.on('mouse:wheel', function(opt) {
        const delta = opt.e.deltaY;
        let zoom = canvas.getZoom();
        
        zoom = zoom * (1 - delta / 500);
        
        const containerWidth = canvasContainer.clientWidth;
        const containerHeight = canvasContainer.clientHeight;
        
        let targetWidth, targetHeight;
        if (workingReferenceImage && workingReferenceImage.visible) {
            targetWidth = imageWidth * 2;
            targetHeight = imageHeight;
        } else {
            targetWidth = imageWidth;
            targetHeight = imageHeight;
        }
        
        const minScaleX = containerWidth / targetWidth;
        const minScaleY = containerHeight / targetHeight;
        const newMinScale = Math.min(minScaleX, minScaleY);
        
        if (zoom < newMinScale) {
            zoom = newMinScale;
        }
        
        canvas.setZoom(zoom);
        currentScale = zoom;
        minScale = newMinScale;
        
        updateImagePosition();
        updateZoomDisplay();
        
        opt.e.preventDefault();
        opt.e.stopPropagation();
    });
    
    updateZoomDisplay();
    bindCanvasEvents();
    switchTool('drag');
    window.currentAlgorithm = window.ALGO_PATCHMATCH;
}

function switchTool(tool) {
    currentTool = tool;

    if (isDrawingRect && tempRect) {
        canvas.remove(tempRect);
        tempRect = null;
        isDrawingRect = false;
        rectStartPoint = null;
        canvas.renderAll();
    }

    isDrawingRect = false;
    canvas.isDrawingMode = false;
    if (tempRect) {
        canvas.remove(tempRect);
        tempRect = null;
        rectStartPoint = null;
    }

    document.querySelectorAll('.tool-button').forEach(btn => {
        btn.classList.remove('active');
        btn.style.background = '';
        btn.style.backgroundColor = '';
        btn.style.color = '';
        btn.style.borderColor = '';
        btn.style.borderWidth = '';
        btn.style.boxShadow = '';
    });

    const activeBtn = document.querySelector(`.tool-button[data-tool="${tool}"]`);
    if (activeBtn) {
        activeBtn.style.background = 'linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%)';
        activeBtn.style.color = '#4A90E2';
        activeBtn.style.borderColor = '#4A90E2';
        activeBtn.style.borderWidth = '1px';
        activeBtn.style.boxShadow = 'inset 0 2px 4px rgba(74,144,226,0.2)';
    }

    if (tool === 'brush' || tool === 'restoreBrush') {
        if (isProcessing) {
            canvas.isDrawingMode = false;
            console.log('正在处理中，无法使用画笔');
        } else {
            canvas.isDrawingMode = true;
            if (!canvas.freeDrawingBrush) {
                canvas.freeDrawingBrush = new fabric.PencilBrush(canvas);
            }
            if (tool === 'restoreBrush') {
                canvas.freeDrawingBrush.color = 'rgba(0, 255, 0, 0.5)';
            } else {
                canvas.freeDrawingBrush.color = 'rgba(0, 150, 255, 0.5)';
            }
            canvas.freeDrawingBrush.width = brushSize;
        }
    } else {
        canvas.isDrawingMode = false;
    }
}

function setBrushSize(size) {
    brushSize = size;
    
    if (canvas.freeDrawingBrush) {
        canvas.freeDrawingBrush.width = brushSize;
    }
    const brushValueElement = document.getElementById('brush-value');
    if (brushValueElement) {
        brushValueElement.textContent = size + 'px';
    }
}

function resizeCanvas() {
    if (!canvasContainer) return;
    
    const containerWidth = canvasContainer.clientWidth;
    const containerHeight = canvasContainer.clientHeight;
    
    canvas.setWidth(containerWidth);
    canvas.setHeight(containerHeight);
    
    canvas.calcViewportBoundaries();
    
    if (comicImage) {
        updateImagePosition();
    }
}

function fitImageToCanvas() {
    if (!comicImage || !canvasContainer) return;
    
    const containerWidth = canvasContainer.clientWidth;
    const containerHeight = canvasContainer.clientHeight;
    
    let targetWidth, targetHeight;
    if (workingReferenceImage && workingReferenceImage.visible) {
        targetWidth = imageWidth * 2;
        targetHeight = imageHeight;
    } else {
        targetWidth = imageWidth;
        targetHeight = imageHeight;
    }
    
    const scaleX = containerWidth / targetWidth;
    const scaleY = containerHeight / targetHeight;
    const scale = Math.min(scaleX, scaleY);
    
    canvas.setZoom(scale);
    currentScale = scale;
    minScale = scale;
    
    centerImage();
    updateZoomDisplay();
    canvas.requestRenderAll();
}

function centerImage() {
    if (!comicImage) return;
    
    const containerWidth = canvasContainer.clientWidth;
    const containerHeight = canvasContainer.clientHeight;
    
    let targetWidth, targetHeight;
    if (workingReferenceImage && workingReferenceImage.visible) {
        targetWidth = imageWidth * 2;
        targetHeight = imageHeight;
    } else {
        targetWidth = imageWidth;
        targetHeight = imageHeight;
    }
    
    const scaledWidth = targetWidth * currentScale;
    const scaledHeight = targetHeight * currentScale;
    
    const left = (containerWidth - scaledWidth) / 2;
    const top = (containerHeight - scaledHeight) / 2;
    
    canvas.viewportTransform[4] = left;
    canvas.viewportTransform[5] = top;
    
    applyViewportBoundaries();
}

function updateImagePosition() {
    if (!comicImage) return;
    
    applyViewportBoundaries();
    canvas.requestRenderAll();
}

function applyViewportBoundaries() {
    if (!comicImage || !canvasContainer) return;
    const containerWidth = canvasContainer.clientWidth;
    const containerHeight = canvasContainer.clientHeight;
    const vpt = canvas.viewportTransform;
    // 确定可视内容的边界（原始坐标）
    let minX = 0, maxX = imageWidth; // 默认只考虑原图
    if (workingReferenceImage && workingReferenceImage.visible) {
        minX = -imageWidth;
        maxX = imageWidth;
    }
    const scaledMinX = minX * currentScale;
    const scaledMaxX = maxX * currentScale;
    const scaledWidth = scaledMaxX - scaledMinX;
    const scaledHeight = imageHeight * currentScale;
    // 水平约束
    if (scaledWidth <= containerWidth) {
        vpt[4] = (containerWidth - scaledWidth) / 2 - scaledMinX;
    } else {
        const maxBound = -scaledMinX;
        const minBound = containerWidth - scaledMaxX;
        if (vpt[4] > maxBound) vpt[4] = maxBound;
        if (vpt[4] < minBound) vpt[4] = minBound;
    }
    // 垂直约束
    if (scaledHeight <= containerHeight) {
        vpt[5] = (containerHeight - scaledHeight) / 2;
    } else {
        if (vpt[5] > 0) vpt[5] = 0;
        if (vpt[5] + scaledHeight < containerHeight) vpt[5] = containerHeight - scaledHeight;
    }
}

function updateZoomDisplay() {
    const zoomPercent = Math.round(currentScale * 100);
}

function bindCanvasEvents() {
    canvas.off('mouse:down');
    canvas.off('mouse:move');
    canvas.off('mouse:up');
    canvas.on('mouse:down', onCanvasMouseDown);
    canvas.on('mouse:move', onCanvasMouseMove);
    canvas.on('mouse:up', onCanvasMouseUp);
    canvas.on('path:created', onPathCreated);
}

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(initCanvas, 100);
});

function onCanvasMouseDown(opt) {
    const evt = opt.e;
    const pointer = canvas.getPointer(opt.e);

    if (currentTool === 'rect' && !isProcessing) {
        isDrawingRect = true;
        rectStartPoint = { x: pointer.x, y: pointer.y };
        tempRect = new fabric.Rect({
            left: rectStartPoint.x,
            top: rectStartPoint.y,
            width: 0,
            height: 0,
            fill: 'rgba(0, 150, 255, 0.3)',
            stroke: 'blue',
            strokeWidth: 1,
            selectable: false,
            evented: false
        });
        canvas.add(tempRect);
        evt.preventDefault();
        return;
    }

    if (currentTool === 'drag') {
        const hitIndex = textImages.findIndex(img => {
            if (!img.visible) return false;
            const left = img.left;
            const top = img.top;
            const right = left + img.width * (img.scaleX || 1);
            const bottom = top + img.height * (img.scaleY || 1);
            return pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom;
        });
        if (hitIndex >= 0) {
            isDraggingText = true;
            draggedTextIndex = hitIndex;
            dragStartX = pointer.x;
            dragStartY = pointer.y;
            dragStartLeft = textImages[hitIndex].left;
            dragStartTop = textImages[hitIndex].top;
            evt.preventDefault();
            return;
        }
    }

    if (currentTool === 'drag') {
        if (currentScale > minScale) {
            isDragging = true;
            lastPosX = evt.clientX;
            lastPosY = evt.clientY;
            canvas.selection = false;
        }
    }
}

function onCanvasMouseMove(opt) {
    const pointer = canvas.getPointer(opt.e);
    const evt = opt.e;

    if (isDrawingRect && tempRect) {
        const currentX = pointer.x;
        const currentY = pointer.y;
        const left = Math.min(rectStartPoint.x, currentX);
        const top = Math.min(rectStartPoint.y, currentY);
        const width = Math.abs(currentX - rectStartPoint.x);
        const height = Math.abs(currentY - rectStartPoint.y);
        tempRect.set({ left, top, width, height });
        canvas.renderAll();
        return;
    }

    if (isDraggingText) {
        const deltaX = pointer.x - dragStartX;
        const deltaY = pointer.y - dragStartY;
        const targetImg = textImages[draggedTextIndex];
        if (targetImg) {
            targetImg.set({
                left: dragStartLeft + deltaX,
                top: dragStartTop + deltaY
            });
            canvas.renderAll();
        }
        return;
    }

    if (currentTool === 'drag' && isDragging) {
        const deltaX = evt.clientX - lastPosX;
        const deltaY = evt.clientY - lastPosY;
        const vpt = canvas.viewportTransform;
        vpt[4] += deltaX;
        vpt[5] += deltaY;
        applyViewportBoundaries();
        canvas.requestRenderAll();
        lastPosX = evt.clientX;
        lastPosY = evt.clientY;
    }

    if (!isDraggingText && !isDragging && !isDrawingRect) {
        const hit = textImages.find(img => {
            if (!img.visible) return false;
            const left = img.left;
            const top = img.top;
            const right = left + img.width * (img.scaleX || 1);
            const bottom = top + img.height * (img.scaleY || 1);
            return pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom;
        });

        if (hit) {
            if (hoveredTextImage && hoveredTextImage !== hit) {
                hoveredTextImage.set('shadow', null);
            }
            if (!hit.shadow) {
                hit.set('shadow', '0 0 10px rgba(74, 144, 226, 0.8)');
                canvas.defaultCursor = 'pointer';
                hoveredTextImage = hit;
                canvas.renderAll();
            }
        } else {
            if (hoveredTextImage) {
                hoveredTextImage.set('shadow', null);
                hoveredTextImage = null;
                canvas.defaultCursor = 'default';
                canvas.renderAll();
            }
        }
    }
}

function onCanvasMouseUp(opt) {
    if (isDrawingRect && tempRect) {
        isDrawingRect = false;
        const width = tempRect.width;
        const height = tempRect.height;
        if (width < 5 || height < 5) {
            canvas.remove(tempRect);
            tempRect = null;
            rectStartPoint = null;
            canvas.renderAll();
            return;
        }

        // 获取矩形在画布坐标系中的位置
        let left = tempRect.left;
        let top = tempRect.top;
        let right = left + width;
        let bottom = top + height;

        // 获取图片的实际边界（图片坐标系，原尺寸）
        // 注意：comicImage 在加载后已经设置了尺寸 imageWidth/imageHeight
        const imgLeft = 0;
        const imgTop = 0;
        const imgRight = imageWidth;
        const imgBottom = imageHeight;

        // 计算与图片区域的交集
        const intersectLeft = Math.max(left, imgLeft);
        const intersectTop = Math.max(top, imgTop);
        const intersectRight = Math.min(right, imgRight);
        const intersectBottom = Math.min(bottom, imgBottom);
        const intersectWidth = intersectRight - intersectLeft;
        const intersectHeight = intersectBottom - intersectTop;

        // 如果没有交集（矩形完全在图片外部），则不进行修复
        if (intersectWidth <= 0 || intersectHeight <= 0) {
            canvas.remove(tempRect);
            tempRect = null;
            rectStartPoint = null;
            canvas.renderAll();
            return;
        }

        // 使用裁剪后的矩形坐标和尺寸（相对于图片）
        const x = intersectLeft;
        const y = intersectTop;
        const w = intersectWidth;
        const h = intersectHeight;

        isProcessing = true;
        canvas.isDrawingMode = false;
        canvas.selection = false;

        processingRect = tempRect; // 保留原始矩形用于删除
        tempRect = null;
        rectStartPoint = null;

        (async () => {
            try {
                let sourceImage = inpaintedImage || comicImage;
                if (!sourceImage) {
                    throw new Error('没有可用的图片');
                }
                const imageDataURL = sourceImage.toDataURL({ format: 'png' });

                const formData = new FormData();
                formData.append('image', dataURItoBlob(imageDataURL), 'image.png');
                formData.append('x', Math.round(x));
                formData.append('y', Math.round(y));
                formData.append('w', Math.round(w));
                formData.append('h', Math.round(h));
                const algorithm = window.currentAlgorithm || window.ALGO_PATCHMATCH;
                formData.append('algorithm', algorithm);

                const response = await fetch('/process_rect_inpaint', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.imageUrl) {
                    await new Promise((resolve, reject) => {
                        fabric.Image.fromURL(result.imageUrl, (img) => {
                            if (inpaintedImage) canvas.remove(inpaintedImage);
                            inpaintedImage = img;
                            inpaintedImage.set({
                                selectable: false,
                                hasControls: false,
                                hasBorders: false,
                                left: 0,
                                top: 0
                            });
                            if (Math.abs(inpaintedImage.width - imageWidth) > 1 || Math.abs(inpaintedImage.height - imageHeight) > 1) {
                                inpaintedImage.scaleToWidth(imageWidth);
                                inpaintedImage.scaleToHeight(imageHeight);
                            }
                            canvas.add(inpaintedImage);
                            if (comicImage) {
                                const comicIndex = canvas.getObjects().indexOf(comicImage);
                                canvas.moveTo(inpaintedImage, comicIndex + 1);
                            } else {
                                canvas.sendToBack(inpaintedImage);
                            }
                            inpaintedImage.set('opacity', currentTextOpacity / 100);
                            canvas.renderAll();
                            resolve();
                        });
                    });
                }
            } catch (error) {
                console.error('矩形修复失败:', error);
            } finally {
                if (processingRect) {
                    canvas.remove(processingRect);
                    processingRect = null;
                }
                isProcessing = false;
                if (currentTool === 'brush' || currentTool === 'restoreBrush') {
                    canvas.isDrawingMode = true;
                    if (!canvas.freeDrawingBrush) {
                        canvas.freeDrawingBrush = new fabric.PencilBrush(canvas);
                    }
                    canvas.freeDrawingBrush.color = currentTool === 'restoreBrush' ? 'rgba(0,255,0,0.5)' : 'rgba(0,150,255,0.5)';
                    canvas.freeDrawingBrush.width = brushSize;
                }
                canvas.renderAll();
            }
        })();

        opt.e.preventDefault();
        return;
    }

    if (currentTool === 'brush') {
        return;
    }
    if (isDraggingText) {
        isDraggingText = false;
        draggedTextIndex = -1;
        canvas.renderAll();
        return;
    }

    if (currentTool === 'drag') {
        isDragging = false;
        canvas.selection = false;
    }
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

function loadLayers(originalUrl, inpaintedUrl, textBlocks) {
    return new Promise((resolve, reject) => {
        // 清除旧的参考图，避免残留
        if (workingReferenceImage) {
            canvas.remove(workingReferenceImage);
            workingReferenceImage = null;
            workingReferenceVisible = false;
            updateSplitButtonActive(false);  // 更新分栏按钮样式
        }
        if (!canvas) {
            reject('Canvas not initialized');
            return;
        }

        const loadImagePromise = (url) => {
            return new Promise((res, rej) => {
                if (!url) {
                    res(null);
                    return;
                }
                fabric.Image.fromURL(url, (img) => {
                    if (img) res(img);
                    else rej(new Error(`Failed to load image: ${url}`));
                });
            });
        };

        // 移除旧对象
        if (comicImage) canvas.remove(comicImage);
        if (inpaintedImage) canvas.remove(inpaintedImage);
        textImages.forEach(img => canvas.remove(img));
        textImages = [];

        Promise.all([loadImagePromise(originalUrl), loadImagePromise(inpaintedUrl)])
            .then(([originalImg, inpaintedImg]) => {
                comicImage = originalImg;
                comicImage.set({
                    selectable: false,
                    hasControls: false,
                    hasBorders: false,
                    left: 0,
                    top: 0
                });
                canvas.add(comicImage);
                canvas.sendToBack(comicImage);
                comicImage.set('opacity', currentRawOpacity / 100);

                imageWidth = comicImage.width;
                imageHeight = comicImage.height;

                if (inpaintedImg) {
                    inpaintedImage = inpaintedImg;
                    inpaintedImage.set({
                        selectable: false,
                        hasControls: false,
                        hasBorders: false,
                        left: 0,
                        top: 0
                    });
                    if (Math.abs(inpaintedImage.width - imageWidth) > 1 || Math.abs(inpaintedImage.height - imageHeight) > 1) {
                        inpaintedImage.scaleToWidth(imageWidth);
                        inpaintedImage.scaleToHeight(imageHeight);
                    }
                    canvas.add(inpaintedImage);
                    canvas.bringForward(inpaintedImage);
                    inpaintedImage.set('opacity', currentTextOpacity / 100);
                }

                currentOriginalUrl = originalUrl;
                currentInpaintedUrl = inpaintedUrl;

                if (textBlocks && textBlocks.length > 0) {
                    const textPromises = textBlocks.map(block => {
                        return loadImagePromise(block.imageUrl).then(img => {
                            if (img) {
                                img.set({
                                    left: block.left,
                                    top: block.top,
                                    selectable: false,
                                    hasControls: false,
                                    hasBorders: false,
                                    visible: block.visible !== undefined ? block.visible : true,
                                    evented: true,
                                    hoverCursor: 'pointer',
                                });
                                return img; // 返回图像对象
                            }
                            return null;
                        });
                    });
                    return Promise.all(textPromises).then(images => {
                        images.forEach((img, idx) => {
                            if (img) {
                                canvas.add(img);
                                textImages[idx] = img; // 按索引赋值，保证顺序
                            }
                        });
                        // 然后设置透明度等
                        textImages.forEach(img => {
                            img.set('opacity', currentTextLayerOpacity / 100);
                        });
                        if (inpaintedImage) {
                            inpaintedImage.set('opacity', currentTextOpacity / 100);
                        }
                        fitImageToCanvas();
                        canvas.renderAll();
                        bindCanvasEvents();
                        resolve();
                    });
                } else {
                    fitImageToCanvas();
                    canvas.renderAll();
                    bindCanvasEvents();
                    resolve();
                }
            })
            .catch(err => {
                console.error('Error loading layers:', err);
                reject(err);
            });
    });
}

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

function generateMaskFromPath(path) {
    return new Promise((resolve) => {
        const containerWidth = canvasContainer.clientWidth;
        const containerHeight = canvasContainer.clientHeight;

        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = containerWidth;
        tempCanvas.height = containerHeight;
        const tempCtx = tempCanvas.getContext('2d');
        tempCtx.fillStyle = '#000000';
        tempCtx.fillRect(0, 0, containerWidth, containerHeight);

        const fabricTempCanvas = new fabric.StaticCanvas(tempCanvas, { enableRetinaScaling: false });
        fabricTempCanvas.setViewportTransform(canvas.viewportTransform);
        path.clone(function(clonedPath) {
            fabricTempCanvas.add(clonedPath);
            fabricTempCanvas.renderAll();

            const scaledWidth = imageWidth * currentScale;
            const scaledHeight = imageHeight * currentScale;
            const vpt = canvas.viewportTransform;
            const imageLeft = vpt[4];
            const imageTop = vpt[5];

            const maskCanvas = document.createElement('canvas');
            maskCanvas.width = imageWidth;
            maskCanvas.height = imageHeight;
            const maskCtx = maskCanvas.getContext('2d');
            maskCtx.fillStyle = '#000000';
            maskCtx.fillRect(0, 0, imageWidth, imageHeight);
            maskCtx.drawImage(
                tempCanvas,
                imageLeft, imageTop, scaledWidth, scaledHeight,
                0, 0, imageWidth, imageHeight
            );

            const imageData = maskCtx.getImageData(0, 0, imageWidth, imageHeight);
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

function restoreUsingMask(maskCanvas) {
    return new Promise((resolve, reject) => {
        const width = imageWidth;
        const height = imageHeight;

        if (!comicImage) {
            reject('没有原图');
            return;
        }

        const originalCanvas = fabric.util.createCanvasElement();
        originalCanvas.width = width;
        originalCanvas.height = height;
        const originalCtx = originalCanvas.getContext('2d');
        const comicElement = comicImage.getElement();
        originalCtx.drawImage(comicElement, 0, 0, width, height);

        const targetCanvas = fabric.util.createCanvasElement();
        targetCanvas.width = width;
        targetCanvas.height = height;
        const targetCtx = targetCanvas.getContext('2d');
        if (inpaintedImage) {
            const inpaintedElement = inpaintedImage.getElement();
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
            if (inpaintedImage) {
                canvas.remove(inpaintedImage);
            }
            inpaintedImage = img;
            inpaintedImage.set({
                selectable: false,
                hasControls: false,
                hasBorders: false,
                left: 0,
                top: 0
            });
            if (Math.abs(inpaintedImage.width - width) > 1 || Math.abs(inpaintedImage.height - height) > 1) {
                inpaintedImage.scaleToWidth(width);
                inpaintedImage.scaleToHeight(height);
            }
            canvas.add(inpaintedImage);

            if (comicImage) {
                const comicIndex = canvas.getObjects().indexOf(comicImage);
                canvas.moveTo(inpaintedImage, comicIndex + 1);
            } else {
                canvas.sendToBack(inpaintedImage);
            }

            inpaintedImage.set('opacity', currentTextOpacity / 100);
            canvas.renderAll();
            resolve();
        });
    });
}

async function onPathCreated(e) {
    if (isProcessing) return;

    const path = e.path;

    if (!comicImage) {
        console.error('没有原图，无法处理');
        canvas.remove(path);
        return;
    }

    isProcessing = true;
    canvas.isDrawingMode = false;
    path.set('evented', false);
    canvas.selection = false;
    canvas.renderAll();

    try {
        const maskCanvas = await generateMaskFromPath(path);

        // 检查生成的蒙版是否全黑（即路径完全在图片外部）
        if (isMaskBlank(maskCanvas)) {
            console.log('绘制区域完全在图片外部，跳过修复');
            return;
        }

        if (currentTool === 'brush') {
            const bbox = path.getBoundingRect();
            const vpt = canvas.viewportTransform;
            const scale = currentScale;

            let imgLeft = (bbox.left - vpt[4]) / scale;
            let imgTop = (bbox.top - vpt[5]) / scale;
            let imgRight = (bbox.left + bbox.width - vpt[4]) / scale;
            let imgBottom = (bbox.top + bbox.height - vpt[5]) / scale;

            const expandX = Math.max((bbox.width / scale) * 5, 50);
            const expandY = Math.max((bbox.height / scale) * 5, 50);

            let cropX = Math.max(0, Math.floor(imgLeft - expandX));
            let cropY = Math.max(0, Math.floor(imgTop - expandY));
            let cropW = Math.min(imageWidth - cropX, Math.ceil(imgRight - imgLeft + 2 * expandX));
            let cropH = Math.min(imageHeight - cropY, Math.ceil(imgBottom - imgTop + 2 * expandY));

            let sourceImage = inpaintedImage || comicImage;
            if (!sourceImage) throw new Error('没有可用的图片');

            const sourceElement = sourceImage.getElement();
            const subImageDataURL = cropImage(sourceElement, cropX, cropY, cropW, cropH);
            const subMaskDataURL = cropImage(maskCanvas, cropX, cropY, cropW, cropH);

            const formData = new FormData();
            formData.append('image', dataURItoBlob(subImageDataURL), 'image.png');
            formData.append('mask', dataURItoBlob(subMaskDataURL), 'mask.png');
            const algorithm = window.currentAlgorithm || window.ALGO_PATCHMATCH;
            formData.append('algorithm', algorithm);

            const response = await fetch('/process_inpaint', { method: 'POST', body: formData });
            const result = await response.json();
            if (result.imageUrl) {
                await mergeRepairedRegion(result.imageUrl, cropX, cropY, cropW, cropH);
            }
        } else if (currentTool === 'restoreBrush') {
            await restoreUsingMask(maskCanvas);
        }
    } catch (error) {
        console.error('处理路径时出错:', error);
    } finally {
        const paths = canvas.getObjects().filter(obj => obj.type === 'path');
        paths.forEach(p => canvas.remove(p));
        isProcessing = false;
        if (currentTool === 'brush' || currentTool === 'restoreBrush') {
            canvas.isDrawingMode = true;
            if (!canvas.freeDrawingBrush) {
                canvas.freeDrawingBrush = new fabric.PencilBrush(canvas);
            }
            canvas.freeDrawingBrush.color = currentTool === 'restoreBrush' 
                ? 'rgba(0, 255, 0, 0.5)'
                : 'rgba(0, 150, 255, 0.5)';
            canvas.freeDrawingBrush.width = brushSize;
        }
        canvas.renderAll();
    }
}

window.canvasControls = {
    zoomIn: function() {
        if (!canvas || !comicImage) return;
        
        let zoom = canvas.getZoom();
        zoom = zoom * 1.1;
        
        if (zoom > maxScale) {
            zoom = maxScale;
        }
        
        canvas.setZoom(zoom);
        currentScale = zoom;
        updateImagePosition();
        updateZoomDisplay();
    },
    
    zoomOut: function() {
        if (!canvas || !comicImage) return;
        
        let zoom = canvas.getZoom();
        zoom = zoom * 0.9;
        
        if (zoom < minScale) {
            zoom = minScale;
        }
        
        canvas.setZoom(zoom);
        currentScale = zoom;
        updateImagePosition();
        updateZoomDisplay();
    },
    
    resetZoom: function() {
        if (!canvas || !comicImage) return;
        
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
        currentTextOpacity = value;
        if (inpaintedImage) {
            inpaintedImage.set('opacity', value / 100);
            canvas.requestRenderAll();
        }
    },

    // 修改：保存当前文本层透明度值并应用到现有文本块
    setTextLayerOpacity: function(value) {
        currentTextLayerOpacity = value;
        if (textImages && textImages.length > 0) {
            textImages.forEach(img => {
                img.set('opacity', value / 100);
            });
            canvas.requestRenderAll();
        }
    },
    
    setTextBlockVisibility: function(index, visible) {
        if (textImages && textImages[index]) {
            textImages[index].set('visible', visible);
            canvas.requestRenderAll();
        }
    },
    updateCurrentPageData: function() {
        if (!window.projectPages || !window.currentImg || !textImages) return;
        const entries = window.projectPages[window.currentImg];
        if (!entries || entries.length !== textImages.length) {
            console.warn('页面条目数量不匹配，无法保存');
            return;
        }
        for (let i = 0; i < entries.length; i++) {
            const img = textImages[i];
            if (img) {
                // 更新 xyxy 为当前 left, top 加上宽高（考虑缩放）
                const newXyxy = [
                    Math.round(img.left),
                    Math.round(img.top),
                    Math.round(img.left + img.width * (img.scaleX || 1)),
                    Math.round(img.top + img.height * (img.scaleY || 1))
                ];
                entries[i].xyxy = newXyxy;
                entries[i].matched = img.visible ? 1 : 0;
                // orig_xyxy 和 raw_xyxy 保持不变（不更新）
            }
        }
        console.log('当前页面数据已同步到项目数据');
    },

    // 新增：高亮或取消高亮指定索引的文本块（使用与鼠标悬停相同的蓝色阴影）
    highlightTextBlock: function(index, highlight) {
        if (!textImages || index < 0 || index >= textImages.length) return;
        const obj = textImages[index];
        if (!obj) return;
        if (highlight) {
            obj.set('shadow', '0 0 10px rgba(74, 144, 226, 0.8)');
        } else {
            obj.set('shadow', null);
        }
        canvas.requestRenderAll();
    },

    toggleWorkingReference: toggleWorkingReference,
    loadWorkingReference: loadWorkingReference,
    removeWorkingReference: function() {
        if (workingReferenceImage) {
            canvas.remove(workingReferenceImage);
            workingReferenceImage = null;
            // 不改变 workingReferenceVisible 状态，只移除对象
        }
        canvas.renderAll();
    },
    isWorkingReferenceVisible: function() { return workingReferenceVisible; }
};

function cropImage(source, x, y, w, h) {
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(source, x, y, w, h, 0, 0, w, h);
    return canvas.toDataURL('image/png');
}

function mergeRepairedRegion(repairedImageUrl, x, y, w, h) {
    return new Promise((resolve) => {
        fabric.Image.fromURL(repairedImageUrl, (repairedImg) => {
            const offscreenCanvas = document.createElement('canvas');
            offscreenCanvas.width = imageWidth;
            offscreenCanvas.height = imageHeight;
            const offscreenCtx = offscreenCanvas.getContext('2d');
            
            const sourceImage = inpaintedImage || comicImage;
            if (!sourceImage) {
                resolve();
                return;
            }
            const sourceElement = sourceImage.getElement();
            offscreenCtx.drawImage(sourceElement, 0, 0, imageWidth, imageHeight);
            
            offscreenCtx.drawImage(repairedImg.getElement(), x, y, w, h);
            
            const newDataURL = offscreenCanvas.toDataURL('image/png');
            
            fabric.Image.fromURL(newDataURL, (newImg) => {
                if (inpaintedImage) canvas.remove(inpaintedImage);
                inpaintedImage = newImg;
                inpaintedImage.set({
                    selectable: false,
                    hasControls: false,
                    hasBorders: false,
                    left: 0,
                    top: 0
                });
                if (Math.abs(inpaintedImage.width - imageWidth) > 1 || Math.abs(inpaintedImage.height - imageHeight) > 1) {
                    inpaintedImage.scaleToWidth(imageWidth);
                    inpaintedImage.scaleToHeight(imageHeight);
                }
                canvas.add(inpaintedImage);
                if (comicImage) {
                    const comicIndex = canvas.getObjects().indexOf(comicImage);
                    canvas.moveTo(inpaintedImage, comicIndex + 1);
                } else {
                    canvas.sendToBack(inpaintedImage);
                }
                inpaintedImage.set('opacity', currentTextOpacity / 100);
                canvas.renderAll();
                resolve();
            });
        });
    });
}

function updateSplitButtonActive(active) {
    const splitBtn = document.querySelector('.tool-button[data-tool="split"]');
    if (splitBtn) {
        if (active) {
            splitBtn.style.background = 'linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%)';
            splitBtn.style.color = '#4A90E2';
            splitBtn.style.borderColor = '#4A90E2';
            splitBtn.style.borderWidth = '1px';
            splitBtn.style.boxShadow = 'inset 0 2px 4px rgba(74,144,226,0.2)';
        } else {
            splitBtn.style.background = '';
            splitBtn.style.color = '';
            splitBtn.style.borderColor = '';
            splitBtn.style.borderWidth = '';
            splitBtn.style.boxShadow = '';
        }
    }
}

function loadWorkingReference() {
    if (!window.projectDirectory || !window.currentImg) {
        window.showToast && window.showToast('没有加载项目', 'error');
        return Promise.reject('No project loaded');
    }
    if (!comicImage) {
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
            console.error('Failed to load working reference:', data.error);
            window.showToast && window.showToast('加载参考图失败：' + data.error, 'error');
            return;
        }
        if (data.imageUrl) {
            return new Promise((resolve, reject) => {
                fabric.Image.fromURL(data.imageUrl, (img) => {
                    if (!img) {
                        reject('Failed to create image from URL');
                        return;
                    }
                    // 移除旧的参考图
                    if (workingReferenceImage) {
                        canvas.remove(workingReferenceImage);
                    }
                    workingReferenceImage = img;
                    workingReferenceImage.set({
                        left: -imageWidth,          // 放在原图左侧
                        top: 0,
                        selectable: false,
                        hasControls: false,
                        hasBorders: false,
                        evented: false,            // 不响应鼠标事件
                        hoverCursor: 'default'
                    });
                    canvas.add(workingReferenceImage);
                    canvas.sendToBack(workingReferenceImage); // 置于底层，但不会覆盖原图位置
                    workingReferenceVisible = true;
                    updateSplitButtonActive(true);
                    adjustViewToIncludeReference(true);
                    canvas.renderAll();
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
        window.showToast && window.showToast('没有加载项目', 'error');
        return;
    }
    if (!comicImage) {
        window.showToast && window.showToast('请先加载图片', 'error');
        return;
    }
    if (workingReferenceImage) {
        // 已存在，切换可见性
        workingReferenceVisible = !workingReferenceVisible;
        workingReferenceImage.set('visible', workingReferenceVisible);
        updateSplitButtonActive(workingReferenceVisible);
        if (workingReferenceVisible) {
            adjustViewToIncludeReference(true);
        } else {
            fitImageToCanvas();
        }
        canvas.renderAll();
    } else {
        // 不存在，加载并显示
        loadWorkingReference();
    }
}

function adjustViewToIncludeReference(include) {
    if (!comicImage || !canvasContainer) return;
    if (include && workingReferenceImage && workingReferenceImage.visible) {
        const containerWidth = canvasContainer.clientWidth;
        const containerHeight = canvasContainer.clientHeight;
        const totalWidth = imageWidth * 2;
        const totalHeight = imageHeight;
        const scaleX = containerWidth / totalWidth;
        const scaleY = containerHeight / totalHeight;
        const scale = Math.min(scaleX, scaleY);
        canvas.setZoom(scale);
        currentScale = scale;
        minScale = scale;
        const scaledTotalWidth = totalWidth * scale;
        const scaledTotalHeight = totalHeight * scale;
        const left = (containerWidth - scaledTotalWidth) / 2;
        const top = (containerHeight - scaledTotalHeight) / 2;
        canvas.viewportTransform[4] = left;
        canvas.viewportTransform[5] = top;
        applyViewportBoundaries();
        canvas.requestRenderAll();
    } else {
        fitImageToCanvas();
    }
}