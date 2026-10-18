# [PRD] 4 M4二期扩展 —— RAG 端侧接线：桌面端 JS 移植（收尾）

## 本阶段交付（接上轮 Android 阶段；至此三端 KB 协议同构完成）

1. **`desktop-app/kb.js`（新增，与 m1-poc rag.py / Android KnowledgeBase.kt 同构）**：
   - `KnowledgeBase`：AES-256-GCM 加密单文件 JSON（存 userData/kb.json.enc；密钥=SHA256(platform-hostname‖可选口令)，端侧绑定）；三类条目 persona/relation/platform，relation 强制绑联系人；增删查 + wipe 联动 FR-6.3；口令不匹配/损坏 → 初始态不崩溃。
   - `LexicalRetriever`：CJK bigram 词面检索（单字兜底），同联系人加权 0.15 次级信号，无重叠且非当前联系人不返回（防噪声），top3 注入——与 Python/Kotlin 打分口径逐行一致。
   - 加解密原语导出（deriveKey/encrypt/decrypt），密文格式 `iv.ciphertext` hex 两段，**与 m1-poc crypto.py 密文互通**。
2. **main.js**：KB IPC handlers（kb:add/remove/list/count/wipe）；`llm:generate` 生成前检索 top3 注入 system「参考资料（…不得据此编造对方未提及的事实）」约束行（compose.py 同款格式），无命中时行为与既有完全一致；检索异常不阻断生成。
3. **preload.js / index.html / renderer.js**：新增「RAG 知识库」管理区——分类下拉（个人事实/关系话术/平台礼仪）+ 联系人（仅关系话术显示）+ 内容输入 + 保存/删除/清空（confirm 防误触）+ 条目计数；主卡片增「当前联系人」可选输入（生成时用于关系话术加权）。

## 验证（实测证据）

- `node --check` 四文件（kb/main/preload/renderer）语法全过 ✅
- kb.js 冒烟测试 10 组断言全过 ✅（`desktop-app/kb.smoke.js` 保留为可重复运行）：增删查持久化重读、非法分类/空文本拒绝、口令错误降级初始态、词面命中、联系人加权命中、无关不注入、bigrams 单字兜底与标点过滤、top3 上限、remove/wipe 持久化
- **跨端密文互通**：Node 加密 → m1-poc crypto.py 解密 ✅；Python 加密 → Node 解密 ✅（AES-256-GCM authTag 拼接口径对齐）
- Electron 启动冒烟 ✅（主进程挂满 8s 被 timeout 强杀，无启动报错；GPU/network 错误为 SIGTERM 噪音）
- m1-poc 全量回归 **79 passed** ✅（本轮未动 Python 代码）

## 未实测项（verified:false，需人工抽查）

- GUI 交互链路（知识条目增删 UI、生成注入实际效果）：`npm start` → 知识库区添加「老板周报口径以部门模板为准」（关系话术，联系人填「老板」）→ 主卡片「当前联系人」填「老板」→ 复制含「周报」消息 → 生成，观察候选是否贴合该口径（未配 Key 时走本地模板，注入仅在云端路径生效）。

## 剩余工作（本单保持 todo）

1. FR-1.6 语义指向判定：需标注测试集；
2. 邮件协议接入：待授权方案拍板；
3. 各端真机/真环境人工抽查（Android 真机、桌面 GUI 交互、iOS swift test + Xcode）。

## 本次回写

无新增单。
