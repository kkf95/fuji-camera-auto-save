# 在檔案頂部添加
session = requests.Session()

async def get_latest_image_url(page_url):
    """從指定頁面爬取圖片網址和地點名稱"""
    try:
        response = session.get(page_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        img_tag = soup.find("img", id="mov")
        image_url = img_tag["src"] if img_tag and img_tag.get("src") else DEFAULT_IMAGE_URL
        location_tag = soup.find("span", class_="auto-style3")
        location = location_tag.text.strip().replace("/ ", "") if location_tag else DEFAULT_LOCATION
        logger.info(f"成功爬取圖片網址：{image_url}，地點：{location}（頁面：{page_url}）")
        return image_url, location
    except Exception as e:
        logger.error(f"爬取圖片網址或地點失敗（頁面：{page_url}）：{e}\n{traceback.format_exc()}")
        return DEFAULT_IMAGE_URL, DEFAULT_LOCATION

async def send_images():
    # ... 其他程式碼不變 ...
    # 修改圖片下載部分
    download_start = time.time()
    response = session.get(request_url, timeout=10)
    download_duration = time.time() - download_start
    # ... 其他程式碼不變 ...