#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyInstaller Runtime Hook for Windows Encoding Fix
这个 hook 文件会在 PyInstaller 打包的 exe 启动时首先运行，
用于解决 Windows 平台上的中文编码问题。
"""

import sys
import os
import platform

# 在程序启动的最早期设置编码
if platform.system() == "Windows":
    # 设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONLEGACYWINDOWSSTDIO'] = '1'
    
    # 重新配置标准输出流
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass
    
    # 如果上述方法不起作用，使用 io.TextIOWrapper
    try:
        import io
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass 