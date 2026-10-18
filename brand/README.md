# EmoReply 品牌标识规范（Logo & Icon System）

> 版本 v1.0 ｜ 2026-09-27 ｜ 适用：EmoReply 高情商聊天回复助手（Android 输入法 / 桌面端 / iOS）
> 定稿图形：超椭圆徽标 + 对话气泡（左下尾）+ 气泡内爱心 + 右上星光

---

## 1. 标志语义

| 元素 | 语义 | 说明 |
| --- | --- | --- |
| 超椭圆徽标（深蓝 → 青渐变） | 产品容器 / 信任感 | 深蓝表达稳重与可信（对话内容敏感），青色表达轻盈与智能 |
| 白色对话气泡（左下尾） | 聊天场景 | 尾巴在左下，方向明确，小尺寸仍可辨 |
| 浅蓝爱心（气泡内） | 高情商 / 共情 | 产品核心差异点：不是「代写」，是「懂你地回复」 |
| 淡青星光（右上） | AI 生成 | 唯一指向 AI 的视觉元素，位置固定在右上，不与爱心抢焦点 |

## 2. 定稿说明（三稿筛选）

| 稿 | 方向 | 配色 | 结论 |
| --- | --- | --- | --- |
| A | 双气泡对话 | 靛蓝 → 品红 | 备选，暖色偏社交，与「助手」调性偏离 |
| B | 双气泡 + 星光 | 紫 → 品红 | 备选，小尺寸下两气泡边界易糊 |
| **C（定稿）** | 气泡 + 爱心 + 星光 | **深蓝 #08275E → 青 #21AFAF** | ✅ 元素最少、语义最准、深底浅图在小尺寸对比度最高 |

三稿原图保留在 `_source/`，换稿只需修改 `_tools/build_assets.py` 顶部 `SRC` 一行后重跑脚本。

## 3. 色彩规范

### 3.1 主色（屏幕 / RGB-HEX）

| 用途 | HEX | RGB | CMYK（未校准，印刷前打样） |
| --- | --- | --- | --- |
| 品牌深蓝（渐变起点 / 单色版） | `#08275E` | 8, 39, 94 | 91 / 59 / 0 / 63 |
| 渐变中段 | `#10517A` | 16, 81, 122 | 87 / 34 / 0 / 52 |
| 品牌青（渐变终点） | `#21AFAF` | 33, 175, 175 | 81 / 0 / 0 / 31 |
| 爱心浅蓝（辅助） | `#8DB9D5` | 141, 185, 213 | 34 / 13 / 0 / 16 |
| 星光淡青（辅助） | `#C5FEF5` | 197, 254, 245 | 22 / 0 / 4 / 0 |
| 气泡白 | `#FFFFFF` | 255, 255, 255 | 0 / 0 / 0 / 0 |
| 字标墨色（正文/标题） | `#10151C` | 16, 21, 28 | 43 / 25 / 0 / 89 |

- 渐变方向：**垂直**，自 `#08275E`（上）→ `#10517A`（55%）→ `#21AFAF`（下）。
- 单色场景（印章、单色印刷、雕刻、深色 UI 反白）：统一使用 `#08275E` 或纯白，**禁止**输出彩色渐变。
- 无障碍：白图形与徽标底对比度 ≥ 8:1（512px 母版实测），满足 WCAG AA 图形对比要求。

### 3.2 字体建议（仅组合标使用）

- 字标西文：**Segoe UI Semibold / Semibold 600**（备选 Inter SemiBold、Helvetica Neue Medium）
- 中文场景：**PingFang SC / Microsoft YaHei / 思源黑体 Medium**
- 字距 `-2`（@1024 画布，字号 150），字标与图形间距 = 图形宽度 × 0.28
- **分发前务必将 `text` 转曲**（`emoreply-lockup.svg` 中为可编辑文本，避免字体缺失导致回退）

## 4. 网格与安全区

| 项目 | 数值（1024 画布） |
| --- | --- |
| 画布 | 1024 × 1024 |
| 徽标边长 | 900（左右上下各留 62，位图母版按 88% 填充） |
| 圆角半径 | 250（= 边长 27.8%，超椭圆观感） |
| 气泡主体 | 椭圆 cx 500 / cy 478 / rx 296 / ry 228 |
| 爱心 | 中心 (545, 552)，288 × 271（原始路径 ×1.5） |
| 星光 | 中心 (653, 294)，直径 154 |
| **安全区（Clear Space）** | 徽标四周 ≥ 112（≈ 徽标边长 1/8），区域内不得放置文字或其他图形 |

## 5. 最小使用尺寸

| 场景 | 最小尺寸 | 说明 |
| --- | --- | --- |
| 屏幕显示（常规） | **24 px** | 低于 24px 时爱心内部会糊，建议切换 `mono` 简化版 |
| 托盘 / ICO 强制 16px | 16 px | 仅用于系统托盘、exe 内嵌等不可选尺寸场景 |
| 印刷 | **8 mm** | 低于此值请使用单色版 |
| 应用商店 / 安装包封面 | 512 px | 直接取 `png/emoreply-512.png` |

## 6. 交付清单

```
brand/
├── logo/                                   # 矢量（可缩放，修改/印刷首选）
│   ├── emoreply-logo.svg                   # 主标（彩色，1024 画布）
│   ├── emoreply-logo-mono.svg              # 单色版（实心徽标 + 气泡/星光镂空，爱心保留实心，#08275E）
│   ├── emoreply-logo-inverse.svg           # 反白版（白色图形，深色底用）
│   └── emoreply-lockup.svg                 # 横向组合标（图形 + EmoReply 字标）
├── png/emoreply-{16,32,48,64,128,256,512,1024}.png   # 透明背景位图
├── ico/emoreply.ico                        # Windows：16/24/32/48/64/128/256 七档内嵌
├── icns/emoreply.icns                      # macOS：ic07/ic08/ic09/ic10（PNG 条目）
├── mono/emoreply-{mono,inverse}-{32,64,128,256,512}.png
├── android/
│   ├── mipmap-{mdpi..xxxhdpi}/ic_launcher.png            # 方形（48→192，API<26 兜底）
│   ├── mipmap-{mdpi..xxxhdpi}/ic_launcher_round.png      # 圆形
│   ├── drawable-{mdpi..xxxhdpi}/ic_launcher_foreground.png # 自适应前景（108dp 画布，图形占 62%）
│   ├── drawable-{mdpi..xxxhdpi}/ic_launcher_background.png # 自适应背景（108dp 满幅渐变）
│   └── store/ic_launcher-playstore.png     # 512，Google Play 用
├── ios/AppIcon.appiconset/                 # Xcode 直接拖入即可（15 张 + Contents.json，无 alpha 满幅版）
├── favicon/                                # favicon.ico（16/32/48）、apple-touch-icon 180、PWA 192/512
├── preview/                                # 应用场景预览
│   ├── preview-sizes.png                   # 16–256px 浅/深底实际渲染 + 单色/反白
│   ├── preview-scenes.png                  # 桌面/任务栏/安装包封面示意
│   ├── emoreply-lockup.png                 # 组合标（深蓝字）
│   └── emoreply-lockup-white.png           # 组合标（白色字，深色底）
├── social-preview.png                      # GitHub OG 图（1280×640，与定稿主标同源）
├── social-preview-cand-{a,b,c}.png         # 三稿对应预览（默认输出 = cand-c = 定稿）
├── _source/                                # AI 生成候选稿（cand-1/2/3）+ 去背母版 master-1024.png
│   └── backup/social-preview-prev-cand-a-20260927.png   # 旧版紫系 OG 图备份
└── _tools/build_assets.py                  # 一键重建（换稿/改色只需改顶部常量）
```

### 与社交预览图的一致性

`tools/gen_social_preview.py`（T01162）以 `_source` 候选稿为输入生成 `brand/social-preview.png`。
T01163 定稿 cand-3 后已重跑该脚本，**仓库 OG 图与主标统一为深蓝/青**；旧版紫系 OG 图备份在
`_source/backup/social-preview-prev-cand-a-20260927.png`，需要回滚直接拷回即可。

## 7. 使用场景建议

| 场景 | 推荐文件 |
| --- | --- |
| Windows 可执行文件 / 安装包 | `ico/emoreply.ico` |
| macOS App / Electron 打包 | `icns/emoreply.icns` |
| 桌面快捷方式 / 任务栏 / 系统托盘 | `ico/emoreply.ico`（托盘建议 `mono/emoreply-mono-32.png`） |
| Android 启动器（Android 8+ 自适应） | `android/mipmap-*/ic_launcher_foreground.png` + `_background.png` |
| Android 旧版 / 通知小图标 | `android/mipmap-*/ic_launcher.png`（通知请用 `mono/emoreply-inverse-*.png`） |
| Google Play 商店 | `android/store/ic_launcher-playstore.png` |
| 网站 favicon / PWA | `png/emoreply-64.png`（建议另导出 favicon.ico） |
| 深色模式启动页 / 深色 UI | `logo/emoreply-logo-inverse.svg` 或 `mono/emoreply-inverse-512.png` |
| 单色印刷 / 信封 / 名片烫印 | `logo/emoreply-logo-mono.svg` |

### 接入方式（两端）

**桌面端（Electron）**——`desktop-app/assets/emoreply.ico`（已放置），`main.js` 中：

```js
win = new BrowserWindow({
  icon: path.join(__dirname, 'assets', 'emoreply.ico'),   // 标题栏 + 任务栏
  ...
});
```

打包成安装包时（electron-builder / NSIS）在 build 配置中再指向同一 ico：

```json
"build": { "win": { "icon": "assets/emoreply.ico" }, "mac": { "icon": "assets/emoreply.icns" } }
```

**Android（T01164 已接入）**——文件已落到 `android-app/app/src/main/res/`：

- `mipmap-{mdpi..xxxhdpi}/ic_launcher.png`、`ic_launcher_round.png`（方形/圆形兜底）
- `drawable-{mdpi..xxxhdpi}/ic_launcher_foreground.png`（108dp 画布、图形 62%）、`ic_launcher_background.png`（满幅渐变）
- `mipmap-anydpi-v26/ic_launcher.xml`、`ic_launcher_round.xml`（`<adaptive-icon>` 组合上述图层）
- `AndroidManifest.xml` 的 `<application>` 增加 `android:icon="@mipmap/ic_launcher"`、`android:roundIcon="@mipmap/ic_launcher_round"`

> 通知小图标（若后续加通知）：系统会对小图标做单色化，请用 `mono/emoreply-inverse-*.png`（白图形），彩色图会被压成灰块。

**iOS（资产已预置，待 Xcode App 工程）**——`brand/ios/AppIcon.appiconset/`（15 张 PNG + `Contents.json`）为**无 alpha 满幅版**（iOS 会再套圆角遮罩，含透明边的图标会被压出不透明底色）。当前 `ios-app` 仅是 SwiftPM 核心包（无 App target），建 Xcode App 工程时把该目录拖入 `Assets.xcassets` 即可。

## 8. 禁用示例（Do / Don't）

| ❌ 禁止 | ✅ 正确 |
| --- | --- |
| 非等比拉伸、压扁/拉长徽标 | 仅等比缩放，锁定长宽比 |
| 改为规范外配色（如红/橙渐变）、低对比浅底放白图形 | 使用 3.1 规范色；浅底用彩色版，深底用反白版 |
| 旋转、倾斜、加 3D 透视 | 保持正置 |
| 自行添加描边、外发光、投影、滤镜 | 使用原图，投影仅由系统容器提供 |
| 把徽标裁切进圆形/异形容器（会切掉气泡尾） | 整标入容器；Android 圆形图标请用 `ic_launcher_round` |
| 在徽标内部叠加文字、角标、版本号 | 文字放在安全区之外 |
| 单色印刷时使用彩色渐变 | 使用 `emoreply-logo-mono.svg` |
| 低于最小尺寸使用（<24px 屏幕 / <8mm 印刷） | 换用小尺寸专用 mono 位图 |
| 修改爱心/星光相对位置与比例 | 保持网格参数（第 4 节）不变 |

## 9. 版权与原创性

- 图形为原创设计（AI 辅助生成 + 矢量化重绘），由「通用几何形态（气泡 / 爱心 / 四角星）」组合构成，**不含任何文字、不含第三方标识要素**，与现有知名聊天/AI 产品标识在构成与配色上可区分。
- 建议后续补充：商标检索（第 9/42 类）与著作权登记，登记前请勿对外正式发布。

## 10. 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-09-27 | 首版：三稿筛选定稿 C，输出矢量 / 位图 / ICO / ICNS / 单色 / Android / 预览全套资产与规范 |
| v1.1 | 2026-10-10 | 社交预览图重生成（T01827）：渐变改为小图插值 + BICUBIC 放大消除水平条带，光晕改 L 蒙版合成；旧图备份 `_source/backup/social-preview-prev-20261010.png` |
