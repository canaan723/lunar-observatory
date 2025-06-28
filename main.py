import feedparser
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
import pytz
import os
import time
import urllib.request
from urllib.parse import urlparse
from itertools import groupby
from operator import itemgetter
import re

# --- 配置区 (采纳您的建议，使用稳定服务器列表) ---
STABLE_RSSHUB_SERVERS = [
    'https://rsshub.rss.tips',
    'https://rsshub.pseudoyu.com',
    'https://rsshub.ktachibana.party',
    'https://rsshub.asailor.org',
    'https://rsshub.email-once.com',
]

# 我们将在这里定义RSS源的“路径”，而不是写死的完整URL
RSS_PATHS = {
    'AO3 | 佐鸣佐': 'https://archiveofourown.org/tags/14303/feed.atom', # 这是完整URL，特殊处理
    'B站 | 佐助': '/bilibili/vsearch/佐助/pubdate/0/1',
    'B站 | 鸣佐': '/bilibili/vsearch/鸣佐/pubdate/0/1',
    'B站 | 佐鸣': '/bilibili/vsearch/佐鸣/pubdate/0/1',
    '微博超话 | 佐鸣': '/weibo/super_index/10080800f63e66b38b96a8ca5ecb2e0b3cfae4/sort_time',
    '微博超话 | 鸣佐': '/weibo/super_index/100808799b9b6da0d5d6d2f398c771b28b4039/sort_time',
}
MAX_ENTRIES_PER_SOURCE = 20

# --- AO3 翻译字典 (保持不变) ---
AO3_RATING_TRANSLATIONS = {
    "Not Rated": "未分级", "General Audiences": "全年龄", "Teen And Up Audiences": "青少年及以上",
    "Mature": "成人级", "Explicit": "限制级"
}
AO3_WARNING_TRANSLATIONS = {
    "Creator Chose Not To Use Archive Warnings": "作者选择不使用作品存档警告", "No Archive Warnings Apply": "无存档警告",
    "Graphic Depictions Of Violence": "涉及暴力内容", "Major Character Death": "主要角色死亡",
    "Rape/Non-Con": "强暴/非自愿性行为", "Underage": "未成年", "Underage Sex": "未成年性行为"
}


def try_fetch_with_failover(path):
    """
    为RSSHub源实现自动故障切换功能。
    它会遍历STABLE_RSSHUB_SERVERS列表，直到成功或全部失败。
    """
    for server in STABLE_RSSHUB_SERVERS:
        url_to_try = f"{server}{path}"
        try:
            print(f"  ↪️ 正在尝试服务器: {server} ...")
            feed = feedparser.parse(url_to_try)
            # 检查是否成功：有内容、不是bozo（格式错误）、有条目
            if feed and not feed.bozo and feed.entries:
                print(f"  ✅ 成功从 {server} 获取数据。")
                return feed # 成功获取，立即返回结果
        except Exception as e:
            print(f"  ❌ 连接到 {server} 失败: {e}")
            continue # 失败，继续尝试下一个
    
    print(f"  🛑 警告: 所有备用服务器均无法成功抓取路径 {path}")
    return None # 所有服务器都失败了，返回None


def fetch_all_feeds():
    all_entries = []
    print("🛰️ 开始追踪月亮轨迹...")
    
    for name, path_or_url in RSS_PATHS.items():
        print(f"\n----- 正在检查源: {name} -----")
        
        feed = None
        # 判断是需要故障切换的RSSHub路径，还是直接的URL
        if path_or_url.startswith('/'): # 我们的RSSHub路径都以'/'开头
            feed = try_fetch_with_failover(path_or_url)
        else: # 这是一个完整的URL，比如AO3
            try:
                print(f"  ↪️ 直接抓取源: {path_or_url}")
                feed = feedparser.parse(path_or_url)
            except Exception as e:
                 print(f"🛑 直接抓取源 '{name}' 失败: {e}")

        if not feed or (feed.bozo and not feed.entries): # 如果抓取失败或内容为空
            if feed and feed.bozo:
                print(f"⚠️ 警告: '{name}' 的Feed格式不规范或抓取失败。详情: {feed.bozo_exception}")
            continue # 跳过此源
            
        print(f"抓取到 {len(feed.entries)} 条原始内容。")
        
        filtered_count = 0
        for entry in feed.entries:
            if 'AO3' in name and 'Language: 中文' not in entry.summary:
                continue

            bilibili_meta = ao3_meta = None
            thumbnail, clean_summary = '', entry.summary
            author = entry.get('author')
            categories = [tag.term for tag in entry.get('tags', [])]

            if 'B站' in name:
                bilibili_meta = {
                    'length': (re.search(r'Length:\s*([\d:]+)', entry.summary) or ['-', '-'])[1],
                    'play': (re.search(r'Play:\s*([\d,]+)', entry.summary) or ['-', '-'])[1],
                    'favorite': (re.search(r'Favorite:\s*([\d,]+)', entry.summary) or ['-', '-'])[1],
                    'danmaku': (re.search(r'Danmaku:\s*([\d,]+)', entry.summary) or ['-', '-'])[1],
                    'comment': (re.search(r'Comment:\s*([\d,]+)', entry.summary) or ['-', '-'])[1],
                }
                meta_keywords = ['Length:', 'AuthorID:', 'Play:', 'Favorite:', 'Danmaku:', 'Comment:', 'Match By:']
                meta_start_index = -1
                for keyword in meta_keywords:
                    found_index = entry.summary.find(keyword)
                    if found_index != -1 and (meta_start_index == -1 or found_index < meta_start_index):
                        meta_start_index = found_index
                clean_summary = entry.summary[:meta_start_index].strip() if meta_start_index != -1 else entry.summary
                clean_summary = clean_summary.replace(entry.title, '').strip()
                if entry.description and '<img src="' in entry.description:
                    start = entry.description.find('<img src="') + 10
                    end = entry.description.find('"', start)
                    thumbnail = 'https:' + entry.description[start:end] if entry.description[start:end].startswith('//') else entry.description[start:end]
            
            elif 'AO3' in name:
                summary_html = entry.summary
                original_rating = (re.search(r'Rating:.*?>(.*?)<\/a>', summary_html) or ['-','-'])[1]
                original_warning = (re.search(r'Warnings:.*?>(.*?)<\/a>', summary_html) or ['-','-'])[1]
                translated_rating = AO3_RATING_TRANSLATIONS.get(original_rating, original_rating)
                translated_warning = AO3_WARNING_TRANSLATIONS.get(original_warning, original_warning)
                ao3_meta = {
                    'words': (re.search(r'Words:\s*([\d,]+)', summary_html) or ['-','-'])[1],
                    'chapters': (re.search(r'Chapters:\s*([\d/]+)', summary_html) or ['-','-'])[1],
                    'rating': translated_rating, 'warnings': translated_warning,
                    'relationships': [rel.replace('*s*', '/') for rel in re.findall(r'relationships/.*?">(.*?)<\/a>', summary_html)],
                }
                clean_summary_match = re.search(r'<p>(?!by)(.*?)<\/p>', summary_html, re.DOTALL)
                clean_summary = clean_summary_match.group(1).strip() if clean_summary_match else ''

            parsed_time = None
            time_struct = entry.get('published_parsed') or entry.get('updated_parsed')
            if time_struct:
                dt_naive = datetime.fromtimestamp(time.mktime(time_struct))
                parsed_time = pytz.utc.localize(dt_naive).astimezone(pytz.timezone('Asia/Shanghai'))

            all_entries.append({
                'source': name, 'title': entry.title, 'link': entry.link, 'author': author,
                'published': parsed_time, 'summary': clean_summary, 'thumbnail': thumbnail,
                'categories': categories, 'bilibili_meta': bilibili_meta, 'ao3_meta': ao3_meta,
            })
            filtered_count += 1
            if filtered_count >= MAX_ENTRIES_PER_SOURCE: break
        print(f"经过滤和处理后，保留 {filtered_count} 条有效内容。")

    all_entries.sort(key=lambda x: x['published'] or datetime.min.replace(tzinfo=pytz.utc), reverse=True)
    print(f"\n✅ 追踪完成，共找到 {len(all_entries)} 条有效记录。")
    return all_entries

def generate_html(entries):
    print("🎨 正在生成观测报告 (HTML)...")
    grouped_entries = {}
    entries.sort(key=itemgetter('source'))
    for source_name, group in groupby(entries, key=itemgetter('source')):
        grouped_entries[source_name] = sorted(list(group), key=lambda x: x['published'] or datetime.min.replace(tzinfo=pytz.utc), reverse=True)
    
    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('template.html')
    update_time = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    
    html_content = template.render(grouped_entries=grouped_entries, update_time=update_time, site_title="Lunar Orbit Observatory", site_slogan="Tracing the Path of the Moon.")
    
    return html_content

if __name__ == "__main__":
    entries = fetch_all_feeds()
    output_content = generate_html(entries)
    
    output_file_path = "index.html"
    with open(output_file_path, "w", encoding="utf-8") as f:
        f.write(output_content)
    
    print(f"✅ 观测报告生成完毕，已保存为 {output_file_path}。")
