# -*- coding: utf-8 -*-
"""
GeminiServiceV2 — gemini_webapi 2.0.0 独立后端 (与 1.21.0 主服务共存)。

设计: 2.0.0 与 1.21.0 同名包不能同进程加载, 所以 V2 走子进程隔离:
  本类通过 subprocess 调用 vendor/gemini_webapi2/run_v2.py (独立 python 进程,
  sys.path 只指向 vendor), 输出 JSON 行协议。

配置: config.yaml services.<task>.provider = 'gemini_web_v2' 即切换。
  例: translation: {provider: gemini_web_v2}  /  cover: {provider: gemini_web_v2}
"""
import asyncio
import json
import os
import subprocess
from typing import Optional

from core.utils.logger import log
from core.services.llm.base import LLMBase

VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), 'vendor', 'gemini_webapi2')
BRIDGE = os.path.join(VENDOR_DIR, 'run_v2.py')
PYTHON = '/opt/homebrew/bin/python3.11'


def _run_bridge(args: list, timeout: int = 180) -> dict:
    """调用 bridge, 返回 JSON。"""
    cmd = [PYTHON, BRIDGE] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'bridge timeout ({timeout}s)'}
    out = proc.stdout.strip()
    if not out:
        return {'ok': False, 'error': f'bridge no output, rc={proc.returncode}, stderr={proc.stderr[-200:]}'}
    try:
        # 取最后一行 JSON (前面的 loguru 输出不算)
        last_json = out.splitlines()[-1]
        return json.loads(last_json)
    except Exception:
        return {'ok': False, 'error': f'bridge bad json: {out[-300:]}'}


class GeminiServiceV2(LLMBase):
    """gemini_webapi 2.0.0 后端 (子进程隔离)。"""

    def __init__(self, service_config: dict = None, **kwargs):
        self.service_config = service_config or {}
        self.provider = 'gemini_web_v2'

    # ── 文本生成 (翻译等) ──
    async def generate_text(self, prompt: str, model: str = None) -> str:
        """兼容 generate_text 接口: 直接透传 prompt (不带 TRANSLATED 包装)。"""
        r = await asyncio.to_thread(
            _run_bridge, ['raw', prompt], 120)
        if not r.get('ok'):
            raise RuntimeError(r.get('error', 'v2 bridge failed'))
        return r.get('text', '')

    async def translate_text(self, text: str, target: str = 'Chinese') -> str:
        """翻译 (带 TRANSLATED: 解析, 与 1.21.0 translator 兼容)。"""
        r = await asyncio.to_thread(_run_bridge, ['translate', text], 120)
        if not r.get('ok'):
            raise RuntimeError(r.get('error', 'v2 bridge failed'))
        return r.get('text', '')

    # ── 图像生成/编辑 (Web 图 API) ──
    async def generate_image(self, prompt: str, input_image_path: str = None,
                             output_path: str = None, model: str = None) -> Optional[str]:
        """生成/编辑图片, 返回输出文件路径 (第一个图)。"""
        args = ['genimg', '--prompt', prompt]
        if input_image_path:
            args += ['--input', input_image_path]
        if output_path:
            args += ['--output', output_path]
        if model:
            args += ['--model', model]
        r = await asyncio.to_thread(_run_bridge, args, 240)
        if not r.get('ok'):
            log.warning(f"⚠️ gemini_web_v2 图像生成失败: {r.get('error', 'unknown')}")
            return None
        imgs = r.get('images', [])
        return imgs[0] if imgs else None

    # 别名, 兼容现有封面调用
    async def generate(self, prompt: str, input_image_path: str = None,
                       output_path: str = None, model: str = None) -> Optional[str]:
        return await self.generate_image(prompt, input_image_path, output_path, model)
