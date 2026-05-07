#!/usr/bin/env python3
"""
飞书数据同步到 GitHub -> 自动触发 Vercel 部署
读取 TrendRadar 推送到飞书的数据，同步到 hot-site 仓库的 data.json
保留 3 天热点，自动扩展标签分类
"""

import json
import base64
import urllib.request
import urllib.error
import os
import re
from datetime import datetime, timedelta

# === 配置 ===
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "YOUR_TOKEN_HERE")
REPO_OWNER = "xkw6666123"
REPO_NAME = "vervel"
FILE_PATH = "data.json"

# 飞书 Webhook 数据文件（TrendRadar 推送的热点）
FEISHU_DATA_FILE = r"C:\Users\Kevin\WorkBuddy\Claw\TrendRadar\feishu_cache.json"

# 保留天数
RETENTION_DAYS = 3

# 平台映射：平台ID → 分类标签
PLATFORM_TAGS = {
    "微博": ["热议", "娱乐", "社会"],
    "贴吧": ["热议", "社会", "网友"],
    "知乎": ["热议", "观点", "深挖"],
    "抖音": ["爆款", "短视频", "热门"],
    "bilibili": ["年轻", "二次元", "热门"],
    "今日头条": ["社会", "资讯", "热议"],
    "百度热搜": ["社会", "热点", "资讯"],
    "澎湃新闻": ["新闻", "社会", "时政"],
    "凤凰网": ["新闻", "国际", "时政"],
    "华尔街见闻": ["财经", "金融", "科技"],
    "财联社热门": ["财经", "金融", "投资"],
    "TrendRadar": ["社会热点", "信息差"],
}

# 争议/爆款关键词（内容标题包含这些词时加标签）
CONTROVERSIAL_KW = [
    "争议", "怒", "撕", "骂", "怼", "吵", "告", "罚",
    "翻车", "塌房", "瓜", "爆料", "出轨", "内幕",
    "震惊", "离谱", "无语", "恶心",
]
VIRAL_KW = [
    "爆", "热搜", "刷屏", "疯传", "火", "万人",
    "全网", "千万", "亿", "破纪录",
]


def github_api(path, method="GET", data=None, token=None):
    """调用 GitHub API"""
    url = f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if data:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))
    except Exception as e:
        return None, {"error": str(e)}


def get_current_data(token):
    """获取 GitHub 上当前的 data.json"""
    status, data = github_api(
        f"/repos/{REPO_OWNER}/{REPO_NAME}/contents/{FILE_PATH}",
        token=token
    )
    if status == 200:
        content = base64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]
        return json.loads(content), sha
    return None, None


def should_retain(article_date_str):
    """判断文章是否在保留期内（3 天）"""
    if not article_date_str:
        return True  # 没日期的保留
    try:
        article_date = datetime.strptime(article_date_str, "%Y-%m-%d").date()
        cutoff = datetime.now().date() - timedelta(days=RETENTION_DAYS)
        return article_date >= cutoff
    except:
        return True


def classify_article(title, source):
    """根据标题和来源自动分类标签"""
    tags = set()
    
    # 来源标签
    base_tags = PLATFORM_TAGS.get(source, ["社会热点", "信息差"])
    for t in base_tags:
        tags.add(t)
    
    # 争议内容检测
    title_lower = title.lower()
    if any(kw in title_lower for kw in CONTROVERSIAL_KW):
        tags.add("争议")
        tags.add("热议")
    
    # 爆款内容检测
    if any(kw in title_lower for kw in VIRAL_KW):
        tags.add("爆款")
        tags.add("热门")
    
    # 确保不超过 5 个标签
    tags_list = list(tags)
    if len(tags_list) > 5:
        # 优先保留爆款/争议/热议
        priority = ["爆款", "争议", "热议", "热门"]
        ordered = [t for t in priority if t in tags_list]
        rest = [t for t in tags_list if t not in priority]
        tags_list = (ordered + rest)[:5]
    
    return tags_list


def update_data_json(new_articles, token):
    """更新 GitHub 上的 data.json（保留3天 + 自动分类）"""
    current_data, sha = get_current_data(token)
    if current_data is None:
        print("❌ 无法获取当前数据")
        return False

    # 追加新文章（去重）
    existing_ids = {a.get("id") for a in current_data.get("articles", [])}
    added = 0
    for article in new_articles:
        if article.get("id") not in existing_ids:
            current_data["articles"].insert(0, article)
            existing_ids.add(article.get("id"))
            added += 1

    # 保留 3 天内的文章
    all_articles = current_data.get("articles", [])
    retained = [a for a in all_articles if should_retain(a.get("date"))]
    current_data["articles"] = retained
    
    # 也清理 inspirations（保留3天）
    if "inspirations" in current_data:
        # inspirations 没有日期字段，保留全部
        pass

    current_data["updated_at"] = datetime.now().isoformat()

    # 上传到 GitHub
    content = json.dumps(current_data, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    payload = {
        "message": f"sync: {added}条新增 · 保留{len(retained)}条·3日内 ({datetime.now().strftime('%m-%d %H:%M')})",
        "content": encoded,
        "sha": sha,
        "branch": "main"
    }

    status, result = github_api(
        f"/repos/{REPO_OWNER}/{REPO_NAME}/contents/{FILE_PATH}",
        method="PUT",
        data=payload,
        token=token
    )

    if status == 200:
        removed = len(all_articles) - len(retained)
        print(f"✅ 同步成功: +{added} · 保留{len(retained)} · 清理{removed}")
        print(f"   Vercel 将自动部署...")
        return True
    else:
        print(f"❌ 同步失败: {result}")
        return False


def read_feishu_cache():
    """读取飞书缓存数据（TrendRadar 推送的数据），自动分类"""
    if not os.path.exists(FEISHU_DATA_FILE):
        print(f"⚠️  飞书缓存文件不存在: {FEISHU_DATA_FILE}")
        return []

    with open(FEISHU_DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 转换为网站格式
    articles = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    for i, item in enumerate(data.get("items", [])[:15]):  # 取前15条
        title = item.get("title", "热点速递")
        source = item.get("source", item.get("platform", "TrendRadar"))
        
        # 自动分类
        tags = classify_article(title, source)
        
        # 如果有平台名称，放入 source
        platform_name = item.get("platform", source)
        
        articles.append({
            "id": int(datetime.now().strftime("%Y%m%d")) * 100 + i,
            "title": title,
            "summary": item.get("content", "")[:100],
            "source": platform_name,
            "date": item.get("date", today),
            "time": item.get("time", datetime.now().strftime("%H:%M")),
            "tags": tags,
            "url": item.get("url", "#"),
            "likes": item.get("likes", item.get("hot_score", 0)),
            "comments": item.get("comments", 0),
        })

    return articles


def main():
    print("=== 热点数据同步 (3天保留) ===\n")

    token = GITHUB_TOKEN
    if token == "YOUR_TOKEN_HERE":
        print("❌ 请设置 GITHUB_TOKEN 环境变量")
        print("   获取方式: https://github.com/settings/tokens")
        return

    # 读取新数据
    new_articles = read_feishu_cache()

    if not new_articles:
        print("⚠️  没有新数据可同步")
        print("   请确保 TrendRadar 已推送数据到飞书")
        return

    print(f"📡 读到 {len(new_articles)} 条新热点")
    for a in new_articles:
        print(f"   [{','.join(a['tags'])}] {a['title'][:40]}")

    # 同步到 GitHub
    update_data_json(new_articles, token)


if __name__ == "__main__":
    main()
