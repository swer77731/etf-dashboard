/**
 * ETF Watch chart watermark — Step 3 ECharts 圖表浮水印 helper.
 *
 * Usage:
 *   const chart = echarts.init(el);
 *   chart.setOption({...your options...});
 *   applyWatermark(chart);   // 在 setOption 之後呼叫
 *
 * 浮水印放在圖表中央,opacity 0.05、字級 60px、白字、不影響互動。
 */
(function (root) {
  function applyWatermark(echartsInstance, opts) {
    if (!echartsInstance || typeof echartsInstance.setOption !== 'function') {
      return;
    }
    var text = (opts && opts.text) || 'ETF Watch';
    var fontSize = (opts && opts.fontSize) || 60;
    var color = (opts && opts.color) || '#ffffff';
    var opacity = (opts && opts.opacity != null) ? opts.opacity : 0.05;

    echartsInstance.setOption({
      graphic: [
        {
          type: 'text',
          left: 'center',
          top: 'middle',
          silent: true,           // 不擋滑鼠
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
        },
      ],
    }, false);  // false = merge,不覆蓋原有 option
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { applyWatermark: applyWatermark };
  } else {
    root.applyWatermark = applyWatermark;
  }
})(typeof window !== 'undefined' ? window : this);
