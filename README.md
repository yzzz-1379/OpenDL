# OpenDL

网页媒体嗅探 + 下载后端调用。浏览器扩展负责抓取页面里的媒体请求（含 Referer/Cookie），
桌面端负责下载；默认把任务交给 [Gopeed](https://gopeed.com) 的本地 API，也可以切回内置的 OpenDL 下载器。

## 组成

| 目录 | 说明 |
|---|---|
| `extension/` | Chrome/Edge MV3 扩展：嗅探、预览、发送任务 |
| `opendl/` | 桌面端（Python 3.11+，仅标准库 + 可选 ffmpeg） |

## 桌面端

```bash
python -m opendl            # 直接运行
```

或双击 `opendl/OpenDL.bat`（无控制台窗口）。

两种模式：

- **常规**：配置存 `%LOCALAPPDATA%\OpenDL\`
- **便携**：程序目录放一个空的 `portable.txt`，配置改存 `程序目录\OpenDL-data\`，整个文件夹可随身带走

### 可选依赖 ffmpeg

DASH（`.mpd`）下载与加密 HLS 回退需要 ffmpeg。查找顺序：

1. 环境变量 `OPEN_DL_FFMPEG`
2. 程序目录 / `bin/` 下的 `ffmpeg.exe`
3. `PATH`
4. winget 安装目录

没装也不影响普通下载，只是遇到 DASH 会提示缺少 ffmpeg。

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

产物在 `dist/`：单文件 exe 与绿色版目录各一份。

## 浏览器扩展

1. 打开 `edge://extensions`（或 `chrome://extensions`）
2. 打开「开发人员模式」
3. 「加载解压缩的扩展」→ 选择 `extension/` 目录
4. 扩展设置里选后端：**Gopeed** 填 `http://127.0.0.1:9999`，或 **OpenDL** 填配对码

## 测试

```bash
python opendl/tests/test_core.py
python opendl/tests/test_hls.py
python opendl/tests/test_integration.py
python opendl/tests/test_ffmpeg.py     # 没装 ffmpeg 会自动跳过
```

## 许可

未指定许可证，作者保留所有权利。
