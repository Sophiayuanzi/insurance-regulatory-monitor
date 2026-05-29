#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
保险监管动态实时抓取脚本
目标：抓取近三个月（相对运行日期）的监管新规、征求意见稿及行政处罚公告
数据源：国家金融监管总局(nfra.gov.cn)、国务院(gov.cn)、香港保监局(ia.org.hk)等
输出：结构化的JSON数据，可用于替换前端模拟数据

使用方式：
    python insurance_regulatory_crawler.py

依赖安装：
    pip install requests beautifulsoup4 lxml playwright
    playwright install chromium  # 仅当需要动态渲染时

合规注意事项：
    - 遵守 robots.txt
    - 请求间隔 ≥ 2秒
    - 仅抓取无条件向社会公开的信息
    - 不模拟登录、不绕过访问控制
"""

import re
import json
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ======================= 配置 =======================
REQUEST_DELAY = 2  # 请求间隔(秒)
DATE_RANGE_DAYS = 90  # 抓取近90天内容
# 输出文件名
OUTPUT_FILE = "regulatory_dashboard_data.json"

# 需要抓取的监管机构配置
SOURCES = {
    "nfra_regulations": {
        "name": "金融监管总局-政策规章",
        "url": "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId=928&itemUrl=ItemListRightList.html&itemName=政策规章规范性文件&itemsubPId=926",
        "list_selector": "ul.list-items li",  # 实际需要根据网页结构调整
        "title_selector": "a",
        "date_selector": ".date",
        "use_dynamic": False  # 若列表页也是动态渲染,需改为True并启用playwright
    },
    "nfra_draft": {
        "name": "金融监管总局-征求意见",
        "url": "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=945&itemId=951&itemUrl=ItemListRightList.html&itemName=征求意见",
        "list_selector": "ul.list-items li",
        "title_selector": "a",
        "date_selector": ".date",
        "use_dynamic": False
    },
    "nfra_penalty": {
        "name": "金融监管总局-行政处罚",
        "url": "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId=4113&itemUrl=ItemListRightList.html&itemName=总局机关&itemsubPId=931&itemsubPName=行政处罚",
        "list_selector": "ul.list-items li",
        "title_selector": "a",
        "date_selector": ".date",
        "use_dynamic": False
    },
    "gov_cn": {
        "name": "国务院-保险相关行政法规",
        "url": "https://sousuo.gov.cn/s.htm?q=保险&advance=true&title=&pubtime=3month",
        # 使用搜索接口，直接通过参数控制时间
        "search_url": "https://sousuo.gov.cn/s.htm",
        "params": {"q": "保险", "advance": "true", "pubtime": "3month"},
        "list_selector": ".result-list li",
        "title_selector": "h4 a",
        "date_selector": ".result-date",
        "use_dynamic": False
    }
}

# 额外使用 Bing 搜索补充（因官网列表页可能不全）
BING_SEARCH_QUERIES = [
    "site:nfra.gov.cn 保险 办法 通知 发布 {date_range}",
    "site:gov.cn 国务院 保险 条例 规定 发布 {date_range}",
    "金融监管总局 保险 行政处罚 {date_range}"
]

# ======================= 工具函数 =======================
def get_date_range() -> tuple:
    """返回近三个月的时间范围字符串 (start_date, end_date)"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=DATE_RANGE_DAYS)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

def is_within_date_range(date_str: str) -> bool:
    """检查日期是否在近三个月内"""
    try:
        # 尝试解析多种日期格式
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
            try:
                pub_date = datetime.strptime(date_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            # 如果都解析不了, 通过正则提取年份月份粗略判断
            match = re.search(r'(\d{4})[年/-](\d{1,2})', date_str)
            if match:
                year, month = int(match.group(1)), int(match.group(2))
                pub_date = datetime(year, month, 1)
            else:
                return False
        delta = datetime.now() - pub_date
        return delta.days <= DATE_RANGE_DAYS
    except Exception:
        return False

def fetch_html(url: str, use_dynamic: bool = False) -> str:
    """
    获取页面HTML
    use_dynamic=True时使用Playwright渲染JavaScript (需要安装playwright)
    """
    if not use_dynamic:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            return resp.text
        except Exception as e:
            logger.error(f"请求失败 {url}: {e}")
            return ""
    else:
        # 动态渲染模式 (需要安装 playwright)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                # 等待列表内容加载（可根据实际情况调整）
                page.wait_for_selector("ul.list-items", timeout=10000)
                html = page.content()
                browser.close()
                return html
        except ImportError:
            logger.error("Playwright未安装，请执行: pip install playwright && playwright install chromium")
            return ""
        except Exception as e:
            logger.error(f"动态渲染失败 {url}: {e}")
            return ""

def parse_list_page(html: str, source_config: Dict) -> List[Dict]:
    """解析列表页，提取标题、日期、详情链接"""
    items = []
    if not html:
        return items
    soup = BeautifulSoup(html, 'lxml')
    # 尝试多种常见列表选择器
    list_container = soup.select(source_config.get('list_selector', 'ul.list-items li'))
    if not list_container:
        # 兼容其他结构
        list_container = soup.select('.list li, .news-list li, .c-list li')
    for li in list_container:
        try:
            # 提取标题和链接
            title_elem = li.select_one(source_config.get('title_selector', 'a'))
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)
            link = title_elem.get('href')
            if link and not link.startswith('http'):
                link = urljoin(source_config['url'], link)
            
            # 提取日期
            date_elem = li.select_one(source_config.get('date_selector', '.date, .time, .pub-date'))
            date_text = date_elem.get_text(strip=True) if date_elem else ""
            if not date_text and 'date_parser_regex' in source_config:
                match = re.search(source_config['date_parser_regex'], str(li))
                if match:
                    date_text = match.group(1)
            
            # 过滤掉明显不相关的标题（如非保险）
            if not re.search(r'保险|风险|偿付|治理|股权|互联网保险', title, re.I):
                # 但行政处罚即使标题不包含“保险”也保留，因为处罚主体可能是保险机构
                if "行政处罚" not in source_config['name']:
                    continue
            
            items.append({
                "title": title,
                "link": link,
                "date": date_text,
                "source": source_config['name']
            })
        except Exception as e:
            logger.warning(f"解析列表项出错: {e}")
            continue
    # 按日期过滤近三个月
    filtered = [item for item in items if is_within_date_range(item['date'])]
    logger.info(f"{source_config['name']} 抓取到 {len(filtered)} 条近三月动态")
    return filtered

def search_bing(query: str) -> List[Dict]:
    """使用Bing搜索补充抓取（需处理反爬，简单使用requests+自定义headers）"""
    # 注意：Bing搜索有反爬机制，本示例仅作为思路，实际可能需要使用第三方库或付费API
    # 更好的做法：使用官方必应API（免费额度）
    # 这里提供一个简化版，实际项目建议改用serpapi或官方API
    results = []
    search_url = "https://www.bing.com/search"
    params = {"q": query, "count": 20}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = requests.get(search_url, params=params, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'lxml')
        for result in soup.select('.b_algo h2 a'):
            title = result.get_text(strip=True)
            link = result.get('href')
            # 提取摘要旁边的日期（简单正则）
            date_match = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', resp.text)
            date_text = date_match.group(1) if date_match else ""
            if is_within_date_range(date_text):
                results.append({
                    "title": title,
                    "link": link,
                    "date": date_text,
                    "source": "Bing搜索补充"
                })
    except Exception as e:
        logger.error(f"Bing搜索失败: {e}")
    return results

def crawl_all_sources() -> Dict:
    """主抓取函数，返回完整的数据结构，匹配前端的板块一、板块二"""
    start_date, end_date = get_date_range()
    logger.info(f"开始抓取近三个月监管动态: {start_date} 至 {end_date}")
    
    all_regulations = []   # 板块一：新规法规
    all_penalties = []     # 板块二：行政处罚
    
    # 遍历配置的每个渠道
    for key, cfg in SOURCES.items():
        if "penalty" in key:
            target_list = all_penalties
        else:
            target_list = all_regulations
        
        # 特殊处理国务院搜索（使用搜索URL参数）
        if key == "gov_cn":
            html = fetch_html(cfg['search_url'], cfg.get('use_dynamic', False))
        else:
            html = fetch_html(cfg['url'], cfg.get('use_dynamic', False))
        items = parse_list_page(html, cfg)
        # 附加分类信息
        for item in items:
            if "征求意见" in cfg['name'] or "draft" in key:
                item['category'] = "征求意见稿"
            elif "行政处罚" in cfg['name'] or "penalty" in key:
                item['category'] = "行政处罚"
            else:
                item['category'] = "监管新规"
        target_list.extend(items)
        time.sleep(REQUEST_DELAY)
    
    # 可选：通过Bing搜索补充新规和处罚
    bing_results = []
    for q_template in BING_SEARCH_QUERIES:
        query = q_template.format(date_range=f"after:{start_date}")
        bing_items = search_bing(query)
        bing_results.extend(bing_items)
    # 分类合并
    for item in bing_results:
        if "行政处罚" in item['title'] or "罚单" in item['title']:
            all_penalties.append(item)
        else:
            all_regulations.append(item)
    
    # 去重（基于标题+链接）
    def deduplicate(items):
        seen = set()
        unique = []
        for i in items:
            key = (i['title'], i.get('link', ''))
            if key not in seen:
                seen.add(key)
                unique.append(i)
        return unique
    
    all_regulations = deduplicate(all_regulations)
    all_penalties = deduplicate(all_penalties)
    
    # 按发布时间倒序排序（最新的在前）
    all_regulations.sort(key=lambda x: x.get('date', ''), reverse=True)
    all_penalties.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    # 构建输出数据结构（与前文静态页面匹配）
    output = {
        "last_updated": datetime.now().isoformat(),
        "date_range": {"start": start_date, "end": end_date},
        "regulations": all_regulations,   # 板块一原始数据
        "penalties": all_penalties        # 板块二原始数据
    }
    return output

def save_to_json(data: Dict, filename: str):
    """保存为JSON文件"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"数据已保存到 {filename}")

def transform_for_frontend(raw_data: Dict) -> Dict:
    """
    将原始抓取数据转换为前端页面可直接使用的格式（按效力层级分组、生成处罚表格数据）
    因为前端静态页面需要固定的层级分组格式，此函数模拟分组逻辑。
    实际使用时可以让前端直接调用API获取原始数据，或者在后端完成分组。
    """
    # 这里简单演示分组：将 regulation 条目映射到示例中的 groups 结构
    # 由于实际抓取的内容没有自带效力层级，可以基于关键词进行智能映射
    groups = {
        "LAW": {"levelName": "【效力层级1】法律 / 行政法规", "items": []},
        "RULE": {"levelName": "【效力层级2】部门规章", "items": []},
        "NORM": {"levelName": "【效力层级3】规范性文件", "items": []},
        "DRAFT": {"levelName": "【效力层级4】征求意见稿 / 立法计划", "items": []}
    }
    for reg in raw_data.get("regulations", []):
        title = reg['title']
        # 简单的规则匹配
        if "征求意见" in title or "草案" in title:
            level_key = "DRAFT"
            level_text = "征求意见稿"
        elif "办法" in title or "规定" in title or "规章" in title:
            level_key = "RULE"
            level_text = "部门规章"
        elif "通知" in title or "指引" in title or "公告" in title:
            level_key = "NORM"
            level_text = "规范性文件"
        elif "法" in title and "条例" in title:
            level_key = "LAW"
            level_text = "法律/行政法规"
        else:
            level_key = "NORM"
            level_text = "其他规范性文件"
        
        groups[level_key]["items"].append({
            "sn": len(groups[level_key]["items"]) + 1,
            "name": title,
            "issuer": reg.get('source', '金融监管总局'),
            "date": reg.get('date', ''),
            "summary": reg.get('title', '')[:80],  # 摘要可用标题代替
            "level": level_text,
            "link": reg.get('link', '#'),
            "linkNote": "官方链接"
        })
    # 过滤空分组
    final_groups = [g for g in groups.values() if g["items"]]
    
    # 处罚表格转换
    penalties_table = []
    for idx, p in enumerate(raw_data.get("penalties", []), 1):
        penalties_table.append({
            "sn": idx,
            "docNum": p.get('title', '')[:30],
            "party": "待提取",  # 需要更细致的解析
            "violation": p.get('title', ''),
            "decision": "详见原文",
            "date": p.get('date', ''),
            "link": p.get('link', '#')
        })
    
    return {
        "regulation_groups": final_groups,
        "penalties": penalties_table
    }

if __name__ == "__main__":
    # 第一步：抓取原始数据
    raw_data = crawl_all_sources()
    # 第二步：保存原始JSON（可用于后续分析）
    save_to_json(raw_data, OUTPUT_FILE)
    # 第三步：转换为前端可直接渲染的格式
    frontend_data = transform_for_frontend(raw_data)
    save_to_json(frontend_data, "frontend_ready_data.json")
    logger.info("抓取完成，已生成 frontend_ready_data.json 供前端使用")
