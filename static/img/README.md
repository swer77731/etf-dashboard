# 品牌資產

## 必需檔案

放好之後 base.html / favicon / 圖表浮水印**自動切換**,不必改程式碼。

| 檔名 | 用途 | 規格 |
|------|------|------|
| logo.svg | 主 LOGO(sidebar / mobile header / 圖表浮水印) | 任意尺寸,SVG 優先 |
| logo.png | logo.svg 缺失時的備案(可選) | 256×256 以上 |
| favicon.ico | 瀏覽器 tab icon | 32×32 / 16×16 |

## 偵測邏輯

 的  會檢查  是否存在,
存在 → 所有 template 自動使用 LOGO;不存在 → 退回「E」placeholder。

ECharts 浮水印同樣自動偵測:
-  存在時, 回傳 url,JS 端  把 LOGO 疊在圖表中央。
