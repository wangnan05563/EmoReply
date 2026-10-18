"""让 tests/ 下的用例能直接 import emoreply（无需安装包）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))