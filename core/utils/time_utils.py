from datetime import datetime, timedelta

def parse_schedule_time(schedule_str: str) -> int:
    """
    解析定时发布时间
    
    支持格式:
    - Unix 时间戳: "1704700800"
    - 日期时间: "2024-01-08 18:00"
    - 相对时间: "+4h" (延迟4小时), "+1d" (延迟1天)
    
    Returns:
        10位 Unix 时间戳，如果解析失败返回 0
    """
    if not schedule_str:
        return 0
    
    schedule_str = schedule_str.strip()
    
    # 纯数字 = Unix 时间戳
    if schedule_str.isdigit():
        return int(schedule_str)
    
    # 相对时间 (+4h, +1d)
    if schedule_str.startswith('+'):
        now = datetime.now()
        value = schedule_str[1:-1]
        unit = schedule_str[-1].lower()
        
        try:
            num = int(value)
            if unit == 'h':
                target = now + timedelta(hours=num)
            elif unit == 'd':
                target = now + timedelta(days=num)
            else:
                print(f"⚠️ 无法识别的时间单位: {unit} (支持 h/d)")
                return 0
            return int(target.timestamp())
        except ValueError:
            print(f"⚠️ 无法解析相对时间: {schedule_str}")
            return 0
    
    # 日期时间格式
    try:
        # 尝试多种格式
        for fmt in ['%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M', '%m-%d %H:%M']:
            try:
                dt = datetime.strptime(schedule_str, fmt)
                # 如果没有年份，使用当前年份
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                return int(dt.timestamp())
            except ValueError:
                continue
        
        print(f"⚠️ 无法解析日期时间: {schedule_str}")
        print("   支持的格式: YYYY-MM-DD HH:MM, +4h, +1d, 或 Unix 时间戳")
        return 0
        
    except Exception as e:
        print(f"⚠️ 解析时间失败: {e}")
        return 0
