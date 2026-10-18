# T01163 设计 logo 及多尺寸图标 —— 处理结果

> 处理时间：2026-09-27 ｜ 交付根目录：`D:/code/otherProjects/28_AIMsg/brand/`
> 图形生成：Agnes Image 2.5 Flash（文生图三稿筛选）→ 程序化去背/重采样/ICO-ICNS 打包 → 手写矢量重绘

## 根因

项目此前**没有任何品牌标识资产**：

- `android-app/app/src/main/AndroidManifest.xml` 未声明 `android:icon`，安装后是系统默认绿机器人图标；`res/` 下也没有 `mipmap-*`（只有 drawable/、layout/、values/、xml/）。
- `desktop-app/package.json` 无图标配置，`main.js` 创建 `BrowserWindow` 时未传 `icon`，任务栏为 Electron 默认图标。
- 安装包（exe/APK）、界面展示、托盘、应用商店均无可用的统一标识。

任务要求：具备专业视觉识别度、兼容 16px–512px，提供矢量源 + 多尺寸位图 + ICO + 单色变体 + 完整规范说明。

## 影响范围

- 新增 `brand/`（42 个交付文件 + 规范文档 + 可重跑脚本），**不改动任何既有业务代码**。
- 改动 1 处代码：`desktop-app/main.js`（新增 `icon` 一行，标题栏/任务栏图标）。
- 新增 2 个资源拷贝：`desktop-app/assets/emoreply.ico`、`emoreply.icns`（供后续打包使用）。
- **一致性修正**：T01162 已产出的 `brand/social-preview.png`（GitHub OG 图）原为紫系旧候选（cand-a），与新定稿主标不一致 → 已重跑 `tools/gen_social_preview.py` 切到定稿稿，OG 图与主标统一为深蓝/青（1280×640 校验通过）；旧版备份在 `brand/_source/backup/social-preview-prev-cand-a-20260927.png`。
- ⚠️ **踩坑**：`agnes-image-2.5-flash/generate.py` 的文件名由 prompt 前 30 字符 slug 生成，本次 `filename_timestamp: false` → 三次生成互相覆盖，且**跨会话覆盖了 T01162 已生成的历史候选稿**。已把候选稿规范化重命名为 `cand-1-violet / cand-2-magenta / cand-3-navy-teal`，并在 `gen_social_preview.py` 顶部写死映射与警示。
- Android / iOS 端**未接入**（需编译验证，已另立单 T01164）。

## 修复（交付物与改动点）

### 1. 定稿图形

超椭圆徽标（深蓝 `#08275E` → 青 `#21AFAF` 竖向渐变）+ 白色对话气泡（尾巴在左下）+ 气泡内浅蓝爱心 `#8DB9D5` + 右上淡青星光 `#C5FEF5`。
语义：气泡＝聊天场景，爱心＝高情商/共情（产品差异点），星光＝AI 生成。三稿（双气泡紫粉 / 双气泡靛品红 / 气泡爱心）中选取第三稿——元素最少、语义最准、深底浅图在小尺寸对比度最高；候选稿保留在 `_source/` 可随时换稿重跑。

### 2. 文件清单（绝对路径 `D:/code/otherProjects/28_AIMsg/brand/`）

| 类别 | 文件 |
| --- | --- |
| 矢量 | `logo/emoreply-logo.svg`（主标 1024 画布）、`logo/emoreply-logo-mono.svg`（单色 #08275E 实心镂空）、`logo/emoreply-logo-inverse.svg`（反白，深底用）、`logo/emoreply-lockup.svg`（图形 + EmoReply 字标，分发前转曲） |
| 位图 | `png/emoreply-{16,32,48,64,128,256,512,1024}.png`（透明背景，Lanczos 重采样） |
| Windows | `ico/emoreply.ico`（16/24/32/48/64/128/256 七档 PNG 条目） |
| macOS | `icns/emoreply.icns`（ic07/ic08/ic09/ic10） |
| 单色/反白 | `mono/emoreply-{mono,inverse}-{32,64,128,256,512}.png` |
| Android | `android/mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/ic_launcher.png`、`ic_launcher_round.png`、`ic_launcher_foreground.png`（自适应前景，白图形+爱心镂空）、`ic_launcher_background.png`（渐变），`android/store/ic_launcher-playstore.png`（512） |
| 预览 | `preview/preview-sizes.png`（16–256px 浅/深底实渲染 + 单色/反白）、`preview/preview-scenes.png`、`preview/emoreply-lockup.png`、`emoreply-lockup-white.png` |
| 规范 | `brand/README.md`（色彩 HEX/RGB/CMYK、字体、网格安全区、最小尺寸、场景建议、禁用示例、原创性与商标提示） |
| 脚本 | `brand/_tools/build_assets.py`（一键重建全套）、`brand/_tools/verify_svg.py`（SVG 子集光栅化校验） |

### 3. 代码改动

- `D:/code/otherProjects/28_AIMsg/desktop-app/main.js`：`new BrowserWindow` 增加 `icon: path.join(__dirname, 'assets', 'emoreply.ico')`（第 23 行）。
- `D:/code/otherProjects/28_AIMsg/desktop-app/assets/`：新增 `emoreply.ico`（100 KB）、`emoreply.icns`（645 KB）。

## 验证（实测证据）

1. **去背正确性**：母版 `brand/_source/master-1024.png` 四角 alpha=0.0、中心 alpha=1.0、图形覆盖率 0.724（对应 900/1024 徽标+安全边距），bbox x61–961 / y61–959。
2. **尺寸族完整性**：脚本断言 42 个文件全部存在且尺寸精确（PNG 16–1024、mono 32–512、Android 48/72/96/144/192、商店 512），**0 问题**。
3. **ICO 目录**：解析头部得到 7 个条目，尺寸序列 `[16,24,32,48,64,128,256]`，文件 100,484 字节。
4. **ICNS**：`ic07/ic08/ic09/ic10` 四档 PNG 条目，660,258 字节。
5. **矢量几何一致性**：`_tools/verify_svg.py` 解析 SVG path/ellipse（含 translate/scale、圆弧与三次贝塞尔展平）后光栅化，与位图母版做 40×40 ASCII 构图比对——徽标轮廓、气泡范围（y250–706）、爱心位置、右上星光、左下气泡尾**逐行对齐**；SVG XML 4 个文件均通过 `ElementTree` 解析。
6. **单色版与位图版一致**：修正前 SVG 把爱心一并镂空、位图保留实心爱心，已统一为「气泡/星光镂空 + 爱心实心」（小尺寸可辨性更好），并用 32×32 ASCII alpha 复核。
7. **桌面端语法**：`node --check main.js` 通过（本环境无法启动 GUI 窗口，未做界面实测）。

⚠️ 本环境无法目视渲染（`Read` 不支持图片预览），视觉校验以像素采样 + ASCII 还原为准；**审美终审需人工过目**：
- 打开 `brand/preview/preview-sizes.png` 看 16–256px 实渲染与单色/反白；
- 打开 `brand/png/emoreply-512.png` 与 `brand/preview/preview-scenes.png` 确认主标与应用效果；
- 若不满意可换稿：改 `_tools/build_assets.py` 顶部 `SRC` 指向 `_source/cand_*.png`（A/B 稿）后重跑脚本，全套资产自动重建。

## 本次回写

- 新增目录 `D:/code/otherProjects/28_AIMsg/brand/`（含 `README.md` 设计规范）。
- 新增 `D:/code/otherProjects/28_AIMsg/docs/t01163-logo-result.md`（本文档）。
- 新增 `D:/code/otherProjects/28_AIMsg/desktop-app/assets/{emoreply.ico,emoreply.icns}`；修改 `desktop-app/main.js`（+1 行 icon 配置）。
- 修改 `D:/code/otherProjects/28_AIMsg/tools/gen_social_preview.py`（CANDS 映射改为规范化文件名，默认输出切到定稿稿）；重生成 `brand/social-preview*.png`。
- 新建后续单 **T01164**（派生自 T01163）：品牌图标接入 Android/iOS 与安装包打包验证。

## 下一步建议（可选）

1. 视觉终审 → 若需微调（如星光位置、爱心比例），改 `logo/*.svg` 参数 + 脚本常量重跑，位图族自动同步。
2. 商标与著作权：建议做第 9/42 类商标检索后再对外正式发布。
3. 安装包打包配置（electron-builder / NSIS 的 `build.win.icon`、`build.mac.icon`）与 Android/iOS 接入已另立单处理。
