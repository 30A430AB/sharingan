window.projectPages = null;
window.projectDirectory = null;
window.currentImg = null;
window.originalProjectPages = null;

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
    toast.style.zIndex = '9999';
    toast.style.fontSize = '14px';
    toast.style.transition = 'opacity 0.3s ease';
    toast.style.opacity = '1';
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => {
            if (toast.parentNode) toast.remove();
        }, 300);
    }, 3000);
}

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
                window.projectPages = data.pages;
                window.projectDirectory = data.directory;
                window.currentImg = data.current_img;
                // 保存原始数据副本
                window.originalProjectPages = JSON.parse(JSON.stringify(data.pages));

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
        innerDiv.appendChild(card);
    });
    container.appendChild(innerDiv);
}

function loadImage(key, directory) {
    // 不再自动保存当前页数据
    // if (window.canvasControls && window.canvasControls.updateCurrentPageData) {
    //     window.canvasControls.updateCurrentPageData();
    // }

    // 保存参考图可见状态
    const wasWorkingVisible = window.canvasControls && window.canvasControls.isWorkingReferenceVisible ? window.canvasControls.isWorkingReferenceVisible() : false;
    if (window.canvasControls && window.canvasControls.removeWorkingReference) {
        window.canvasControls.removeWorkingReference();
    }

    // 从原始数据中获取该页的条目（确保位置信息是原始的）
    const entries = window.originalProjectPages ? window.originalProjectPages[key] : (window.projectPages ? window.projectPages[key] : []);
    
    // 重置 window.projectPages 中该页的数据为原始数据，丢弃未保存的修改
    if (window.projectPages && key) {
        window.projectPages[key] = JSON.parse(JSON.stringify(entries));
    }

    fetch('/get_image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ directory: directory, key: key, entries: entries })
    })
    .then(response => response.json())
    .then(data => {
        if (data.originalImageUrl) {
            window.currentImg = key;
            return window.canvasControls.loadLayers(data.originalImageUrl, data.inpaintedImageUrl, data.textBlocks)
                .then(() => {
                    // 传入 key 以便 updateTextBlocks 访问 window.projectPages
                    updateTextBlocks(data.textBlocks, key);
                    highlightCurrentThumbnail(key);
                    if (wasWorkingVisible) {
                        return window.canvasControls.loadWorkingReference();
                    }
                });
        } else {
            showToast('加载图片失败：' + (data.error || '未知错误'), 'error');
        }
    })
    .catch(err => {
        console.error('Error loading image:', err);
        showToast('加载图片失败', 'error');
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
        // 从 window.projectPages 获取当前可能被修改的可见性
        let currentVisible = block.visible; // 默认原始值
        if (window.projectPages && window.projectPages[pageKey] && window.projectPages[pageKey][index]) {
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
            const newVisible = !(block.visible); // 直接切换当前块的 visible 属性
            block.visible = newVisible;
            eyeBtn.innerHTML = `<span class="material-icons">${newVisible ? 'visibility' : 'visibility_off'}</span>`;
            if (window.canvasControls && window.canvasControls.setTextBlockVisibility) {
                window.canvasControls.setTextBlockVisibility(index, newVisible);
            }
            // 可选：同步到 window.projectPages，但保存时会覆盖
            if (window.projectPages && window.projectPages[pageKey] && window.projectPages[pageKey][index]) {
                window.projectPages[pageKey][index].matched = newVisible ? 1 : 0;
            }
        });

        footerDiv.appendChild(eyeBtn);
        card.appendChild(footerDiv);

        card.addEventListener('mouseenter', () => {
            if (window.canvasControls && window.canvasControls.highlightTextBlock) {
                window.canvasControls.highlightTextBlock(index, true);
            }
        });
        card.addEventListener('mouseleave', () => {
            if (window.canvasControls && window.canvasControls.highlightTextBlock) {
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
        window.projectPages = data.pages;
        window.projectDirectory = data.directory;
        window.currentImg = data.current_img;
        window.originalProjectPages = JSON.parse(JSON.stringify(data.pages));

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
    if (window.canvasControls && window.canvasControls.updateCurrentPageData) {
        window.canvasControls.updateCurrentPageData();
    }

    if (!window.projectDirectory || !window.projectPages || !window.currentImg) {
        showToast('没有可保存的项目', 'error');
        return;
    }

    const directory = window.projectDirectory;
    const key = window.currentImg;
    const entries = window.projectPages[key];

    const jsonPayload = {
        directory: directory,
        pages: window.projectPages,
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
        // 保存成功后，将当前 projectPages 更新为原始数据
        window.originalProjectPages = JSON.parse(JSON.stringify(window.projectPages));
        showToast('保存成功', 'success');
    })
    .catch(err => {
        console.error('保存出错:', err);
        showToast('保存失败：' + err.message, 'error');
    });
};

async function saveImages(directory, key, entries) {
    let imageToSave = null;
    if (inpaintedImage) {
        imageToSave = inpaintedImage;
    } else if (comicImage) {
        imageToSave = comicImage;
    }
    if (imageToSave) {
        // 创建离屏 canvas，以不透明度 1 绘制图片，忽略滑块透明度
        const offCanvas = document.createElement('canvas');
        offCanvas.width = imageWidth;
        offCanvas.height = imageHeight;
        const offCtx = offCanvas.getContext('2d');
        // 获取原生图像元素（原始图片数据）
        const imgElement = imageToSave.getElement();
        offCtx.drawImage(imgElement, 0, 0, imageWidth, imageHeight);
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

    const resultPayload = {
        directory: directory,
        key: key,
        entries: entries
    };
    const resResponse = await fetch('/save_result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(resultPayload)
    });
    const resResult = await resResponse.json();
    if (!resResult.success) {
        throw new Error('保存结果图失败：' + (resResult.error || '未知错误'));
    }
}

// 高亮当前页缩略图并滚动到中间
function highlightCurrentThumbnail(currentKey) {
    const scrollContainer = document.querySelector('[name="thumbnail-list"]');
    if (!scrollContainer) return;
    const innerContainer = scrollContainer.querySelector('.thumbnail-container');
    if (!innerContainer) return;
    const cards = innerContainer.querySelectorAll('.thumbnail-card');
    let currentCard = null;
    cards.forEach(card => {
        const key = card.getAttribute('data-key');
        if (key === currentKey) {
            card.style.boxShadow = '0 0 5px rgba(33, 150, 243, 0.5)';
            currentCard = card;
        } else {
            card.style.border = '';
            card.style.boxShadow = '';
        }
    });

    if (currentCard) {
        // 计算并设置滚动位置，使当前卡片在可视区域垂直居中
        const containerRect = scrollContainer.getBoundingClientRect();
        const cardRect = currentCard.getBoundingClientRect();
        const relativeTop = cardRect.top - containerRect.top;
        const targetScrollTop = scrollContainer.scrollTop + relativeTop - (scrollContainer.clientHeight / 2) + (cardRect.height / 2);
        scrollContainer.scrollTop = targetScrollTop;
    }
}

window.goToPrevPage = function() {
    if (!window.projectPages || !window.currentImg || !window.projectDirectory) {
        showToast('没有加载项目', 'error');
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
        showToast('没有加载项目', 'error');
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

// ==================== 匹配结果面板交互 ====================
window.initMatchResultPanel = function(textDir, thumbDir) {
    window.textDir = textDir;
    window.thumbDir = thumbDir;

    var currentRow = null;
    var modal = null;

    function createModal() {
        if (modal) return modal;
        modal = document.createElement('div');
        modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); display:none; justify-content:center; align-items:center; z-index:10000;';
        modal.innerHTML = '<div style="background:white; border-radius:12px; width:80%; max-width:800px; max-height:80%; display:flex; flex-direction:column; overflow:hidden;">' +
            '<h3 style="text-align:center; margin:16px 0 8px 0; font-size:16px;">选择文本图片</h3>' +
            '<div id="thumb-grid" class="modern-scrollbar" style="flex:1; overflow-y:auto; display:grid; grid-template-columns:repeat(auto-fill, minmax(120px,1fr)); gap:12px; padding:0 20px 20px 20px;"></div>' +
            '<div style="display:flex; justify-content:center; gap:20px; padding:16px 20px; border-top:1px solid #eee;">' +
            '<button id="cancel-btn" style="padding:8px 24px; background:#f0f0f0; border:none; border-radius:4px; cursor:pointer;">取消</button>' +
            '<button id="confirm-btn" style="padding:8px 24px; background:#007bff; color:white; border:none; border-radius:4px; cursor:pointer;">确定</button>' +
            '</div></div>';
        document.body.appendChild(modal);
        modal.querySelector('#cancel-btn').onclick = function() {
            modal.style.display = 'none';
            currentRow = null;
        };
        modal.querySelector('#confirm-btn').onclick = async function() {
            try {
                var selectedDiv = modal.querySelector('.thumb-selected');
                if (!selectedDiv) {
                    alert('请选择一个文本图片');
                    return;
                }
                var originalFileName = selectedDiv.getAttribute('data-original-filename');
                var fullTextPath = selectedDiv.getAttribute('data-full-path');
                var finalImageUrl = selectedDiv.getAttribute('data-image-url');
                if (!originalFileName || !fullTextPath || !finalImageUrl) {
                    alert('数据错误');
                    return;
                }
                if (currentRow) {
                    var wrapper = currentRow.querySelector('.thumb-wrapper');
                    var imgEl = wrapper.querySelector('img');
                    var nameLabel = currentRow.querySelector('.text-col .text-xs');
                    if (nameLabel) nameLabel.innerText = originalFileName;
                    if (imgEl) {
                        imgEl.src = finalImageUrl;
                        imgEl.style.backgroundImage = 'none';
                        imgEl.style.objectFit = '';
                        imgEl.style.height = '150px';
                        imgEl.style.width = 'auto';
                    }
                    wrapper.dataset.deleted = 'false';
                    imgEl.onclick = null;
                    
                    var rawPath = currentRow.getAttribute('data-raw-path');
                    fetch('/update_match_text', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ raw_path: rawPath, new_text_path: fullTextPath })
                    }).then(function(res) { return res.json(); }).then(function(data) {
                        if (!data.success) console.error('更新匹配失败', data);
                    }).catch(function(err) { console.error('更新匹配出错', err); });
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
        return new Promise(function(resolve) {
            var thumbUrl = '/thumbs/thumb_text_' + baseName + '.jpg';
            var originalUrl = '/text_original/' + encodeURIComponent(originalFileName);
            var testImg = new Image();
            testImg.onload = function() { resolve(thumbUrl); };
            testImg.onerror = function() { resolve(originalUrl); };
            testImg.src = thumbUrl;
        });
    }

    async function showImageSelector(row) {
        try {
            currentRow = row;
            var modal = createModal();
            var grid = modal.querySelector('#thumb-grid');
            grid.innerHTML = '<div style="text-align:center;">加载中...</div>';
            modal.style.display = 'flex';
            try {
                var response = await fetch('/get_text_images', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text_dir: window.textDir })
                });
                var data = await response.json();
                if (data.error) throw new Error(data.error);
                var files = data.files;
                if (files.length === 0) {
                    grid.innerHTML = '<div style="text-align:center;">没有可用的文本图片</div>';
                    return;
                }
                grid.innerHTML = '';
                var promises = files.map(function(file) {
                    var baseName = file.replace(/\.[^/.]+$/, '');
                    return getBestImageUrl(baseName, file).then(function(url) {
                        return { file: file, url: url };
                    });
                });
                var results = await Promise.all(promises);
                for (var i = 0; i < results.length; i++) {
                    var file = results[i].file;
                    var bestUrl = results[i].url;
                    var div = document.createElement('div');
                    div.style.cssText = 'cursor:pointer; text-align:center; padding:4px; border:1px solid #ddd; border-radius:4px;';
                    div.innerHTML = `<img src="${bestUrl}" style="width:100%; height:auto; max-height:100px; object-fit:contain;" onerror="this.src='${bestUrl}'" />`;
                    (function(f, currentDiv, imgUrl) {
                        currentDiv.onclick = function() {
                            grid.querySelectorAll('.thumb-selected').forEach(function(el) { el.classList.remove('thumb-selected'); });
                            currentDiv.classList.add('thumb-selected');
                            currentDiv.style.borderColor = '#007bff';
                            currentDiv.setAttribute('data-original-filename', f);
                            currentDiv.setAttribute('data-full-path', window.textDir + '/' + f);
                            currentDiv.setAttribute('data-image-url', imgUrl);
                        };
                    })(file, div, bestUrl);
                    grid.appendChild(div);
                }
                if (!document.querySelector('#thumb-selected-style')) {
                    var style = document.createElement('style');
                    style.id = 'thumb-selected-style';
                    style.textContent = '.thumb-selected { border-color: #007bff !important; background-color: #e7f3ff; }';
                    document.head.appendChild(style);
                }
            } catch (err) {
                console.error(err);
                grid.innerHTML = '<div style="text-align:center;">加载失败</div>';
            }
        } catch (err) {
            console.error('加载图片列表失败:', err);
            var grid = modal.querySelector('#thumb-grid');
            if (grid) grid.innerHTML = '<div style="text-align:center;">加载失败</div>';
        }
    }

    // 为所有 .thumb-wrapper 绑定事件
    document.querySelectorAll('.thumb-wrapper').forEach(function(wrapper) {
        var img = wrapper.querySelector('img');
        var delBtn = wrapper.querySelector('.del-btn');
        wrapper.dataset.deleted = 'false';
        
        wrapper.addEventListener('mouseenter', function() {
            if (wrapper.dataset.deleted === 'false') {
                delBtn.style.display = 'flex';
            }
        });
        wrapper.addEventListener('mouseleave', function() {
            delBtn.style.display = 'none';
        });
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (wrapper.dataset.deleted === 'true') return;
            wrapper.dataset.deleted = 'true';
            img.src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
            img.style.height = '150px';
            img.style.width = 'auto';
            img.style.objectFit = 'none';
            img.style.backgroundImage = "url('/static/icons/add.svg')";
            img.style.backgroundRepeat = 'no-repeat';
            img.style.backgroundPosition = 'center';
            img.style.backgroundSize = '24px 24px';
            delBtn.style.display = 'none';
            var nameLabel = wrapper.closest('.row') ? wrapper.closest('.row').querySelector('.text-col .text-xs') : null;
            if (nameLabel) nameLabel.innerText = '';
            img.onclick = function() {
                if (wrapper.dataset.deleted === 'true') {
                    var row = wrapper.closest('.row') || wrapper.closest('.flex-nowrap');
                    showImageSelector(row);
                }
            };
            var row = wrapper.closest('[data-raw-path]');
            if (row) {
                var rawPath = row.getAttribute('data-raw-path');
                fetch('/update_match_text', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ raw_path: rawPath, new_text_path: '' })
                }).then(function(res) { return res.json(); }).then(function(data) {
                    if (!data.success) console.error('清空 text_path 失败', data);
                }).catch(function(err) { console.error('清空 text_path 出错', err); });
            }
        });
    });
};

// 更新算法标签
window.updateAlgorithmLabel = function(algorithm) {
    let el = document.getElementById('algorithm-selector');
    if (el) el.innerText = algorithm;
    window.currentAlgorithm = algorithm;
};