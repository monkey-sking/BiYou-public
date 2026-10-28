import os
from core.utils.config import load_config
from core.utils.i18n import print_t
from core.utils.logger import log
from core.services.image_translator import ImageTranslationService
from core.services.watermark.factory import WatermarkServiceFactory

CONFIG = load_config()
MAX_OUTPUT_SIZES = {
    "16:9": (1920, 1080),
    "4:3": (1200, 900),
    "3:4": (1080, 1440),
    "6:7": (1080, 1260),
    "9:16": (1080, 1920),
}

class CoverWorkflow:
    """
    Orchestrator for the Cover Processing Workflow.
    Composes:
    1. Image Translation Service (LLM)
    2. Watermark Removal Service (CV/Rules)
    """
    
    @staticmethod
    async def run(
        image_path: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        prompt_template: str | None = None,
        **kwargs,
    ) -> str:
        """
        Executes the full cover processing pipeline.
        """
        
        # 1. Image Translation / Generation
        prompts = CONFIG.get('prompts', {})
        prompt_template = prompt_template or prompts.get('cover_generation', "")
        
        print_t('cover_gen_start')
        generated_path = await ImageTranslationService.process(
            image_path=image_path, 
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            prompt_template=prompt_template,
            **kwargs
        )
        
        if not generated_path:
            return None
            
        # 2. Watermark Removal (prefer reverse-alpha, fallback inpaint)
        # Run before compression to preserve original pixel fidelity for reverse alpha.
        # Only the Gemini fallback and older providers carry a baked watermark. The
        # domestic browser providers (jimeng/hunyuan/doubao) are clean, and tongyi
        # removes its own watermark inside the service, so skip the generic pass for
        # them to avoid false-positive inpainting that blurs clean content.
        CLEAN_PROVIDERS = {"jimeng", "tongyi", "hunyuan", "doubao"}
        used_provider = getattr(ImageTranslationService, "last_provider", None)
        if os.path.isfile(generated_path) and used_provider not in CLEAN_PROVIDERS:
            watermark_service = WatermarkServiceFactory.get_service()
            print_t('cover_watermark_try')
            wm_result = watermark_service.remove_watermark(generated_path)

            if wm_result.success:
                print_t('cover_watermark_ok')
                log.info(f"✅ Watermark removal successful ({wm_result.method}). {wm_result.message}")
            elif wm_result.status == 'not_found':
                print_t('cover_watermark_skip', wm_result.message or "No watermark detected")
                log.info(f"ℹ️ Watermark skip ({wm_result.method}): {wm_result.message}")
            else:
                print_t('cover_watermark_fail', wm_result.message or "Unknown error")
                log.warning(f"⚠️ Watermark removal failed ({wm_result.method}): {wm_result.message}")
        elif os.path.isfile(generated_path):
            log.info(f"ℹ️ 跳过通用去水印 (provider={used_provider}, 输出无内嵌水印)")

            # 3. Image Compression (Reduce file size for large images)
            # Bilibili recommended: 16:9 -> 1920x1080, 4:3 -> 1200x900
            try:
                from PIL import Image
                img = Image.open(generated_path)
                width, height = img.size
                
                # 1. Force convert to RGB/JPEG format
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # We trust the model (Banana 2) to generate the correct aspect ratio natively
                # based on our updated prompts.yaml.
                # Do NOT forcibly crop the center anymore, as it cuts off heads/feet from square generations.
                # We only compress/resize if it's monstrously huge, retaining original physical subject bounding.
                
                # 2. Resize maintaining aspect ratio if it exceeds platform-friendly max dimensions
                max_size = MAX_OUTPUT_SIZES.get(aspect_ratio)
                if max_size and (width > max_size[0] or height > max_size[1]):
                    img.thumbnail(max_size, Image.LANCZOS)

                # 2b. Upscale tiny/low-res provider output to a usable minimum so covers
                # are never delivered as a small blurry thumbnail. High-quality LANCZOS.
                # (Does not add true detail but guarantees a consistent, non-tiny cover.)
                if max_size and (img.size[0] < max_size[0] and img.size[1] < max_size[1]):
                    scale = min(max_size[0] / img.size[0], max_size[1] / img.size[1])
                    new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
                    img = img.resize(new_size, Image.LANCZOS)

                # 3. Force save as JPEG
                img.save(generated_path, "JPEG", quality=85, optimize=True)
                    
                log.info(f"📐 图片已处理 (RGB/JPEG): {width}x{height} → {img.size[0]}x{img.size[1]} ({aspect_ratio})")
            except Exception as e:
                log.warning(f"⚠️ 图片裁剪/压缩失败: {e}")
        
        return generated_path
