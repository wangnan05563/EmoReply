# T01099 · Windows TSF 输入法扩展原型（M4.4）

> 交付物：① TSF 客户端探针（已编译运行，全链路验证通过）② 完整 IME 工作量分解与 Go/No-Go 建议
> 位置：`desktop-app/tsf-proto/`（tsf_probe.cpp / build.bat / probe-out.txt）

---

## 一、原型验证结果（已实测）

`tsf_probe.exe`（MSVC 14.16 + Windows SDK 10.0.19041 编译，x64）运行输出：

```
[1] ITfThreadMgr CoCreate: 0x00000000        ← TSF COM 体系入口可用
[2] ThreadMgr Activate: 0x00000000 (clientId=32)  ← 取得客户端 id
[3] InputProcessorProfiles CoCreate: 0x00000000
[4] GetLanguageList: 0x00000000 (languages=1)
    TIP langid=0x0804  微软五笔 / 搜狗拼音输入法 / 微软拼音
    TIP langid=0x0000  触控输入更正 / 触控输入文本插入 / 语音识别
[5] TIP 总数=7
==== TSF 客户端链路验证通过 ====
```

**结论：本机工具链（VS2017 BuildTools MSVC 14.16 + WinSDK 19041）可完整支持 TSF 开发；COM/枚举/激活链路全部打通。** 这回答了任务的核心前置问题——"TSF 路线在技术上是否可走"：**可走**。

开发踩坑记录：① vcvars64.bat 在本机报"系统找不到指定的路径"，需手动设置 PATH/INCLUDE/LIB（build.bat 已固化）；② 无 BOM UTF-8 源码被 MSVC 14.16 按 GBK 解码致编译错乱，必须 `/utf-8`；③ 枚举方法名为 `EnumProfiles`（非 GetProfiles）；④ BSTR 需链 oleaut32.lib。

## 二、完整 TSF IME 的工作量分解（若 Go）

| 模块 | 内容 | 工时估计 |
|------|------|---------|
| DLL 骨架 | DllMain + IClassFactory + ITfTextInputProcessor（Activate/Deactivate）+ COM 注册（DllRegisterServer 写 HKLM CTF/TIP 键） | 3–5 天 |
| 按键接管 | ITfKeyEventProvider（KeyDown/PreservedKey），拦截触发键呼出候选 | 2–3 天 |
| 文本合成 | ITfContext::RequestEditSession + ITfComposition（预设文本上屏/删除） | 3–5 天 |
| 候选 UI | ITfUIElement / ITfCandidateListUIElement：系统渲染（快）或自绘窗口（自由度高，需跨进程同步） | 3–5 天 |
| 与桌面端联动 | IME 运行在**目标应用进程内**，与 Electron 主进程需命名管道/共享内存通信（候选数据下发、选中回传） | 3–5 天 |
| 签名与安装 | **Windows 10+ 要求 IME DLL 有签名**：开发期 TestSigning 自签可跑，正式分发需 EV 证书 attestation 签名 | 2–3 天 + 证书 |
| 联调与兼容 | 不同应用（Electron/记事本/Office/微信）的 TSF 实现差异调试 | 1–2 周 |

**合计：约 4–6 周**（1 人全职），另有 EV 代码签名证书年度成本。

## 三、与悬浮窗方案对比

| 维度 | 悬浮窗+剪贴板（现状，Electron） | TSF IME |
|------|-------------------------------|---------|
| 交付形态 | 独立悬浮窗，用户手动粘贴 | 候选条直接出现在任意输入框 |
| 体验 | 中（切换窗口） | 优（原地注入） |
| 工程量 | 已交付（POC） | 4–6 周 + 签名门槛 |
| 风险 | 低 | 中高（跨应用兼容、目标进程内通信、签名） |
| 与产品当前阶段匹配 | ✅ 验证期 | 适合正式版/付费版 |

## 四、Go/No-Go 建议

**No-Go（现阶段）**：概念验证阶段维持悬浮窗+剪贴板形态；TSF 的体验收益（原地候选条）对应的是正式版的差异化体验，应在正式版排期中作为独立里程碑启动，且启动前先决策签名证书采购。

**已留好的启动资产**：tsf-proto 目录（工具链已验证、编译脚本固化、客户端 API 已走通），后续 IME DLL 骨架可直接在其上开工，无重复摸索成本。
