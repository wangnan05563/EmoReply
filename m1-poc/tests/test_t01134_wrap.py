"""T01134：读屏折行拆分致语义偏差——换行识别优化。

验收点（几何纯函数钉死，Android 侧同构移植）：
1. 同一气泡内折行（行间空白小）→ 合并为一条消息，中文直接拼接、英文断词补空格；
2. 相邻两条独立消息（气泡间距大）→ 不合并；
3. 阈值用「行间空白 / 行高」相对比例，抗屏幕密度漂移（旧绝对常量 26px 在
   density 3.5 设备上漏判，即本任务报的缺陷）；
4. 同 block（ML Kit 同一视觉块）放宽、跨 block 收紧，防误并；
5. 端到端：截图复现场景合并后，split_dialogue_turns 取到完整对方发言。
"""
from emoreply.preprocess import (
    SPEAKER_OTHER,
    OcrLine,
    merge_wrapped_lines,
    split_dialogue_turns,
)

# 真实量级坐标（SM-G9980：1440x3200 / density 3.5，16sp 中文行高约 64px）
LINE_H = 64
FOLD_GAP = 30        # 同气泡折行：行间空白 ~30px（= 0.47 × 行高）
BUBBLE_GAP = 136     # 独立消息：气泡间距 + 内边距，行间空白 ~136px（= 2.1 × 行高）


def _line(text, top, *, block=0, left=300, right=1200):
    return OcrLine(text=text, left=left, right=right, top=top, bottom=top + LINE_H, block=block)


class TestMergeWrappedLines:
    def test_same_bubble_fold_merged(self):
        # 同 block 折行：行间空白 30px < 行高 64px → 合并（折行点在「进/一」之间）
        out = merge_wrapped_lines([_line("Hello,我已被你创建成功,等你进", 1000), _line("一步开发部署我的灵魂。", 1094)])
        assert len(out) == 1
        assert out[0].text == "Hello,我已被你创建成功,等你进一步开发部署我的灵魂。"

    def test_cross_block_fold_still_merged(self):
        # 跨 block 但行间空白 0.47×行高 < 0.6 → 仍判为折行（ML Kit 偶把同气泡切成两块）
        out = merge_wrapped_lines([
            _line("今天的天气", 1000, block=0),
            _line("真不错", 1094, block=1),
        ])
        assert len(out) == 1
        assert out[0].text == "今天的天气真不错"

    def test_independent_messages_not_merged(self):
        # 独立消息：行间空白 2.1×行高 → 不合并（防把两条消息并成一条）
        out = merge_wrapped_lines([
            _line("第一条消息", 1000, block=0),
            _line("第二条消息", 1000 + LINE_H + BUBBLE_GAP, block=1),
        ])
        assert [l.text for l in out] == ["第一条消息", "第二条消息"]

    def test_same_block_far_apart_not_merged(self):
        # 同 block 但行间空白 1.09×行高 > 1.0 → 不合并（ML Kit 把相邻两条并入一个 block 时不误并）
        gap = int(LINE_H * 1.09)
        out = merge_wrapped_lines([
            _line("上一条", 1000, block=0),
            _line("下一条", 1000 + LINE_H + gap, block=0),
        ])
        assert [l.text for l in out] == ["上一条", "下一条"]

    def test_three_lines_fold_merged_into_one(self):
        # 连续三行折行 → 合并为一条（与「上一原始行」比较，块首行 top 不参与判定）
        out = merge_wrapped_lines([
            _line("第一段", 1000),
            _line("第二段", 1000 + LINE_H + FOLD_GAP),
            _line("第三段", 1000 + 2 * (LINE_H + FOLD_GAP)),
        ])
        assert len(out) == 1
        assert out[0].text == "第一段第二段第三段"
        assert out[0].bottom == 1000 + 2 * (LINE_H + FOLD_GAP) + LINE_H

    def test_english_wrap_gets_space(self):
        # 英文断词折行：ASCII 字母数字交界补空格，避免单词粘连
        out = merge_wrapped_lines([_line("please check the", 1000), _line("attachment", 1094)])
        assert out[0].text == "please check the attachment"

    def test_chinese_wrap_no_space(self):
        out = merge_wrapped_lines([_line("今晚一起", 1000), _line("吃饭吗", 1094)])
        assert out[0].text == "今晚一起吃饭吗"

    def test_input_order_irrelevant(self):
        # 输入顺序无关：内部按 top 升序
        a, b = _line("上半句", 1000), _line("下半句", 1094)
        assert merge_wrapped_lines([b, a])[0].text == "上半句下半句"

    def test_single_line_untouched(self):
        out = merge_wrapped_lines([_line("只有一行", 1000)])
        assert len(out) == 1
        assert out[0].text == "只有一行"

    def test_input_not_mutated(self):
        # 纯函数：合并结果写入新对象，不改动入参
        a, b = _line("上半句", 1000), _line("下半句", 1094)
        merge_wrapped_lines([a, b])
        assert (a.text, a.bottom) == ("上半句", 1000 + LINE_H)
        assert b.text == "下半句"

    def test_overlapping_boxes_merged(self):
        # ML Kit 行框重叠（gap 为负）→ 仍视为折行
        out = merge_wrapped_lines([_line("上半句", 1000), _line("下半句", 1050)])
        assert len(out) == 1

    def test_merged_box_union(self):
        # 合并后包围盒取并集（供侧别判定用的 right 不被丢失）
        out = merge_wrapped_lines([
            _line("短", 1000, left=300, right=500),
            _line("很长的一行", 1094, left=280, right=1300),
        ])
        assert (out[0].left, out[0].right) == (280, 1300)


class TestReadScreenEndToEnd:
    def test_screenshot_scenario_current_is_complete(self):
        # 端到端复现报障截图：机器人一条消息折两行 + 我方一条 → 合并后 current 为完整句
        merged = merge_wrapped_lines([
            _line("Hello,我已被你创建成功,等你进", 1000, block=0, right=1100),
            _line("一步开发部署我的灵魂。", 1094, block=0, right=1050),
            _line("你好", 1000 + 2 * LINE_H + BUBBLE_GAP, block=1, left=1200, right=1360),
        ])
        labeled = [
            ("我：" if l.right > 1140 else "对方：") + l.text for l in merged
        ]
        assert labeled == [
            "对方：Hello,我已被你创建成功,等你进一步开发部署我的灵魂。",
            "我：你好",
        ]
        history, current = split_dialogue_turns(labeled)
        assert current == (SPEAKER_OTHER, "Hello,我已被你创建成功,等你进一步开发部署我的灵魂。")
        assert history == [("我", "你好")]

    def test_unmerged_split_yields_half_sentence(self):
        # 反证：不合并（旧行为）时 current 只剩后半句——正是本任务报的语义偏差
        lines = ["对方：Hello,我已被你创建成功,等你进", "对方：一步开发部署我的灵魂。"]
        _, current = split_dialogue_turns(lines)
        assert current == (SPEAKER_OTHER, "一步开发部署我的灵魂。")