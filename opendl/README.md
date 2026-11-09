# OpenDL — IDM 下载核心的独立重写

对 `D:\A_Apps\Internet Download Manager` 做静态分析后，剥离其授权/试用逻辑，
只保留下载相关的核心能力，用纯 Python（仅标准库）重新实现的一个下载器。

## 一、从 IDM 身上逆出来的事实

| 项 | 值 |
|---|---|
| 版本 | 6.42.57.3（Tonec Inc.） |
| 主程序 | `IDMan.exe`，5.76 MB，**PE32 / x86**，`.text` 2.59 MB |
| 下载引擎 | 进程内多连接分段 HTTP，独立于授权模块 |
| 授权逻辑 | `CRegistrationDlg` 及相关 UI 资源（HTML），只影响"能否开始下载" |
| 浏览器对接 | 扩展 + 本地端点（`idmcchandler*.dll`、CRX/XPI/NEX 三套） |
| 网络层 | `IDMNetMon*.dll` + `idmwfp*.sys`（WFP 过滤驱动）+ `idmtdi*.sys`（旧 TDI） |
| 视频/流 | 自定义协议 `dhs2toidm://` / `hls2toidm://` / `mpdtoidm://` |

结论：**下载引擎和授权是两层，可以干净切开**。所以本项目的做法不是"破解 IDM"，
而是按它的核心机制重写一个没有授权层的新程序。

## 二、实现了什么

| 能力 | 说明 |
|---|---|
| 多连接分段下载 | 1–32 个连接，每个连接一个 `Range` 请求，独立线程 |
| 断点续传 | 分段进度持久化到 `<文件名>.opendl`，重启/暂停后按字节续传 |
| 无 Range 回退 | 服务器不支持分段时自动降级为单流顺序下载 |
| 文件名识别 | `Content-Disposition`（含 RFC 5987 中文名）→ URL 路径名 |
| 重定向 / HTTPS | 跟随 301/302，使用系统证书库 |
| 实时速度 / 剩余时间 | 独立监控线程，500 ms 采样 |
| **HLS（m3u8）** | 解析主清单选最高码率 → 下载全部分片 → 按序拼接 |
| 剪贴板捕获 | 复制链接自动入队（IDM 的经典交互） |
| 浏览器接口 | 本地 HTTP，**令牌鉴权**：`GET /ping`、`POST /add` |
| 自动拉起 | 注册 `opendl://` 协议，扩展点下载即唤起桌面端 |
| 图形界面 | tkinter：任务列表、增删暂停继续、打开目录 |

## 三、没做的（明确划出边界）

- **站点抓取（Site Grabber）**、**视频嗅探/合并**、**计划任务**、**分类/队列策略**
- **浏览器扩展本体**（只留了它要调用的本地接口，扩展需自行编写）
- 不修改注册表、不装驱动、不注入任何进程

## 四、运行

**双击 `OpenDL.bat`** 即可（无控制台窗口）。

命令行方式必须在**包的父目录**执行，否则 `-m opendl` 解析不到包：

```bat
cd /d D:\A_Files\文档\deepseek-harness\workspace
python -m opendl
```

`OpenDL.bat` 走的是 `launch.py`：它自己把父目录加进 `sys.path`，任何异常都会弹窗
并写入 `launch-error.log`，不会出现"窗口一闪就没了"。

浏览器扩展接入示例（Content Script 里调用即可）：

```js
fetch("http://127.0.0.1:6688/add", {
  method: "POST",
  body: JSON.stringify({ url: location.href, referer: document.referrer })
});
```

## 五、浏览器扩展（可选）

`extension/` 是一个 MV3 扩展，复刻「页面内嗅探」的做法：不依赖任何扩展 API 去读响应体，
而是把钩子打进页面自己的 JS 上下文（`world: "MAIN"`），因此页面能看到的它也能看到。

装载方式：`chrome://extensions` → 打开「开发者模式」→「加载已解压的扩展程序」→ 选 `opendl\extension`。

**首次配对**：打开 OpenDL，窗口底部会显示 `配对码 a1b2c3d4…`；
点扩展图标 → 底部输入框粘贴完整配对码 → 保存。完整配对码在
`%LOCALAPPDATA%\OpenDL\token.txt`。没配对时接口一律返回 403。

能抓：XHR / fetch 里带 `video/*`、`audio/*`、`mpegurl`、`dash+xml` 的响应，
以及 `.mp4/.m3u8/.mpd/.ts/...` 结尾的直链，和页面里 `blob:` 媒体、MSE 流的存在。

抓到的条目在弹窗里点「下载」→ POST 到本机 `:6688` → 进 OpenDL 队列。

```bat
node opendl\extension\hook_test.js
```

`hook_test.js` 在 Node 里用桩对象跑 hook.js，验证过滤逻辑（视频捕获、HTML 过滤、m3u8 靠扩展名捕获）。

## 六、代码结构

```
opendl/
  core.py          探测 + 分段 + 多线程下载 + 续传状态
  integrations.py  剪贴板监听 + 本地 HTTP 接口
  ui.py            tkinter 界面
  tests/test_core.py  本地 HTTP 服务器上的端到端测试
```

## 七、测试结果

```
[1] 分段下载 4 连接: PASS (status=complete, ranges=5, got=12582912)
[2] 暂停后断点续传: PASS (paused_at=4194304, 总共传输=12845057, 状态=complete)
[3] 服务器不支持分段: PASS (status=complete, size=12582912)
[4] Content-Disposition 文件名: PASS (got '测试.zip')
```

第 2 项的判据是关键：12 MB 的文件在 4 MB 处暂停再继续，服务端总共只发送了
12.8 MB——说明续传是**从断点接着传**，不是从头重下（那样会接近 25 MB）。
