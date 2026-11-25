import datetime
import re
import time
import unittest
from unittest import mock

from core import calculator


def calc(expression):
    return calculator.calculate(expression)


def result(expression):
    """The part after '= ' or '≈ ', or the error text."""
    text = calc(expression)
    if text.startswith("Error:"):
        return text
    return text.rsplit(" = ", 1)[-1] if " = " in text else "≈ " + text.rsplit(" ≈ ", 1)[-1]


class ExactArithmetic(unittest.TestCase):
    def test_decimals_are_exact(self):
        self.assertEqual(result("0.1 + 0.2"), "0.3")
        self.assertEqual(result("1249 * 19 / 100"), "237.31")
        self.assertEqual(result("1/7 * 7"), "1")
        self.assertEqual(result("26.2 * 1.609344"), "42.1648128")

    def test_precedence_and_operators(self):
        self.assertEqual(result("2 + 3 * 4"), "14")
        self.assertEqual(result("(2 + 3) * 4"), "20")
        self.assertEqual(result("-2 ** 2"), "-4")
        self.assertEqual(result("17 // 5"), "3")
        self.assertEqual(result("17 % 5"), "2")
        self.assertEqual(result("2 ** -2"), "0.25")

    def test_non_terminating_results_are_rounded(self):
        self.assertEqual(result("10 / 3"), "≈ 3.333333333")
        self.assertEqual(result("87.40 / 3"), "≈ 29.13333333")

    def test_echoes_the_expression(self):
        self.assertEqual(calc("  1249 * 19 / 100 "), "1249 * 19 / 100 = 237.31")

    def test_large_and_small_numbers(self):
        self.assertEqual(result("2 ** 200"), "≈ 1.606938044e+60")
        self.assertEqual(result("1e-9 * 3"), "3e-9")
        self.assertEqual(result("factorial(10)"), "3628800")

    def test_spoken_operator_symbols(self):
        self.assertEqual(result("2^8"), "256")
        self.assertEqual(result("6 × 7"), "42")
        self.assertEqual(result("10 ÷ 4"), "2.5")
        self.assertEqual(result("5 − 7"), "-2")


class Functions(unittest.TestCase):
    def test_round_half_away_from_zero(self):
        self.assertEqual(result("round(2.5)"), "3")
        self.assertEqual(result("round(-2.5)"), "-3")
        self.assertEqual(result("round(0.125, 2)"), "0.13")
        self.assertEqual(result("round(2.675, 2)"), "2.68")
        self.assertEqual(result("round(1234, -2)"), "1200")
        self.assertEqual(result("round(87.40 / 3, 2)"), "29.13")

    def test_roots(self):
        self.assertEqual(result("sqrt(16)"), "4")
        self.assertEqual(result("sqrt(2.25)"), "1.5")
        self.assertEqual(result("sqrt(2)"), "≈ 1.414213562")
        self.assertEqual(result("sqrt(2) ** 2"), "2")  # float noise dropped
        self.assertEqual(result("27 ** (1/3)"), "3")

    def test_logs_and_trig(self):
        self.assertEqual(result("log10(1000)"), "3")
        self.assertEqual(result("log10(1)"), "0")
        self.assertEqual(result("log2(8)"), "3")
        self.assertEqual(result("log(8, 2)"), "3")
        self.assertEqual(result("ln(e)"), "1")
        self.assertEqual(result("sin(radians(30))"), "0.5")
        self.assertEqual(result("sin(pi)"), "0")
        self.assertEqual(result("degrees(pi)"), "180")

    def test_min_max_abs_floor_ceil(self):
        self.assertEqual(result("min(3, 1.5, 2)"), "1.5")
        self.assertEqual(result("max(3, 1.5, 2)"), "3")
        self.assertEqual(result("abs(-4.2)"), "4.2")
        self.assertEqual(result("floor(-2.5)"), "-3")
        self.assertEqual(result("ceil(2.1)"), "3")

    def test_syntax_text_names_every_function(self):
        for name in list(calculator.FUNCS) + list(calculator.CONSTANTS):
            self.assertRegex(calculator.SYNTAX, rf"\b{name}\b")


class Dates(unittest.TestCase):
    def test_days_between(self):
        self.assertEqual(result('days_between("2026-11-25", "2026-12-24")'), "29")
        self.assertEqual(result('days_between("2028-02-01", "2028-03-01")'), "29")  # leap year
        self.assertEqual(result('days_between("2026-12-24", "2026-11-25")'), "-29")

    def test_add_days_and_weekday(self):
        self.assertEqual(result('add_days("2026-11-25", 90)'), "2027-02-23 (Tuesday)")
        self.assertEqual(result('add_days("2026-03-01", -1)'), "2026-02-28 (Saturday)")
        self.assertEqual(result('weekday("2027-03-14")'), "Sunday")

    def test_german_date_format_is_accepted(self):
        self.assertEqual(result('"24.12.2026"'), "2026-12-24 (Thursday)")

    def test_today(self):
        with mock.patch.object(calculator, "_today", return_value=datetime.date(2026, 11, 25)):
            self.assertEqual(result('days_between(today(), "2026-12-24")'), "29")
            self.assertEqual(result("weekday(today())"), "Wednesday")

    def test_date_errors(self):
        self.assertIn("dates only work", calc("today() + 1"))
        self.assertIn("expected a date", calc("weekday(5)"))
        self.assertIn("not a date", calc('weekday("Christmas")'))
        self.assertIn("whole number", calc('add_days("2026-11-25", 1.5)'))
        self.assertTrue(calc('add_days("9999-12-31", 1)').startswith("Error:"))


class Rejected(unittest.TestCase):
    def assertError(self, expression, fragment=""):
        text = calc(expression)
        self.assertTrue(text.startswith("Error:"), f"{expression!r} -> {text!r}")
        self.assertIn(fragment, text)

    def test_code_is_never_run(self):
        for expression in ['__import__("os").system("id")', "().__class__", "lambda: 1", "[1, 2]",
                           "{1: 2}", "x + 1", "open('f')", "eval('1')", "pi.real", "(1).__class__",
                           "[x for x in (1,)]", "1 if 1 else 2", "1 < 2", "1 and 2", "f'{1}'",
                           "True + 1", "None", "1j", "b'1'", "abs(x=1)", "(lambda: 1)()"]:
            with self.subTest(expression=expression):
                self.assertError(expression)

    def test_helpful_messages(self):
        self.assertError("3,5 * 2", "dot as decimal separator")
        self.assertError("19% * 1249", "percentages as * 19 / 100")
        self.assertError("sqrt", "call it like sqrt(...)")
        self.assertError("round(1.5, ndigits=0)", "without names")
        self.assertError("sqrt(1, 2)", "wrong number of arguments")
        self.assertError("", "empty")

    def test_math_errors(self):
        self.assertError("1 / 0", "division by zero")
        self.assertError("5 % 0", "division by zero")
        self.assertError("0 ** -1", "division by zero")
        self.assertError("sqrt(-1)", "no real")
        self.assertError("(-8) ** (1/3)", "no real")
        self.assertError("log(-1)", "positive")
        self.assertError("asin(2)", "no real")
        self.assertError("log(8, 1)", "base")

    def test_limits_answer_fast(self):
        for expression in ["2 ** 10 ** 10", "9 ** 9 ** 9", "10 ** 999 * 10 ** 999", "factorial(171)",
                           "factorial(10 ** 6)", "exp(1000)", "1e999", "(" * 150 + "1" + ")" * 150,
                           "1+" * 200 + "1", "add_days(today(), 10 ** 9)"]:
            with self.subTest(expression=expression[:40]):
                start = time.perf_counter()
                text = calc(expression)
                self.assertLess(time.perf_counter() - start, 0.5)
                self.assertTrue(text.startswith("Error:"), text)

    def test_large_float_power_stays_usable(self):
        self.assertEqual(result("1.0001 ** 5000"), "≈ 1.648680056")
        self.assertEqual(result("1.05 ** 30"), "≈ 4.321942375")

    def test_never_raises(self):
        for expression in [None, "   ", ")", "1 +", "'", "\x00", "1\n2", "1; 2"]:
            with self.subTest(expression=expression):
                self.assertTrue(re.match(r"Error:", calc(expression)))


if __name__ == "__main__":
    unittest.main()
