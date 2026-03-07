# Sharingan

深度学习驱动的漫画自动重嵌工具。

![screenshot](assets/screenshot.png)

<table style="border-collapse: collapse; border: none; width: 100%;">
  <thead>
    <tr>
      <th style="border: none; padding: 0 0 10px 0; text-align: center;">raw</th>
      <th style="border: none; padding: 0 0 10px 0; text-align: center;">text</th>
      <th style="border: none; padding: 0 0 10px 0; text-align: center;">result</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="border: none; padding: 0; text-align: center;">
        <img src="assets/raw.jpg" style="width: 100%; height: auto;" alt="raw">
      </td>
      <td style="border: none; padding: 0; text-align: center;">
        <img src="assets/text.jpg" style="width: 100%; height: auto;" alt="text">
      </td>
      <td style="border: none; padding: 0; text-align: center;">
        <img src="assets/result.png" style="width: 100%; height: auto;" alt="result">
      </td>
    </tr>
  </tbody>
</table>

## 快速开始

### 环境要求
- Python 3.10.6

### 下载
1. 克隆仓库  
  ```bash
git clone https://github.com/30A430AB/sharingan.git&&cd sharingan
```
2. 手动下载 [data](https://github.com/30A430AB/sharingan/releases/download/sankougyoku/data.zip) 文件夹，解压后放于源码目录下的对应位置

3. 安装依赖
```bash
pip install -r requirements.txt
```
## 使用

### 命令行模式
```bash
python cli.py <原始图片文件夹路径> <文本图片文件夹路径>
```

>[!NOTE]
>原始图片与文本图片文件名必须一致
>文本图片分辨率可能被改变，请提前做好备份

### GUI 模式
```bash
python gui.py
```

#### 工具说明
- 修复画笔：按下鼠标左键拖动抹除文字
- 还原画笔：按下鼠标左键拖动清除修复结果
- 矩形工具：按下鼠标左键拖动矩形框抹除框内文字

## 致谢

本项目使用了以下开源项目
- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator)
- [comic-text-detector](https://github.com/dmMaze/comic-text-detector)
- [patchmatch](https://github.com/vacancy/PyPatchMatch) [修改版](https://github.com/dmMaze/PyPatchMatchInpaint)
- [lama](https://github.com/advimman/lama)(本程序使用微调版) [simple-lama-inpainting](https://github.com/enesmsahin/simple-lama-inpainting)
