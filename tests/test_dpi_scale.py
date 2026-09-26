"""DPI 缩放换算的测试。

背景：进程若未声明 DPI 感知，Windows 会把整个窗口位图放大（150% 缩放下放大 1.5 倍），
文字边缘因此发糊。修复方式是启动时声明 DPI 感知，并用 px() 把界面里的
逻辑像素换算成物理像素——Tk 的 `tk scaling` 只自动缩放「以磅为单位」的字体，
像素尺寸（含编辑器的负数字号）必须自己换算。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import main  # noqa: E402


class PxScalingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._real = main.SCALE

    def tearDown(self) -> None:
        main.SCALE = self._real

    def test_px_is_identity_at_100_percent(self) -> None:
        main.SCALE = 1.0
        for value in (0, 1, 12, 20, 46, 178, 306, 1180):
            self.assertEqual(main.px(value), value)

    def test_px_scales_at_150_percent(self) -> None:
        main.SCALE = 1.5
        self.assertEqual(main.px(20), 30)
        self.assertEqual(main.px(178), 267)
        self.assertEqual(main.px(306), 459)
        self.assertEqual(main.px(46), 69)
        self.assertEqual(main.px(1180), 1770)
        self.assertEqual(main.px(760), 1140)

    def test_px_rounds_half_up_not_bankers(self) -> None:
        """15 * 1.5 = 22.5，应进位到 23，而不是 round() 的银行家舍入 22。"""
        main.SCALE = 1.5
        self.assertEqual(main.px(15), 23)
        self.assertEqual(main.px(5), 8)   # 7.5 -> 8
        self.assertEqual(main.px(7), 11)  # 10.5 -> 11

    def test_px_scales_at_200_percent(self) -> None:
        main.SCALE = 2.0
        self.assertEqual(main.px(15), 30)
        self.assertEqual(main.px(1180), 2360)

    def test_px_never_returns_float(self) -> None:
        main.SCALE = 1.25
        self.assertIsInstance(main.px(37), int)


class ScaleFromEnvTests(unittest.TestCase):
    """测试用的 JIANJI_SCALE 覆盖值解析。"""

    def test_valid_values(self) -> None:
        self.assertEqual(main._scale_from_env("1"), 1.0)
        self.assertEqual(main._scale_from_env("1.5"), 1.5)
        self.assertEqual(main._scale_from_env("2"), 2.0)

    def test_clamped_to_at_least_one(self) -> None:
        """缩放不应小于 1，否则界面会被缩得看不清。"""
        self.assertEqual(main._scale_from_env("0.5"), 1.0)
        self.assertEqual(main._scale_from_env("0"), 1.0)
        self.assertEqual(main._scale_from_env("-3"), 1.0)

    def test_invalid_values_return_none(self) -> None:
        self.assertIsNone(main._scale_from_env(None))
        self.assertIsNone(main._scale_from_env(""))
        self.assertIsNone(main._scale_from_env("abc"))
        self.assertIsNone(main._scale_from_env("1.5x"))


class ScaleWiringTests(unittest.TestCase):
    def test_scale_is_at_least_one(self) -> None:
        """缩放不应小于 1；具体值取决于运行机器的显示器设置。"""
        self.assertGreaterEqual(main.SCALE, 1.0)

    def test_layout_constants_are_ints(self) -> None:
        for name in ("NAV_W", "CARDS_W", "GUTTER_W"):
            self.assertIsInstance(getattr(main, name), int, name)
            self.assertGreater(getattr(main, name), 0, name)


if __name__ == "__main__":
    unittest.main()
