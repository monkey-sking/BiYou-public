#!/usr/bin/env python3
"""
Bilibili Collection (合集) API Service
Handles creating, listing, and managing video collections.
"""
import json
import requests
import os
from typing import Optional
from core.utils.logger import log
from core.services.bili_api import get_bilibili_credentials, create_session


class BiliCollectionAPI:
    """B站合集 API 封装"""
    
    def __init__(self, session: Optional[requests.Session] = None):
        """
        初始化合集 API
        Args:
            session: 可选的已认证 requests.Session，如果不提供则自动创建
        """
        if session:
            self.session = session
        else:
            cookie_data = get_bilibili_credentials()
            if not cookie_data:
                raise RuntimeError("无法获取 Bilibili 凭证，请确保已登录")
            self.session = create_session(cookie_data)
        
        self.csrf = self.session.cookies.get('bili_jct', '')
        self._mid = None  # 用户 mid，延迟获取
    
    @property
    def mid(self) -> int:
        """获取当前用户的 mid"""
        if self._mid is None:
            self._mid = self._get_my_mid()
        return self._mid
    
    def _get_my_mid(self) -> int:
        """从 API 获取当前登录用户的 mid"""
        try:
            url = "https://api.bilibili.com/x/web-interface/nav"
            resp = self.session.get(url, timeout=10).json()
            if resp.get('code') == 0:
                return resp['data']['mid']
        except Exception as e:
            log.warning(f"获取用户 mid 失败: {e}")
        return 0

    def get_my_collections(self) -> list[dict]:
        """
        获取当前用户的所有合集列表
        Returns: [{"id": int, "title": str, "cover": str, "count": int}, ...]
        """
        collections = []
        page = 1
        page_size = 20
        
        while True:
            try:
                # 正确的端点
                url = "https://member.bilibili.com/x2/creative/web/seasons"
                params = {
                    "pn": page,
                    "ps": page_size,
                }
                resp = self.session.get(url, params=params, timeout=10).json()
                
                if resp.get('code') != 0:
                    log.warning(f"获取合集列表失败: {resp.get('message')}")
                    break
                
                data = resp.get('data', {})
                items = data.get('seasons') or []  # 处理 null 情况
                
                for item in items:
                    # Item is a wrapper containing 'season', 'seasonStat', etc.
                    season_info = item.get('season', {})
                    if not season_info:
                        season_info = item # Fallback if structure changes again
                        
                    collections.append({
                        "id": season_info.get('id'),
                        "title": season_info.get('title', ''),
                        "cover": season_info.get('cover', ''),
                        "count": season_info.get('ep_num', 0), # Correct key for count seems to be ep_num
                        "description": season_info.get('desc', ''),
                    })
                
                # 检查是否还有更多
                total = data.get('total', 0)
                if page * page_size >= total or not items:
                    break
                page += 1
                
            except Exception as e:
                log.error(f"获取合集列表异常: {e}")
                break
        
        return collections

    def create_collection(self, title: str, desc: str = "", cover: str = "") -> Optional[dict]:
        """
        创建新合集
        Args:
            title: 合集标题 (必填)
            desc: 合集描述
            cover: 封面 URL (必须是 B站图片 URL)
        Returns: {"id": int, "title": str} 或 None
        """
        # 默认封面 (如果未提供)
        if not cover:
            cover = "https://i0.hdslb.com/bfs/face/member/noface.jpg"

        try:
            url = "https://member.bilibili.com/x2/creative/web/season/add"
            data = {
                "title": title[:50],
                "desc": desc[:200] if desc else "",
                "cover": cover,
                "csrf": self.csrf,
            }
            
            # 使用 application/x-www-form-urlencoded
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://member.bilibili.com/platform/upload-manager/ep"
            }
            
            resp = self.session.post(url, data=data, headers=headers, timeout=15).json()
            
            if resp.get('code') == 0:
                data = resp.get('data')
                sid = None
                
                # 情况 1: data 是字典，包含 season 对象
                if isinstance(data, dict):
                    season_data = data.get('season', {})
                    sid = season_data.get('id') or data.get('id')
                # 情况 2: data 直接是 ID (int/str)
                elif isinstance(data, (int, str)):
                    sid = data
                    
                if sid:
                    log.info(f"✅ 合集创建成功: {title} (ID: {sid})")
                    return {
                        "id": sid,
                        "title": title, # API 可能没返回标题，使用请求的标题
                    }
                else:
                    log.error(f"无法解析合集 ID, 响应: {resp}")
                    return None
            else:
                log.error(f"创建合集失败: {resp.get('message')}")
                return None
                
        except Exception as e:
            log.error(f"创建合集异常: {e}")
            return None

    def add_video_to_collection(self, season_id: int, aid: int, cid: Optional[int] = None, title: str = "") -> bool:
        """
        将视频添加到合集
        Args:
            season_id: 合集 ID
            aid: 视频的 AV 号
            cid: 视频的 CID (可选，如果不提供则尝试获取)
            title: 单集标题 (可选，默认为空)
        Returns: 是否成功
        """
        try:
            # 1. 获取 CID (如果未提供)
            if not cid:
                cid = self._get_video_cid(aid)
            
            # 2. 获取默认 section_id
            section_id = self._get_default_section(season_id)
            if not section_id:
                log.error(f"无法获取合集 {season_id} 的默认分节")
                return False
            
            url = "https://member.bilibili.com/x2/creative/web/season/section/episodes/add"
            params = {"csrf": self.csrf}
            
            # 浏览器抓包显示参数使用 camelCase
            payload = {
                "sectionId": section_id,
                "episodes": [{
                    "aid": aid,
                    "cid": cid or 0, # cid 可能是必须的
                    "title": title[:50] if title else "", # 限制长度并使用传入标题
                    "charging_pay": 0,
                    "member_first": 0,
                    "limited_free": False
                }],
            }
            
            resp = self.session.post(
                url, 
                params=params, 
                json=payload, 
                timeout=15
            ).json()
            
            if resp.get('code') == 0:
                log.info(f"✅ 视频 av{aid} 已添加到合集 {season_id}")
                return True
            else:
                log.error(f"添加视频到合集失败: {resp.get('message')}")
                return False
                
        except Exception as e:
            log.error(f"添加视频到合集异常: {e}")
            return False

    def upload_cover(self, path: str) -> Optional[str]:
        """
        上传本地图片作为封面
        Args:
            path: 本地图片路径
        Returns: B站图片 URL (不带协议头) 或 None
        """
        if not os.path.exists(path):
            log.error(f"封面文件不存在: {path}")
            return None
            
        try:
            import base64
            with open(path, 'rb') as f:
                raw_data = f.read()
            
            url = "https://member.bilibili.com/x/vu/web/cover/up"
            data = {
                'cover': b'data:image/jpeg;base64,' + base64.b64encode(raw_data),
                'csrf': self.csrf
            }
            
            resp = self.session.post(url, data=data, timeout=30).json()
            
            if resp.get('code') == 0:
                data = resp.get('data', {})
                url = data.get('url', '')
                if url:
                    # 返回去除 http: 的 URL，保持一致性
                    clean_url = url.replace("http:", "").replace("https:", "")
                    if clean_url.startswith("//"):
                        clean_url = "https:" + clean_url
                    log.info(f"✅ 封面上传成功: {clean_url}")
                    return clean_url
            
            log.error(f"封面上传失败: {resp.get('message')}")
            return None
            
        except Exception as e:
            log.error(f"封面上传异常: {e}")
            return None

    def update_collection(self, season_id: int, title: str = None, desc: str = None, cover: str = None) -> bool:
        """
        更新合集信息
        Args:
            season_id: 合集 ID
            title: 新标题 (可选)
            desc: 新描述 (可选)
            cover: 新封面URL (可选，支持本地路径自动上传)
        Returns: 是否成功
        """
        try:
            # 1. 获取现有信息以保留未修改字段
            current_info = self._get_collection_info(season_id)
            if not current_info:
                log.error(f"无法获取合集 {season_id} 信息")
                return False
            
            # 2. 处理封面上传
            final_cover = current_info.get('cover', '')
            if cover:
                if os.path.exists(cover):
                    uploaded = self.upload_cover(cover)
                    if uploaded:
                        final_cover = uploaded
                else:
                    final_cover = cover

            # 3. 构建更新数据
            url = f"https://member.bilibili.com/x2/creative/web/season/edit?csrf={self.csrf}"
            
            payload = {
                "season": {
                    "id": season_id,
                    "title": title or current_info.get('title', ''),
                    "desc": desc or current_info.get('desc', '') or current_info.get('intro', ''),
                    "cover": final_cover
                },
                "sorts": []
            }
            
            headers = {
                "Content-Type": "application/json",
                "Referer": "https://member.bilibili.com/platform/upload-manager/ep"
            }
            
            resp = self.session.post(url, json=payload, headers=headers, timeout=15)
            # log.info(f"Update response status: {resp.status_code}")
            
            try:
                resp = resp.json()
            except Exception as e:
                log.error(f"Failed to parse JSON response. Status: {resp.status_code}")
                return False
            
            if resp.get('code') == 0:
                log.info(f"✅ 合集更新成功: {season_id}")
                return True
            else:
                log.error(f"更新合集失败: {resp.get('message')}")
                return False
                
        except Exception as e:
            log.error(f"更新合集异常: {e}")
            return False

    def _get_collection_info(self, season_id: int) -> Optional[dict]:
        """获取合集详细信息 (内部用)"""
        try:
            url = "https://member.bilibili.com/x2/creative/web/season"
            params = {"id": season_id}
            resp = self.session.get(url, params=params, timeout=10).json()
            
            if resp.get('code') == 0:
                data = resp.get('data', {})
                season = data.get('season', {})
                return season
        except Exception as e:
            log.warning(f"获取合集详情失败: {e}")
        return None

    def add_video_by_bvid(self, season_id: int, bvid: str) -> bool:
        """Video add wrapper"""
        aid = self._bvid_to_aid(bvid)
        if not aid:
            log.error(f"无法将 {bvid} 转换为 aid")
            return False
        return self.add_video_to_collection(season_id, aid)

    def _get_default_section(self, season_id: int) -> Optional[int]:
        """获取合集的默认分节 ID，如果没有则创建"""
        try:
            # 1. 获取合集详情（包含分节列表）
            # GET https://member.bilibili.com/x2/creative/web/season?id={season_id}
            url = "https://member.bilibili.com/x2/creative/web/season"
            params = {"id": season_id}
            resp = self.session.get(url, params=params, timeout=10).json()
            
            if resp.get('code') == 0:
                data = resp.get('data', {})
                # 数据结构: data.sections.sections (list)
                sections_data = data.get('sections', {})
                sections = sections_data.get('sections', [])
                
                if sections:
                    return sections[0].get('id')
            
            # 2. 如果没有分节，创建一个
            log.info(f"合集 {season_id} 无分节，正在创建默认分节...")
            create_url = "https://member.bilibili.com/x2/creative/web/season/section/add"
            
            # 使用 camelCase 参数
            create_data = {
                "type": 0,
                "seasonId": season_id,
                "title": "正片",
                "csrf": self.csrf
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://member.bilibili.com/platform/upload-manager/ep"
            }
            
            resp = self.session.post(create_url, data=create_data, headers=headers, timeout=15).json()
            
            if resp.get('code') == 0:
                data = resp.get('data', {})
                # 返回可能是 id 或对象
                return data.get('id') if isinstance(data, dict) else data
            else:
                log.error(f"创建分节失败: {resp.get('message')}")
                return None
                
        except Exception as e:
            log.error(f"获取/创建分节失败: {e}")
            return None

    def _bvid_to_aid(self, bvid: str) -> Optional[int]:
        """将 BVID 转换为 AID"""
        try:
            # 使用 view 接口同时获取 cid
            return self._get_video_info(bvid).get('aid')
        except:
            return None

    def _get_video_cid(self, aid: int) -> int:
        """获取视频的 CID"""
        try:
            url = f"https://api.bilibili.com/x/web-interface/view?aid={aid}"
            resp = self.session.get(url, timeout=10).json()
            if resp.get('code') == 0:
                return resp['data']['cid']
        except Exception as e:
            log.warning(f"获取 CID 失败: {e}")
        return 0

    def _get_video_info(self, bvid: str) -> dict:
        """获取视频详细信息"""
        try:
            url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            resp = self.session.get(url, timeout=10).json()
            if resp.get('code') == 0:
                return resp['data']
        except Exception as e:
            log.warning(f"获取视频信息失败: {e}")
        return {}

    def get_collection_videos(self, season_id: int) -> list[dict]:
        """
        获取合集内的视频列表
        Args:
            season_id: 合集 ID
        Returns: [{"aid": int, "bvid": str, "title": str}, ...]
        """
        videos = []
        page = 1
        page_size = 30
        
        while True:
            try:
                url = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
                params = {
                    "mid": self.mid,
                    "season_id": season_id,
                    "sort_reverse": False,
                    "page_num": page,
                    "page_size": page_size,
                }
                resp = self.session.get(url, params=params, timeout=10).json()
                
                if resp.get('code') != 0:
                    break
                
                data = resp.get('data', {})
                archives = data.get('archives', [])
                
                for item in archives:
                    videos.append({
                        "aid": item.get('aid'),
                        "bvid": item.get('bvid'),
                        "title": item.get('title', ''),
                    })
                
                # 检查分页
                page_info = data.get('page', {})
                total = page_info.get('total', 0)
                if page * page_size >= total:
                    break
                page += 1
                
            except Exception as e:
                log.error(f"获取合集视频列表异常: {e}")
                break
        
        return videos

    def find_collection_by_name(self, name: str) -> Optional[dict]:
        """
        按名称查找合集
        Args:
            name: 合集名称 (支持部分匹配)
        Returns: {"id": int, "title": str, ...} 或 None
        """
        collections = self.get_my_collections()
        
        # 先尝试精确匹配
        for c in collections:
            if c['title'] == name:
                return c
        
        # 再尝试部分匹配
        for c in collections:
            if name in c['title'] or c['title'] in name:
                return c
        
        return None

    def get_or_create_collection(self, name: str, desc: str = "") -> Optional[dict]:
        """
        获取或创建合集
        Args:
            name: 合集名称
            desc: 描述 (仅创建时使用)
        Returns: {"id": int, "title": str} 或 None
        """
        existing = self.find_collection_by_name(name)
        if existing:
            log.info(f"📁 使用已有合集: {existing['title']} (ID: {existing['id']})")
            return existing
        
        log.info(f"📁 创建新合集: {name}")
        return self.create_collection(name, desc)


def get_my_videos(session: requests.Session, page: int = 1, page_size: int = 20) -> tuple[list[dict], int]:
    """
    获取当前用户的视频列表 (用于整理合集)
    Args:
        session: 已认证的 session
        page: 页码
        page_size: 每页数量
    Returns: (videos_list, total_count)
    """
    try:
        url = "https://member.bilibili.com/x/web/archives"
        params = {
            "status": "",  # 空 = 全部
            "pn": page,
            "ps": page_size,
        }
        resp = session.get(url, params=params, timeout=15).json()
        
        if resp.get('code') != 0:
            log.error(f"获取视频列表失败: {resp.get('message')}")
            return [], 0
        
        data = resp.get('data', {})
        arc_list = data.get('arc_audits', [])
        total = data.get('page', {}).get('count', 0)
        
        videos = []
        for item in arc_list:
            arc = item.get('Archive', {})
            videos.append({
                "aid": arc.get('aid'),
                "bvid": arc.get('bvid'),
                "title": arc.get('title', ''),
                "cover": arc.get('cover', ''),
                "ctime": arc.get('ctime', 0),
                "state": arc.get('state', 0),
                "typeid": arc.get('typeid', 0),
            })
        
        return videos, total
        
    except Exception as e:
        log.error(f"获取视频列表异常: {e}")
        return [], 0


# ============================================================
# CLI 测试入口
# ============================================================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Bilibili 合集 API 测试")
    parser.add_argument("--list", action="store_true", help="列出所有合集")
    parser.add_argument("--videos", action="store_true", help="列出所有视频")
    parser.add_argument("--create", type=str, help="创建新合集 (传入标题)")
    parser.add_argument("--update-season", type=str, help="更新合集 (传入合集名称或ID)")
    parser.add_argument("--set-cover", type=str, help="设置封面 (传入本地路径)")
    args = parser.parse_args()
    
    try:
        api = BiliCollectionAPI()
        
        if args.list:
            print("\n📁 我的合集列表:")
            collections = api.get_my_collections()
            if not collections:
                print("   (暂无合集)")
            for c in collections:
                print(f"   - [{c['id']}] {c['title']} ({c['count']} 个视频)")
        
        if args.videos:
            print("\n🎬 我的视频列表 (前 20 个):")
            videos, total = get_my_videos(api.session)
            print(f"   共 {total} 个视频")
            for v in videos[:20]:
                print(f"   - [{v['bvid']}] {v['title']}")
        
        if args.create:
            result = api.create_collection(args.create)
            if result:
                print(f"\n✅ 合集创建成功: {result}")
            else:
                print(f"\n❌ 合集创建失败")

        if args.update_season and args.set_cover:
            # 尝试解析 ID
            sid = None
            if args.update_season.isdigit():
                sid = int(args.update_season)
            else:
                # 按名称查找
                coll = api.find_collection_by_name(args.update_season)
                if coll:
                    sid = coll['id']
            
            if sid:
                print(f"🔄 正在更新合集 {sid} 封面...")
                if api.update_collection(sid, cover=args.set_cover):
                     print(f"✅ 封面更新成功!")
                else:
                     print(f"❌ 封面更新失败")
            else:
                print(f"❌ 未找到合集: {args.update_season}")
                
    except Exception as e:
        print(f"❌ 错误: {e}")
