"""PyInstaller 入口：把 opendl 打包成单文件 exe。

显式导入所有子模块，避免 PyInstaller 的静态分析漏掉函数内的延迟导入。
"""

import opendl.core          # noqa: F401
import opendl.ffmpeg        # noqa: F401
import opendl.hls           # noqa: F401
import opendl.integrations  # noqa: F401
import opendl.protocol      # noqa: F401
import opendl.ui            # noqa: F401
from opendl.launch import main

if __name__ == "__main__":
    main()
