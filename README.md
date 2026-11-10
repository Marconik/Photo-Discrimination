# Photo-Discrimination

Windows 本地相册 OCR 与文字搜索桌面应用。支持按需下载 PaddleOCR CPU/GPU 环境与模型、勾选照片识别、OCR.json 读写、关键词搜索和移动导出。

## 启动

本机已安装 Python 3.10 与 PySide6 6.8.2.1，可直接双击 `start.bat`，或在项目目录运行：

```powershell
py -3.10 main.py
# 也可直接载入一个相册
py -3.10 main.py "E:\我的相册"
```

新环境建议使用独立虚拟环境，Python 3.10 或 3.11：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

`start.bat` 优先使用本项目 `.venv`，没有时使用 `py -3.10`。`requirements.txt` 只安装桌面依赖；首次运行不会自动安装 OCR、下载模型或配置 API。识别环境由用户在设置中明确选择下载。

## 当前功能

- 导入一个本地文件夹，列出 JPG/JPEG、PNG、WebP、BMP 照片，暂不递归子文件夹。
- 点击列表行查看原图预览和对应文字；滚轮缩放、拖动、适应窗口、打开原图。
- 每行右侧勾选框独立于当前预览；支持勾选搜索结果、清空勾选、只看勾选。
- 搜索和切换预览均保留勾选，隐藏的勾选数量会明确显示。
- 读取相册内 `OCR.json`；可编辑、粘贴文字并保存，覆盖本张照片的旧文字。
- 搜索多个以空白分隔的关键词，默认须全部命中，顺序不限。只搜索 OCR 文字，不搜索文件名。
- 将所有勾选照片移动到新建文件夹，目标保存对应 OCR.json，源索引移除成功移动的条目。

下载并启用识别工具后，勾选照片并点击“识别勾选照片”。程序在独立进程中初始化一次模型，逐张识别、去除文字空白并覆盖本张旧结果，保存到当前相册的 OCR.json。失败保留旧结果；成功但没有文字时保存空字符串。识别过程中可停止，已保存结果保留。

## 识别工具设置

1. 点击左侧“识别工具设置”。
2. 手动选择 `OCR · PaddleOCR · CPU` 或 `OCR · PaddleOCR · GPU`。
3. GPU 版本必须手动选择：`cu118`、`cu126` 或 `cu129`。CPU 不需要 CUDA 选择。
4. 选择 PP-OCRv5 轻量版或高精度版；二者均用于中英日文字识别。CPU/GPU 共用同一套文字模型文件。
5. 点击“下载并部署”，等待运行依赖、模型下载以及真实推理验证完成。支持显示日志和取消。
6. 验证成功后点击“使用此配置”。未完成的下载不能启用，不会默默回退到 CPU。

目前只提供本地 PaddleOCR 选项，没有 VLM 模型或 API 供应商配置。模型权重、运行库与用户配置都不会提交到 Git；新克隆项目仍是未配置状态。

目录与组件：

| 内容 | 位置 / 版本 |
| --- | --- |
| CPU / 各 CUDA 版本独立环境 | `runtimes/cpu`、`runtimes/gpu-cu118` 等 |
| 检测与文字模型 | `models/PP-OCRv5_mobile_det`、`models/PP-OCRv5_mobile_rec` 等 |
| 启用配置与验证记录 | `data/settings/` |
| 下载缓存 / 第三方缓存 | `cache/` |
| PaddlePaddle / PaddleOCR / PaddleX | 3.2.2 / 3.3.2 / 3.3.13 |
| 其余已验证依赖约束 | `requirements-ocr-constraints.txt` |

GPU 首次下载还包含 CUDA/cuDNN 运行库，可能数 GB，模型本身轻量版约 22 MB、高精度版约 166 MB；通常无需额外安装完整 CUDA Toolkit。GPU 构建必须与本机驱动兼容。

本机部署已完成：RTX 3050 Laptop 4GB、驱动 546.30，使用 `GPU / cu118 / PP-OCRv5 轻量版`，实测设备为 `gpu:0`。已完成中英日演示图片识别、逐图 JSON 保存、关键词搜索，以及禁止 Python 网络连接后的本地推理验证。这不代表其他 CUDA 构建或所有图片的准确率均已测试。

也可用同一部署逻辑手动运行命令：

```powershell
py -3.10 scripts/deploy_ocr.py --device gpu --variant cu118 --model mobile --activate
```

如果部署进程被强制终止，可能留下 `data/settings/deployment.lock`；确认没有部署进程后再移除该文件并重试。常规失败与取消会自动释放锁。

快捷键：`Ctrl+O` 导入、`Ctrl+F` 搜索、`Ctrl+S` 保存当前文字、`F5` 刷新。

## OCR.json 格式

与照片放在同一个文件夹中，使用 UTF-8 编码；一张照片对应一段文字，键是包含扩展名的实际文件名：

```json
{
  "东京旅行.jpg": "東京駅2026年予約番号ABC-123",
  "购物小票.png": "发票金额￥200Coffee"
}
```

保存时移除空格、制表符、换行等 Unicode 空白。原有全角字母、数字和符号仍保留；搜索时统一全角半角、英文大小写。输入 `東京 2026` 会返回同时包含这两项的照片；输入 `%` 或 `_` 按普通符号查找。清空搜索显示全部照片。

没有条目表示尚无识别结果；条目为空字符串表示已保存但没有文字。导入时不修改文件，没有 JSON 时首次识别或手动保存自动创建。无对应文件的旧条目暂时保留，避免误删用户索引。

格式错误、重复键、非字符串值会提示错误并保留原 JSON。保存使用同目录临时文件与原子替换；检测到外部修改时要求刷新，避免覆盖已有变化。暂不支持多个应用实例并发写入同一相册。

## 移动导出

点击“移动导出勾选照片”，选择父目录、输入新的文件夹名，再核对数量、隐藏勾选数和目标位置后确认。已有目标目录不会被覆盖。

实现采用逐张复制后删除源文件，支持不同磁盘。每张目标照片和对应文字保存后，才移除该张原图并更新源索引。失败即停止余下批次，已完成的移动保持完成；异常时可能保留重复副本或源索引旧条目，错误提示会说明。目标保留完整副本优先于回滚删除。

## 界面演示

以下脚本生成测试卡片及**手工预置**的 JSON 文字，用于体验搜索；没有执行 OCR，不涉及真实照片：

```powershell
py -3.10 scripts/create_demo.py
py -3.10 main.py "data\界面演示_手工预置文字"
```

演示文件夹已存在时生成脚本会停止，不覆盖已有文件。数据目录与相册 JSON 均被 Git 忽略。

## 验证

```powershell
py -3.10 -m unittest discover -v
```

48 项自动测试覆盖短词/符号/多关键词、原子保存失败、外部 JSON 变更、移动失败的数据保留、界面导入/预览/勾选/搜索/保存/移动、首次运行不下载、GPU 手动版本选择、下载失败不能启用，以及识别期间文件变化和停止时结果保留。界面测试以 Qt offscreen 模式运行，移动仅作用于临时测试文件。真实 GPU 验证另行执行，不是上述模拟测试的结果。

## 代码组织

- `photo_discrimination/album.py`：JSON、文字规范化、相册扫描和文件移动。
- `photo_discrimination/models.py`：照片列表、勾选与筛选模型。
- `photo_discrimination/window.py`：Qt 桌面界面、异步载入和移动任务。
- `photo_discrimination/ocr_settings.py`：工具选择、下载日志、取消和启用。
- `photo_discrimination/ocr_runtime.py`：独立环境安装、模型下载和推理验证。
- `photo_discrimination/ocr_worker.py`、`ocr_service.py`：独立进程实际识别、逐图保存。
- `photo_discrimination/style.py`：桌面样式。
- `main.py`、`start.bat`：启动入口。

最新范围见 [项目规划](docs/PROJECT_PLAN.md)。

## 许可证

[MIT](LICENSE)。第三方依赖各自许可证适用。
