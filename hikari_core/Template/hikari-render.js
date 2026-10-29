/*!
 * hikari-render.js —— 浏览器端模板渲染器（Nunjucks）
 *
 * ▍它解决什么
 *   把「Jinja 在 Python 里渲染」换成「Nunjucks 在浏览器里渲染」，让**成品截图**和
 *   **本地预览/编辑器**跑同一份模板、同一条渲染路径 —— 这样「预览即成品」不再靠
 *   两份实现手工对齐来维持。
 *
 * ▍怎么用（Python 侧见 hikari_core/core/js_render.py 的 render_shell）
 *   页面里放三样东西，本脚本会自己找：
 *     1. <script src="…/nunjucks.min.js">                      ← 必须先加载
 *     2. <script id="hikari-payload" type="application/json">  ← 内联载荷（模板源码 + 数据）
 *     3. 最后调一次 window.HikariRender.boot()
 *
 * ▍为什么模板要走「内联 + 自定义 loader」而不是 fetch
 *   渲染产物是以「写临时文件 → 浏览器打开 file://」的方式加载的，而 **file:// 下
 *   fetch 本地文件会被 CORS 拦掉**（实测 Failed to fetch）。所以模板源码由 Python
 *   内联进页面，本脚本用内存 loader 交给 Nunjucks。
 *
 * ▍写法上的几个硬要求
 *   · autoescape 必须 false —— 服务端的 Jinja 环境本来就不开。（开了会把 URL 里的
 *     `&` 转成 `&amp;`，而 <style> 块内不解析字符引用 → 背景图/头像直接失效。）
 *   · 兼容层里的自定义函数必须**逐字**对齐 Python 版（如 ba_text_em 的字表），
 *     近似实现会以「文本全对但像素对不上」的形式暴露，很难查。
 *   · **别在模板里用 pathlib 的运算符**：`template_path / "x"` 在 Jinja 下永远为真
 *     （拼出来的是对象，不查文件在不在），在 JS 下则是「对象 / 字符串 = NaN」，恒假 ——
 *     条件是静默走错分支，页面上没有任何报错。要判断资源在不在，用 asset_exists()。
 *
 * ▍对外接口
 *   HikariRender.boot()                      载入后自动渲染并替换整个文档（生产路径）
 *   HikariRender.render(entry, data, opts)   只返回 HTML 字符串（测试 / 编辑器用）
 *   window.__hikari_render_done              true=渲染完成；出错时是 error 字符串
 */
(function (global) {
  'use strict';

  var PAYLOAD_ID = 'hikari-payload';

  /* ============================================================
     1. Python 语义兼容层
     ============================================================ */

  /* Python 的 `'%.Nf'` 用**四舍六入五成双**（round-half-even），JS 的 toFixed 是
     「.5 一律进位」（half away from zero）—— 实测差异：存活率 56.25 在 Python 里是
     `56.2`、在 JS 里是 `56.3`（单船列表那一格，逐像素只差 17×24 像素的一条）。
     只有**缩放后恰好是 .5** 时才需要特殊处理，其余情况两者一致。 */
  function pyToFixed(value, prec) {
    var n = Number(value);
    if (!isFinite(n)) return String(n);
    var scale = Math.pow(10, prec);
    var scaled = n * scale;
    var rounded;
    if (Math.abs(scaled - Math.round(scaled)) === 0.5) {
      var lower = Math.floor(scaled);                       /* 负数也统一往下取 */
      rounded = (Math.abs(lower) % 2 === 0) ? lower : lower + 1;
    } else {
      rounded = Math.round(scaled);
    }
    return (rounded / scale).toFixed(prec);
  }

  /* 千分位：把 pyToFixed 的结果按整数部分补逗号（Python 的 '{:,}' 系列）。 */
  function groupThousands(text) {
    var neg = text.charAt(0) === '-';
    if (neg) text = text.slice(1);
    var parts = text.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + parts.join('.');
  }

  /* Python 的 `'%.2f' | format(x)` —— 只实现模板里实际用到的转换符：
     %d %i %u %s %f %.Nf，以及 [flags][width] 里的 `-`（左对齐）与 `0`（补零）。 */
  function pyPercentFormat(fmt, args) {
    var i = 0;
    return String(fmt).replace(/%([-+ 0]*)(\d+)?(?:\.(\d+))?([diufFeEsS%])/g,
      function (m, flags, width, prec, conv) {
        if (conv === '%') return '%';
        var v = args[i++], s;
        if (conv === 'd' || conv === 'i' || conv === 'u') s = String(Math.trunc(Number(v) || 0));
        else if (conv === 'f' || conv === 'F') s = pyToFixed(v || 0, prec === undefined ? 6 : +prec);
        else s = String(v === undefined ? '' : v);
        if (width) {
          var w = +width;
          s = flags.indexOf('-') >= 0 ? s.padEnd(w)
            : (flags.indexOf('0') >= 0 ? s.padStart(w, '0') : s.padStart(w));
        }
        return s;
      });
  }

  /* Python 的 `'{}'.format()` 里模板只用到两种规格（实测 grep 全量）：`{:,}` 与 `{:,.0f}`。
     挂在 String.prototype 上是为了让模板里 `'{:,}'.format(x)` 这种**方法调用**原样可用
     —— 它不是过滤器，改不成 addFilter。
     ▍遇到没实现的规格必须**抛错**，不能静默回落到 String(v)：那会输出未格式化的原始
       浮点数（实测踩过：`{:.0f}` 漏实现 → 页面上出现 `2061.73` 而不是 `2,062`），
       而且不报错，只能靠逐像素比对才发现。 */
  Object.defineProperty(String.prototype, 'format', {
    value: function () {
      var a = arguments;
      return String(this).replace(/\{([^}]*)\}/g, function (m, spec) {
        var parts = spec.split(':'), ix = parts[0], fmt = parts[1];
        var v = a[ix === '' ? 0 : +ix];
        if (fmt === undefined) return String(v);
        /* `{:,}` 在 Python 里是「按 repr 打印 + 千分位」，**不**做四舍五入
           （'{:,}'.format(2061.73) == '2,061.73'）→ 这里就用原样字符串补逗号。
           带 `f` 的规格才走 pyToFixed（要的是 Python 的四舍六入五成双，不是 toLocaleString
           那套随实现/区域变的口径）。 */
        if (fmt === ',') return groupThousands(String(v));
        if (fmt === ',.0f') return groupThousands(pyToFixed(v, 0));
        if (fmt === ',.2f') return groupThousands(pyToFixed(v, 2));
        throw new Error('hikari-render: 未实现的 format 规格 "{' + fmt + '}" —— 补进 pyPercentFormat/String.format');
      });
    },
    enumerable: false, writable: true, configurable: true,
  });

  /* Jinja 的 `x | int` / `x | int(缺省)`：
       null / 空串 / 非整数字符串 → 缺省；float 按**截断**（Jinja 的 int(12.7)=12）。
     ▍注意 `| int` 与 `| int(缺省)` 不是一回事：裸 int 会把 null 与非数字串统统变 0，
       缺省不为 0 的字段（如模糊强度缺省 100）遇到一个 null 就会静默跑偏。 */
  function intOr(value, dflt) {
    if (value === null || value === undefined || value === '') return dflt;
    if (typeof value === 'string' && !/^\s*[+-]?\d+\s*$/.test(value)) return dflt;
    var n = Number(value);
    if (!isFinite(n)) return dflt;
    return n < 0 ? Math.ceil(n) : Math.floor(n);
  }

  /* dict.get(k, default) —— Jinja 支持，Nunjucks 没有。
     ▍**不能**给 Object.prototype 加 get 来兼容：property descriptor 会"继承"到这个
       get，JS 内部的 Object.defineProperty 会直接抛
       「Cannot both specify accessors and a value or writable attribute」，
       连 Nunjucks 自己的报错包装都会一起炸掉。所以模板里改写成了 dget(obj, k, d)。 */
  function dictGet(obj, key, dflt) {
    return (obj && obj[key] !== undefined) ? obj[key] : dflt;
  }

  /* Python 的 str.rsplit(sep, 1)：从右侧切一刀，返回 [head, tail]；
     找不到分隔符时 Python 返回 [整串]，行为要一致。 */
  function rsplit1(s, sep) {
    var text = String(s);
    var i = text.lastIndexOf(sep);
    return i < 0 ? [text] : [text.slice(0, i), text.slice(i + sep.length)];
  }

  function pad(n, w) { return String(n).padStart(w, '0'); }

  /* 模板里的 time 三件套。frozenTime 只给回归比对用（两侧冻同一时刻，
     否则「数据更新」那行永远差几秒、逐像素永远不为 0）。 */
  function makeTime(frozenTime) {
    return {
      strftime: function (fmt, t) {
        var d = new Date(t * 1000);
        return String(fmt)
          .replace(/%Y/g, d.getFullYear()).replace(/%m/g, pad(d.getMonth() + 1, 2))
          .replace(/%d/g, pad(d.getDate(), 2)).replace(/%H/g, pad(d.getHours(), 2))
          .replace(/%M/g, pad(d.getMinutes(), 2)).replace(/%S/g, pad(d.getSeconds(), 2));
      },
      /* Python 的 localtime 返回 struct_time；这里的 strftime 直接吃 epoch 秒，
         所以恒等即可。 */
      localtime: function (t) { return t; },
      time: function () { return (frozenTime === undefined || frozenTime === null) ? Date.now() / 1000 : frozenTime; },
    };
  }

  /* server_cn：服务器键名 / 别名 → 中文名，未知值原样返回（与 Python 的
     render_helpers.server_cn 逐字对齐：None → ''，其余按小写查表、查不到返回原文）。
     表由 Python 内联下来（模板目录与真值都只有一份）。 */
  function makeServerCn(table) {
    return function (value) {
      if (value === null || value === undefined) return '';
      var text = String(value);
      var key = text.toLowerCase();
      return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : text;
    };
  }

  /* ba_text_em：按字体实测字表估算一段文字的 advance 宽度（em）。
     ▍字表由 Python 侧内联下来（render_helpers._BA_EM），**不要在这边再抄一份**，
       也不要拿 `text.length` 近似 —— 实测近似值会让 BA logo 的宽度/字号跑偏，
       文本层面完全一致、但逐像素差 0.5%。 */
  function makeBaTextEm(table) {
    return function (text) {
      var s = 0, str = String(text || '');
      for (var i = 0; i < str.length; i++) {
        var em = table[str.charAt(i)];
        s += (em === undefined) ? 1.0 : em;
      }
      return s;
    };
  }

  /* ============================================================
     2. 模板 loader（源码内联在载荷里，不走网络）
     ============================================================ */
  function makeLoader(templates) {
    return {
      async: false,
      getSource: function (name) {
        if (Object.prototype.hasOwnProperty.call(templates, name)) {
          return { src: templates[name], path: name, noCache: true };
        }
        /* 漏内联就立刻报错，别让 Nunjucks 去猜 —— 报错信息要能直接定位到缺哪个文件 */
        throw new Error('hikari-render: 模板未内联 "' + name
          + '" —— 检查 Python 侧 collect_templates 是否顺着 from/import/include 收全了');
      },
    };
  }

  /* 「模板目录里有没有这个资源」—— 给 {% if asset_exists('echarts.js') %} 用。
     为什么要专门给个函数：模板里原来写的是 Jinja 的 pathlib 运算
         {% if template_path and template_path / "echarts.js" %}
     pathlib 的 `/` 拼出来永远是个**真值对象**（它不检查文件在不在），所以那行在
     Jinja 下的真实语义就是「template_path 存在」；而 JS 侧没有 pathlib，
     `template_path` 只是个 { as_uri } 对象，对象 / 字符串 = NaN = **恒假** ——
     迁移后这条判断会静默翻到 else（CDN）那一支，页面上不报错、只是 echarts 换源。
     文件在不在只有 Python 侧知道（载荷里的 assets 就是模板目录的顶层文件名清单），
     所以判断改成走这个函数。 */
  function makeAssetExists(names) {
    var set = {};
    (names || []).forEach(function (name) { set[String(name)] = true; });
    return function (name) { return set[String(name)] === true; };
  }

  /* ============================================================
     3. 环境构建 + 渲染
     ============================================================ */
  function buildEnv(payload, nunjucks) {
    if (!nunjucks) throw new Error('hikari-render: 没找到 nunjucks，检查前面的 <script src="…/nunjucks.min.js">');
    var env = new nunjucks.Environment(makeLoader(payload.templates), {
      /* 与服务端 Jinja 环境保持一致（那边也是不开的）。原因见文件头。 */
      autoescape: false,
    });

    /* ▍缺省值必须显式补 0：Jinja 的 `| int` / `int()` 在值是 null、未定义、非数字串时
       都回**缺省 0**，而 intOr 的缺省是 undefined（模板没写缺省参数时）→ 拿去做
       `'{:,}'.format(x | int)` 会印出 'undefined'。场均伤害那一格就是这么踩到的。 */
    function intWithDefault(value, dflt) {
      return intOr(value, dflt === undefined ? 0 : dflt);
    }
    env.addFilter('int', intWithDefault);
    env.addFilter('format', function (v) { return pyPercentFormat(String(v), [].slice.call(arguments, 1)); });
    env.addFilter('max', function (a) {
      return Array.isArray(a) ? a.reduce(function (x, y) { return y > x ? y : x; }) : a;
    });
    env.addFilter('min', function (a) {
      return Array.isArray(a) ? a.reduce(function (x, y) { return y < x ? y : x; }) : a;
    });

    env.addGlobal('int', intWithDefault);   /* 模板会把 int 当函数调：int(abs(created)) */
    env.addGlobal('abs', Math.abs);
    env.addGlobal('dget', dictGet);
    env.addGlobal('rsplit1', rsplit1);
    env.addGlobal('asset_exists', makeAssetExists(payload.assets));
    env.addGlobal('range', function (a, b) {
      var s = (b === undefined) ? 0 : a, t = (b === undefined) ? a : b, r = [];
      for (var i = s; i < t; i++) r.push(i);
      return r;
    });
    env.addGlobal('enumerate', function (a) { return a.map(function (v, i) { return [i, v]; }); });
    env.addGlobal('server_cn', makeServerCn(payload.serverCn || {}));
    env.addGlobal('ba_text_em', makeBaTextEm(payload.baEm || {}));
    env.addGlobal('time', makeTime(payload.frozenTime));

    return env;
  }

  /* 渲染成 HTML 字符串。opts.assetBase 决定模板里 template_path.as_uri() 拼出的前缀
     （CSS/JS/echarts 都靠它定位，必须是 file:// 绝对地址 —— 页面是从临时目录打开的）。
     opts.assets = 模板目录的顶层文件名清单，只给模板里的 asset_exists() 判断用。 */
  function render(entry, data, opts) {
    opts = opts || {};
    var payload = {
      entry: entry,
      templates: opts.templates || {},
      data: data,
      assetBase: opts.assetBase,
      assets: opts.assets,          /* 模板目录的顶层文件名清单，供 asset_exists() 判断 */
      baEm: opts.baEm,
      serverCn: opts.serverCn,
      frozenTime: opts.frozenTime,
    };
    var env = buildEnv(payload, opts.nunjucks || global.nunjucks);
    return env.render(entry, {
      template_path: { as_uri: function () { return opts.assetBase; } },
      data: data,
    });
  }

  /* ============================================================
     4. 生产入口：读内联载荷 → 渲染 → 替换整个文档
     ============================================================ */
  function readPayload() {
    var node = document.getElementById(PAYLOAD_ID);
    if (!node) throw new Error('hikari-render: 找不到 #' + PAYLOAD_ID + ' 载荷节点');
    var payload = JSON.parse(node.textContent);
    if (!payload.templates || !payload.entry) {
      throw new Error('hikari-render: 载荷缺少 templates / entry');
    }
    return payload;
  }

  function boot() {
    try {
      var payload = readPayload();
      var html = render(payload.entry, payload.data, {
        templates: payload.templates,
        assetBase: payload.assetBase,
        assets: payload.assets,
        baEm: payload.baEm,
        serverCn: payload.serverCn,
        frozenTime: payload.frozenTime,
      });
      /* 用 document.write 整体替换：模板渲染出来的本来就是一份完整 HTML 文档。
         document.open 会清空文档但**保留 window**，所以下面的完成标记不会丢；
         写进去的 <script>（echarts 等）照常执行。 */
      document.open();
      document.write(html);
      document.close();
      global.__hikari_render_done = true;
    } catch (err) {
      /* 渲染失败必须留下**看得见**的痕迹：
         · window.__hikari_render_done 给截图服务当信号（拿到 error 字符串就说明
           是模板报错，而不是"页面还在加载"）；
         · 同时把错误写进 DOM —— 这样出错时截出来的图不是一张白页，
           跑 --dump-dom 的守门脚本也能直接读到原因。 */
      var message = (err && err.message) ? err.message : String(err);
      global.__hikari_render_done = 'error: ' + message;
      try {
        document.documentElement.setAttribute('data-hikari-render', 'error');
        var box = document.createElement('pre');
        box.id = 'hikari-render-error';
        box.style.cssText = 'color:#c00;font:14px/1.5 monospace;white-space:pre-wrap;padding:16px';
        box.textContent = 'hikari-render 渲染失败：\n' + message;
        (document.body || document.documentElement).appendChild(box);
      } catch (e) { /* 忽略：DOM 不可用时至少 signal 已经写上了 */ }
      try { console.error(err); } catch (e) { /* 同上 */ }
    }
  }

  global.HikariRender = {
    boot: boot,
    render: render,
    /* 供守门测试/编辑器复用：单独构建环境（不碰 document） */
    buildEnv: buildEnv,
    version: '2026-09-18',
  };
}(typeof window !== 'undefined' ? window : this));
