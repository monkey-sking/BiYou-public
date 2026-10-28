# LLM Factory Skill

This document describes the LLM service abstraction layer in BiYou.

## Overview

BiYou supports multiple LLM providers through a Factory pattern, allowing easy switching between:
- `gemini_official` (API key based)
- `gemini_web` (Browser cookie based)

---

## Usage

**File:** `automation/src/services/llm/factory.py`

```python
from src.services.llm.factory import LLMFactory

# Get service for a specific task
llm = LLMFactory.get_service('translation')  # For text translation
llm = LLMFactory.get_service('cover')        # For image generation
llm = LLMFactory.get_service('analysis')     # For content analysis
```

---

## Configuration

**File:** `config/config.yaml`

```yaml
services:
  translation:
    provider: gemini_official
    model: gemini-1.5-pro-latest
  cover:
    provider: gemini_web
    model: Thinking
  analysis:
    provider: gemini_official
    model: gemini-1.5-flash-latest
```

---

## Key Patterns

### Task-Specific Configuration

Different tasks may need different models:
- **Translation**: Needs strong reasoning → `gemini-1.5-pro`
- **Cover**: Needs image editing → `gemini_web` (for Imagen access)
- **Analysis**: Needs speed → `gemini-1.5-flash`

### Adding New Providers

1. Create a new class extending `LLMBase` in `services/llm/`.
2. Implement required methods: `generate_text()`, `generate_image()`.
3. Update `LLMFactory` to recognize the new provider name.
