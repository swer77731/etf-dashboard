/**
 * ETF Watch chart watermark — Step 3 ECharts 圖表浮水印 helper.
 *
 * Usage:
 *   const chart = echarts.init(el);
 *   chart.setOption({...your options...});
 *
 *   // 純文字浮水印
 *   applyWatermark(chart);
 *
 *   // 含 LOGO + 文字(logoUrl 從 _common_ctx 的 logo_url 傳進來)
 *   applyWatermark(chart, { logoUrl: "/static/img/logo.svg" });
 *
 *   // 自訂
 *   applyWatermark(chart, {
 *     text: "ETF Watch",
 *     logoUrl: "/static/img/logo.svg",
 *     fontSize: 60,
 *     opacity: 0.05,
 *     color: "#ffffff",
 *     logoSize: 80,
 *   });
 *
 * 浮水印放在圖表中央,opacity 預設 0.05、不影響互動。
 * 截圖匯出時也會帶到品牌(Stealth Branding 增長策略)。
 */
(function (root) {
  function applyWatermark(echartsInstance, opts) {
    if (!echartsInstance || typeof echartsInstance.setOption !== 'function') {
      return;
    }
    opts = opts || {};
    var text = opts.text || 'ETF Watch';
    var fontSize = opts.fontSize || 60;
    var color = opts.color || '#ffffff';
    var opacity = opts.opacity != null ? opts.opacity : 0.05;
    var logoUrl = opts.logoUrl || null;
    var logoSize = opts.logoSize || 80;
    var verticalGap = opts.verticalGap || 12;

    var graphic = [];

    if (logoUrl) {
      // LOGO 在上,文字在下,垂直置中對齊
      var totalH = logoSize + verticalGap + fontSize;
      graphic.push({
        type: 'image',
        left: 'center',
        top: 'middle',
        silent: true,
        z: 0,
        bounding: 'raw',
        style: {
          image: logoUrl,
          width: logoSize,
          height: logoSize,
          opacity: opacity,
        },
        // 上移半個總高,讓 LOGO + 文字組合在中央對齊
        shape: { y: -totalH / 2 },
      });
      graphic.push({
        type: 'text',
        left: 'center',
        top: 'middle',
        silent: true,
        z: 0,
        style: {
          text: text,
          fontSize: fontSize,
          fontWeight: 600,
          fill: color,
          opacity: opacity,
          textAlign: 'center',
          textVerticalAlign: 'middle',
        },
        // 下移到 LOGO 之下
        shape: { y: (logoSize / 2 + verticalGap) },
      });
    } else {
      // 純文字版
      graphic.push({
        type: 'text',
        left: 'center',
        top: 'middle',
        silent: true,
        z: 0,
        style: {
          text: text,
          fontSize: fontSize,
          fontWeight: 600,
          fill: color,
          opacity: opacity,
          textAlign: 'center',
          textVerticalAlign: 'middle',
        },
      });
    }

    echartsInstance.setOption({ graphic: graphic }, false);  // false = merge
  }

  // 從 page-level 全域變數讀預設(由 Jinja 注入)
  // window.ETF_WATCH_LOGO_URL = "{{ logo_url }}" (or null)
  function applyDefaultWatermark(echartsInstance, extraOpts) {
    var opts = extraOpts || {};
    if (typeof root.ETF_WATCH_LOGO_URL === 'string' && root.ETF_WATCH_LOGO_URL) {
      opts.logoUrl = opts.logoUrl || root.ETF_WATCH_LOGO_URL;
    }
    applyWatermark(echartsInstance, opts);
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { applyWatermark: applyWatermark, applyDefaultWatermark: applyDefaultWatermark };
  } else {
    root.applyWatermark = applyWatermark;
    root.applyDefaultWatermark = applyDefaultWatermark;
  }
})(typeof window !== 'undefined' ? window : this);
