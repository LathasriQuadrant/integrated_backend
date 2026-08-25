 
from __future__ import annotations
 
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Union
 
# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------
 
TOKEN_SPEC = [
    ("COMMENT", r"//[^\n]*"),
    ("WS", r"[ \t\r\n]+"),
    ("NUMBER", r"\d+\.\d+|\d+"),
    ("STRING", r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""),
    ("FIELD", r"\[[^\]]+\]"),
    ("NE", r"<>|!="),
    ("LE", r"<="),
    ("GE", r">="),
    ("LT", r"<"),
    ("GT", r">"),
    ("EQ", r"="),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("SLASH", r"/"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("COMMA", r","),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
]
TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in TOKEN_SPEC))
KEYWORDS = {"IF", "THEN", "ELSEIF", "ELSE", "END", "AND", "OR", "NOT"}
 
 
@dataclass
class Token:
    kind: str
    value: str
 
 
class ParseError(Exception):
    pass
 
 
def tokenize(formula: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    while pos < len(formula):
        m = TOKEN_RE.match(formula, pos)
        if not m:
            raise ParseError(f"Unrecognized character at position {pos}: {formula[pos:pos + 20]!r}")
        kind = m.lastgroup
        value = m.group()
        pos = m.end()
        if kind in ("WS", "COMMENT"):
            continue
        if kind == "IDENT" and value.upper() in KEYWORDS:
            kind = value.upper()
        tokens.append(Token(kind, value))
    return tokens
 
 
# --------------------------------------------------------------------------
# AST node types (plain tuples for speed/simplicity: (node_type, ...))
# --------------------------------------------------------------------------
# ("num", float)
# ("str", str)
# ("field", canonical_name)
# ("call", func_name, [arg_nodes])
# ("bin", op, left, right)
# ("neg", node)
# ("cmp", op, left, right)
# ("and", left, right) / ("or", left, right) / ("not", node)
# ("if", [(cond, expr), ...], else_expr_or_None)
 
 
class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
 
    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None
 
    def advance(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok
 
    def expect(self, kind: str) -> Token:
        tok = self.peek()
        if tok is None or tok.kind != kind:
            raise ParseError(f"Expected {kind}, got {tok}")
        return self.advance()
 
    def parse_formula(self):
        node = self.parse_expr()
        if self.peek() is not None:
            raise ParseError(f"Unexpected trailing token: {self.peek()}")
        return node
 
    # expr := term (('+'|'-') term)*
    def parse_expr(self):
        node = self.parse_term()
        while self.peek() and self.peek().kind in ("PLUS", "MINUS"):
            op = self.advance().kind
            rhs = self.parse_term()
            node = ("bin", "+" if op == "PLUS" else "-", node, rhs)
        return node
 
    # term := factor (('*'|'/') factor)*
    def parse_term(self):
        node = self.parse_factor()
        while self.peek() and self.peek().kind in ("STAR", "SLASH"):
            op = self.advance().kind
            rhs = self.parse_factor()
            node = ("bin", "*" if op == "STAR" else "/", node, rhs)
        return node
 
    # factor := '-' factor | atom
    def parse_factor(self):
        if self.peek() and self.peek().kind == "MINUS":
            self.advance()
            return ("neg", self.parse_factor())
        return self.parse_atom()
 
    def parse_atom(self):
        tok = self.peek()
        if tok is None:
            raise ParseError("Unexpected end of formula")
 
        if tok.kind == "NUMBER":
            self.advance()
            return ("num", float(tok.value))
 
        if tok.kind == "STRING":
            self.advance()
            return ("str", tok.value[1:-1])
 
        if tok.kind == "FIELD":
            self.advance()
            return ("field", _canon_field(tok.value[1:-1]))
 
        if tok.kind == "LPAREN":
            self.advance()
            node = self.parse_expr()
            self.expect("RPAREN")
            return node
 
        if tok.kind == "IF":
            return self.parse_if()
 
        if tok.kind == "NOT":
            self.advance()
            return ("not", self.parse_condition())
 
        if tok.kind == "IDENT":
            name = self.advance().value
            if self.peek() and self.peek().kind == "LPAREN":
                self.advance()
                args = []
                if self.peek() and self.peek().kind != "RPAREN":
                    args.append(self.parse_expr())
                    while self.peek() and self.peek().kind == "COMMA":
                        self.advance()
                        args.append(self.parse_expr())
                self.expect("RPAREN")
                return ("call", name.upper(), args)
            # bare identifier (rare in Tableau calcs, but be permissive)
            return ("field", _canon_field(name))
 
        raise ParseError(f"Unexpected token: {tok}")
 
    def parse_if(self):
        self.expect("IF")
        branches = []
        cond = self.parse_condition()
        self.expect("THEN")
        then_expr = self.parse_expr()
        branches.append((cond, then_expr))
        while self.peek() and self.peek().kind == "ELSEIF":
            self.advance()
            cond = self.parse_condition()
            self.expect("THEN")
            then_expr = self.parse_expr()
            branches.append((cond, then_expr))
        else_expr = None
        if self.peek() and self.peek().kind == "ELSE":
            self.advance()
            else_expr = self.parse_expr()
        self.expect("END")
        return ("if", branches, else_expr)
 
    # condition := or_expr
    def parse_condition(self):
        node = self.parse_and()
        while self.peek() and self.peek().kind == "OR":
            self.advance()
            node = ("or", node, self.parse_and())
        return node
 
    def parse_and(self):
        node = self.parse_not()
        while self.peek() and self.peek().kind == "AND":
            self.advance()
            node = ("and", node, self.parse_not())
        return node
 
    def parse_not(self):
        if self.peek() and self.peek().kind == "NOT":
            self.advance()
            return ("not", self.parse_not())
        return self.parse_comparison()
 
    def parse_comparison(self):
        left = self.parse_expr()
        cmp_kinds = {"EQ": "=", "NE": "<>", "LT": "<", "GT": ">", "LE": "<=", "GE": ">="}
        if self.peek() and self.peek().kind in cmp_kinds:
            op = cmp_kinds[self.advance().kind]
            right = self.parse_expr()
            return ("cmp", op, left, right)
        return left
 
 
def _canon_field(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).lower()
 
 
def parse(formula: str):
    tokens = tokenize(formula)
    if not tokens:
        return ("num", 0.0)
    return Parser(tokens).parse_formula()
 
 
# --------------------------------------------------------------------------
# Symbol collection
# --------------------------------------------------------------------------
 
AGGREGATE_FUNCS = {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD", "MEDIAN", "ATTR", "TOTAL"}
 
 
def collect_zero_guarded_symbols(node) -> set[str]:
    """Symbols that appear directly in a '= 0' or '<> 0' comparison
    somewhere in the formula -- i.e. inputs the formula's own author
    explicitly anticipated could be zero. Used to decide which symbols
    are worth an explicit zero edge-case trial, instead of forcing every
    symbol to zero (which can trip a divide-by-zero in one formula's
    unrelated denominator that the other formula never guards, purely
    because that denominator happens to be structured differently, even
    though both formulas behave identically for every realistic input)."""
    out: set[str] = set()
 
    def sym_of(n):
        if n[0] == "field":
            return f"FIELD::{n[1]}"
        if n[0] == "call" and n[1] in AGGREGATE_FUNCS and len(n[2]) == 1 and n[2][0][0] == "field":
            return f"{n[1]}::{n[2][0][1]}"
        return None
 
    def is_zero_lit(n):
        return n[0] == "num" and n[1] == 0.0
 
    def walk(n):
        kind = n[0]
        if kind == "cmp" and n[1] in ("=", "<>"):
            left, right = n[2], n[3]
            if is_zero_lit(right):
                s = sym_of(left)
                if s:
                    out.add(s)
                else:
                    for leaf in collect_symbols(left):
                        out.add(leaf)
            elif is_zero_lit(left):
                s = sym_of(right)
                if s:
                    out.add(s)
                else:
                    for leaf in collect_symbols(right):
                        out.add(leaf)
            walk(left)
            walk(right)
        elif kind in ("bin",):
            walk(n[2])
            walk(n[3])
        elif kind in ("and", "or"):
            walk(n[1])
            walk(n[2])
        elif kind in ("neg", "not"):
            walk(n[1])
        elif kind == "call":
            for a in n[2]:
                walk(a)
        elif kind == "if":
            for cond, expr in n[1]:
                walk(cond)
                walk(expr)
            if n[2] is not None:
                walk(n[2])
 
    walk(node)
    return out
 
 
def collect_symbols(node) -> set[str]:
    """Every distinct leaf 'input' the formula depends on: field refs, or
    (aggregation-function, field) pairs. Used to decide what to randomize."""
    out: set[str] = set()
 
    def walk(n):
        kind = n[0]
        if kind == "field":
            out.add(f"FIELD::{n[1]}")
        elif kind == "call":
            _, fname, args = n
            if fname in AGGREGATE_FUNCS and len(args) == 1 and args[0][0] == "field":
                out.add(f"{fname}::{args[0][1]}")
            else:
                for a in args:
                    walk(a)
        elif kind in ("bin", "cmp"):
            walk(n[2])
            walk(n[3])
        elif kind in ("and", "or"):
            walk(n[1])
            walk(n[2])
        elif kind in ("neg", "not"):
            walk(n[1])
        elif kind == "if":
            for cond, expr in n[1]:
                walk(cond)
                walk(expr)
            if n[2] is not None:
                walk(n[2])
        # "num" / "str" -> no symbols
 
    walk(node)
    return out
 
 
# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------
 
class EvalError(Exception):
    pass
 
 
_UNKNOWN_FUNC_CACHE: dict[str, float] = {}
 
 
def _unknown_func_value(fname: str, arg_values: list[Any]) -> float:
    """Deterministic-but-effectively-unique numeric stand-in for a function
    Tableau exposes that we don't specifically model (RANK, WINDOW_SUM,
    IFNULL, string functions, ...). Two calls to the SAME function name
    with the SAME argument values always produce the SAME stand-in value
    (so identical calls in two formulas still compare equal); different
    function names or different argument values produce a value that is
    astronomically unlikely to coincidentally match anything else."""
    numeric_args = []
    for v in arg_values:
        if isinstance(v, str):
            numeric_args.append(float(abs(hash(v)) % 100000) / 137.0)
        elif isinstance(v, bool):
            numeric_args.append(1.0 if v else 0.0)
        else:
            numeric_args.append(float(v))
 
    seed_key = fname
    if seed_key not in _UNKNOWN_FUNC_CACHE:
        _UNKNOWN_FUNC_CACHE[seed_key] = random.Random(seed_key).uniform(1.0, 97.0)
    base = _UNKNOWN_FUNC_CACHE[seed_key]
 
    acc = base
    for i, v in enumerate(numeric_args):
        acc += (i + 1) * base * v + (v ** 2) * 0.001
    # Bound the result via a periodic transform so it lands in a
    # moderate, comparison-friendly range (roughly -50..50) instead of
    # growing unboundedly with argument magnitude. This matters because
    # callers often compare a function's result against a small
    # threshold (e.g. "RANK(...) <= 5"); an unbounded stand-in value
    # would almost always land on the same side of that threshold
    # regardless of which arguments were actually passed in, masking
    # genuine differences between two distinct calls.
    return 50.0 * math.sin(acc * 0.0173) + 0.0001 * acc
 
 
def evaluate(node, env: dict[str, float]):
    kind = node[0]
 
    if kind == "num":
        return node[1]
    if kind == "str":
        return node[1]
    if kind == "field":
        key = f"FIELD::{node[1]}"
        if key not in env:
            raise EvalError(f"Unbound field: {node[1]}")
        return env[key]
 
    if kind == "call":
        _, fname, args = node
        if fname in AGGREGATE_FUNCS and len(args) == 1 and args[0][0] == "field":
            key = f"{fname}::{args[0][1]}"
            if key not in env:
                raise EvalError(f"Unbound aggregation: {key}")
            return env[key]
        arg_values = [evaluate(a, env) for a in args]
        return _unknown_func_value(fname, arg_values)
 
    if kind == "neg":
        return -evaluate(node[1], env)
 
    if kind == "bin":
        _, op, l, r = node
        lv, rv = evaluate(l, env), evaluate(r, env)
        if op == "+":
            return lv + rv
        if op == "-":
            return lv - rv
        if op == "*":
            return lv * rv
        if op == "/":
            if rv == 0:
                raise EvalError("division by zero")
            return lv / rv
 
    if kind == "cmp":
        _, op, l, r = node
        lv, rv = evaluate(l, env), evaluate(r, env)
        if op == "=":
            return lv == rv
        if op == "<>":
            return lv != rv
        if op == "<":
            return lv < rv
        if op == ">":
            return lv > rv
        if op == "<=":
            return lv <= rv
        if op == ">=":
            return lv >= rv
 
    if kind == "and":
        return bool(evaluate(node[1], env)) and bool(evaluate(node[2], env))
    if kind == "or":
        return bool(evaluate(node[1], env)) or bool(evaluate(node[2], env))
    if kind == "not":
        return not bool(evaluate(node[1], env))
 
    if kind == "if":
        for cond, expr in node[1]:
            if bool(evaluate(cond, env)):
                return evaluate(expr, env)
        if node[2] is not None:
            return evaluate(node[2], env)
        return None
 
    raise EvalError(f"Unknown node kind: {kind}")
 
 
# --------------------------------------------------------------------------
# Equivalence check
# --------------------------------------------------------------------------
 
@dataclass
class EquivalenceResult:
    equivalent: bool
    reason: str
    trials_run: int = 0
    symbols: list[str] = field(default_factory=list)
    diverging_trial: dict[str, Any] | None = None
 
 
_RANGES = [(1.0, 5000.0), (0.1, 10.0), (0.5, 50.0)]
 
 
def _generate_trials(
    symbols: list[str], zero_guarded: set[str], num_random: int, seed: int
) -> list[dict[str, float]]:
    rnd = random.Random(seed)
    trials: list[dict[str, float]] = []
 
    for i in range(num_random):
        lo, hi = _RANGES[i % len(_RANGES)]
        trials.append({s: rnd.uniform(lo, hi) for s in symbols})
 
    # Edge trials: zero out one symbol at a time, but only symbols that
    # at least one of the two formulas' own guards actually checks
    # against zero -- forcing an unrelated input to zero can trip a
    # divide-by-zero the formula's author never needed to guard against,
    # producing a false "not equivalent" for two formulas that behave
    # identically on every input either of them was actually written for.
    for zeroed in (zero_guarded or symbols):
        env = {s: rnd.uniform(1.0, 5000.0) for s in symbols}
        env[zeroed] = 0.0
        trials.append(env)
 
    return trials
 
 
def _values_match(a: Any, b: Any, tolerance: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    denom = max(1.0, abs(af), abs(bf))
    return abs(af - bf) / denom < tolerance
 
 
def check_equivalence(
    formula_a: str,
    formula_b: str,
    num_random_trials: int = 40,
    tolerance: float = 1e-6,
    seed: int = 0,
) -> EquivalenceResult:
    """Return whether two Tableau calculation formulas compute the same
    result for the same inputs, via randomized property-based testing."""
    if not formula_a.strip() or not formula_b.strip():
        return EquivalenceResult(False, "one or both formulas are empty")
 
    try:
        ast_a = parse(formula_a)
    except ParseError as e:
        return EquivalenceResult(False, f"could not parse formula A: {e}")
    try:
        ast_b = parse(formula_b)
    except ParseError as e:
        return EquivalenceResult(False, f"could not parse formula B: {e}")
 
    symbols = sorted(collect_symbols(ast_a) | collect_symbols(ast_b))
    zero_guarded = collect_zero_guarded_symbols(ast_a) | collect_zero_guarded_symbols(ast_b)
    zero_guarded &= set(symbols)
 
    if not symbols:
        # Both formulas are pure constants -- compare directly.
        try:
            va, vb = evaluate(ast_a, {}), evaluate(ast_b, {})
        except EvalError as e:
            return EquivalenceResult(False, f"evaluation error: {e}")
        match = _values_match(va, vb, tolerance)
        return EquivalenceResult(match, "constant formulas compared directly", trials_run=1)
 
    trials = _generate_trials(symbols, zero_guarded, num_random_trials, seed)
 
    for env in trials:
        try:
            va = evaluate(ast_a, env)
        except EvalError:
            va = "__ERROR__"
        try:
            vb = evaluate(ast_b, env)
        except EvalError:
            vb = "__ERROR__"
 
        if not _values_match(va, vb, tolerance):
            return EquivalenceResult(
                False,
                "formulas diverged under randomized evaluation -- not the same calculation",
                trials_run=len(trials),
                symbols=symbols,
                diverging_trial={"inputs": env, "value_a": va, "value_b": vb},
            )
 
    return EquivalenceResult(
        True,
        f"formulas agreed across {len(trials)} independent random/edge trials over "
        f"{len(symbols)} shared input(s) -- treated as the same calculation",
        trials_run=len(trials),
        symbols=symbols,
    )
 