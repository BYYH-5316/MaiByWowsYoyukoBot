// ==========================================================
// Hikari 模板体系 v6 — 全局脚本（全模板共用）
// 与 v5 差异：
// - 表格行样式（玻璃条/圆角）改由 CSS 内建，不再逐行 JS 叠加
// - 无海报背景时统一注入浅色主题覆盖（含 .recent-battle-data-title）
// ==========================================================

// 军团标签颜色修正：无有效颜色（#b3b3b3 默认灰）时改为黑色
const firstSpan = document.querySelector('.user-info span:first-child');

if (firstSpan) {
    const color = getComputedStyle(firstSpan).color;
    if (color === 'rgb(179, 179, 179)') {
        firstSpan.style.color = '#000000'; // 改为黑色
    }
}

// 头部信息列（.clan-user-server）已改为顶部对齐，见 avatar-v6.css。
// 原先「无签名时把 gap 撑到 40px」的补偿已移除：那会让无签名时的行距
// 与有签名时不一致，反而破坏「和有签名效果一样」的观感。

// ==========================================================
// 无海报背景时切换浅色主题（毛玻璃改为实色，保证文字可读）
// ==========================================================
// 注意：大背景图（poster）由宏挂在 `.main-content::before` 上，`.main-content`
// 元素自身的 background-image 恒为 none。只读元素自身会把「有海报」误判成
// 「没海报」，于是永久注入下面那套浅色主题 —— 其中 `.main-content{background:#FCFCFE}`
// 是不透明的，正好盖住 z-index:-1 的伪元素，海报就再也显示不出来。
// 所以必须连伪元素一起看，且用「有没有 url() 图层」来判定（::before 上的黑色
// 遮罩层是 linear-gradient，没海报时它也可能存在，不能只判 !== 'none'）。
const el = document.querySelector('.main-content');
const hasAnyBackground = !!el && [
    window.getComputedStyle(el).backgroundImage,
    window.getComputedStyle(el, '::before').backgroundImage
].some((bg) => !!bg && bg.indexOf('url(') !== -1);

if (!hasAnyBackground) {
    const styleElement = document.createElement('style');
    styleElement.textContent = `
        .main-content { background: #FCFCFE; }
        .one-background-color {
            background: #F5F3FA;
            backdrop-filter: none;
            -webkit-backdrop-filter: none;
            border: 1.5px solid #DCD5EE;
            box-shadow: 0 2px 10px rgba(80, 70, 120, 0.08);
        }
        .information-body {
            background: #FFFFFF;
            border-color: #E4DDF2;
            box-shadow: 0 2px 10px rgba(80, 70, 120, 0.06);
        }
        .information-col + .information-col { border-top-color: #EFEAF8; }
        .information-col:first-child,
        .information-col.header-col { background: #F5F3FA; }
        .information-col:not(:first-child):nth-child(even) { background: #FAF8FE; }
        .information-col-item { color: #3c3c4c; }
        .random-header {
            background: #F5F3FA;
            border-color: #DCD5EE;
            box-shadow: none;
            color: #4a4a5a;
        }
        .recent-battle-data-title {
            background: #F5F3FA;
            border: 1.5px solid #DCD5EE;
        }
        .chart-box {
            background: #FFFFFF;
            border-color: #E4DDF2;
        }
        .ship-data-col + .ship-data-col { border-top-color: #EFEAF8; }
        .ship-information-col { color: #606266; }
        .recnet-time { color: #6b7080; }
        .footer { color: #8a8f9c; }
        .footer p { text-shadow: none; }
    `;
    document.head.appendChild(styleElement);
}

