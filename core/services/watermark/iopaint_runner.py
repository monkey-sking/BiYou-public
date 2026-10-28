"""IOPaint (LaMa) inpainting wrapper for watermark removal.

IOPaint + LaMa runs in its own Python 3.11 venv (torch/Pillow 9.5 are pinned
and don't support the project's Python 3.14). BiYou calls this module as a
subprocess wrapper so watermark removal uses LaMa (far better than cv2.inpaint
at reconstructing texture behind text/logo watermarks).

CLI usage:  iopaint_runner.py <image_path> <mask_path> <out_dir>
"""
import os
import shutil
import subprocess
import sys
import tempfile

# IOPaint venv created by `python3.11 -m venv ~/.iopaint-venv`
IOPAINT_VENV = os.path.expanduser(
    os.environ.get("IOPAINT_VENV", "~/.iopaint-venv")
)
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")


def _venv_python() -> str:
    return os.path.join(IOPAINT_VENV, "bin", "python")


def venv_ready() -> bool:
    return os.path.isfile(_venv_python())


def _iopaint_installed() -> bool:
    if not venv_ready():
        return False
    try:
        subprocess.run(
            [_venv_python(), "-c", "import iopaint"],
            capture_output=True, timeout=30,
        )
        return True
    except Exception:
        return False


def inpaint(image_path: str, mask_path: str, output_path: str) -> bool:
    """Run LaMa inpainting on image with the given mask. Returns True on success.

    IOPaint's CLI treats --output as a directory in batch mode, so we use a temp
    dir and move the result to output_path.
    """
    if not _iopaint_installed():
        return False
    tmp = tempfile.mkdtemp(prefix="iopaint_")
    try:
        env = dict(os.environ)
        env["HF_ENDPOINT"] = HF_ENDPOINT
        proc = subprocess.run(
            [
                _venv_python(), "-m", "iopaint", "run",
                "--model=lama",
                "--device=cpu",
                f"--image={image_path}",
                f"--mask={mask_path}",
                f"--output={tmp}",
            ],
            capture_output=True, text=True, timeout=600, env=env,
        )
        # Result is <tmp>/<basename of image>
        base = os.path.splitext(os.path.basename(image_path))[0]
        result = os.path.join(tmp, base + ".png")
        if not os.path.isfile(result):
            for f in os.listdir(tmp):
                if f.lower().endswith((".png", ".jpg", ".jpeg")):
                    result = os.path.join(tmp, f)
                    break
        if os.path.isfile(result):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            shutil.move(result, output_path)
            return True
        return False
    except Exception:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # <image> <mask> <out_dir>
    img, mask, out = sys.argv[1], sys.argv[2], sys.argv[3]
    ok = inpaint(img, mask, os.path.join(out, "result.png"))
    print("OK" if ok else "FAIL")
    sys.exit(0 if ok else 1)
