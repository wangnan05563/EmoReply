# T01531 PC 端全局图标按钮改造方案（无文字 · 无边框 · 大图标 + 悬浮倾斜 + 文字提示）

> 交付物：全局图标按钮组件（样式定义 + 倾斜动画逻辑 + Tooltip 集成）+ 本替换清单。
> 验证截图：`desktop-app/docs/screenshots/t01531-01-default.png` / `t01531-02-hover-tilt-tip.png` / `t01531-03-all-sections.png`
> 回归探针：`desktop-app/tmp/t01531-probe.js`（CDP，10 项断言全过）；全量 UI E2E 51/51 通过。

## 技术选型说明

项目为 Electron + 原生 HTML/CSS/JS 悬浮窗（无构建链、CSP `default-src 'self'`）。引入 Ant Design / Element Plus / Framer Motion 等需要打包器与运行时依赖，会破坏现有「零构建 loadFile」架构。故采用**同等开源技术栈自研**：

- 图标：内联 SVG symbol 精灵（lucide 风格手绘描边，24 网格 / 1.8 描边 / currentColor），MIT 同源风格，零外部请求
- 倾斜（Tilt）：CSS 3D transform（perspective + rotateX/rotateY），JS 按鼠标在元素内坐标计算角度，requestAnimationFrame 合帧防卡顿
- 提示（Tooltip）：自研轻量组件（`data-tip` 属性 + fixed 定位浮层，300ms 延迟，淡入淡出）

## 组件规格

| 规格项 | 取值 |
| --- | --- |
| 主按钮尺寸 | 34×34px（`button.ibtn`；返工自 44px 缩小，用户反馈图标过大） |
| 行内小按钮 | 26×26px（`button.ibtn.sm`：候选三动作/删除/历史动作/向导；返工自 30px） |
| 图标尺寸 | 18px / 14px（随按钮档位；返工自 22/16） |
| 边框 | 全部去除（含快捷指令胶囊原 1px 边框；色板选中环为状态指示保留） |
| 底色块 | 无（返工：透明底 + 无阴影，仅图标着色；hover 淡色底 `--c-accent-soft` 提供可点反馈——原色块/阴影被反馈看起来像边框） |
| 圆角 | 9px（小按钮 7px） |
| 悬停 | 淡色底 + scale 1.05 + 3D 倾斜（最大 ±9°，随鼠标位置分布） |
| 按下 | scale 0.94 回弹 |
| 忙碌态 | 图标隐藏 + 白色转圈伪元素（替代原「生成中…」文案切换） |
| Tooltip | 「名称｜功能说明」，300ms 延迟，上方优先、越界翻转/夹取，深色主题反色 |
| 减弱动效 | disabled 按钮不参与倾斜；窗口失焦自动复位 |

## 按钮替换清单（名称 → 图标 → 提示文案）

### 静态按钮（index.html）

| 按钮 | 图标 | 提示文案 |
| --- | --- | --- |
| 读剪贴板 btnRead | clipboard | 读剪贴板｜读取剪贴板中的对方消息原文填入消息区 |
| 生成回复 btnGen | sparkles | 生成回复｜按所选语气生成候选回复（多模型套件时并行对比） |
| 美化剪贴板 btnBeautify | wand | 美化剪贴板｜AI 润色剪贴板原文并写回剪贴板 |
| 截图取文 btnOcr | camera | 截图取文｜全屏截图 OCR 识别对方消息（离线识别） |
| 撤回 btnUndo | undo | 撤回｜恢复被本应用覆盖的剪贴板内容（多层栈） |
| 保存条目 btnKbSave | save | 保存条目｜将下方知识存入本机加密知识库 |
| 清空知识库 btnKbWipe | trash | 清空知识库｜删除全部知识条目（不可恢复） |
| 保存设置 btnSaveCfg | save | 保存设置｜将接口/模型/密钥等配置写入本机 |
| 检查当前配置 btnCheck | zap | 检查当前配置｜向已配置接口发送测试请求验证连通 |
| 学习语气 btnToneLearn | ear | 学习语气｜从粘贴的聊天记录提取惯用句式注入知识库 |
| 添加 Ollama btnAddOllama | plus | 添加 Ollama｜把本机 Ollama 离线模型套件加入多模型配置 |
| 检查 Ollama 连通 btnCheckOllama | activity | 检查 Ollama 连通｜探测本机 11434 服务并列出可用模型 |
| 刷新 btnHistoryRefresh | refresh | 刷新｜重新加载本机加密历史列表 |
| 清空历史 btnHistoryClear | trash | 清空历史｜删除本机存储的全部历史（不可恢复） |
| 稍后再说 wzSkip | arrow-right | 稍后再说｜跳过向导，之后可在「设置」里配置 |
| 保存并测试连接 wzTest | check | 保存并测试连接｜保存密钥与模型，并发送一句测试请求验证连通 |
| 最小化 winMin（T01530） | 内联横线 | 最小化｜窗口收进任务栏，托盘单击或 Alt+Shift+E 唤回 |

### 动态按钮（renderer.js 生成）

| 按钮 | 图标 | 提示文案 |
| --- | --- | --- |
| 候选·复制 .act | copy | 复制｜复制候选文本到剪贴板（自行粘贴发送） |
| 候选·回填 .act | paste-in | 回填｜隐藏本窗并粘贴到原窗口（自行发送） |
| 候选·换一条 .act | refresh | 换一条｜重新生成，避开已给候选 |
| 知识库·删除 .del | trash | 删除条目｜从知识库移除这条知识（不可恢复） |
| 历史·复制候选N | copy（成功短暂换 check） | 复制候选N｜候选正文前 40 字 |
| 历史·回填候选1 | paste-in | 回填候选1｜隐藏本窗并粘贴到原窗口（自行发送） |

### 保留文字 + 统一接入倾斜/提示（例外口径，已在代码注释注明）

| 控件 | 理由 |
| --- | --- |
| 快捷指令胶囊 .cmd | 指令名是用户自定义数据（文字即内容），图标化会丢失语义；已去边框并接入 Tilt+Tooltip（提示=名称+提示词摘要） |
| 强调色色板 .sw | 色块本身即图形指示；选中环为状态指示保留；接入 Tooltip（色名｜切换强调色） |

## 测试资产同步

- `scripts/ui-e2e.js` 4 处按文字匹配的选择器改为 `data-tip` 优先（换一条/复制/删除/点击记录），全量回归 51/51 PASS
- 新增 `tmp/t01531-probe.js`：DOM 结构断言（全按钮有 data-tip、功能按钮有图标、无可见边框、主按钮 34px 档、扁平无底色块）+ Tilt/Tooltip 行为断言 + 截图，含窗口可见性守卫（防后台节流挂起）
