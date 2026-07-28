"""脚本公共引导：把项目根加入 sys.path，并把标准输出切到 UTF-8。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
