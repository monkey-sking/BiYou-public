import pandas as pd
import sys
import os

def analyze(file_path):
    print(f"📊 Analyzing file: {os.path.basename(file_path)}\n")
    
    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return

    # 1. Overall Statistics
    total_videos = len(df)
    total_views = df['Views (播放)'].sum()
    total_likes = df['Likes (点赞)'].sum()
    total_coins = df['Coins (硬币)'].sum()
    total_favs = df['Favorites (收藏)'].sum()
    
    print("## 📈 整体数据概览")
    print(f"- **视频总数**: {total_videos}")
    print(f"- **总播放量**: {total_views:,}")
    print(f"- **总点赞数**: {total_likes:,}")
    print(f"- **总硬币数**: {total_coins:,}")
    print(f"- **总收藏数**: {total_favs:,}")
    
    if total_views > 0:
        avg_play = total_views / total_videos
        print(f"- **平均播放**: {avg_play:,.0f}")
    
    print("\n" + "="*30 + "\n")

    # 2. Engagement Rates (Interactive Quality)
    # Avoid division by zero
    df['Like Rate'] = df.apply(lambda x: (x['Likes (点赞)'] / x['Views (播放)']) * 100 if x['Views (播放)'] > 100 else 0, axis=1)
    df['Coin Rate'] = df.apply(lambda x: (x['Coins (硬币)'] / x['Views (播放)']) * 100 if x['Views (播放)'] > 100 else 0, axis=1)
    df['Fav Rate'] = df.apply(lambda x: (x['Favorites (收藏)'] / x['Views (播放)']) * 100 if x['Views (播放)'] > 100 else 0, axis=1)
    
    # "Fans Stickiness" (Coin/Like ratio) - High value means loyal fans
    df['Hardcore Ratio'] = df.apply(lambda x: (x['Coins (硬币)'] / x['Likes (点赞)']) * 100 if x['Likes (点赞)'] > 10 else 0, axis=1)

    # 3. Top Performers
    print("## 🏆 表现最佳 TOP 3")
    
    def print_top(metric, col_name):
        top = df.sort_values(by=col_name, ascending=False).head(3)
        print(f"\n### {metric} Top 3:")
        for idx, row in top.iterrows():
            title = row['Title']
            val = row[col_name]
            print(f"1. [{val:,}] {title}")

    print_top("播放量", 'Views (播放)')
    print_top("点赞数", 'Likes (点赞)')
    print_top("收藏数", 'Favorites (收藏)')

    # 4. Hidden Gems (High engagement but maybe lower views)
    print("\n## 💎 高质量互动 (粉丝粘性高)")
    top_hardcore = df.sort_values(by='Hardcore Ratio', ascending=False).head(3)
    print("\n### 硬币/点赞比最高 (铁粉多):")
    for idx, row in top_hardcore.iterrows():
        title = row['Title']
        val = row['Hardcore Ratio']
        print(f"1. [{val:.1f}%] {title}")


    # 5. Recent Trend (Last 10 videos for better accuracy)
    print("\n## 📅 最近 10 个视频表现与商单预估")
    if 'Publish Time' in df.columns:
        df['Publish Time'] = pd.to_datetime(df['Publish Time'])
        recent = df.sort_values(by='Publish Time', ascending=False).head(10)
        
        recent_views = []
        for idx, row in recent.iterrows():
            date = row['Publish Time'].strftime('%Y-%m-%d')
            title = row['Title']
            views = row['Views (播放)']
            recent_views.append(views)
            print(f"- {date}: {views:,} 播放 | {title[:30]}...")

        # Calculate Average Views of recent videos
        if recent_views:
            avg_recent_views = sum(recent_views) / len(recent_views)
            print(f"\n📈 **近期均播 (最近10个)**: {avg_recent_views:,.0f}")
            
            # Estimate Sponsorship Quote
            # Low: 0.5 yuan/view, High: 1.5 yuan/view (for vertical/niche content)
            quote_low = avg_recent_views * 0.5
            quote_high = avg_recent_views * 2.5 # Increased upper bound for high-quality niche content (UE5/Fitness)
            
            print("\n💰 **潜在商单报价预估 (参考)**")
            print(f"> 基于近期均播计算，仅供参考，实际看粉丝粘性与广告主预算")
            print(f"- **基础推广 (贴片/植入)**: ¥{quote_low:,.0f} - ¥{avg_recent_views:,.0f}")
            print(f"- **深度定制 (专场视频)**: ¥{avg_recent_views:,.0f} - ¥{quote_high:,.0f}")
            print(f"\n💡 你的内容垂直度高（UE5/健身），报价通常可以偏高位。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Try to find the latest csv in current dir
        files = [f for f in os.listdir('.') if f.startswith('bilibili_data_') and f.endswith('.csv')]
        if files:
            files.sort(key=os.path.getmtime, reverse=True)
            analyze(files[0])
        else:
            print("Usage: python analyze_local_data.py <csv_file>")
    else:
        analyze(sys.argv[1])
