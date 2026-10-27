"""
Dramaseek 命令行版 - 无 GUI 下载短剧

用法:
    python download_drama.py <剧集网址>                    # 下载全部剧集
    python download_drama.py <剧集网址> --start 1 --end 10 # 下载指定区间
    python download_drama.py <剧集网址> --info              # 只看剧集信息，不下载

支持平台:
    - shortlinetv.com
    - reelshort.com

默认保存到: <本目录>/downloads/<剧名>/
"""
import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yt_dlp
from curl_cffi import requests as cffi_requests

try:
    from src.api_clients import ShortLineTVClient, ReelShortClient
    from src.config import config
except ImportError:
    # 允许在仓库外执行时手动指定 sys.path
    sys.path.insert(0, str(Path(__file__).parent))
    from src.api_clients import ShortLineTVClient, ReelShortClient
    from src.config import config

DEFAULT_OUTPUT = Path(__file__).parent / "downloads"


def detect_platform(url: str) -> str:
    """根据URL自动识别平台"""
    if "shortlinetv.com" in url:
        return "shortlinetv"
    if "reelshort.com" in url:
        return "reelshort"
    raise SystemExit(f"❌ 无法识别的平台，目前支持 shortlinetv.com 和 reelshort.com，收到: {url}")


def fetch_reelshort_page(url: str, retries: int = 3) -> str:
    """用 Chrome TLS 指纹请求 reelshort 页面，带退避重试。

    reelshort 的 WAF 对普通 requests/yt-dlp 返回 403，但用 curl_cffi 模拟
    Chrome 指纹时，虽然状态码仍可能是 403，body 里却带完整 HTML（含
    内嵌 m3u8），因此只看内容不看状态码。并发过高可能触发限流，加退避。
    """
    import time as _time
    last_err = None
    for attempt in range(retries):
        try:
            r = cffi_requests.get(url, impersonate="chrome", timeout=30)
            if len(r.text) > 5000:
                return r.text
            last_err = f"内容过短({len(r.text)}B) HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        _time.sleep(2 * (attempt + 1))
    # 注意：这里抛普通 Exception 而非 SystemExit——SystemExit 是 BaseException，
    # 会穿透 worker 的 except Exception 把整个进程杀掉
    raise Exception(f"reelshort 页面获取失败（可能被限流）: {last_err}")


def extract_m3u8(html: str) -> str | None:
    """从剧集页面 HTML 中提取内嵌 m3u8 直链"""
    m = re.search(r'https?://[^"\'\s\\]+\.m3u8[^"\'\s\\]*', html)
    return m.group(0) if m else None


def fetch_episodes(url: str, start: int, end: int, is_default_range: bool):
    """获取剧集列表，返回 (platform, drama_name, episodes, cover_url)"""
    platform = detect_platform(url)

    if platform == "shortlinetv":
        client = ShortLineTVClient()
        video_id = client.extract_video_id(url)
        if not video_id:
            raise SystemExit(f"❌ 无法从URL提取video_id，需要 /videos/<数字> 格式: {url}")
        print(f"🔍 平台: ShortLineTV | video_id: {video_id}")
        api_data = client.get_episodes(video_id)
        episodes, cover_url = client.parse_episodes(api_data, start, end, is_default_range)
        drama_name = api_data.get("list", {}).get("title") or f"video_{video_id}"
    else:
        client = ReelShortClient()
        slug = client.extract_slug(url)
        if not slug:
            raise SystemExit(f"❌ 无法从URL提取slug，需要 /episodes/<...> 格式: {url}")
        print(f"🔍 平台: ReelShort | slug: {slug}")

        # ---- 绕过 WAF 的新链路：直接从页面 HTML 提取 ----
        # 1. 用 Chrome 指纹拿剧集页面（WAF 可能返回 403 状态码但 body 完整）
        html = fetch_reelshort_page(url)
        m3u8 = extract_m3u8(html)
        if not m3u8:
            raise SystemExit("❌ 页面中未找到 m3u8，reelshort 页面结构可能已变化")

        # 2. 通过 data API 拿全剧集列表（同样忽略 403 状态码取 body）
        api_url = f"https://www.reelshort.com/_next/data/1792653904033/en/movie/{slug}.json?slug={slug}"
        r = cffi_requests.get(api_url, impersonate="chrome", timeout=30)
        try:
            movie = r.json().get("pageProps", {}).get("data", {})
        except Exception:
            movie = {}
        if not movie.get("online_base"):
            raise SystemExit("❌ 无法获取剧集列表（data API 失败）")

        drama_name = movie.get("book_title") or slug
        cover_url = movie.get("book_pic")
        episodes = filter_reelshort_episodes(movie["online_base"], slug, start, end, is_default_range)

    return platform, drama_name, episodes, cover_url


def filter_reelshort_episodes(online_base: list, slug: str, start: int, end: int, is_default_range: bool):
    """过滤 reelshort 剧集列表（区间/chapter_type 逻辑与原版一致）"""
    episodes = []
    for item in online_base:
        episode_num = item.get("serial_number", 0)
        chapter_id = item.get("chapter_id", "")
        chapter_type = item.get("chapter_type", 1)

        if is_default_range and start == 0 and end == 0:
            in_range = True
            if chapter_type != 1:
                continue
        elif start == end:
            in_range = (episode_num == start)
            allow_non_type1 = (episode_num == 0 and start == 0)
            if not allow_non_type1 and chapter_type != 1:
                continue
        else:
            in_range = (start <= episode_num <= end)
            allow_non_type1 = (episode_num == 0 and start == 0)
            if not allow_non_type1 and chapter_type != 1:
                continue

        if not in_range:
            continue

        if episode_num == 0:
            episode_url = f"https://www.reelshort.com/episodes/trailer-{slug}-{chapter_id}"
        else:
            episode_url = f"https://www.reelshort.com/episodes/episode-{episode_num}-{slug}-{chapter_id}"

        episodes.append({
            "episode_num": episode_num,
            "episode_name": f"Episode {episode_num:02d}",
            "episode_url": episode_url,
            "download_url": None,  # 下载时逐集抓页面提取 m3u8
        })

    return sorted(episodes, key=lambda x: x["episode_num"])


def download_cover(cover_url: str, storage_path: Path) -> Path | None:
    """下载剧集封面"""
    if not cover_url:
        return None
    try:
        if not cover_url.startswith("http"):
            cover_url = "https:" + cover_url if cover_url.startswith("//") else cover_url
        resp = requests.get(cover_url, timeout=config.API_TIMEOUT,
                            headers={"user-agent": "Mozilla/5.0"})
        resp.raise_for_status()
        # 从URL或Content-Type推断扩展名
        ext = "jpg"
        m = re.search(r'\.(jpe?g|png|webp)(?:[?#]|$)', cover_url, re.I)
        if m:
            ext = m.group(1).lower()
        elif "png" in resp.headers.get("content-type", ""):
            ext = "png"
        elif "webp" in resp.headers.get("content-type", ""):
            ext = "webp"
        cover_file = storage_path / f"cover.{ext}"
        cover_file.write_bytes(resp.content)
        return cover_file
    except Exception as e:
        print(f"⚠️ 封面下载失败: {e}")
        return None


def resolve_download_url(episode: dict) -> str:
    """解析出真正的下载地址。

    shortlinetv: API 已给直链；
    reelshort: download_url 为 None，需抓剧集页面提取内嵌 m3u8。
    """
    if episode.get("download_url"):
        return episode["download_url"]
    html = fetch_reelshort_page(episode["episode_url"])
    m3u8 = extract_m3u8(html)
    if not m3u8:
        raise Exception(f"页面中未找到 m3u8: {episode['episode_url']}")
    return m3u8


def download_one(episode: dict, drama_name: str, base_dir: Path, index: int, total: int) -> tuple:
    """下载单集，返回 (episode, ok, filepath/error)"""
    num = episode["episode_num"]
    safe_drama = config.sanitize_filename(drama_name)
    storage_path = base_dir / safe_drama
    storage_path.mkdir(parents=True, exist_ok=True)

    safe_name = config.sanitize_filename(episode["episode_name"])
    output_template = str(storage_path / f"{safe_name}.%(ext)s")

    # 已存在则跳过（断点续传意义上的幂等）
    for ext in config.VIDEO_EXTENSIONS:
        existing = storage_path / f"{safe_name}.{ext}"
        if existing.exists() and existing.stat().st_size > 0:
            return episode, True, existing

    ydl_opts = {
        "format": "best",
        "outtmpl": output_template,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
    }

    try:
        real_url = resolve_download_url(episode)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([real_url])
        # 找到实际落盘的文件
        actual = None
        for ext in config.VIDEO_EXTENSIONS:
            f = storage_path / f"{safe_name}.{ext}"
            if f.exists():
                actual = f
                break
        # 兜底：yt-dlp 可能落成其他扩展名，按前缀找一遍
        if actual is None:
            candidates = [f for f in storage_path.iterdir()
                          if f.name.startswith(safe_name) and ".part" not in f.name]
            actual = candidates[0] if candidates else None
        if actual:
            return episode, True, actual
        return episode, False, "下载完成但未找到输出文件"
    except Exception as e:
        # 清理半成品临时文件
        for f in storage_path.iterdir():
            if f.name.startswith(safe_name) and ".part" in f.name:
                try:
                    f.unlink()
                except OSError:
                    pass
        return episode, False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Dramaseek 命令行版短剧下载器")
    parser.add_argument("url", help="剧集网址 (shortlinetv / reelshort)")
    parser.add_argument("--start", type=int, default=None, help="起始集号")
    parser.add_argument("--end", type=int, default=None, help="结束集号")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUTPUT), help="保存目录 (默认 ./downloads)")
    parser.add_argument("--info", action="store_true", help="只显示剧集信息，不下载")
    parser.add_argument("--jobs", type=int, default=2, help="并发下载数 (默认2，reelshort 并发过高会被限流)")
    args = parser.parse_args()

    # 默认区间语义与原版一致：不传 start/end = 下载全部
    if args.start is None and args.end is None:
        start, end, is_default = 0, 0, True
    else:
        start = args.start if args.start is not None else 0
        end = args.end if args.end is not None else 0
        is_default = False

    platform, drama_name, episodes, cover_url = fetch_episodes(args.url, start, end, is_default)

    print(f"\n📺 剧名: {drama_name}")
    print(f"🎬 本次匹配剧集数: {len(episodes)}")

    if args.info:
        for ep in episodes:
            print(f"   第{ep['episode_num']}集  {ep['episode_name']}")
        print(f"🖼️  封面: {cover_url or '无'}")
        return

    if not episodes:
        print("⚠️ 没有匹配到任何剧集，请检查集数区间")
        return

    nums = [ep["episode_num"] for ep in episodes]
    print(f"📦 集数范围: {min(nums)} ~ {max(nums)}")
    print(f"💾 保存到: {Path(args.out).resolve()}\n")

    base_dir = Path(args.out)
    safe_drama = config.sanitize_filename(drama_name)
    (base_dir / safe_drama).mkdir(parents=True, exist_ok=True)

    # 下载封面
    cover_file = download_cover(cover_url, base_dir / safe_drama)
    if cover_file:
        print(f"🖼️  封面已保存: {cover_file.name}")

    # 并发下载
    ok_count, fail_list = 0, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(download_one, ep, drama_name, base_dir, i, len(episodes)): ep
                   for i, ep in enumerate(episodes)}
        for future in as_completed(futures):
            ep, ok, result = future.result()
            tag = "✅" if ok else "❌"
            print(f"{tag} 第{ep['episode_num']}集  {result if isinstance(result, str) else result.name}")
            if ok:
                ok_count += 1
            else:
                fail_list.append((ep["episode_num"], result))

    print(f"\n{'='*50}")
    print(f"🏁 完成: {ok_count}/{len(episodes)} 集成功，耗时 {time.time()-t0:.0f}s")
    if fail_list:
        print("失败清单:")
        for num, err in fail_list:
            print(f"   第{num}集: {err[:120]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
