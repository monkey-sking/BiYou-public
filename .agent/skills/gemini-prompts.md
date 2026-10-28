# Gemini Prompts Skill

This document describes the prompt engineering patterns used when interacting with the Gemini API in BiYou.

## Cover Generation Prompts

**Used in:** `automation/src/modules/cover.py`

### Default Prompt Template
```
Edit this fitness video thumbnail image.
Replace all English text to Chinese. Keep any other languages unchanged.
Keep the same person, colors, layout and background.
Output in {aspect_ratio} {widescreen|standard} aspect ratio format.
Generate only one image.
```

### Key Principles
1. **Preserve Identity**: Always instruct to "keep the same person, colors, layout."
2. **Single Output**: End with "Generate only one image" to avoid multiple variations.
3. **Aspect Ratio**: Specify explicitly (`16:9` or `4:3`).

---

## Translation Prompts

**Used in:** `automation/src/modules/translator.py`

### Key Principles
1. **Tone Matching**: Request translations that match Bilibili's casual, engaging tone.
2. **Length Control**: For titles, request concise translations (under 80 characters).
3. **Context Awareness**: Provide video category context to improve accuracy (e.g., "This is a fitness video").

---

## Tag Generation Prompts

**Used in:** `automation/src/modules/fetcher.py`

### Key Principles
1. **Platform Specificity**: Ask for "Bilibili popular tags" not just "Chinese tags."
2. **Limit Count**: Request a fixed number of tags (e.g., "Generate exactly 5 tags").
3. **Avoid Generic**: Instruct to avoid overly broad tags like "视频" or "中国".
