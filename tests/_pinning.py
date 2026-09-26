"""测试用的「屏幕宽」钉桩。

「内容行宽」的比例基准是**显示器宽度**（推导见 `main.EDITOR_SIDE_RATIO` 上面那段）。
跑测试那台机器显示器多宽是随机的，而默认窗口下编辑区通常只占屏幕 40% 上下，
正好落在**第二档**（「5% 地板」）：

    正文块宽 = 编辑区宽 − 10%·屏幕宽

这一档里**和 `line_width` 设置完全无关**。于是「把行宽调大 → 留白变小 →
装饰/徽标左移」这类断言在真机上根本无从观测——改前改后是同一个值。

`pin_screen_share()` 把屏幕宽钉成「编辑区正好占 share」，把用例挪到第一档
（正文块宽 = 行宽 × 屏幕宽），`line_width` 的作用才显出来。
反过来，`share` 取大一点（编辑区几乎占满屏幕）时，行宽拉到 80% 会把留白挤没，
用来测「留白不够就藏起徽标」。
"""
from __future__ import annotations


def pin_screen_share(app, share: float) -> int:
    """钉住屏幕宽，让编辑区正好占 `share`。返回钉住的屏幕宽。"""
    column = app.editor.master.winfo_width()
    assert column > 1, "编辑区还没排好版，钉不了屏幕宽"
    app._screen_width_override = max(1, int(column / share))
    app._current_pad = -1
    app._apply_editor_geometry()
    app.root.update()
    return app._screen_width_override
