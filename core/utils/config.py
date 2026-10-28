import os
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 向上两级找到项目根目录 (core/utils -> core -> root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR)) 


CONFIG_FILE = os.path.join(PROJECT_ROOT, "config", "config.yaml")

def load_config():
    """Load configuration from config.yaml"""
    if not os.path.exists(CONFIG_FILE):
        print(f"⚠️ Warning: Config file not found at {CONFIG_FILE}. Using defaults.")
        return get_defaults()
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        try:
            config = yaml.safe_load(f)
            config = resolve_paths(config)
            
            # Load prompts
            prompts_file = config.get('prompts_file', os.path.join(PROJECT_ROOT, "config", "prompts.yaml"))
            if os.path.exists(prompts_file):
                with open(prompts_file, 'r', encoding='utf-8') as pf:
                    config['prompts'] = yaml.safe_load(pf) or {}
            else:
                 config['prompts'] = {}

            return config
        except yaml.YAMLError as e:
            print(f"❌ Error parsing config file: {e}")
            return get_defaults()

def get_defaults():
    """Return default configuration"""
    return {
        "browser": "ego",
        "browser_profile": "~/Library/Application Support/Citro Labs/ego lite",
        "browser_debug_port": 39222,
        "video_source_dir": "./videos",
        "cover_dir": "./videos",
        "cookie_file": "cookies.json",
        "metadata_file": "video_metadata.json",
        "comment_hook": "",
        "comment_link": "",
        "plugin_comment_hook": "",
        "plugin_comment_link": "",
        "plugin_comment_keywords": [],
        "target_language": "zh",
        "proxy": None
    }

def resolve_paths(config):
    """Resolve relative paths to absolute paths based on PROJECT_ROOT"""
    for key in ['video_source_dir', 'cover_dir', 'cookie_file', 'metadata_file', 'prompts_file']:
        if key in config and config[key]:
            path = config[key]
            if not os.path.isabs(path):
                config[key] = os.path.abspath(os.path.join(PROJECT_ROOT, path))
    return config

def update_config(key, value):
    """Update a specific configuration key and save to file"""
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            try:
                config = yaml.safe_load(f) or {}
            except:
                pass
    
    config[key] = value
    
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        return True
    except Exception as e:
        print(f"❌ Failed to update config: {e}")
        return False

def get_gemini_key():
    """Helper to get Gemini API Key from config"""
    # Try multiple common keys
    key = CONFIG.get('gemini_api_key')
    if not key:
        key = CONFIG.get('gemini', {}).get('api_key')
    return key

# 全局配置实例，供其他模块直接导入使用
CONFIG = load_config()
