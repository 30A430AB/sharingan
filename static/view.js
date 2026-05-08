// ==================== 状态管理 ====================
window.projectPages = null;           // 当前工作数据（可修改）
window.initialProjectPages = null;    // 初始只读数据（用于重置）
window.projectDirectory = null;
window.currentImg = null;

// 缓存上一次高亮的缩略图元素，用于性能优化
let lastHighlightedCard = null;

// ==================== 工具函数 ====================
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.textContent = message;
    toast.style.position = 'fixed';
    toast.style.top = '20px';
    toast.style.left = '50%';
    toast.style.transform = 'translateX(-50%)';
    toast.style.padding = '12px 20px';
    toast.style.backgroundColor = type === 'success' ? '#4caf50' : (type === 'error' ? '#f44336' : '#2196f3');
    toast.style.color = 'white';
    toast.style.borderRadius = '4px';
    toast.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
    toast.style.zIndex = '99999';
    toast.style.fontSize = '14px';
    toast.style.transition = 'opacity 0.3s ease';
    toast.style.opacity = '1';
    toast.style.pointerEvents = 'none';
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => {
            if (toast.parentNode) toast.remove();
        }, 300);
    }, 3000);
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

// ==================== 项目加载与保存 ====================
window.selectProjectFile = function() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = function(e) {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function(ev) {
            const jsonData = ev.target.result;
            fetch('/load_project', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: jsonData
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    showToast('Error: ' + data.error, 'error');
                    return;
                }
                // 初始化状态
                window.projectDirectory = data.directory;
                window.currentImg = data.current_img;
                
                const rawPages = data.pages || {};
                window.projectPages = {};
                for (const [k, v] of Object.entries(rawPages)) {
                    if (Array.isArray(v)) {
                        // 兼容旧格式（纯数组）
                        window.projectPages[k] = v;
                    } else if (v && typeof v === 'object') {
                        // 新格式：将 { entries: [...], textBoxes: [...] } 转为带属性的数组
                        const arr = v.entries || [];
                        arr.textBoxes = v.textBoxes || [];
                        window.projectPages[k] = arr;
                    } else {
                        window.projectPages[k] = [];
                    }
                }
                window.initialProjectPages = JSON.parse(JSON.stringify(window.projectPages));

                if (data.imageUrl) {
                    window.canvasControls.loadLayers(data.imageUrl, data.inpaintedImageUrl, data.textBlocks);
                    if (window.canvasControls.setRegions) {
                        window.canvasControls.setRegions(data.regions);
                    }
                } else {
                    console.warn('No imageUrl in response');
                }
                updateTextBlocks(data.textBlocks);
                if (data.thumbnails && data.thumbnails.length > 0) {
                    generateThumbnails(data.thumbnails, data.directory);
                    highlightCurrentThumbnail(window.currentImg);
                } else {
                    console.warn('No thumbnails received');
                }
            })
            .catch(err => {
                console.error('Error loading project:', err);
                showToast('Failed to load project', 'error');
            });
        };
        reader.readAsText(file);
    };
    input.click();
};

function generateThumbnails(thumbnails, directory) {
    const container = document.querySelector('[name="thumbnail-list"]');
    if (!container) {
        console.error('Thumbnail container not found');
        return;
    }
    container.innerHTML = '';
    const innerDiv = document.createElement('div');
    innerDiv.className = 'thumbnail-container';

    // 使用 DocumentFragment 批量添加，减少重排
    const fragment = document.createDocumentFragment();
    thumbnails.forEach(item => {
        const key = item.key;
        const thumbUrl = item.thumb_url;
        const card = document.createElement('div');
        card.className = 'thumbnail-card';
        card.setAttribute('data-key', key);
        const img = document.createElement('img');
        img.className = 'thumbnail-image';
        img.src = thumbUrl;
        img.alt = `缩略图${key}`;
        card.appendChild(img);
        const numberDiv = document.createElement('div');
        numberDiv.style.position = 'absolute';
        numberDiv.style.bottom = '4px';
        numberDiv.style.left = '0';
        numberDiv.style.right = '0';
        numberDiv.style.color = 'black';
        numberDiv.style.textAlign = 'center';
        numberDiv.style.padding = '2px 0';
        numberDiv.style.fontSize = 'clamp(10px, 2vw, 15px)';
        numberDiv.textContent = key;
        card.appendChild(numberDiv);
        card.addEventListener('click', () => loadImage(key, directory));
        fragment.appendChild(card);
    });
    innerDiv.appendChild(fragment);
    container.appendChild(innerDiv);
}

function loadImage(key, directory) {
    // 保存参考图可见状态
    const wasWorkingVisible = window.canvasControls?.isWorkingReferenceVisible?.() || false;
    if (window.canvasControls?.removeWorkingReference) {
        window.canvasControls.removeWorkingReference();
    }

    // 从初始只读数据中获取该页的条目（真正的原始数据）
    const initialEntries = window.initialProjectPages ? window.initialProjectPages[key] : [];
    
    // 重置当前工作数据为该页的初始数据（丢弃未保存的修改）
    if (window.projectPages && key) {
        const clonedEntries = JSON.parse(JSON.stringify(initialEntries));
        // ========== 兼容保留挂在数组上的 textBoxes 属性 ==========
        if (initialEntries.textBoxes) {
            clonedEntries.textBoxes = JSON.parse(JSON.stringify(initialEntries.textBoxes));
        }

        window.projectPages[key] = clonedEntries;
    }

    fetch('/get_image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ directory: directory, key: key, entries: initialEntries })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.originalImageUrl) {
            throw new Error(data.error || '未知错误');
        }
        window.currentImg = key;
        return window.canvasControls.loadLayers(data.originalImageUrl, data.inpaintedImageUrl, data.textBlocks)
            .then(() => {
                updateTextBlocks(data.textBlocks, key);
                highlightCurrentThumbnail(key);
                if (wasWorkingVisible) {
                    return window.canvasControls.loadWorkingReference();
                }
            })
            .then(() => {
                // 自动保存当前页索引到项目文件（不阻塞UI）
                fetch('/update_current_page', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ directory: directory, current_img: key })
                }).catch(err => console.warn('自动保存当前页失败:', err));
            });
    })
    .catch(err => {
        console.error('Error loading image:', err);
        showToast('加载图片失败：' + err.message, 'error');
    });
}

function updateTextBlocks(textBlocks, pageKey) {
    const countElement = document.getElementById('text-block-count');
    if (countElement) {
        countElement.textContent = `文本块 (${textBlocks.length})`;
    }
    const container = document.getElementById('text-block-list');
    if (!container) return;
    container.innerHTML = '';

    textBlocks.forEach((block, index) => {
        // 从当前工作数据中获取可见性状态（优先）
        let currentVisible = block.visible;
        if (window.projectPages?.[pageKey]?.[index]) {
            const entry = window.projectPages[pageKey][index];
            currentVisible = (entry.matched === 1);
        }

        const card = document.createElement('div');
        card.className = 'text-block-card';
        card.setAttribute('data-index', index);

        const imageDiv = document.createElement('div');
        imageDiv.className = 'text-block-image';
        const img = document.createElement('img');
        img.src = block.imageUrl;
        img.alt = `文本块${index+1}`;
        imageDiv.appendChild(img);
        card.appendChild(imageDiv);

        const footerDiv = document.createElement('div');
        footerDiv.className = 'text-block-footer';

        const eyeBtn = document.createElement('div');
        eyeBtn.className = 'text-block-button';
        const initialIcon = currentVisible ? 'visibility' : 'visibility_off';
        eyeBtn.innerHTML = `<span class="material-icons">${initialIcon}</span>`;
        eyeBtn.setAttribute('data-index', index);

        eyeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const newVisible = !block.visible;
            block.visible = newVisible;
            eyeBtn.innerHTML = `<span class="material-icons">${newVisible ? 'visibility' : 'visibility_off'}</span>`;
            if (window.canvasControls?.setTextBlockVisibility) {
                window.canvasControls.setTextBlockVisibility(index, newVisible);
            }
            // 立即同步到工作数据
            if (window.projectPages?.[pageKey]?.[index]) {
                window.projectPages[pageKey][index].matched = newVisible ? 1 : 0;
            }
        });

        footerDiv.appendChild(eyeBtn);
        card.appendChild(footerDiv);

        card.addEventListener('mouseenter', () => {
            if (window.canvasControls?.highlightTextBlock) {
                window.canvasControls.highlightTextBlock(index, true);
            }
        });
        card.addEventListener('mouseleave', () => {
            if (window.canvasControls?.highlightTextBlock) {
                window.canvasControls.highlightTextBlock(index, false);
            }
        });

        container.appendChild(card);
    });
}

window.loadProjectFromData = function(data) {
    const payload = typeof data === 'string' ? data : JSON.stringify(data);
    fetch('/load_project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showToast('Error: ' + data.error, 'error');
            return;
        }
        window.projectDirectory = data.directory;
        window.currentImg = data.current_img;
        window.projectPages = JSON.parse(JSON.stringify(data.pages));
        window.initialProjectPages = JSON.parse(JSON.stringify(data.pages));

        if (data.imageUrl) {
            window.canvasControls.loadLayers(data.imageUrl, data.inpaintedImageUrl, data.textBlocks);
            if (window.canvasControls.setRegions) {
                window.canvasControls.setRegions(data.regions);
            }
        } else {
            console.warn('No imageUrl in response');
        }
        updateTextBlocks(data.textBlocks);
        if (data.thumbnails && data.thumbnails.length > 0) {
            generateThumbnails(data.thumbnails, data.directory);
            highlightCurrentThumbnail(window.currentImg);
        } else {
            console.warn('No thumbnails received');
        }
    })
    .catch(err => {
        console.error('Error loading project from data:', err);
        showToast('Failed to load project', 'error');
    });
};

window.saveProject = function() {
    // 保存前将当前画布上的位置同步到 window.projectPages
    if (window.canvasControls?.updateCurrentPageData) {
        window.canvasControls.updateCurrentPageData();
    }

    if (!window.projectDirectory || !window.projectPages || !window.currentImg) {
        showToast('没有可保存的项目', 'error');
        return;
    }

    const directory = window.projectDirectory;
    const key = window.currentImg;
    const entries = window.projectPages[key];

    // ========== 持久化用户添加的文本框数据 ==========
    if (window.canvasControls?.getUserTextBoxes) {
        window.projectPages[key].textBoxes = window.canvasControls.getUserTextBoxes();
    }
    
    // 将 projectPages 转换为标准对象格式，防止 JSON.stringify 丢失数组上的自定义属性
    const pagesToSave = {};
    for (const k in window.projectPages) {
        const pageArr = window.projectPages[k];
        pagesToSave[k] = {
            entries: pageArr,
            textBoxes: pageArr.textBoxes || []
        };
    }

    const jsonPayload = {
        directory: directory,
        pages: pagesToSave,
        current_img: key
    };

    fetch('/save_project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(jsonPayload)
    })
    .then(response => response.json())
    .then(result => {
        if (!result.success) {
            throw new Error('保存 JSON 失败：' + (result.error || '未知错误'));
        }
        return saveImages(directory, key, entries);
    })
    .then(() => {
        // 保存成功后，将当前工作数据同步到初始只读数据
        window.initialProjectPages = JSON.parse(JSON.stringify(window.projectPages));
        showToast('保存成功', 'success');
    })
    .catch(err => {
        console.error('保存出错:', err);
        showToast('保存失败：' + err.message, 'error');
    });
};

async function saveImages(directory, key, entries) {
    // 通过 canvasControls 获取当前画布图片数据，避免直接访问全局变量
    let imageToSave = null;
    let imgWidth = 0, imgHeight = 0;
    if (window.canvasControls?.getInpaintedImage) {
        const info = window.canvasControls.getInpaintedImage();
        imageToSave = info.image;
        imgWidth = info.width;
        imgHeight = info.height;
    }
    if (!imageToSave && window.canvasControls?.getComicImage) {
        const info = window.canvasControls.getComicImage();
        imageToSave = info.image;
        imgWidth = info.width;
        imgHeight = info.height;
    }
    if (imageToSave) {
        const offCanvas = document.createElement('canvas');
        offCanvas.width = imgWidth;
        offCanvas.height = imgHeight;
        const offCtx = offCanvas.getContext('2d');
        const imgElement = imageToSave.getElement();
        offCtx.drawImage(imgElement, 0, 0, imgWidth, imgHeight);
        const imageDataURL = offCanvas.toDataURL('image/png');
        const imageBlob = dataURItoBlob(imageDataURL);
        const formData = new FormData();
        formData.append('directory', directory);
        formData.append('key', key);
        formData.append('image', imageBlob, key + '.png');
        const response = await fetch('/save_inpainted', {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        if (!result.success) {
            throw new Error('保存 inpainted 失败：' + (result.error || '未知错误'));
        }
    }

    // ========== 获取用户文本图层图片 ==========
    let textLayerBlob = null;
    if (window.canvasControls?.getTextLayerImage) {
        const textLayerDataURL = await window.canvasControls.getTextLayerImage();
        if (textLayerDataURL) {
            textLayerBlob = dataURItoBlob(textLayerDataURL);
        }
    }

    // ========== 保存结果图（包含文本图层） ==========
    const formData = new FormData();
    formData.append('directory', directory);
    formData.append('key', key);
    formData.append('entries', JSON.stringify(entries));
    if (textLayerBlob) {
        formData.append('text_layer', textLayerBlob, 'text_layer.png');
    }

    const resResponse = await fetch('/save_result', {
        method: 'POST',
        body: formData
    });
    const resResult = await resResponse.json();
    if (!resResult.success) {
        throw new Error('保存结果图失败：' + (resResult.error || '未知错误'));
    }
}

// 高亮当前页缩略图并滚动到中间（性能优化版）
function highlightCurrentThumbnail(currentKey) {
    const scrollContainer = document.querySelector('[name="thumbnail-list"]');
    if (!scrollContainer) return;
    const innerContainer = scrollContainer.querySelector('.thumbnail-container');
    if (!innerContainer) return;
    const cards = innerContainer.querySelectorAll('.thumbnail-card');
    
    // 清除上一个高亮
    if (lastHighlightedCard) {
        lastHighlightedCard.style.boxShadow = '';
    }
    
    let currentCard = null;
    for (const card of cards) {
        if (card.getAttribute('data-key') === currentKey) {
            card.style.boxShadow = '0 0 5px rgba(33, 150, 243, 0.5)';
            currentCard = card;
            break;
        }
    }
    lastHighlightedCard = currentCard;

    if (currentCard) {
        const containerRect = scrollContainer.getBoundingClientRect();
        const cardRect = currentCard.getBoundingClientRect();
        const relativeTop = cardRect.top - containerRect.top;
        const targetScrollTop = scrollContainer.scrollTop + relativeTop - (scrollContainer.clientHeight / 2) + (cardRect.height / 2);
        scrollContainer.scrollTop = targetScrollTop;
    }
}

window.goToPrevPage = function() {
    if (!window.projectPages || !window.currentImg || !window.projectDirectory) {
        showToast('项目未加载', 'error');
        return;
    }
    const keys = Object.keys(window.projectPages);
    const currentIndex = keys.indexOf(window.currentImg);
    if (currentIndex <= 0) {
        showToast('已经是第一页', 'info');
        return;
    }
    const prevKey = keys[currentIndex - 1];
    loadImage(prevKey, window.projectDirectory);
};

window.goToNextPage = function() {
    if (!window.projectPages || !window.currentImg || !window.projectDirectory) {
        showToast('项目未加载', 'error');
        return;
    }
    const keys = Object.keys(window.projectPages);
    const currentIndex = keys.indexOf(window.currentImg);
    if (currentIndex === -1 || currentIndex >= keys.length - 1) {
        showToast('已经是最后一页', 'info');
        return;
    }
    const nextKey = keys[currentIndex + 1];
    loadImage(nextKey, window.projectDirectory);
};

// ==================== 匹配结果面板交互（使用事件委托） ====================
window.initMatchResultPanel = function(textDir, thumbDir) {
    window.textDir = textDir;
    window.thumbDir = thumbDir;

    setTimeout(() => {
        const container = document.getElementById('match-result-dialog');
        if (!container) return;

        if (container._matchPanelHandler) {
            container.removeEventListener('click', container._matchPanelHandler);
            container.removeEventListener('mouseenter', container._matchPanelHandler);
            container.removeEventListener('mouseleave', container._matchPanelHandler);
        }

        const handler = function(e) {
            // 仅当为点击事件且目标为删除按钮或其子元素时执行删除
            if (e.type === 'click') {
                const delBtn = e.target.closest('.del-btn');
                if (delBtn) {
                    e.stopPropagation();
                    const wrapper = delBtn.closest('.thumb-wrapper');
                    if (!wrapper || wrapper.dataset.deleted === 'true') return;

                    const img = wrapper.querySelector('img');
                    wrapper.dataset.deleted = 'true';

                    // 隐藏原始图片
                    img.style.display = 'none';
                    delBtn.style.display = 'none';

                    // 确保 wrapper 有固定高度，使绝对定位的图标能够垂直居中
                    wrapper.style.height = '150px';
                    wrapper.style.display = 'inline-block'; // 保持与原图片一致

                    // 移除可能已存在的旧图标
                    const oldIcon = wrapper.querySelector('.add-placeholder-icon');
                    if (oldIcon) oldIcon.remove();

                    // 创建 Material 加号图标作为占位符
                    const addIcon = document.createElement('i');
                    addIcon.className = 'material-icons add-placeholder-icon';
                    addIcon.style.cssText = 'position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 36px; color: #888; cursor: pointer; line-height: 1; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center;';
                    addIcon.textContent = 'add';
                    wrapper.style.position = 'relative';
                    wrapper.appendChild(addIcon);

                    // 清除文件名标签
                    const nameLabel = wrapper.closest('.row')?.querySelector('.text-col .text-xs');
                    if (nameLabel) nameLabel.innerText = '';

                    // 点击加号图标打开选择器
                    addIcon.onclick = () => {
                        if (wrapper.dataset.deleted === 'true') {
                            const row = wrapper.closest('[data-raw-path]');
                            showImageSelector(row);
                        }
                    };

                    // 向后端发送清空请求
                    const row = wrapper.closest('[data-raw-path]');
                    if (row) {
                        const rawPath = row.getAttribute('data-raw-path');
                        fetch('/update_match_text', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ raw_path: rawPath, new_text_path: '', text_dir: window.textDir })
                        }).then(res => res.json()).then(data => {
                            if (!data.success) console.error('清空 text_path 失败', data);
                        }).catch(err => console.error('清空 text_path 出错', err));
                    }
                    return;
                }
            }

            // 处理鼠标悬停显示/隐藏删除按钮（仅当未删除状态）
            if (e.type === 'mouseenter' || e.type === 'mouseleave') {
                const wrapper = e.target.closest('.thumb-wrapper');
                if (wrapper && wrapper.dataset.deleted === 'false') {
                    const delBtn = wrapper.querySelector('.del-btn');
                    if (delBtn) {
                        delBtn.style.display = e.type === 'mouseenter' ? 'flex' : 'none';
                    }
                }
            }
        };

        container.addEventListener('click', handler);
        container.addEventListener('mouseenter', handler, true);
        container.addEventListener('mouseleave', handler, true);
        container._matchPanelHandler = handler;

        // 初始化
        container.querySelectorAll('.thumb-wrapper').forEach(wrapper => {
            const delBtn = wrapper.querySelector('.del-btn');
            if (delBtn) delBtn.style.display = 'none';
            wrapper.dataset.deleted = 'false';
            const img = wrapper.querySelector('img');
            if (img) img.style.display = 'block';
            const oldIcon = wrapper.querySelector('.add-placeholder-icon');
            if (oldIcon) oldIcon.remove();
        });
    }, 150);
};

// 图片选择器模态框（独立函数，避免闭包问题）
let currentRow = null;
let modal = null;

function createModal() {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); display:none; justify-content:center; align-items:center; z-index:10000;';
    modal.innerHTML = `
        <div style="background:white; border-radius:12px; width:80%; max-width:800px; max-height:80%; display:flex; flex-direction:column; overflow:hidden;">
            <h3 style="text-align:center; margin:16px 0 8px 0; font-size:16px;">选择文本图片</h3>
            <div id="thumb-grid" class="modern-scrollbar" style="flex:1; overflow-y:auto; display:grid; grid-template-columns:repeat(auto-fill, minmax(120px,1fr)); gap:12px; padding:0 20px 20px 20px;"></div>
            <div style="display:flex; justify-content:center; gap:20px; padding:16px 20px; border-top:1px solid #eee;">
                <button id="cancel-btn" style="padding:8px 24px; background:#f0f0f0; border:none; border-radius:4px; cursor:pointer;">取消</button>
                <button id="confirm-btn" style="padding:8px 24px; background:#007bff; color:white; border:none; border-radius:4px; cursor:pointer;">确定</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    
    modal.querySelector('#cancel-btn').onclick = () => {
        modal.style.display = 'none';
        currentRow = null;
    };
    modal.querySelector('#confirm-btn').onclick = async () => {
        try {
            const selectedDiv = modal.querySelector('.thumb-selected');
            if (!selectedDiv) {
                alert('请选择一个文本图片');
                return;
            }
            const originalFileName = selectedDiv.getAttribute('data-original-filename');
            const fullTextPath = selectedDiv.getAttribute('data-full-path');
            const finalImageUrl = selectedDiv.getAttribute('data-image-url');
            if (!originalFileName || !fullTextPath || !finalImageUrl) {
                alert('数据错误');
                return;
            }
            if (currentRow) {
                const wrapper = currentRow.querySelector('.thumb-wrapper');
                const imgEl = wrapper.querySelector('img');
                const nameLabel = currentRow.querySelector('.text-col .text-xs');
                if (nameLabel) nameLabel.innerText = originalFileName;

                // 移除加号占位图标
                const addIcon = wrapper.querySelector('.add-placeholder-icon');
                if (addIcon) addIcon.remove();

                // 恢复图片显示
                imgEl.style.display = 'block';
                imgEl.src = finalImageUrl;
                imgEl.style.backgroundImage = 'none';
                imgEl.style.objectFit = '';
                imgEl.style.height = '150px';
                imgEl.style.width = 'auto';
                imgEl.style.backgroundColor = 'transparent';

                wrapper.dataset.deleted = 'false';
                imgEl.onclick = null;
                
                const rawPath = currentRow.getAttribute('data-raw-path');
                fetch('/update_match_text', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ raw_path: rawPath, new_text_path: fullTextPath, text_dir: window.textDir })
                }).then(res => res.json()).then(data => {
                    if (!data.success) console.error('更新匹配失败', data);
                }).catch(err => console.error('更新匹配出错', err));
            }
            modal.style.display = 'none';
            currentRow = null;
        } catch (err) {
            console.error('确认时出错:', err);
            alert('操作失败，请重试');
        }
    };
    return modal;
}

function getBestImageUrl(baseName, originalFileName) {
    return new Promise(resolve => {
        const thumbUrl = '/thumbs/thumb_text_' + baseName + '.jpg';
        const originalUrl = '/text_original/' + encodeURIComponent(originalFileName);
        const testImg = new Image();
        testImg.onload = () => resolve(thumbUrl);
        testImg.onerror = () => resolve(originalUrl);
        testImg.src = thumbUrl;
    });
}

async function showImageSelector(row) {
    try {
        currentRow = row;
        const modal = createModal();
        const grid = modal.querySelector('#thumb-grid');
        grid.innerHTML = '<div style="text-align:center;">加载中...</div>';
        modal.style.display = 'flex';
        
        const response = await fetch('/get_text_images', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text_dir: window.textDir })
        });
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        const files = data.files;
        if (files.length === 0) {
            grid.innerHTML = '<div style="text-align:center;">没有可用的文本图片</div>';
            return;
        }
        grid.innerHTML = '';
        
        const promises = files.map(file => {
            const baseName = file.replace(/\.[^/.]+$/, '');
            return getBestImageUrl(baseName, file).then(url => ({ file, url, baseName }));
        });
        const results = await Promise.all(promises);
        
        for (const { file, url, baseName } of results) {
            const div = document.createElement('div');
            div.style.cssText = 'cursor:pointer; text-align:center; padding:4px; border:1px solid #ddd; border-radius:4px;';
            // ★ 新增文件名显示，显示为 baseName.jpg
            div.innerHTML = `
                <img src="${url}" style="width:100%; height:auto; max-height:100px; object-fit:contain;" onerror="this.src='${url}'" />
                <div style="font-size:12px; margin-top:4px; word-break:break-all;">${baseName}.jpg</div>
            `;
            div.onclick = () => {
                grid.querySelectorAll('.thumb-selected').forEach(el => {
                    el.classList.remove('thumb-selected');
                    el.style.borderColor = '';
                });
                div.classList.add('thumb-selected');
                div.style.borderColor = '#007bff';
                div.setAttribute('data-original-filename', file);
                div.setAttribute('data-full-path', window.textDir + '/' + file);
                div.setAttribute('data-image-url', url);
            };
            grid.appendChild(div);
        }
        
        if (!document.querySelector('#thumb-selected-style')) {
            const style = document.createElement('style');
            style.id = 'thumb-selected-style';
            style.textContent = '.thumb-selected { border-color: #007bff !important; background-color: #e7f3ff; }';
            document.head.appendChild(style);
        }
    } catch (err) {
        console.error('加载图片列表失败:', err);
        const grid = modal.querySelector('#thumb-grid');
        if (grid) grid.innerHTML = '<div style="text-align:center;">加载失败</div>';
    }
}

// 更新算法标签
window.updateAlgorithmLabel = function(algorithm) {
    const el = document.getElementById('algorithm-selector');
    if (el) el.innerText = algorithm;
    window.currentAlgorithm = algorithm;
};