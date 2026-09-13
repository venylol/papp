"""Launch the bundled WeChat CLI with an explicit local module search path."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WECHAT = ROOT / "third_party" / "wechat-decrypt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--check-local", action="store_true")
    args = parser.parse_args()
    if not args.check_local:
        checked = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8",
             str(ROOT / "support/install_dependencies.py"), "--check-wechat"],
            check=False,
        )
        if checked.returncode:
            print("请先运行 papp-install-deps.cmd 安装依赖。", flush=True)
            return checked.returncode

    # python311._pth excludes the script directory even when it is the cwd.
    sys.path.insert(0, str(WECHAT))
    os.environ["WECHAT_DECRYPT_APP_DIR"] = str(WECHAT)
    os.environ.pop("WECHAT_DECRYPT_NONINTERACTIVE", None)
    os.environ.pop("WECHAT_DECRYPT_GUI", None)
    os.chdir(WECHAT)
    importlib.import_module("key_utils")
    importlib.import_module("config")
    cli = importlib.import_module("main")
    print("[OK] 微信本地主程序、配置模块和 key_utils 已加载。", flush=True)
    if args.check or args.check_local:
        return 0

    print("请先登录本机微信。首次运行将检测数据目录；多个账号时请选择正确账号。", flush=True)
    print("自动检测失败时，请按 config.example.json 填写本目录 config.json。", flush=True)
    sys.argv = [str(WECHAT / "main.py"), "decrypt"]
    try:
        result = cli.main()
    except SystemExit as exc:
        if exc.code:
            print("[ERROR] 解密失败，请查看上方错误；仅在进程访问权限不足时尝试管理员运行。", flush=True)
        raise
    print("[OK] 解密命令完成，可以打开 PAPP 前端。", flush=True)
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
