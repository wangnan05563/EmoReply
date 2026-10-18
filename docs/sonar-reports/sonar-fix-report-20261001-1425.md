# SonarQube 代码质量扫描修复报告（2026-10-01，三期收口后新增代码批次）

## 一、扫描环境与口径

- SonarQube Server 26.1（localhost:9000），sonar-scanner 8.0.1（JAVA_HOME=jdk-25.0.1），项目 key=`28_AIMsg`。
- 本轮为 T01362（09-30，123→6）之后的首轮增量扫描：覆盖三期 M2/M3/M4 新增代码
  （desktop-app 的 llm/renderer/main/sanitize/ocr/tone、scripts/*.smoke.js、ui-shots.js、perf/sut-harness.js）。
- `sonar-project.properties` 排除项新增 `**/perf/scripts/**`、`**/perf/output/**`（压测脚手架为技能 vendored 副本，非业务源码）。
- 修复前后对比：**OPEN 问题 86 → 12**（修复/重构 74 条，86.0%）。
  - CRITICAL 4 → 0；MINOR 36 → 0；MAJOR 46 → 12（余量全部为下述有意保留/不适用项）。
  - 无 BUG / VULNERABILITY / SECURITY HOTSPOT 残留。
- 服务端面板：http://localhost:9000/dashboard?id=28_AIMsg

## 二、修复明细（按文件，紧凑口径）

| 文件 | 主要修复 |
|---|---|
| desktop-app/llm.js | generateCore 认知复杂度 21→达标（拆 stubResult/dedupeBySimilarity/collectFanoutCandidates/retrieveKnowledge 四个辅助函数，检索 try/catch 内聚）；可选链 ×8；SSE 空 catch 补 debug 日志；`match`→`exec`；`Object.assign`→spread |
| desktop-app/main.js | llm:generate handler 整行死解构删除（消 17 条）；ollama:check 嵌套 5 层→拆 checkOllamaTags+resolveOllamaModels；paste:back 可选链；5 处静默 catch 补 console.warn |
| desktop-app/renderer.js | keydown 复杂度 18→拆 selectCandidateByNumber/focusAdjacentCandidate；streamEls const→let（S3500）；嵌套回调提取 restoreLabel；嵌套三元展开（二轮）；2 处空 catch 补日志 |
| desktop-app/sanitize.js | S5843 复杂度 76 大正则**等价重写**为「首词+尾缀段+内层词 DP 解析」（4 条小正则+贪婪+DP，30 万次×2 轮模糊差分与原正则零分歧）；META_REPLY 复杂度 43 拆三条；字符类去重；replaceAll ×13；String.raw ×3；`.at(-1)`；嵌套三元展开（二轮）；innerWordLengthsAt 复杂度 26→拆 pushRepeatLengths/pushHaoLengths+查表（二轮，30 万次差分零分歧） |
| desktop-app/ocr.js / tone.js | replace(/g)→replaceAll ×3 |
| scripts/*.smoke.js | llm.smoke：删未用 body、raw 累积改 `req.resume()`（两处）、`main;` 死表达式删除、嵌套赋值拆分、String.raw；断言零削弱 |
| scripts/ui-shots.js | CDP 嵌套回调提取具名 `send()` |
| perf/sut-harness.js | 3 处静默 catch 补 console.warn |

## 三、验证

1. **语法**：14 个改动文件 `node --check` 全过。
2. **冒烟**：6 套共 54 例全绿（generate 4 / sanitize 33 / tone 3 / llm 7 / kb 6 / ocr 1）。
3. **等价性**：sanitize 两处高风险重写各做 30 万次随机模糊差分（确认词解析 + META_REPLY 拆分），与原实现零分歧。
4. **SonarQube 复扫**：4 轮扫描迭代至 OPEN=12（首轮 86 → 二轮 18 → 三轮 19 → 终轮 12；二三轮为修复引入的少量新点当场清零）。

## 四、剩余 12 条 OPEN 的说明（不适用/有意保留，非疏漏）

| 类别 | 数量 | 理由 |
|---|---|---|
| javascript:S7785（main.js/renderer.js×2/四个 smoke/ui-shots） | 8 | Electron 主进程/渲染进程/Node 脚本均为 **CommonJS**，语法上不存在 top-level await，规则不适用（T01362 同口径；切 ESM 时一并消除） |
| kotlin:S7204（release 未启用混淆） | 1 | T01362 已保留：待真机验证阶段随 R8/ProGuard 映射管理一并处理 |
| kotlin:S107 ×2（Compose.buildPrompt 19 参 / Pipeline.handle 10 参） | 2 | T01362 已保留：参数均为需求逐项叠加的独立语义，POC 阶段回归风险大于收益 |
| python:S107（compose.build_prompt 17 参） | 1 | 同上（20+ 测试调用点，重构波及面大） |

## 五、质量门与权限说明

- 与 T01362 相同：Quality Gate 的 New Code 条件因无覆盖率上报/历史基线显示 ERROR，属数据口径问题，与代码质量无关。实际口径：OPEN 86→12，CRITICAL/BUG/VULNERABILITY/MINOR 清零。
- 拟将 8 条 S7785 标记 FALSE-POSITIVE、4 条 S107/S7204 标记 ACCEPTED：当前 MCP token 返回 **Insufficient privileges**（与 T01362 一致，token 缺「Administer Issues」权限）。需在 SonarQube 管理界面（Administration → Permissions）为该用户授权后补标记。

## 六、后续事项

- ~~生产构建复验~~ **已完成（2026-10-01 14:48）**：electron-builder 重建通过（NSIS 113MB + portable 113MB + win-unpacked），asar 校验含本轮全部新代码标记（resolveOllamaModels/retrieveKnowledge/collectFanoutCandidates/selectCandidateByNumber/NO_REPLY_INNER_PAIR），打包 exe 冒烟通过（/health 200 version=20260930-004、/generate 200 降级 2 候选、404 正常）。
- 若需云端生成路径压测/审计，需用户配置真实 Key 后复跑（本地降级路径已于性能测试报告中覆盖）。
