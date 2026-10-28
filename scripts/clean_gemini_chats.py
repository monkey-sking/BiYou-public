"""
批量删除 Gemini 对话

用法:
  python3 scripts/clean_gemini_chats.py           # 删除所有已记录的 BiYou 对话
  python3 scripts/clean_gemini_chats.py 2026-02-28 # 只删除指定日期的
  python3 scripts/clean_gemini_chats.py --all      # 清空账号下所有对话
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "gemini_chats.jsonl")


async def clean_all():
    """清空账号下所有对话"""
    from core.utils.cookie import get_gemini_client
    print("🔐 正在连接 Gemini...")
    client = await get_gemini_client()
    if not client:
        print("❌ 无法连接 Gemini 客户端")
        return
    
    if not hasattr(client, 'list_chats'):
        print("❌ 当前 gemini_webapi 版本不支持 list_chats")
        return
    
    print("📋 获取对话列表...")
    chats = await client.list_chats()
    
    if not chats:
        print("✅ 没有对话")
        return
    
    print(f"🗑️  找到 {len(chats)} 个对话，开始删除...")
    deleted = 0
    for i, chat in enumerate(chats, 1):
        try:
            cid = chat.cid if hasattr(chat, 'cid') else chat
            await client.delete_chat(cid)
            deleted += 1
            if i % 10 == 0 or i == len(chats):
                print(f"  进度: {i}/{len(chats)}")
        except Exception as e:
            print(f"  ⚠️ {e}")
    
    # 同时清空本地记录
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    
    print(f"\n🎉 完成！删除了 {deleted}/{len(chats)} 个对话")


async def clean_by_log(target_date=None):
    """根据 BiYou 记录的 cid 删除"""
    if not os.path.exists(LOG_FILE):
        print("✅ 没有 BiYou 对话记录 (logs/gemini_chats.jsonl 不存在)")
        return
    
    with open(LOG_FILE, "r") as f:
        entries = [json.loads(line.strip()) for line in f if line.strip()]
    
    if not entries:
        print("✅ 记录文件为空")
        return
    
    if target_date:
        to_delete = [e for e in entries if e.get("date") == target_date]
        to_keep = [e for e in entries if e.get("date") != target_date]
        print(f"📅 筛选日期: {target_date}")
    else:
        to_delete = entries
        to_keep = []
    
    cids = list(dict.fromkeys(e["cid"] for e in to_delete))
    
    if not cids:
        print(f"✅ 没有需要删除的对话")
        return
    
    print(f"🗑️  准备删除 {len(cids)} 个 BiYou 对话...")
    
    from core.utils.cookie import get_gemini_client
    print("🔐 正在连接 Gemini...")
    client = await get_gemini_client()
    if not client:
        print("❌ 无法连接 Gemini 客户端")
        return
    
    deleted = 0
    for i, cid in enumerate(cids, 1):
        try:
            await client.delete_chat(cid)
            deleted += 1
            print(f"  ✅ [{i}/{len(cids)}] {cid}")
        except Exception as e:
            print(f"  ⚠️ [{i}/{len(cids)}] {cid} - {e}")
    
    with open(LOG_FILE, "w") as f:
        for e in to_keep:
            f.write(json.dumps(e) + "\n")
    
    print(f"\n🎉 完成！删除 {deleted} 个，保留 {len(to_keep)} 条记录")


async def main():
    if "--all" in sys.argv:
        await clean_all()
    else:
        target_date = None
        for arg in sys.argv[1:]:
            if not arg.startswith("-"):
                target_date = arg
                break
        await clean_by_log(target_date)


if __name__ == "__main__":
    asyncio.run(main())
