"""T01339 复验反馈：视频/图片等非纯文本不纳入识别——噪声行过滤。"""

from emoreply.preprocess import OcrLine, filter_non_text_noise


def _line(text: str, top: int = 0) -> OcrLine:
    return OcrLine(text=text, left=0, top=top, right=len(text) * 10, bottom=top + 30)


class TestMediaPlaceholders:
    def test_placeholders_dropped(self):
        lines = [_line("[图片]"), _line("【视频】"), _line("[动画表情]"),
                 _line("[文件]"), _line(" [红包] ")]
        assert filter_non_text_noise(lines) == []

    def test_real_text_unaffected(self):
        lines = [_line("买了4斤牛肉,我明天早上送回去"), _line("好")]
        assert filter_non_text_noise(lines) == lines


class TestOverlayBadge:
    def test_plus_badges_dropped(self):
        lines = [_line("1止部+"), _line("E個+"), _line("12+"), _line("点赞+")]
        assert filter_non_text_noise(lines) == []

    def test_plus_badge_with_spaces(self):
        assert filter_non_text_noise([_line(" 5 +")]) == []

    def test_plus_inside_sentence_kept(self):
        # + 不在行尾（真实消息），不误杀
        lines = [_line("加个+1呗，明天一起")]
        assert filter_non_text_noise(lines) == lines


class TestSymbolNoise:
    def test_symbol_garbage_dropped(self):
        lines = [_line("★☆◇◆※"), _line("™©®§¶")]
        assert filter_non_text_noise(lines) == []

    def test_emoji_not_noise(self):
        # emoji 属合法内容，不算符号噪声
        lines = [_line("好👌"), _line("哈哈哈😂")]
        assert filter_non_text_noise(lines) == lines

    def test_normal_punct_not_noise(self):
        lines = [_line("好呀，周末一起去！")]
        assert filter_non_text_noise(lines) == lines


class TestConservative:
    def test_sender_name_kept(self):
        lines = [_line("爸")]
        assert filter_non_text_noise(lines) == lines

    def test_short_mixed_kept(self):
        # 短混合行（数字+中文）不满足任一判据 → 保守保留
        lines = [_line("第3名"), _line("3-5点开会")]
        assert filter_non_text_noise(lines) == lines

    def test_empty_dropped(self):
        lines = [_line("   ")]
        assert filter_non_text_noise(lines) == []
