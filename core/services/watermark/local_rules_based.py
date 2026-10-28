import numpy as np
from PIL import Image
from core.utils.logger import log
from .base import BaseWatermarkService, WatermarkResult


class LocalRulesBasedWatermarkService(BaseWatermarkService):
    def remove_watermark(self, image_path: str, output_path: str = None) -> WatermarkResult:
        """
        Removes the Gemini ✦ watermark from the bottom-right corner.
        Detects BOTH bright (white on dark) AND dark (black/gray on light) watermarks.
        Uses morphological background estimation + OpenCV inpainting.
        Skips inpainting if no watermark is confidently detected.
        """
        import cv2
        
        if not output_path:
            output_path = image_path
            
        try:
            img = Image.open(image_path)
            original_mode = img.mode
            width, height = img.size
            
            # Watermark size scales with resolution
            if width > 2000 or height > 2000:
                wm_radius = 70   # high-res original
            elif width > 1200 or height > 1200:
                wm_radius = 55   # compressed 1920x1080
            else:
                wm_radius = 40   # small images
            
            log.info(f"💧 Watermark removal for {width}x{height} (radius={wm_radius})")
            
            img_arr = np.array(img.convert("RGB"), dtype=np.uint8)
            
            # --- 1. Search ROI: generous region around expected watermark ---
            search_margin = wm_radius + 40
            roi_y1 = max(0, height - search_margin)
            roi_x1 = max(0, width - search_margin)
            roi = img_arr[roi_y1:height, roi_x1:width]
            
            # --- 2. Background Estimation (Morphological) ---
            # Convert ROI to Grayscale (V channel from HSV is best for brightness)
            hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
            v_channel = hsv[:,:,2]
            
            # Morphological Opening: removes bright features smaller than kernel
            # Morphological Closing: removes dark features smaller than kernel
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
            bg_open = cv2.morphologyEx(v_channel, cv2.MORPH_OPEN, kernel)
            bg_close = cv2.morphologyEx(v_channel, cv2.MORPH_CLOSE, kernel)
            
            # Bright watermark: original brighter than background
            # Threshold lowered to 8 (from 20) to catch faint white-on-white Gemini ✦
            diff_bright = cv2.subtract(v_channel, bg_open)
            _, mask_bright = cv2.threshold(diff_bright, 8, 255, cv2.THRESH_BINARY)
            
            # Dark watermark: original darker than background (Gemini ✦ is dark on light bg)
            diff_dark = cv2.subtract(bg_close, v_channel)
            _, mask_dark = cv2.threshold(diff_dark, 8, 255, cv2.THRESH_BINARY)
            
            # Combine both masks
            combined_mask = cv2.bitwise_or(mask_bright, mask_dark)
            
            # --- 2b. Edge-based Fallback (Canny) ---
            # When the ✦ sparkle is nearly identical brightness to background,
            # morphological diff fails. Canny detects shape edges regardless of contrast.
            edges = cv2.Canny(v_channel, 30, 80)
            combined_mask = cv2.bitwise_or(combined_mask, edges)
            
            # Clean up noise
            clean_kernel = np.ones((3,3), np.uint8)
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, clean_kernel, iterations=1)
            # Slight dilation to connect nearby fragments of the ✦ symbol
            combined_mask = cv2.dilate(combined_mask, clean_kernel, iterations=2)
            
            # --- 3. Detection via Connected Components ---
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(combined_mask, 8)
            
            cx, cy = None, None
            detected = False
            
            if num_labels > 1:
                best_idx = -1
                best_score = 0
                
                roi_h, roi_w = roi.shape[:2]
                
                for idx in range(1, num_labels):
                    area = stats[idx, cv2.CC_STAT_AREA]
                    blob_cx = centroids[idx][0]
                    blob_cy = centroids[idx][1]
                    # Filter: reasonable watermark size (very small noise to medium symbol)
                    # For high resolution images (e.g. 2700x1500+), the watermark blob can easily exceed 8000 pixels.
                    # But it can also be broken into tiny pieces (area < 50), so we must be very lenient.
                    if area < 5 or area > 18000:
                        continue
                    
                    # Aspect ratio check removed: The ✦ often breaks into thin fragments during thresholding
                    
                    # Filter: must be in bottom-right portion of ROI
                    if blob_cx < roi_w * 0.15 or blob_cy < roi_h * 0.15:
                        continue
                    
                    # Score: prefer blobs closer to bottom-right corner with reasonable size
                    dist_to_corner = np.sqrt((blob_cx - roi_w)**2 + (blob_cy - roi_h)**2)
                    score = area / max(dist_to_corner, 1)
                    
                    if score > best_score:
                        best_score = score
                        best_idx = idx
                
                if best_idx > 0:
                    det_cx = roi_x1 + centroids[best_idx][0]
                    det_cy = roi_y1 + centroids[best_idx][1]
                    blob_w = stats[best_idx, cv2.CC_STAT_WIDTH]
                    blob_h = stats[best_idx, cv2.CC_STAT_HEIGHT]
                    det_radius = max(blob_w, blob_h) // 2 + 18
                    
                    cx, cy = int(det_cx), int(det_cy)
                    wm_radius = max(wm_radius, int(det_radius))
                    detected = True
                    log.info(f"🔍 Detected watermark at ({cx}, {cy}), radius={wm_radius}, area={stats[best_idx, cv2.CC_STAT_AREA]}")
            
            if not detected:
                # No watermark found — skip inpainting to avoid damaging clean images
                log.info("📍 No watermark detected, skipping inpaint")
                return WatermarkResult(
                    status="not_found",
                    message="No watermark detected in ROI",
                    method="local_rules"
                )
            
            # --- 4. Mask Creation and Inpaint ---
            mask = np.zeros((height, width), dtype=np.uint8)
            cv2.circle(mask, (cx, cy), wm_radius + 10, 255, -1)
            
            result = cv2.inpaint(img_arr, mask, inpaintRadius=14, flags=cv2.INPAINT_NS)
            
            # Save
            res_img = Image.fromarray(result)
            if output_path.lower().endswith(('.jpg', '.jpeg')):
                res_img.save(output_path, quality=95, subsampling=0)
            else:
                if original_mode == "RGBA":
                    res_img = res_img.convert("RGBA")
                res_img.save(output_path)
                
            log.info(f"✨ Watermark removed (inpaint at {cx},{cy} r={wm_radius}): {output_path}")
            return WatermarkResult(
                status="removed",
                message=f"inpaint at ({cx},{cy}) r={wm_radius}",
                method="local_rules"
            )
            
        except Exception as e:
            log.error(f"❌ Failed to remove watermark: {e}")
            import traceback
            traceback.print_exc()
            return WatermarkResult(
                status="error",
                message=str(e),
                method="local_rules"
            )
