import os
import json
import re
import asyncio
from core.services.llm.factory import LLMFactory
from core.utils.config import load_config
from core.utils.logger import log

CONFIG = load_config()

class SubtitleTranslator:
    """
    Service to translate subtitle files (VTT/SRT) using LLM.
    """
    
    @staticmethod
    def extract_json_array(text: str):
        import json
        text = text.replace('```json', '').replace('```', '').strip()
        if '[END]' in text:
            text = text.split('[END]')[0].strip()
        
        # Try direct load
        try:
            return json.loads(text)
        except:
            pass
            
        # Try finding outermost brackets
        start = text.find('[')
        end = text.rfind(']')
        if start != -1 and end > start:
            try:
                # Try from start to last ]
                return json.loads(text[start:end+1])
            except:
                pass
                
        return None

    @staticmethod
    async def translate_subtitle(input_path: str, output_path: str, chunk_size: int = 500, source_lang: str = "en", target_lang: str = "zh") -> bool:
        """
        Translates text in a subtitle file from English to Chinese.
        """
        if not os.path.exists(input_path):
            log.error(f"Subtitle file not found: {input_path}")
            return False

        try:
            # 1. Parse VTT/SRT
            with open(input_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Extract text blocks
            # We strictly identify text lines vs metadata/timecodes
            parsed_blocks = [] # List of {'indices': [i, ...], 'text': "..."}
            
            time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2})')
            
            is_vtt = 'WEBVTT' in lines[0]
            
            current_block_text = []
            current_indices = []
            
            # Simple state machine
            # State: 0=Transition/Timecode, 1=Text
            
            for i, line in enumerate(lines):
                line_strip = line.strip()
                
                # Skip header/empty
                if not line_strip:
                    continue
                if is_vtt and 'WEBVTT' in line:
                    continue
                if '-->' in line:
                    # Timestamp line, next is text
                    continue
                if line_strip.isdigit():
                    # SRT index
                    continue
                
                # Assume it's text
                current_block_text.append(line_strip)
                current_indices.append(i)

            # NOTE: The above simple parsing is risky for multi-line subs.
            # Better approach: Just collect ALL text lines that are NOT timestamps/indices.
            # But we need to put them back exactly.
            
            # Robust Parsing Strategy:
            # 1. Parse into blocks with start/end times
            # 2. Filter out short segments (rolling caption artifacts)
            # 3. Clean tags (<c>, <00:xx>, etc)
            # 4. Deduplicate consecutive identical text
            
            time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}:\d{2})')
            tag_pattern = re.compile(r'<[^>]+>')
            
            parsed_entries = [] # {start, end, text_lines, raw_lines}
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if '-->' in line:
                    # Found a timestamp line
                    time_line = line
                    # Parse duration
                    parts = line.split('-->')
                    if len(parts) == 2:
                         # Simple duration check
                         try:
                             def parse_ts(t_str):
                                 t_str = t_str.strip().split(' ')[0] # Remove align modifiers
                                 # Standardize format
                                 t_str = t_str.replace(',', '.')
                                 if '.' in t_str:
                                     base, ms = t_str.split('.')
                                     h, m, s = map(int, base.split(':'))
                                     return h*3600 + m*60 + s + int(ms)/1000.0
                                 else:
                                     h, m, s = map(int, t_str.split(':'))
                                     return h*3600 + m*60 + s
                                     
                             start_sec = parse_ts(parts[0])
                             end_sec = parse_ts(parts[1])
                             duration = end_sec - start_sec
                             
                             # Filter: Skip < 0.1s segments (rolling caption artifacts)
                             if duration <= 0.1:
                                 i += 1
                                 # Skip associated text
                                 while i < len(lines) and lines[i].strip() and '-->' not in lines[i]:
                                     i += 1
                                 continue
                                 
                         except:
                             pass # Fallback if parsing fails

                    # Collect text lines until next block
                    text_content = []
                    raw_block_lines = [time_line]
                    
                    i += 1
                    while i < len(lines):
                        current_line = lines[i].strip()
                        if not current_line: # Empty line separator
                            i += 1
                            continue 
                        if '-->' in current_line: # Next block started (unexpected but possible) or valid next block
                            break
                        if current_line.isdigit() and (i+1 < len(lines) and '-->' in lines[i+1]): # Next block index
                            break
                            
                        raw_block_lines.append(lines[i].strip())
                        
                        # CLEANING: Remove tags like <c.color> or <00:01:02>
                        clean_text = tag_pattern.sub('', current_line).strip()
                        if clean_text:
                            text_content.append(clean_text)
                        
                        i += 1
                    
                    # Deduplication:
                    # If this block's text is identical to the previous block's text, skip it?
                    # Rolling captions often repeat the FULL sentence in the final block, 
                    # and partials in previous blocks.
                    # Or they have: "Hello" -> "Hello world" -> "Hello world!"
                    # We want to translate the *final* stable line or unique lines.
                    # Simple heuristic: Just keep it for now, but the clean tags help translation.
                    
                    if text_content:
                        full_text = " ".join(text_content)
                        # Check against last entry to avoid pure duplicates
                        if parsed_entries and parsed_entries[-1]['text'] == full_text:
                            pass # Skip exact duplicate
                        else:
                            parsed_entries.append({
                                'line_index': i, # Approximate, purely for references if needed
                                'text': full_text,
                                'original_block': raw_block_lines # Start indices? Complex to reconstruct exactly line-by-line if we drop. 
                            })

                else:
                    i += 1

            # Mapping for translation:
            # We will ignore original indices for reconstruction and just append new blocks?
            # NO, we must reconstruct the file.
            # actually, if we filter, we simply don't write back the filtered lines.
            # But the original code relied on line-index mapping `translated_map`.
            # Since we change the structural logic (skipping blocks), we can't map 1:1 to original lines easily.
            # We should rewrite the file completely based on parsed_entries.
            
            # Reformulate text_lines_map for the translator loop
            text_lines_map = []
            for idx, entry in enumerate(parsed_entries):
                text_lines_map.append((idx, entry['text']))

            # 2. Chunk and Translate
            total_lines = len(text_lines_map)
            translated_map = {} # index -> translated_text
            
            prompts = CONFIG.get('prompts', {})
            prompt_template = prompts.get('subtitle_translation')
            if not prompt_template:
                log.error("Missing 'subtitle_translation' prompt.")
                return False
                
            llm = LLMFactory.get_service('translation') # Reuse translation provider
            
            # Sequential Processing (Large Batch)
            for i in range(0, total_lines, chunk_size):
                chunk = text_lines_map[i : i+chunk_size]
                chunk_indices = [x[0] for x in chunk]
                chunk_texts = [x[1] for x in chunk]
                
                log.info(f"Processing batch {i//chunk_size + 1}/{(total_lines-1)//chunk_size + 1} ({len(chunk_texts)} lines)...")
                
                prompt = prompt_template.format(
                    text='\n'.join(chunk_texts),
                    source_lang=source_lang,
                    target_lang=target_lang,
                    count=len(chunk_texts)
                )
                
                try:
                    # Serial LLM Call to avoid rate limits
                    resp = await llm.generate_text(prompt)
                    
                    # Parse JSON output
                    translations = SubtitleTranslator.extract_json_array(resp)
                    if translations is None:
                        log.error(f"Failed to parse JSON for chunk starting at {chunk_indices[0]}")
                        translations = []
                    
                    if len(translations) != len(chunk_texts):
                        log.warning(f"Translation count mismatch. Input: {len(chunk_texts)}, Output: {len(translations)}")
                        # Fallback: fill mismatch
                        for k in range(len(chunk_texts)):
                            if k < len(translations):
                                translated_map[chunk_indices[k]] = translations[k]
                            else:
                                translated_map[chunk_indices[k]] = chunk_texts[k]
                    else:
                        for k, t in enumerate(translations):
                            translated_map[chunk_indices[k]] = t
                            
                except Exception as e:
                    log.error(f"Translation chunk failed: {e}")
                    # Fallback to original on error
                    for k in range(len(chunk_texts)):
                         translated_map[chunk_indices[k]] = chunk_texts[k]
            
            # 3. Reconstruct
            # Since we filtered blocks, we recreate the file content
            new_lines = ["WEBVTT\n\n"]
            
            for idx, trans_text in translated_map.items():
                # idx is now the index in parsed_entries
                original_entry = parsed_entries[idx]
                
                # Re-use original timestamp text (first item in raw original block was timestamp line)
                # It might have align tags, allow keeping them or strict clean? 
                # Keeping original timestamp line is safest.
                # But we stripped it in the parsing loop logic above (raw_lines not fully captured safely in loop structure).
                # Actually we did: raw_block_lines = [time_line, ...] (in previous edit).
                # Let's assume we can access raw timestamp from the parsed structure if we saved it.
                # In my previous edit I saved 'original_block' list.
                
                # Wait, I need to make sure 'original_block' was actually saved in the chunk above.
                # Yes, in the replacement content I added: 'original_block': raw_block_lines
                
                timestamp_line = original_entry['original_block'][0]
                new_lines.append(f"{timestamp_line}\n")
                new_lines.append(f"{trans_text}\n\n")
                
            # 4. Save
            with open(output_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                
            return True

        except Exception as e:
            log.error(f"Subtitle translation failed: {e}")
            return False

if __name__ == "__main__":
    # Simple CLI test
    import sys
    if len(sys.argv) > 2:
        asyncio.run(SubtitleTranslator.translate_subtitle(sys.argv[1], sys.argv[2]))
