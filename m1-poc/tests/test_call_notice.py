"""通话类系统提示过滤（用户反馈 2026-09-27：「对方已取消」被当成对方消息）。

根因：微信语音通话的系统提示（居中灰字）经 OCR 后混入对话上下文，生成层误判为
「对方撤回了消息」，候选方向整体跑偏（真机实测生成「撤回得这么快」类回复）。
"""
from emoreply.preprocess import strip_group_noise


class TestCallNoticeFiltered:
    def test_call_cancelled_by_other(self):
        assert strip_group_noise(["对方已取消"]) == []

    def test_ocr_leading_noise_letter(self):
        # 真机样本：气泡旁通话图标被 OCR 误读成单字母「s」
        assert strip_group_noise(["s对方已取消"]) == []

    def test_call_hung_up(self):
        assert strip_group_noise(["已挂断"]) == []

    def test_call_no_answer(self):
        assert strip_group_noise(["对方无应答"]) == []

    def test_call_duration_notice(self):
        assert strip_group_noise(["通话时长 00:28"]) == []

    def test_invite_voice_call(self):
        assert strip_group_noise(["邀请你语音通话"]) == []

    def test_mixed_keeps_real_messages(self):
        lines = ["对方已取消", "行那我们3点多过去", "s对方已取消"]
        assert strip_group_noise(lines) == ["行那我们3点多过去"]


class TestRealMessagesKept:
    def test_order_cancelled_not_filtered(self):
        # 前缀是中文词而非图标误读 → 不是通话提示
        assert strip_group_noise(["订单已取消"]) == ["订单已取消"]

    def test_please_cancel_message(self):
        assert strip_group_noise(["取消吧"]) == ["取消吧"]

    def test_i_refused_message(self):
        assert strip_group_noise(["我拒绝了"]) == ["我拒绝了"]

    def test_order_cancelled_by_other(self):
        # 结尾不是固定文案 → 保留
        assert strip_group_noise(["对方已取消订单"]) == ["对方已取消订单"]