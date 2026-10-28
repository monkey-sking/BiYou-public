
import os
from core.utils.config import load_config

TRANS = {
    'en': {
        # Generic Logs
        'info': "ℹ️ {}",
        'warning': "⚠️ {}",
        'error': "❌ {}",
        
        # Browser Selection
        'init_setup_browser': "⚙️ Initial Setup: Browser Selection",
        'select_browser_prompt': "Please select the browser where you are logged into:",
        'browser_yt': " - YouTube (for age-gated content)",
        'browser_gemini': " - Gemini (for API access)",
        'browser_bili': " - Bilibili (for upload)",
        'select_default_browser': "Select default browser:",
        'browser_configured': "✅ Configured default browser: {}",
        'config_save_fail': "⚠️ Failed to save configuration. Will use selection for this run only.",
        'browser_none': "❌ No browser selected. Defaulting to Chrome.",
        'browser_using': "🌍 Using configured browser: {}",
        
        # Steps
        'step_0': "STEP 0: Download Video",
        'step_0_fail': "❌ Download failed. Aborting.",
        'step_0_no_url': "ℹ️ No URL provided, checking for latest video...",
        'step_0_success': "✅ Downloaded: {}",
        'step_0a': "⚡️ [Step 0a] Fetching metadata and thumbnail...",
        'step_0b': "🚀 [Step 0b] Starting video download in background...",
        'step_0c': "⏳ [Step 0c] Waiting for video download...",
        
        'step_1': "STEP 1: Fetch Metadata & Translate",
        'step_1_skip': "⏩ Skipping Step 1 (Translate)",
        'step_1_topic_search': "🔍 Searching for activity topic (Keyword: {}) ...",
        'step_1_5': "STEP 1.5: Generate AI Subtitles",
        'step_1_5_skip': "⏭️ Skipping subtitle generation (--skip-subtitle)",
        
        'step_2': "STEP 2: Process Cover (Gemini API)",
        'step_2_skip': "⏩ Skipping Step 2 (Cover)",
        
        'step_3': "STEP 3: Upload to Bilibili",
        'step_3_skip': "⏩ Skipping Step 3 (Upload)",
        
        'step_4': "STEP 4: Post Comment",
        'step_4_skip': "⏩ Skipping Step 4 (Comment)",
        
        # Cleanup
        'cleanup_title': "🧹 Cleanup & Archiving",
        'cleanup_archived': "✅ Archived metadata to: {}",
        'cleanup_removed': "🗑️ Removed temporary file: {}",
        'cleanup_fail': "⚠️ Failed to archive/cleanup: {}",
        'cleanup_skip': "⚠️ Video not fully processed or missing path/BVID. Skipping cleanup.",
        'cleanup_source_video': "🗑️ Deleted source video: {}",
        'cleanup_audio': "🗑️ Cleaned up temporary audio file",
        'workflow_complete': "\n✅ Workflow Completed!",
        'url_invalid_single': "❌ The input is not a supported video URL. Stopping: {}",
        'url_invalid_hint': "ℹ️ Currently only YouTube video URLs are supported (watch / shorts / youtu.be).",
        'url_invalid_multi_skip': "⚠️ Skipped {} non-video URLs before processing.",
        'url_invalid_summary': "⚠️ Skipped {} non-video URLs in this run.",
        'url_invalid_list_title': "   - Skipped non-video URLs:",
        'url_no_valid': "❌ No valid video URLs detected. Stopping.",
        'workflow_start': "🚀 Starting Unified Workflow",
        'dry_run_enabled': "⚡️ DRY RUN MODE ENABLED",
        'workflow_invalid_schedule': "❌ Invalid schedule time format",
        'workflow_schedule_future_error': "⚠️ Schedule time must be at least 4 hours in the future (Earliest: {})",
        'workflow_using_meta': "👉 Using latest metadata file: {}",
        'workflow_cover_fail': "❌ Cover processing failed: {}",
        'workflow_wait_dl': "⚠️ Waiting for background download to finish...",
        'workflow_dl_wait_fail': "❌ Video download failed",
        'workflow_dl_error': "❌ Error during video download: {}",
        'workflow_bg_download': "🚀 Starting background download...",
        'workflow_preflight': "🔍 Running Pre-flight Checks...",
        'workflow_preflight_ok': "✅ All pre-flight checks passed!\n",
        'workflow_preflight_fail': "❌ Pre-flight checks failed. Aborting.",
        'workflow_bili_check': "👉 Checking Bilibili login status ({})...",
        'workflow_bili_ok': "   ✅ Bilibili: Logged in ({})",
        'workflow_bili_fail': "   ❌ Bilibili: Invalid Cookie ({})",
        'workflow_bili_no_cookie': "   ❌ Bilibili: Cookie not found",
        'workflow_gemini_check': "👉 Checking Gemini login status ({})...",
        'workflow_gemini_ok': "   ✅ Gemini: Cookie valid",
        'workflow_gemini_fail': "   ❌ Gemini: Invalid Cookie ({})",
        'workflow_gemini_err': "   ❌ Gemini: Check failed ({})",

        # Downloader
        'dl_start': "\n🚀 Start downloading: {}",
        'dl_dir': "📂 Save directory: {}",
        'dl_complete': "✅ Download complete: {}",
        'dl_path': "📂 Video file ready: {}",
        'dl_fail': "❌ Download failed: {}",
        'dl_clean_temp': "🧹 Cleaned up temporary files (.part, .ytdl)",
        'dl_bg_start': "⏳ Background download started: {} ...",
        'dl_bg_start': "⏳ Background download started: {} ...",
        'dl_confirmed': "✅ Video download confirmed: {}",
        'dl_meta_done': "✅ Metadata fetch complete: {}",
        'dl_skip_exists': "⏩ Video already exists, skipping download: {}",
        'dl_strat_best_mp4': "Attempting best quality MP4 download...",
        'dl_strat_best_any': "⚠️ Falling back to best available quality (any format)...",
        'dl_strat_compat': "⚠️ Falling back to compatibility mode (single file)...",
        
        # Fetcher
        'fetch_title': "  Fetching Latest Video Info",
        'fetch_found': "📂 Found unprocessed video: {}",
        'fetch_all_done': "✅ All videos have been processed (History check).",
        'fetch_info_loaded': "✅ Loaded local metadata (info.json)",
        'fetch_info_fail': "⚠️ Failed to read info.json: {}",
        'fetch_fail': "❌ Failed to get video info: {}",
        'fetch_used_arg': "📂 Using specified video: {}",
        'fetch_original_title': "\n📺 Original title: {}",
        'fetch_original_desc': "📝 Original description: {}...",
        'fetch_translating': "🔤 Translating with Gemini...",
        'fetch_trans_done': "✅ Translation complete",
        'fetch_trans_fail': "⚠️ Translation failed: {}",
        'fetch_cn_title': "\n📺 Chinese title: {}",
        'fetch_cn_desc': "📝 Chinese description: {}...",
        'fetch_metadata_saved': "✅ Metadata saved to: {}",
        'fetch_ready': "  ✅ Metadata ready!",
        'fetch_resume_found': "💡 Found existing metadata for this video",
        'fetch_select_cat': "Select category:",
        'fetch_cat_life': "  [1] Life-Daily (default)",
        'fetch_cat_sports': "  [2] Life-Sports",
        'fetch_cat_knowledge': "  [3] Knowledge-Science",
        'fetch_cat_fitness': "  [4] Fashion-Fitness (recommended)",
        'fetch_resume_prompt': "❓ Found existing metadata for this video. Use it?",
        'fetch_resume_hint': "   (y: Use existing | n: Fetch new)",
        'fetch_resume_skip': "✅ Using existing metadata, skipping fetch.",
        
        # Translator
        'trans_testing': "🔤 Testing translation...",
        'trans_original': "Original: {}",
        'trans_result': "Translated: {}",
        'trans_original_title': "\n📺 Original: {}",
        'trans_cn_title': "📺 Chinese:  {}",
        'trans_original_desc': "\n📝 Original: {}",
        'trans_cn_desc': "📝 Chinese:  {}",
        'trans_auto': "\n🤖 Auto-translating...",
        'trans_module_missing': "⚠️ gemini_translate module not found, returning original",
        'ai_analyzing': "\n🤖 Analyzing content (Category & Tags)...",
        'ai_cat': "✅ AI Recommended Category: {}",
        'ai_tags': "✅ AI Generated Tags: {}...",
        'ai_fail': "⚠️ AI Analysis failed: {}",
        'ai_activity_check': "🔍 Searching for relevant activity/topics...",
        
        # Cover
        'cover_processing': "\n🚀 Processing: {} -> {}",
        'cover_prompt': "✍️ Prompt: {}...",
        'cover_model': "🤖 Using model: {} ({})",
        'cover_no_img': "❌ Gemini did not generate any images",
        'cover_saved': "💾 Saved to: {}",
        'cover_watermark_try': "🌊 Attempting to remove watermark...",
        'cover_watermark_ok': "✅ Watermark removed successfully",
        'cover_watermark_skip': "ℹ️ Watermark skipped: {}",
        'cover_watermark_fail': "⚠️ Watermark removal failed: {}",
        'cover_using_source': "📸 Using source cover: {}",
        'cover_init_client': "\n🔐 Initializing Gemini client with browser cookies...",
        'cover_cookie_fail': "❌ Could not find __Secure-1PSID cookie in Chrome",
        'cover_cookie_found': "✅ Found Cookies: __Secure-1PSID (length={})",
        'cover_client_ok': "✅ Gemini client initialized successfully",
        'cover_gen_start': "🤖 Starting AI processing...",
        'cover_done': "\n✅ Cover processing complete:",
        'cover_16_9': "   16:9: {}",
        'cover_4_3': "   4:3: {}",
        'cover_4_3': "   4:3: {}",
        'cover_no_source': "❌ Source cover not found",
        'cover_use_existing_prompt': "👉 Use existing covers? (Y/n): ",
        'cover_using_existing': "👉 Using existing cover from metadata/file...",
        'cover_regenerating': "🔄 Regenerating cover due to user request...",
        
        # Uploader
        'up_title': "\n========== Bilibili Video Upload ==========",
        'up_file': "\n📺 Video: {}",
        'up_meta_title': "📝 Title: {}",
        'up_meta_desc': "📝 Description: {}...",
        'up_tags': "🏷️ Tags: {}",
        'up_cat': "📂 Category: {} (tid={})",
        'up_cat_wrong': "⚠️ Category '{}' is invalid, falling back to '生活-日常' (tid=21)",
        'up_cat_invalid': "⚠️ Category '{}' invalid, falling back to '{}' (tid={})",
        'up_cover_16_path': "🖼️ Cover 16:9: {}",
        'up_cover_43_path': "🖼️ Cover 4:3: {}",
        'up_schedule': "⏰ Schedule: {}",
        'up_schedule_now': "⏰ Schedule: Publish Immediately",
        'up_dry_run': "⚡️ DRY RUN MODE: Skipping confirmation and upload.",
        'up_cancel': "Upload cancelled",
        'up_credentials_edge': "🔄 Fetching Bilibili credentials from Edge...",
        'up_credentials_ok': "✅ Successfully fetched Bilibili credentials from {}",
        'up_pre_check_ok': "✅ Pre-upload check passed (All assets found).",
        'up_pre_check_fail': "⚠️ Pre-upload check failed:",
        'up_missing_item': "   - Missing/Invalid: {}",
        'up_auto_confirm_skip': "❌ [Auto] Assets missing and auto-confirm enabled. Aborting.",
        'up_sub_trans_start': "🤖 Translating subtitles (AI): {} -> .zh-Hans.vtt",
        'up_sub_trans_done': "✅ Subtitle translation complete: {}",
        'up_sub_trans_fail': "⚠️ Subtitle translation failed",
        'up_sub_trans_err': "⚠️ Subtitle translation error: {}",
        'up_desc_label': "📝 Description: {}",
        'up_schedule_warn': "⚠️ Warning: Schedule time must be at least 4 hours later.",
        'up_schedule_earliest': "   Earliest valid time: {}",
        'up_start': "\n🚀 Starting upload...\n",
        'up_using_cookies': "Using cookies to upload",
        'up_cover_16': "🖼️ Uploading cover (16:9): {}",
        'up_cover_43': "🖼️ Uploading cover (4:3): {}",
        'up_cover_url': "   URL: {}",
        'up_video': "\n📤 Uploading video: {}",
        'up_submit': "\n📝 Submitting...",
        'up_success': "\n========== ✅ Upload Success! ==========",
        'up_bvid': "\n🔗 Upload ID: {}",
        'up_saved': "✅ Upload info saved to: {}",
        'up_fail': "\n❌ Upload failed: {}",
        
        # Subtitle
        'sub_title': "🎬 AI Subtitle Processing",
        'sub_audio_exists': "⏭️ Audio already exists: {}",
        'sub_title': "STEP 1.5: Generate AI Subtitles",
        'sub_audio_exists': "🎵 Audio file exists, skipping extraction: {}",
        'sub_extract_audio': "🎵 Extracting audio: {}",
        'sub_extract_done': "✅ Audio extraction complete: {}",
        'sub_extract_fail': "❌ Audio extraction failed: {}",
        'sub_transcribe': "🎤 Transcribing audio with Gemini...",
        'sub_transcribe_done': "✅ Transcription complete: {} segments",
        'sub_transcribe_fail': "❌ Transcription failed: {}",
        'sub_json_fail': "⚠️ JSON parse failed: {}",
        'sub_json_raw': "   Raw response: {}...",
        'sub_translate': "🌐 Translating {} subtitle segments...",
        'sub_translate_batch': "   Processing Batch: {}",
        'sub_translate_done': "✅ Translation complete",
        'sub_translate_fail': "❌ Translation failed: {}",
        'sub_parsed': "📂 Parsed existing subtitle: {} ({} segments)",
        'sub_parse_fail': "⚠️ Failed to parse subtitle: {} ({})",
        'sub_reuse_en': "♻️ Reusing existing source subtitle",
        'sub_reuse_source': "♻️ Reusing existing source subtitle",
        'sub_reuse_target': "♻️ Reusing existing target subtitle",
        'sub_detected_target': "👀 Detected existing subtitle matches target language: {}",
        'sub_only_target': "⚠️ Only target subtitle available, bilingual ASS skipped",
        'sub_srt_saved': "💾 SRT subtitle saved: {}",
        'sub_ass_saved': "💾 Bilingual ASS subtitle saved: {}",
        'sub_burn': "🔥 Burning subtitles into video...",
        'sub_burn_done': "✅ Burned video ready: {}",
        'sub_burn_fail': "❌ Failed to burn subtitles: {}",
        'sub_burn_done': "✅ Subtitle burn complete: {}",
        'sub_burn_fail': "❌ Subtitle burn failed: {}",
        'sub_skip_no_trans': "⚠️ Transcription failed, skipping subtitle generation",
        'sub_video_not_found': "⚠️ Video file not found, skipping subtitle generation",
        'sub_done': "\n✅ Subtitle processing complete:",
        'sub_en': "   English: {}",
        'sub_zh': "   Chinese: {}",
        'sub_bilingual': "   Bilingual: {}",
        'sub_fail': "⚠️ Subtitle generation failed: {}",
        
        # Commenter
        'comment_target': "🎯 Target video: {} (aid={})",
        'comment_hook': "1️⃣ Posting main comment (Hook)...",
        'comment_hook_ok': "✅ Main comment posted: rpid={}",
        'comment_link': "2️⃣ Posting link reply (Link)...",
        'comment_link_ok': "✅ Link reply posted",
        'comment_get_aid_fail': "⚠️ Failed to get AID: {}",
        'comment_no_bvid': "❌ Metadata missing BVID. Please run upload first.",
        'comment_no_cookies': "❌ Cookies not found",
        'comment_no_csrf': "❌ Cookie missing bili_jct (CSRF Token)",
        'comment_hook_fail': "❌ Main comment failed: {}",
        'comment_link_fail': "❌ Link reply failed: {}",
        'comment_pin_fail': "⚠️ Pin failed: {}",
        'comment_pin': "3️⃣ Pinning main comment...",
        'comment_pin_ok': "✅ Pinned successfully!",
        'comment_done': "\n🎉 Done! View result: {}",
        'comment_dry_post': "⚡️ DRY RUN: Would post comment: {}...",
        'comment_dry_reply': "⚡️ DRY RUN: Would reply to {}: {}...",
        'comment_dry_pin': "⚡️ DRY RUN: Would pin rpid={}",
        'comm_dry_run': "⚡️ [Dry Run] Skipping automatic comment flow",
        'comm_fail_meta': "❌ Comment failed: Metadata file not found",
        'comm_skip_cat': "ℹ️ Category is '{}', not fitness-related, skipping comment.",
        'comm_start': "💬 Posting main comment for video (aid={})...",
        'comment_ok_main': "✅ Main comment posted! (rpid={})",
        'comm_top_start': "📌 Pinning the comment (with retries)...",
        'comm_ok_top': "✅ Comment pinned successfully!",
        'comm_fail_top': "⚠️ Failed to pin comment: {}",
        'comm_sub_start': "🔗 Posting sub-comment (link) (with retries)...",
        'comm_ok_sub': "✅ Sub-comment (link) posted successfully!",
        'comm_fail_sub': "⚠️ Failed to post sub-comment: {}",
        'comm_err': "❌ Automatic comment flow exception: {}",
        'prompt_input': "👉 Please Enter [Y/n]: ",
    },
    'zh': {
        # Generic Logs
        'info': "ℹ️ {}",
        'warning': "⚠️ {}",
        'error': "❌ {}",
        
        # Browser Selection
        'init_setup_browser': "⚙️ 初始化设置: 浏览器选择",
        'select_browser_prompt': "请选择您已登录账号的浏览器:",
        'browser_yt': " - YouTube (用于下载年龄限制视频)",
        'browser_gemini': " - Gemini (用于 API 访问)",
        'browser_bili': " - Bilibili (用于视频上传)",
        'select_default_browser': "请选择默认浏览器:",
        'browser_configured': "✅ 已配置默认浏览器: {}",
        'config_save_fail': "⚠️ 配置保存失败，仅本次运行有效。",
        'browser_none': "❌ 未选择浏览器，默认使用 Chrome。",
        'browser_using': "🌍 使用配置的浏览器: {}",

        # Steps
        'step_0': "步骤 0: 下载视频",
        'step_0_fail': "❌ 下载失败，终止流程。",
        'step_0_no_url': "ℹ️ 未提供 URL，将检查最新下载的视频...",
        'step_0_success': "✅ 下载完成: {}",
        'step_0a': "⚡️ [Step 0a] 快速获取元数据和封面...",
        'step_0b': "🚀 [Step 0b] 后台启动视频流下载...",
        'step_0c': "⏳ [Step 0c] 等待视频下载完成...",
        
        'step_1': "步骤 1: 获取元数据 & 翻译",
        'step_1_skip': "⏩ 跳过步骤 1 (翻译)",
        'step_1_topic_search': "🔍 正在搜索适合的活动话题 (关键词: {}) ...",
        'step_1_5': "步骤 1.5: 生成 AI 字幕",
        'step_1_5_skip': "⏭️ 跳过字幕生成 (--skip-subtitle)",
        
        'step_2': "步骤 2: 处理封面 (Gemini API)",
        'step_2_skip': "⏩ 跳过步骤 2 (封面)",
        
        'step_3': "步骤 3: 上传至 Bilibili",
        'step_3_skip': "⏩ 跳过步骤 3 (上传)",
        
        'step_4': "步骤 4: 发布置顶评论",
        'step_4_skip': "⏩ 跳过步骤 4 (评论)",
        
        # Cleanup
        'cleanup_title': "🧹 清理 & 归档",
        'cleanup_archived': "✅ 元数据已归档至: {}",
        'cleanup_removed': "🗑️ 已删除临时文件: {}",

        'cleanup_fail': "⚠️ 归档/清理失败: {}",
        'cleanup_skip': "⚠️ 视频未完全处理或缺少 BVID，跳过清理。",
        'cleanup_source_video': "🗑️ 已删除源视频文件: {}",
        'cleanup_audio': "🗑️ 已清理临时音频文件",
        'workflow_complete': "\n✅ 工作流执行完成！",
        'url_invalid_single': "❌ 输入的链接不是受支持的视频链接，已停止: {}",
        'url_invalid_hint': "ℹ️ 当前仅支持 YouTube 视频链接 (watch / shorts / youtu.be)。",
        'url_invalid_multi_skip': "⚠️ 预处理时已跳过 {} 个非视频链接。",
        'url_invalid_summary': "⚠️ 本次共跳过 {} 个非视频链接。",
        'url_invalid_list_title': "   - 已跳过的非视频链接:",
        'url_no_valid': "❌ 未检测到可处理的视频链接，已停止。",
        'workflow_start': "🚀 启动统一自动化工作流",
        'dry_run_enabled': "⚡️ 模拟运行模式已启用",
        'workflow_invalid_schedule': "❌ 无效的定时发布时间格式",
        'workflow_schedule_future_error': "⚠️ 定时发布时间必须至少在未来 4 小时之后 (最早: {})",
        'workflow_using_meta': "👉 使用最新的元数据文件: {}",
        'workflow_cover_fail': "❌ 封面处理失败: {}",
        'workflow_wait_dl': "⚠️ 等待后台下载任务结束...",
        'workflow_dl_wait_fail': "❌ 视频下载失败",
        'workflow_dl_error': "❌ 视频下载期间发生错误: {}",
        'workflow_bg_download': "🚀 开始后台异步下载...",
        'workflow_preflight': "🔍 正在进行预检 (Pre-flight Checks)...",
        'workflow_preflight_ok': "✅ 所有预检通过！\n",
        'workflow_preflight_fail': "❌ 预检失败，程序退出。",
        'workflow_bili_check': "👉 检查 Bilibili 登录状态 ({})...",
        'workflow_bili_ok': "   ✅ Bilibili: 已登录 ({})",
        'workflow_bili_fail': "   ❌ Bilibili: Cookie 无效 ({})",
        'workflow_bili_no_cookie': "   ❌ Bilibili: 未找到 Cookie",
        'workflow_gemini_check': "👉 检查 Gemini 登录状态 ({})...",
        'workflow_gemini_ok': "   ✅ Gemini: Cookie 有效",
        'workflow_gemini_fail': "   ❌ Gemini: Cookie 无效 ({})",
        'workflow_gemini_err': "   ❌ Gemini: 检查失败 ({})",

        # Downloader
        'dl_start': "\n🚀 开始下载: {}",
        'dl_dir': "📂 保存目录: {}",
        'dl_complete': "✅ 下载完成: {}",
        'dl_path': "📂 视频文件就绪: {}",
        'dl_fail': "❌ 下载失败: {}",
        'dl_clean_temp': "🧹 已清理临时下载文件 (.part, .ytdl)",
        'dl_bg_start': "⏳ 后台下载开始: {} ...",
        'dl_bg_start': "⏳ 后台下载开始: {} ...",
        'dl_confirmed': "✅ 视频下载确认完成: {}",
        'dl_meta_done': "✅ 元数据获取完成: {}",
        'dl_skip_exists': "⏩ 视频文件已存在，跳过下载: {}",
        'dl_strat_best_mp4': "正在尝试下载最佳质量 MP4...",
        'dl_strat_best_any': "⚠️ 正在回退到最佳可用质量 (任何格式)...",
        'dl_strat_compat': "⚠️ 正在回退到兼容模式 (单文件)...",
        
        # Fetcher
        'fetch_title': "  获取最新视频信息",
        'fetch_found': "📂 发现待处理视频: {}",
        'fetch_all_done': "✅ 所有视频均已处理完毕 (History check).",
        'fetch_info_loaded': "✅ 读取到本地元数据 (info.json)",
        'fetch_info_fail': "⚠️ 读取 info.json 失败: {}",
        'fetch_fail': "❌ 获取视频信息失败: {}",
        'fetch_used_arg': "📂 使用指定的视频文件: {}",
        'fetch_original_title': "\n📺 原标题: {}",
        'fetch_original_desc': "📝 原描述: {}...",
        'fetch_translating': "🔤 正在使用 Gemini 翻译...",
        'fetch_trans_done': "✅ 翻译完成",
        'fetch_trans_fail': "⚠️ 翻译失败: {}",
        'fetch_cn_title': "\n📺 中文标题: {}",
        'fetch_cn_desc': "📝 中文描述: {}...",
        'fetch_metadata_saved': "✅ 元数据已保存到: {}",
        'fetch_ready': "  ✅ 元数据准备完成！",
        'fetch_resume_found': "💡 发现此视频已存在处理好的元数据",
        'fetch_select_cat': "选择全站分区：",
        'fetch_cat_life': "  [1] 生活-日常 (默认)",
        'fetch_cat_sports': "  [2] 生活-运动",
        'fetch_cat_knowledge': "  [3] 知识-科普",
        'fetch_cat_fitness': "  [4] 时尚-健身塑形 (推荐)",
        'fetch_resume_prompt': "❓ 发现已存在元数据，是否直接使用？",
        'fetch_resume_hint': "   (输入 y 使用现有数据，输入 n 重新获取)",
        'fetch_resume_skip': "✅ 已复用元数据，跳过翻译步骤。",
        
        # Translator
        'trans_testing': "🔤 测试翻译...",
        'trans_original': "原文: {}",
        'trans_result': "翻译: {}",
        'trans_original_title': "\n📺 原文: {}",
        'trans_cn_title': "📺 译文:  {}",
        'trans_original_desc': "\n📝 原文: {}",
        'trans_cn_desc': "📝 译文:  {}",
        'trans_auto': "\n🤖 正在自动翻译...",
        'trans_module_missing': "⚠️ gemini_translate 模块未找到，返回原文",
        'ai_analyzing': "\n🤖 正在进行 AI 内容分析 (分区 & 标签)...",
        'ai_cat': "✅ AI 推荐分区: {}",
        'ai_tags': "✅ AI 生成标签: {}...",
        'ai_fail': "⚠️ AI 分析失败: {}",
        'ai_activity_check': "🔍 正在搜索相关活动话题...",
        
        # Cover
        'cover_processing': "\n🚀 正在处理: {} -> {}",
        'cover_prompt': "✍️ 提示词: {}...",
        'cover_model': "🤖 使用模型: {} ({})",
        'cover_no_img': "❌ Gemini 未生成任何图片",
        'cover_saved': "💾 已保存至: {}",
        'cover_watermark_try': "🌊 正在尝试去除水印...",
        'cover_watermark_ok': "✅ 水印去除成功",
        'cover_watermark_skip': "ℹ️ 跳过去水印: {}",
        'cover_watermark_fail': "⚠️ 水印去除失败: {}",
        'cover_using_source': "📸 使用源封面: {}",
        'cover_init_client': "\n🔐 正在初始化 Gemini 客户端 (使用浏览器 Cookies)...",
        'cover_cookie_fail': "❌ 在 Chrome 中未找到 __Secure-1PSID cookie",
        'cover_cookie_found': "✅ 找到 Cookies: __Secure-1PSID (长度={})",
        'cover_client_ok': "✅ Gemini 客户端初始化成功",
        'cover_gen_start': "🤖 正在开启 AI 处理...",
        'cover_done': "\n✅ 封面处理完成:",
        'cover_16_9': "   16:9: {}",
        'cover_4_3': "   4:3: {}",
        'cover_no_source': "❌ 未找到源封面图片",
        'cover_use_existing_prompt': "👉 是否使用现有封面？(Y/n): ",
        'cover_using_existing': "👉 检测到并使用现有封面...",
        'cover_regenerating': "🔄 正在重新生成封面...",
        
        # Uploader
        'up_title': "\n========== Bilibili 视频上传 ==========",
        'up_file': "\n📺 视频: {}",
        'up_meta_title': "📝 标题: {}",
        'up_meta_desc': "📝 描述: {}...",
        'up_tags': "🏷️ 标签: {}",
        'up_cat': "📂 分区: {} (tid={})",
        'up_cat_wrong': "⚠️ 分区 '{}' 无效，回退到 '生活-日常' (tid=21)",
        'up_cat_invalid': "⚠️ 分区 '{}' 无效，回退到 '{}' (tid={})",
        'up_cover_16_path': "🖼️ 封面16:9: {}",
        'up_cover_43_path': "🖼️ 封面4:3: {}",
        'up_schedule': "⏰ 定时发布: {}",
        'up_schedule_now': "⏰ 发布时间: 立即发布",
        'up_dry_run': "⚡️ 模拟运行模式: 跳过确认与上传步骤。",
        'up_confirm': "确认上传？(y/n): ",
        'up_cancel': "已取消上传",
        'up_credentials_edge': "🔄 正在从 {} 浏览器获取 Bilibili 凭证...",
        'up_credentials_ok': "✅ 成功从 {} 获取 Bilibili 凭证",
        'up_pre_check_ok': "✅ 上传资产预检通过 (All assets found).",
        'up_pre_check_fail': "⚠️ 上传资产预检失败 (Pre-upload Check Failed):",
        'up_missing_item': "   - 缺失/无效: {}",
        'up_auto_confirm_skip': "❌ [Auto] 检测到资产缺失且开启了自动确认模式，程序退出。",
        'up_sub_trans_start': "🤖 正在翻译字幕 (AI): {} -> .zh-Hans.vtt",
        'up_sub_trans_done': "✅ 字幕翻译完成: {}",
        'up_sub_trans_fail': "⚠️ 字幕翻译失败",
        'up_sub_trans_err': "⚠️ 字幕翻译出错: {}",
        'up_desc_label': "📝 描述: {}",
        'up_schedule_warn': "⚠️ 定时发布时间必须至少在未来 4 小时之后",
        'up_schedule_earliest': "   最早有效时间: {}",
        'up_start': "\n🚀 开始上传...\n",
        'up_using_cookies': "使用 cookies 上传",
        'up_cover_16': "🖼️ 正在上传封面 (16:9): {}",
        'up_cover_43': "🖼️ 正在上传封面 (4:3): {}",
        'up_cover_url': "   URL: {}",
        'up_video': "\n📤 上传视频: {}",
        'up_submit': "\n📝 提交稿件...",
        'up_success': "\n========== ✅ 上传成功！ ==========",
        'up_bvid': "\n🔗 稿件ID: {}",
        'up_saved': "✅ 已保存上传信息到: {}",
        'up_fail': "\n❌ 上传失败: {}",
        
        # Subtitle
        'sub_title': "步骤 1.5: 生成 AI 字幕",
        'sub_audio_exists': "🎵 音频文件已存在，跳过提取: {}",
        'sub_extract_audio': "🎵 提取音频: {}",
        'sub_extract_done': "✅ 音频提取完成: {}",
        'sub_extract_fail': "❌ 音频提取失败: {}",
        'sub_transcribe': "🎤 使用 Gemini 转写音频...",
        'sub_transcribe_done': "✅ 转写完成: {} 个片段",
        'sub_transcribe_fail': "❌ 转写失败: {}",
        'sub_json_fail': "⚠️ JSON 解析失败: {}",
        'sub_json_raw': "   原始响应: {}...",
        'sub_translate': "🌐 翻译 {} 个字幕片段...",
        'sub_translate_batch': "   正在翻译批次: {}",
        'sub_translate_done': "✅ 翻译完成",
        'sub_translate_fail': "❌ 翻译失败: {}",
        'sub_parsed': "📂 解析现有字幕: {} ({} 条)",
        'sub_parse_fail': "⚠️ 解析字幕失败: {} ({})",
        'sub_reuse_en': "♻️ 复用现有源语言字幕，跳过听写",
        'sub_reuse_source': "♻️ 复用现有源语言字幕",
        'sub_reuse_target': "♻️ 复用现有目标语言字幕",
        'sub_detected_target': "👀 检测到现有字幕已是目标语言: {} (跳过翻译)",
        'sub_only_target': "⚠️ 仅有目标语言字幕，跳过生成双语 ASS",
        'sub_srt_saved': "💾 SRT 字幕已保存: {}",
        'sub_ass_saved': "💾 双语 ASS 字幕已保存: {}",
        'sub_burn': "🔥 正在烧录字幕到视频...",
        'sub_burn_done': "✅ 烧录完成，准备上传: {}",
        'sub_burn_fail': "❌ 烧录失败: {}",
        'sub_burn_done': "✅ 字幕烧录完成: {}",
        'sub_burn_fail': "❌ 字幕烧录失败: {}",
        'sub_skip_no_trans': "⚠️ 转写失败，跳过字幕生成",
        'sub_video_not_found': "⚠️ 视频文件未找到，跳过字幕生成",
        'sub_done': "\n✅ 字幕处理完成:",
        'sub_en': "   英文: {}",
        'sub_zh': "   中文: {}",
        'sub_bilingual': "   双语: {}",
        'sub_fail': "⚠️ 字幕生成失败: {}",
        
        # Commenter
        'comment_target': "🎯 目标视频: {} (aid={})",
        'comment_get_aid_fail': "⚠️ 获取AID失败: {}",
        'comment_no_bvid': "❌ Metadata中未找到BVID，请先执行上传步骤。",
        'comment_no_cookies': "❌ 未找到 Cookies",
        'comment_no_csrf': "❌ Cookie中缺少 bili_jct (CSRF Token)",
        'comment_hook_fail': "❌ 主评论发送失败: {}",
        'comment_link_fail': "❌ 链接回复失败: {}",
        'comment_pin_fail': "⚠️ 置顶失败: {}",
        'comment_hook': "1️⃣ 发送主评论 (Hook)...",
        'comment_hook_ok': "✅ 主评论发送成功: rpid={}",
        'comment_link': "2️⃣ 发送链接回复 (Link)...",
        'comment_link_ok': "✅ 链接回复发送成功",
        'comment_pin': "3️⃣ 置顶主评论...",
        'comment_pin_ok': "✅ 置顶成功！",
        'comment_done': "\n🎉 流程结束! 查看效果: {}",
        'comment_dry_post': "⚡️ DRY RUN: Would post comment: {}...",
        'comment_dry_reply': "⚡️ DRY RUN: Would reply to {}: {}...",
        'comment_dry_pin': "⚡️ DRY RUN: Would pin rpid={}",
        'comm_dry_run': "⚡️ [Dry Run] 跳过自动评论流程",
        'comm_fail_meta': "❌ 自动评论失败: 未找到元数据文件",
        'comm_skip_cat': "ℹ️ 当前分区为 '{}'，非健身类，跳过自动评论。",
        'comm_start': "💬 正在为视频 (aid={}) 发表主评论...",
        'comment_ok_main': "✅ 主评论成功! (rpid={})",
        'comm_top_start': "📌 正在置顶该评论 (等待同步)...",
        'comm_ok_top': "✅ 评论置顶成功！",
        'comm_fail_top': "⚠️ 评论置顶失败: {}",
        'comm_sub_start': "🔗 正在发表子评论 (等待同步)...",
        'comm_ok_sub': "✅ 子评论 (链接) 发表成功！",
        'comm_fail_sub': "⚠️ 子评论发表失败: {}",
        'comm_err': "❌ 自动评论流程异常: {}",
        'comm_fallback_norule': "ℹ️ 未匹配到规则 (分类: '{}')，发通用评论兜底",
        'comm_fallback_legacy': "ℹ️ 旧系统未匹配 (分类: '{}')，发通用评论兜底",
        'comm_exists': "ℹ️ UP主已有评论 ({}), 跳过避免重复",
        'comm_resume_main': "♻️ 主评论已结算 (rpid={}), 跳过发帖继续后续步骤",
        'comm_resume_no_rpid': "♻️ 主评论已存在但无 rpid 记录，无法置顶/子回复，跳过",
        'comm_pending_main': "⏸ 检测到未结算的主评论 intent (上次可能崩溃在发帖中)，若 B站 已存在则本次不会重复发帖",
        'comm_skip_sub_resumed': "♻️ 该子评论已结算/挂起，跳过避免重复",
        'comm_effect_fail': "⚠️ 评论 effect 记录失败（不影响主流程）: {}",
        'prompt_input': "👉 请输入 [Y/n]: ",
    }

}

import threading
from contextlib import contextmanager

class I18n:
    def __init__(self):
        self.lang = 'zh'
        self.load_language()
        self.lock = threading.Lock()
        self.allow_bg_logs = True
        self.bg_buffer = []  # Buffer for background logs when suppressed
        
    def load_language(self):
        config = load_config()
        self.lang = config.get('language', 'zh')
        if self.lang not in TRANS:
            self.lang = 'zh'
            
    def get(self, key, *args):
        """Get translated string"""
        t_map = TRANS.get(self.lang, TRANS['zh'])
        text = t_map.get(key, key)
        
        if args:
            try:
                return text.format(*args)
            except:
                return text
        return text

    def print_t(self, key, *args):
        """Print translated string with thread safety and background suppression"""
        msg = self.get(key, *args)
        
        is_main_thread = (threading.current_thread() is threading.main_thread())
        
        with self.lock:
            if is_main_thread:
                # Main thread always prints
                print(msg)
            else:
                # Background thread checks permission
                if self.allow_bg_logs:
                    print(msg)
                if self.bg_buffer:
                    from core.utils.logger import log
                    print("\n--- Background Tasks Update ---")
                    for msg in self.bg_buffer:
                        print(msg)
                        log.info(f"[BG] {msg}")
                    print("-------------------------------")
                    self.bg_buffer.clear()

    @contextmanager
    def suspend_background_logging(self):
        """Context manager to temporarily silence background threads (e.g. during input)"""
        with self.lock:
            self.allow_bg_logs = False
        
        try:
            yield
        finally:
            with self.lock:
                self.allow_bg_logs = True
                # Flush buffer
                if self.bg_buffer:
                    from core.utils.logger import log
                    print("\n--- Background Tasks Update ---")
                    for msg in self.bg_buffer:
                        print(msg)
                        log.info(f"[BG] {msg}")
                    print("-------------------------------")
                    self.bg_buffer.clear()

# Global instance
i18n = I18n()

def print_t(key, *args):
    # Also log to file for traceability
    try:
        from core.utils.logger import log
        msg = i18n.get(key, *args)
        log.info(msg)
    except:
        pass
    i18n.print_t(key, *args)
