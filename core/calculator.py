"""Exact calculator for the tool `calculate`: arithmetic, percentages and date math, without eval.

Gemini can't be trusted with mental arithmetic, and no Live model supports Google's code execution tool.
So Gemini writes a Python-style expression and this module evaluates it:
- Only whitelisted syntax runs (numbers, + - * / // % **, parentheses, the functions in FUNCS, pi and e),
  walked from the `ast` tree. Nothing is ever passed to eval or exec.
- Numbers are Fractions, so decimal arithmetic is exact (0.1 + 0.2 = 0.3, 1249 * 19 / 100 = 237.31).
  sqrt, log, trig and non-integer powers fall back to floats and are shown with 10 significant digits.
- Limits on input length, exponents, factorials and result size keep every input fast on the Pi.
Stdlib only. Test off the Pi with: python3 -m core.calculator "1249 * 19 / 100"
"""
import ast
import datetime
import decimal
import math
from fractions import Fraction

MAX_CHARS = 300
MAX_DIGITS = 1000            # largest exact result (numerator or denominator)
MAX_BITS = math.ceil(MAX_DIGITS * math.log2(10))
MAX_FACTORIAL = 170
MAX_DAYS = 100_000           # add_days range, about 270 years
EXACT_DIGITS = 15            # results with at most this many significant digits are shown exactly
ROUND_DIGITS = 10            # everything else is rounded to this many significant digits
FLOAT_DIGITS = 12            # float results are cut to this many digits first, which drops binary noise

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
REPLACE = {"×": "*", "÷": "/", "−": "-", "^": "**"}

# Shown to Gemini as the tool's parameter description (core/tools.py). Keep it in sync with FUNCS.
SYNTAX = (
    "Python-style expression. Dot as decimal separator, no thousands separators: 3.5 * 2. "
    "Percentages as * 19 / 100. Operators + - * / // % ** and parentheses. "
    "Functions: sqrt, round(x, digits), floor, ceil, abs, min, max, factorial, exp, ln, log(x, base), log10, log2, "
    "sin, cos, tan, asin, acos, atan (radians; convert with radians() and degrees()). Constants: pi, e. "
    "Dates as \"YYYY-MM-DD\": today(), days_between(start, end), add_days(date, n), weekday(date)."
)


class CalcError(ValueError):
    pass


# --- values ---------------------------------------------------------------------------------------

def _num(v):
    if isinstance(v, (Fraction, float)):
        return v
    if isinstance(v, datetime.date):
        raise CalcError("dates only work inside days_between, add_days and weekday")
    raise CalcError(f"expected a number, got {v!r}")


def _int(v, lo, hi, what):
    v = _num(v)
    if v != int(v):
        raise CalcError(f"{what} must be a whole number")
    if not lo <= v <= hi:
        raise CalcError(f"{what} must be between {lo} and {hi}")
    return int(v)


def _exact(v):
    """Fraction of a number; a float counts as the decimal it prints as (2.675 -> 2675/1000)."""
    return v if isinstance(v, Fraction) else Fraction(repr(v))


def _check(v):
    if isinstance(v, float):
        if math.isinf(v) or math.isnan(v):
            raise CalcError("result too large or undefined")
    elif isinstance(v, Fraction):
        if max(v.numerator.bit_length(), v.denominator.bit_length()) > MAX_BITS:
            raise CalcError(f"result too large (over {MAX_DIGITS} digits)")
    return v


def _date(v):
    if isinstance(v, datetime.date):
        return v
    raise CalcError('expected a date like "2026-12-24"')


def _parse_date(text):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise CalcError(f'not a date: {text!r} (write dates as "YYYY-MM-DD")')


# --- operators ------------------------------------------------------------------------------------

def _divide(op):
    def run(a, b):
        a, b = _num(a), _num(b)
        if b == 0:
            raise ZeroDivisionError
        return op(a, b)
    return run


def _pow(a, b):
    a, b = _num(a), _num(b)
    if isinstance(a, Fraction) and isinstance(b, Fraction) and b.denominator == 1:
        bits = max(a.numerator.bit_length(), a.denominator.bit_length(), 1)
        if abs(b.numerator) * bits <= MAX_BITS:
            if a == 0 and b < 0:
                raise ZeroDivisionError
            return a ** b.numerator
    # Fractional exponent or a result too large to keep exact: float, which fails fast instead of hanging
    try:
        r = float(a) ** float(b)
    except OverflowError:
        raise CalcError("result too large")
    if isinstance(r, complex):
        raise CalcError("no real result (root of a negative number)")
    return r


BIN_OPS = {
    ast.Add: lambda a, b: _num(a) + _num(b),
    ast.Sub: lambda a, b: _num(a) - _num(b),
    ast.Mult: lambda a, b: _num(a) * _num(b),
    ast.Div: _divide(lambda a, b: a / b),
    ast.FloorDiv: _divide(lambda a, b: a // b),
    ast.Mod: _divide(lambda a, b: a % b),
    ast.Pow: _pow,
}


# --- functions ------------------------------------------------------------------------------------

def _sqrt(x):
    x = _num(x)
    if x < 0:
        raise CalcError("no real square root of a negative number")
    if isinstance(x, Fraction):
        n, d = math.isqrt(x.numerator), math.isqrt(x.denominator)
        if n * n == x.numerator and d * d == x.denominator:
            return Fraction(n, d)
    return math.sqrt(x)


def _round(x, digits=Fraction(0)):
    """Rounds half away from zero, as people expect (2.5 -> 3, 0.125 -> 0.13), not banker's rounding."""
    x = _exact(_num(x))
    q = Fraction(10) ** _int(digits, -20, 20, "digits")
    r = math.floor(abs(x) * q + Fraction(1, 2))
    return Fraction(r if x >= 0 else -r) / q


def _factorial(n):
    return Fraction(math.factorial(_int(n, 0, MAX_FACTORIAL, "factorial argument")))


def _positive(x):
    x = _num(x)
    if x <= 0:
        raise CalcError("logarithm needs a positive number")
    return x


def _log(x, base=None):
    x = _positive(x)
    if base is None:
        return math.log(x)
    base = _num(base)
    if base <= 0 or base == 1:
        raise CalcError("logarithm base must be positive and not 1")
    return math.log(x, base)


def _float_fn(fn):
    return lambda x: fn(_num(x))


def _trig(fn):
    # round away float noise: sin(pi) = 0 instead of 1.2e-16, sin(radians(30)) = 0.5
    return lambda x: round(fn(_num(x)), 15)


def _today():
    return datetime.date.today()


def _add_days(d, n):
    try:
        return _date(d) + datetime.timedelta(days=_int(n, -MAX_DAYS, MAX_DAYS, "number of days"))
    except OverflowError:
        raise CalcError("date out of range")


FUNCS = {
    "sqrt": _sqrt,
    "round": _round,
    "floor": lambda x: Fraction(math.floor(_num(x))),
    "ceil": lambda x: Fraction(math.ceil(_num(x))),
    "abs": lambda x: abs(_num(x)),
    "min": lambda *xs: min(_num(x) for x in xs),
    "max": lambda *xs: max(_num(x) for x in xs),
    "factorial": _factorial,
    "exp": _float_fn(math.exp),
    "ln": lambda x: _log(x),
    "log": _log,
    "log10": lambda x: math.log10(_positive(x)),
    "log2": lambda x: math.log2(_positive(x)),
    "sin": _trig(math.sin),
    "cos": _trig(math.cos),
    "tan": _trig(math.tan),
    "asin": _trig(math.asin),
    "acos": _trig(math.acos),
    "atan": _trig(math.atan),
    "radians": _float_fn(math.radians),
    "degrees": _float_fn(math.degrees),
    "today": lambda: _today(),
    "days_between": lambda a, b: Fraction((_date(b) - _date(a)).days),
    "add_days": _add_days,
    "weekday": lambda d: WEEKDAYS[_date(d).weekday()],
}
CONSTANTS = {"pi": math.pi, "e": math.e}


# --- evaluation -----------------------------------------------------------------------------------

def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        v = node.value
        if type(v) in (int, float):
            try:
                return _check(Fraction(repr(v)))
            except ValueError:  # 1e999 parses as inf
                raise CalcError("number too large")
        if isinstance(v, str):
            return _parse_date(v)
        raise CalcError(f"{v!r} is not allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in BIN_OPS:
        return _check(BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _num(_eval(node.operand))
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        if node.id in FUNCS:
            raise CalcError(f"{node.id} is a function, call it like {node.id}(...)")
        raise CalcError(f"unknown name {node.id!r}")
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name not in FUNCS:
            raise CalcError(f"unknown function {name or ast.unparse(node.func)!r}")
        if node.keywords:
            raise CalcError(f"pass the arguments of {name}() without names")
        args = [_eval(a) for a in node.args]
        try:
            return _check(FUNCS[name](*args))
        except TypeError:
            raise CalcError(f"wrong number of arguments for {name}()")
    if isinstance(node, ast.Tuple):
        raise CalcError("commas are only allowed between function arguments; use a dot as decimal separator (3.5)")
    raise CalcError(f"{type(node).__name__} is not allowed")


def _digits(fr, prec):
    with decimal.localcontext() as ctx:
        ctx.prec = prec
        return (decimal.Decimal(fr.numerator) / decimal.Decimal(fr.denominator)).normalize()


def _show(v):
    """(sign, text): '=' and the exact value when it is short, else '≈' and ROUND_DIGITS significant digits.

    Fractions are exact when they fit in EXACT_DIGITS. Floats (sqrt, log, trig) are first rounded to
    FLOAT_DIGITS to drop binary noise (log10(1000) = 2.9999999999999996 -> 3) and count as exact only
    when what is left is short."""
    if isinstance(v, datetime.date):
        return "=", f"{v.isoformat()} ({WEEKDAYS[v.weekday()]})"
    if isinstance(v, str):
        return "=", v
    if isinstance(v, float):
        d = _digits(Fraction(repr(v)), FLOAT_DIGITS)
        exact = len(d.as_tuple().digits) <= ROUND_DIGITS
        if not exact:
            d = _digits(Fraction(repr(v)), ROUND_DIGITS)
    else:
        d = _digits(v, EXACT_DIGITS)
        exact = Fraction(d) == v
        if not exact:
            d = _digits(v, ROUND_DIGITS)
    sign = "=" if exact else "≈"
    if d == 0:
        return sign, "0"
    if -7 < d.adjusted() < EXACT_DIGITS:
        return sign, format(d, "f")
    return sign, format(d, "e")


def evaluate(expression):
    """Value of the expression (Fraction, float, date or weekday name). Raises CalcError and friends."""
    text = (expression or "").strip()
    if not text:
        raise CalcError("empty expression")
    if len(text) > MAX_CHARS:
        raise CalcError(f"expression too long (max {MAX_CHARS} characters)")
    for old, new in REPLACE.items():
        text = text.replace(old, new)
    return _eval(ast.parse(text, mode="eval"))


def calculate(expression):
    """Tool entry point: 'expression = result' or 'Error: ...'. Never raises."""
    try:
        sign, shown = _show(evaluate(expression))
        return f"{(expression or '').strip()} {sign} {shown}"
    except ZeroDivisionError:
        return "Error: division by zero."
    except SyntaxError:
        return ("Error: invalid syntax. Write it like Python: 3.5 * 2, percentages as * 19 / 100, "
                'dates as "2026-12-24".')
    except (RecursionError, MemoryError):
        return "Error: expression too complex."
    except OverflowError:
        return "Error: number too large."
    except ValueError as e:  # CalcError and math errors such as 'math domain error'
        msg = str(e)
        if msg == "math domain error":
            msg = "no real result (outside the function's domain)"
        return f"Error: {msg}."


if __name__ == "__main__":
    import sys
    print(calculate(" ".join(sys.argv[1:]) or "1249 * 19 / 100"))
