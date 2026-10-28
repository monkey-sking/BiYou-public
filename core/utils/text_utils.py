import re

def clean_bilibili_description(text: str) -> str:
    """
    Clean Markdown from text and convert Markdown links to plain URLs for Bilibili.
    """
    if not text:
        return text
        
    # 1. Convert Markdown links: [Title](URL) -> URL (Title)
    # We use a non-greedy match for the title to handle multiple links correctly.
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\2 (\1)', text)
    
    # 2. Remove Bold and Italics: **text**, __text__, *text*, _text_
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    
    # 3. Remove Markdown Headers: # Header
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    
    # 4. Remove horizontal rules
    text = re.sub(r'^\s*[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    
    # 5. Remove trailing spaces on each line
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    
    # 6. Consolidate multiple empty lines (3 or more -> 1 empty line)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()
