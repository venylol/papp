"""Install/check PAPP runtime packages without reading private application data."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
WECHAT_FILES = (
    "main.py", "config.py", "config.example.json", "decrypt_db.py",
    "find_all_keys.py", "find_all_keys_windows.py", "key_scan_common.py",
    "key_utils.py", "mcp_server.py", "decode_image.py", "papp_mapping_helper.py",
)
WECHAT_IMPORTS = ("Crypto.Cipher.AES", "zstandard", "mcp.server.fastmcp")
MODEL_IMPORTS = ("numpy", "pandas", "scipy", "sklearn", "joblib", "torch", "matplotlib")


def check_layout() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("本包要求 Windows x64；不支持 macOS、Linux 或 ARM 原生环境。")
    if Path(sys.executable).resolve() != (ROOT / "runtime/python/python.exe").resolve():
        raise RuntimeError("请通过发布包根目录 CMD 使用包内 Python。")
    required = [
        ROOT / "bin/Windows/papp_GB.exe",
        ROOT / "third_party/checkin-frontend/PAPP-Local-Frontend.exe",
        ROOT / "vendor/engines/Egaroucid_for_Console_7_8_1_Windows_SIMD/Egaroucid_for_Console_7_8_1_SIMD.exe",
        ROOT / "support/requirements-runtime.txt",
        ROOT / "third_party/player-analysis-toolkit/research/tcn_loss_model/provenance/source_snapshot/human-opening-book.json",
        ROOT / "third_party/player-analysis-toolkit/research/tcn_loss_model/scripts/data/papp_player_profile_query.py",
        ROOT / "third_party/player-analysis-toolkit/research/tcn_loss_model/src/oq_player_profile.py",
        *(ROOT / "third_party/wechat-decrypt" / name for name in WECHAT_FILES),
    ]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError("发布包缺少程序文件，安装 Python 依赖不能补回这些文件：\n" + "\n".join(missing))


def check_imports(wechat_only: bool) -> int:
    failed = []
    for module in WECHAT_IMPORTS + (() if wechat_only else MODEL_IMPORTS):
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-c",
             f"import importlib; importlib.import_module({module!r})"],
            cwd=ROOT, check=False, capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode:
            failed.append(module)
            print(f"[FAIL] {module}\n{result.stderr.strip()}", flush=True)
        else:
            print(f"[OK] {module}", flush=True)
    if failed:
        print("依赖缺失或无法加载：" + ", ".join(failed), flush=True)
        return 1
    print("依赖导入检查通过。此检查不代表微信版本兼容或完整业务测试通过。", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true", help="只显示安装命令，不联网、不安装")
    modes.add_argument("--check", action="store_true", help="仅检查程序文件和运行库导入")
    modes.add_argument("--check-wechat", action="store_true", help="仅检查微信依赖，不读取微信数据")
    args = parser.parse_args()
    check_layout()
    if args.check or args.check_wechat:
        return check_imports(args.check_wechat)
    command = [
        sys.executable, "-I", "-B", "-X", "utf8", "-m", "pip",
        "--disable-pip-version-check", "install", "--index-url", MIRROR,
        "--only-binary=:all:", "--no-cache-dir", "--no-compile",
        "--timeout", "60", "--retries", "2",
        "-r", str(ROOT / "support/requirements-runtime.txt"),
    ]
    print("安装位置：" + str(ROOT / "runtime/python"), flush=True)
    print("镜像：" + MIRROR, flush=True)
    print("保留满足要求的现有库。请先关闭 PAPP 服务及调查任务。", flush=True)
    if args.plan:
        print(subprocess.list2cmdline(command))
        return 0
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("PIP_")}
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    if result.returncode:
        print("安装未完成。请保留上方报错；检查网络、磁盘空间和目录写入权限后重试。", flush=True)
        return result.returncode
    return check_imports(False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
